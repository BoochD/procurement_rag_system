from __future__ import annotations

import base64
import json
import mimetypes
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from shared_modules.llm_models import OPENAI_MODEL, get_chatGPT_client
from summary_model.extraction_models import CommercialOfferSchema


COMMERCIAL_OFFER_VLM_PROMPT_VERSION = "commercial-offer-vlm-1.0.0"


@dataclass
class CommercialOfferVlmOptions:
    enabled: bool = True
    model: str = OPENAI_MODEL
    max_pages: int = 8
    pdf_zoom: float = 2.0


@dataclass
class CommercialOfferVlmResult:
    offer: CommercialOfferSchema
    metrics: dict[str, Any] = field(default_factory=dict)


COMMERCIAL_OFFER_VLM_PROMPT = """
Ты извлекаешь коммерческое предложение из PDF/скана/изображения.

Верни строгую CommercialOfferSchema.

Извлекай только видимые данные:
- поставщик, ИНН;
- исходящий номер и дату;
- дату КП;
- предмет закупки;
- строки ТРУ: наименование, ОКПД2/КТРУ если есть, товарный знак, модель,
  единица, количество, цена за единицу, итог строки;
- итоговую сумму КП;
- НДС: текст, ставка, сумма, включён/не включён;
- срок поставки/оказания услуг;
- место поставки/оказания услуг;
- авансовый платёж, если явно указан.

Правила:
- Не придумывай строки и реквизиты.
- Не пересчитывай НДС, если в документе неоднозначно.
- Если поле не видно или не уверенно распознано, оставь null и добавь
  понятное предупреждение в parser_warnings.
- Товарный знак и модель клади в отдельные поля item.trademark/item.model.
- source_pages заполни номерами страниц, с которых извлекались данные.
""".strip()


def extract_commercial_offer_with_vlm(
    path: Path,
    *,
    options: CommercialOfferVlmOptions | None = None,
) -> CommercialOfferVlmResult:
    options = options or CommercialOfferVlmOptions()
    started = time.perf_counter()
    base_offer = CommercialOfferSchema(
        document_title=path.name,
        parser_warnings=[],
    )
    if not options.enabled:
        base_offer.parser_warnings.append("VLM parsing disabled for commercial offer.")
        return CommercialOfferVlmResult(base_offer, {"enabled": False})

    try:
        images = _document_images(path, max_pages=options.max_pages, pdf_zoom=options.pdf_zoom)
    except Exception as error:
        base_offer.parser_warnings.append(f"Не удалось подготовить КП для VLM: {error}")
        return CommercialOfferVlmResult(
            base_offer,
            {
                "enabled": True,
                "calls": 0,
                "errors": [str(error)],
                "duration_seconds": round(time.perf_counter() - started, 3),
            },
        )

    if not images:
        base_offer.parser_warnings.append("Не удалось получить изображения страниц КП.")
        return CommercialOfferVlmResult(
            base_offer,
            {
                "enabled": True,
                "calls": 0,
                "errors": ["no_images"],
                "duration_seconds": round(time.perf_counter() - started, 3),
            },
        )

    payload = {
        "schema_version": "commercial-offer-vlm-payload-1.0.0",
        "prompt_version": COMMERCIAL_OFFER_VLM_PROMPT_VERSION,
        "file_name": path.name,
        "pages": [item["page"] for item in images],
    }
    try:
        response = _call_vlm(images, payload=payload, model=options.model)
        content = response["choices"][0]["message"]["content"]
        data = json.loads(content)
        if isinstance(data, dict):
            data.setdefault("document_title", path.name)
            data.setdefault("source_pages", [item["page"] for item in images])
        offer = CommercialOfferSchema.model_validate(data)
        metrics = {
            "enabled": True,
            "calls": 1,
            "pages": [item["page"] for item in images],
            "usage": response.get("usage"),
            "duration_seconds": round(time.perf_counter() - started, 3),
        }
        return CommercialOfferVlmResult(offer, metrics)
    except (json.JSONDecodeError, KeyError, ValidationError, Exception) as error:
        base_offer.parser_warnings.append(f"VLM не вернула корректную схему КП: {error}")
        return CommercialOfferVlmResult(
            base_offer,
            {
                "enabled": True,
                "calls": 1,
                "errors": [str(error)],
                "duration_seconds": round(time.perf_counter() - started, 3),
            },
        )


def _document_images(path: Path, *, max_pages: int, pdf_zoom: float) -> list[dict[str, Any]]:
    suffix = path.suffix.casefold()
    if suffix == ".pdf":
        return _pdf_images(path, max_pages=max_pages, pdf_zoom=pdf_zoom)
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        return [{"page": 1, "mime": mime, "data": path.read_bytes()}]
    raise ValueError(f"Unsupported commercial offer format for VLM: {path.suffix}")


def _pdf_images(path: Path, *, max_pages: int, pdf_zoom: float) -> list[dict[str, Any]]:
    try:
        import fitz  # type: ignore
    except ImportError as error:
        raise RuntimeError("Для VLM-парсинга PDF КП нужен пакет PyMuPDF.") from error

    result: list[dict[str, Any]] = []
    with fitz.open(path) as document:
        matrix = fitz.Matrix(pdf_zoom, pdf_zoom)
        for index, page in enumerate(document[:max_pages], start=1):
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            result.append(
                {
                    "page": index,
                    "mime": "image/png",
                    "data": pixmap.tobytes("png"),
                }
            )
    return result


def _call_vlm(
    images: list[dict[str, Any]],
    *,
    payload: dict[str, Any],
    model: str,
) -> dict[str, Any]:
    client = get_chatGPT_client()
    content: list[dict[str, Any]] = [
        {"type": "text", "text": json.dumps(payload, ensure_ascii=False, default=str)}
    ]
    for item in images:
        image_data = base64.b64encode(item["data"]).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{item['mime']};base64,{image_data}"},
            }
        )
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": COMMERCIAL_OFFER_VLM_PROMPT},
            {"role": "user", "content": content},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "commercial_offer_extraction",
                "schema": CommercialOfferSchema.model_json_schema(),
                "strict": True,
            },
        },
    )
    return response.model_dump(mode="json")
