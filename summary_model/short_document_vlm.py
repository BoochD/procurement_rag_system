from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from shared_modules.llm_models import OPENAI_VLM_MODEL
from summary_model.checks.normalization import normalize_decimal
from summary_model.commercial_offer_vlm import _call_vlm, _document_images, _page_text_payload
from summary_model.domain.models import DocumentType
from summary_model.extraction.structured_recovery import StructuredRecovery, recover_model
from summary_model.extraction_models import (
    ExplanatoryNoteSchema,
    MoneyValue,
    PurchaseRequestSchema,
)


SHORT_DOCUMENT_VLM_PROMPT_VERSION = "short-document-vlm-1.0.0"

PURCHASE_REQUEST_PDF_VLM_PROMPT = """
Ты извлекаешь обращение о проведении закупки из короткого PDF или скана.
Верни только строгую PurchaseRequestSchema.

Извлеки только явно видимые сведения: номер и дату обращения, предмет закупки,
НМЦК, способ закупки, основание закупки у единственного поставщика, срок,
этапы и список приложений. Для каждого приложения сохрани номер, название и
тип, если он виден. Не смешивай приложение с основным документом.

Даты верни в ISO-формате YYYY-MM-DD. Денежную сумму верни в nmck.raw как
видимый текст и в nmck.amount как число. Не придумывай значения: неизвестные
скаляры оставляй null, списки -- пустыми. В parser_warnings кратко укажи только
действительно нечитаемые существенные поля.
""".strip()

EXPLANATORY_NOTE_PDF_VLM_PROMPT = """
Ты извлекаешь пояснительную записку из короткого PDF или скана.
Верни только строгую ExplanatoryNoteSchema.

Извлеки только явно видимые сведения: предмет закупки, НМЦК, способ закупки и
полный текст обоснования. Не сокращай обоснование до одной фразы, если в нём
есть несколько видимых причин или нормативных ссылок. Денежную сумму верни в
nmck.raw как видимый текст и в nmck.amount как число. Не придумывай значения:
неизвестные скаляры оставляй null. В parser_warnings кратко укажи только
действительно нечитаемые существенные поля.
""".strip()


@dataclass
class ShortDocumentVlmOptions:
    enabled: bool = True
    model: str = OPENAI_VLM_MODEL
    max_pages: int = 4
    pdf_zoom: float = 2.0


@dataclass
class ShortDocumentVlmResult:
    document: PurchaseRequestSchema | ExplanatoryNoteSchema
    metrics: dict[str, object] = field(default_factory=dict)


def extract_short_document_with_vlm(
    path: Path,
    document_type: DocumentType,
    *,
    options: ShortDocumentVlmOptions | None = None,
) -> ShortDocumentVlmResult:
    schema, prompt, schema_name = _schema_prompt_for_type(document_type)
    options = options or ShortDocumentVlmOptions()
    base = schema(document_title=path.name, parser_warnings=[])
    started = time.perf_counter()
    if not options.enabled:
        base.parser_warnings.append("VLM parsing disabled for PDF document.")
        return ShortDocumentVlmResult(base, {"enabled": False})

    try:
        images = _document_images(path, max_pages=options.max_pages, pdf_zoom=options.pdf_zoom)
    except Exception as error:
        base.parser_warnings.append(f"Не удалось подготовить PDF для VLM: {error}")
        return ShortDocumentVlmResult(
            base,
            _error_metrics(started, error, calls=0),
        )
    if not images:
        base.parser_warnings.append("Не удалось получить изображения страниц PDF.")
        return ShortDocumentVlmResult(base, _error_metrics(started, "no_images", calls=0))

    payload = {
        "schema_version": "short-document-vlm-payload-1.0.0",
        "prompt_version": SHORT_DOCUMENT_VLM_PROMPT_VERSION,
        "file_name": path.name,
        "document_type": document_type.value,
        "pages": [item["page"] for item in images],
        "page_texts": _page_text_payload(images),
    }
    try:
        response = _call_vlm(
            images,
            payload=payload,
            model=options.model,
            system_prompt=prompt,
            schema=schema,
            schema_name=schema_name,
        )
        content = response["choices"][0]["message"]["content"]
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("VLM returned a non-object JSON payload.")
        data.setdefault("document_title", path.name)
        data = _normalize_short_document_payload(data, document_type)
        recovery = recover_model(schema, data)
        if not isinstance(recovery.value, schema):
            raise ValueError(recovery.error or "VLM response did not match document schema.")
        document = recovery.value
        return ShortDocumentVlmResult(
            document,
            {
                "enabled": True,
                "calls": 1,
                "pages": [item["page"] for item in images],
                "usage": response.get("usage"),
                "structured_recovery": _recovery_metrics(recovery),
                "duration_seconds": round(time.perf_counter() - started, 3),
            },
        )
    except Exception as error:
        base.parser_warnings.append(
            f"VLM не вернула корректную схему документа: {type(error).__name__}."
        )
        return ShortDocumentVlmResult(base, _error_metrics(started, error, calls=1))


def _schema_prompt_for_type(
    document_type: DocumentType,
) -> tuple[type[PurchaseRequestSchema] | type[ExplanatoryNoteSchema], str, str]:
    if document_type == DocumentType.REQUEST:
        return PurchaseRequestSchema, PURCHASE_REQUEST_PDF_VLM_PROMPT, "purchase_request_extraction"
    if document_type == DocumentType.EXPLANATORY_NOTE:
        return ExplanatoryNoteSchema, EXPLANATORY_NOTE_PDF_VLM_PROMPT, "explanatory_note_extraction"
    raise ValueError(f"Unsupported short PDF document type: {document_type.value}")


def _normalize_short_document_payload(data: dict[str, object], document_type: DocumentType) -> dict[str, object]:
    normalized = dict(data)
    if document_type == DocumentType.REQUEST:
        value = normalized.get("request_date")
        if isinstance(value, str):
            try:
                normalized["request_date"] = datetime.strptime(value.strip(), "%d.%m.%Y").date()
            except ValueError:
                pass
    nmck = normalized.get("nmck")
    if isinstance(nmck, dict):
        normalized_nmck = dict(nmck)
        amount = normalized_nmck.get("amount")
        if isinstance(amount, str):
            normalized_amount = normalize_decimal(amount)
            if normalized_amount is not None:
                normalized_nmck["amount"] = normalized_amount
        normalized["nmck"] = normalized_nmck
    return normalized


def _recovery_metrics(recovery: StructuredRecovery) -> dict[str, object]:
    return {
        "status": recovery.status,
        "warnings": recovery.all_warnings,
        "lossy_warnings": recovery.lossy_warnings,
    }


def _error_metrics(started: float, error: object, *, calls: int) -> dict[str, object]:
    return {
        "enabled": True,
        "calls": calls,
        "errors": [str(error)],
        "duration_seconds": round(time.perf_counter() - started, 3),
    }
