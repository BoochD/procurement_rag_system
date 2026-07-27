from __future__ import annotations

import base64
import json
import mimetypes
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from shared_modules.llm_models import OPENAI_MODEL, get_chatGPT_client
from summary_model.checks.normalization import normalize_decimal
from summary_model.extraction_models import CommercialOfferItem, CommercialOfferSchema, MoneyValue


COMMERCIAL_OFFER_VLM_PROMPT_VERSION = "commercial-offer-vlm-1.2.0"


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
- Если таблица содержит агрегатную строку `1` и детальные строки `1.1`, `1.2`,
  ..., агрегатную строку не добавляй как отдельный item: извлекай только
  детальные строки. Строки этапов услуг при этом являются реальными позициями.
- В payload может быть приложен извлечённый текст PDF. Используй его вместе с
  изображениями: числа и реквизиты из текстового слоя обычно точнее OCR.
- Если страница является только техническим приложением с характеристиками и
  не содержит ценовых строк КП, не превращай комплектующие/характеристики в
  items. Верни пустой items для такой страницы.
- При обработке одной страницы многостраничного КП извлекай только видимую на
  ней часть. Не повторяй агрегатные строки и не восстанавливай невидимые строки.
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

    page_errors: list[str] = []
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

    deterministic_offer = _offer_from_embedded_text(path.name, images)
    payload = {
        "schema_version": "commercial-offer-vlm-payload-1.0.0",
        "prompt_version": COMMERCIAL_OFFER_VLM_PROMPT_VERSION,
        "file_name": path.name,
        "pages": [item["page"] for item in images],
        "page_texts": _page_text_payload(images),
    }
    try:
        if _embedded_text(images):
            vlm_offer, responses = _extract_vlm_offer(
                images,
                payload=payload,
                model=options.model,
                file_name=path.name,
            )
        else:
            page_offers: list[CommercialOfferSchema] = []
            responses = []
            for item in images:
                page_payload = {
                    **payload,
                    "pages": [item["page"]],
                    "page_texts": [],
                }
                try:
                    page_offer, page_responses = _extract_vlm_offer(
                        [item],
                        payload=page_payload,
                        model=options.model,
                        file_name=path.name,
                    )
                except Exception as error:
                    page_errors.append(f"страница {item['page']}: {type(error).__name__}: {error}")
                    continue
                page_offers.append(page_offer)
                responses.extend(page_responses)
            if not page_offers:
                raise ValueError("VLM не вернула корректную схему ни для одной страницы КП.")
            vlm_offer = _merge_page_offers(page_offers, path.name)
            if page_errors:
                vlm_offer.parser_warnings.append(
                    "Часть страниц КП не распознана: " + "; ".join(page_errors)
                )

        offer = _merge_offer_with_deterministic(vlm_offer, deterministic_offer)
        if not _offer_has_content(offer):
            offer.parser_warnings.append("VLM не распознала реквизиты и ценовые строки КП.")
        metrics = {
            "enabled": True,
            "calls": len(responses),
            "pages": [item["page"] for item in images],
            "usage": [response.get("usage") for response in responses if response.get("usage")],
            "embedded_text_characters": len(_embedded_text(images)),
            "duration_seconds": round(time.perf_counter() - started, 3),
        }
        if page_errors:
            metrics["errors"] = page_errors
        return CommercialOfferVlmResult(offer, metrics)
    except (json.JSONDecodeError, KeyError, ValidationError, Exception) as error:
        deterministic_offer.parser_warnings.append(
            f"VLM не вернула корректную схему КП: {type(error).__name__}. Использован текстовый слой PDF."
        )
        return CommercialOfferVlmResult(
            deterministic_offer,
            {
                "enabled": True,
                "calls": 1 if _embedded_text(images) else len(images),
                "errors": [str(error)],
                "duration_seconds": round(time.perf_counter() - started, 3),
            },
        )


def _extract_vlm_offer(
    images: list[dict[str, Any]],
    *,
    payload: dict[str, Any],
    model: str,
    file_name: str,
) -> tuple[CommercialOfferSchema, list[dict[str, Any]]]:
    response = _call_vlm(images, payload=payload, model=model)
    content = response["choices"][0]["message"]["content"]
    data = json.loads(content)
    if isinstance(data, dict):
        data.setdefault("document_title", file_name)
        data.setdefault("source_pages", [item["page"] for item in images])
    return CommercialOfferSchema.model_validate(data), [response]


def _merge_page_offers(
    offers: list[CommercialOfferSchema],
    file_name: str,
) -> CommercialOfferSchema:
    result = CommercialOfferSchema(document_title=file_name)
    scalar_fields = (
        "supplier_name",
        "inn",
        "outgoing_number",
        "outgoing_date",
        "offer_date",
        "purchase_subject",
        "delivery_term_text",
        "delivery_place",
        "advance_payment_text",
        "vat_text",
        "vat_rate",
        "vat_included",
        "vat_amount",
        "total_amount",
    )
    merged_items: list[CommercialOfferItem] = []
    seen_items: set[tuple[str, str]] = set()
    for offer in offers:
        for field_name in scalar_fields:
            value = getattr(offer, field_name)
            if getattr(result, field_name) in (None, "") and value not in (None, ""):
                setattr(result, field_name, value)
        result.source_pages.extend(offer.source_pages)
        for item in offer.items:
            if item.unit_price is None and item.total_price is None:
                continue
            key = (
                str(item.row_number or "").strip(),
                " ".join(str(item.name or "").casefold().split())[:180],
            )
            if key in seen_items:
                continue
            seen_items.add(key)
            merged_items.append(item)
        result.parser_warnings.extend(offer.parser_warnings)
    result.items = merged_items
    result.source_pages = sorted(set(result.source_pages))
    result.parser_warnings = list(dict.fromkeys(result.parser_warnings))
    return result


def _offer_has_content(offer: CommercialOfferSchema) -> bool:
    return bool(
        offer.supplier_name
        or offer.outgoing_number
        or offer.total_amount
        or offer.items
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
                    "text": page.get_text("text"),
                }
            )
    return result


def _page_text_payload(images: list[dict[str, Any]], *, total_limit: int = 40000) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    remaining = total_limit
    for item in images:
        text = str(item.get("text") or "").strip()
        if not text or remaining <= 0:
            continue
        text = text[:remaining]
        result.append({"page": item["page"], "text": text})
        remaining -= len(text)
    return result


def _embedded_text(images: list[dict[str, Any]]) -> str:
    return "\n".join(str(item.get("text") or "") for item in images).strip()


def _offer_from_embedded_text(file_name: str, images: list[dict[str, Any]]) -> CommercialOfferSchema:
    text = _embedded_text(images)
    offer = CommercialOfferSchema(
        document_title=file_name,
        source_pages=[int(item["page"]) for item in images if str(item.get("text") or "").strip()],
    )
    if not text:
        return offer

    header = re.search(
        r"(?m)^\s*(?P<date>\d{2}\.\d{2}\.\d{4})\s*№\s*(?P<number>[^\r\n]+?)\s*$",
        text,
    )
    if header:
        offer.outgoing_date = _parse_date(header.group("date"))
        offer.offer_date = offer.outgoing_date
        offer.outgoing_number = header.group("number").strip()

    supplier = re.search(
        r"\b(?P<supplier>ООО\s+(?:«[^»]+»|\"[^\"]+\"|[А-ЯЁA-Z][^,\n]{1,60}?))\s+предлагает\b",
        text,
        flags=re.IGNORECASE,
    )
    if supplier:
        offer.supplier_name = " ".join(supplier.group("supplier").split())

    total = re.search(
        r"Сумма\s+коммерческого\s+предложения\s+составляет\s*:\s*"
        r"(?P<amount>\d[\d\s\u00a0]*(?:[,.]\d{2})?)",
        text,
        flags=re.IGNORECASE,
    )
    if total:
        raw_amount = total.group("amount").strip()
        offer.total_amount = MoneyValue(raw=raw_amount, amount=normalize_decimal(raw_amount))

    delivery_term = re.search(
        r"(?mi)^\s*Срок\s+(?:поставки|оказания|выполнения)[^:\r\n]*:\s*(?P<value>[^\r\n]+)",
        text,
    )
    if delivery_term:
        offer.delivery_term_text = delivery_term.group("value").strip()

    has_without_vat = bool(re.search(r"\bбез\s+НДС\b", text, flags=re.IGNORECASE))
    has_vat_rate = bool(re.search(r"(?im)^\s*(?:20|22)\s*$", text))
    if has_without_vat and has_vat_rate:
        offer.vat_text = "В КП есть позиции с НДС и позиции без НДС."
    elif has_without_vat:
        offer.vat_text = "НДС не облагается."

    offer.items = _offer_items_from_text(text)
    return offer


def _offer_items_from_text(text: str) -> list[CommercialOfferItem]:
    lines = [" ".join(line.split()) for line in text.replace("\u00a0", " ").splitlines()]
    markers = [
        (index, line)
        for index, line in enumerate(lines)
        if re.fullmatch(r"\d+\.\d+", line)
    ]
    items: list[CommercialOfferItem] = []
    for marker_index, (start, row_number) in enumerate(markers):
        end = markers[marker_index + 1][0] if marker_index + 1 < len(markers) else len(lines)
        segment = [line for line in lines[start + 1:end] if line]
        if marker_index + 1 == len(markers):
            for stop_index, line in enumerate(segment):
                if re.match(r"Сумма\s+коммерческого\s+предложения", line, flags=re.IGNORECASE):
                    segment = segment[:stop_index]
                    break
        unit_index = next((i for i, line in enumerate(segment) if _looks_like_unit(line)), None)
        if unit_index is None or len(segment) < unit_index + 5:
            continue
        value_lines = segment[unit_index + 1:unit_index + 5]
        quantity = normalize_decimal(value_lines[0])
        unit_price = normalize_decimal(value_lines[1])
        vat_text = value_lines[2]
        total_price = normalize_decimal(value_lines[3])
        if quantity is None or unit_price is None or total_price is None:
            continue
        raw_name = " ".join(segment[:unit_index]).strip()
        registry_notes = re.findall(r"№\s*реестровой\s+записи\s+\d+", raw_name, flags=re.IGNORECASE)
        name = re.sub(r"№\s*реестровой\s+записи\s+\d+", "", raw_name, flags=re.IGNORECASE).strip(" ,")
        trademark = None
        trademark_match = re.search(r"товарный\s+знак\s+(.+)$", name, flags=re.IGNORECASE)
        if trademark_match:
            trademark = trademark_match.group(1).strip()
        vat_rate = normalize_decimal(vat_text) if re.fullmatch(r"\d+(?:[,.]\d+)?", vat_text) else None
        items.append(
            CommercialOfferItem(
                row_number=row_number,
                name=name,
                trademark=trademark,
                unit=segment[unit_index],
                quantity=quantity,
                quantity_raw=value_lines[0],
                unit_price=unit_price,
                unit_price_raw=value_lines[1],
                total_price=total_price,
                total_price_raw=value_lines[3],
                vat_rate=vat_rate,
                vat_text=vat_text,
                notes=registry_notes,
                evidence_text="PDF text layer",
            )
        )
    return items


def _looks_like_unit(value: str) -> bool:
    normalized = re.sub(r"[^а-яa-z]", "", value.casefold().replace("ё", "е"))
    return normalized in {"шт", "штука", "штук", "услед", "услуга", "компл", "комплект"}


def _parse_date(value: str) -> Any:
    try:
        return datetime.strptime(value, "%d.%m.%Y").date()
    except ValueError:
        return None


def _merge_offer_with_deterministic(
    vlm_offer: CommercialOfferSchema,
    deterministic_offer: CommercialOfferSchema,
) -> CommercialOfferSchema:
    data = vlm_offer.model_dump(mode="python")
    deterministic_fields = (
        "supplier_name",
        "outgoing_number",
        "outgoing_date",
        "offer_date",
        "delivery_term_text",
        "vat_text",
        "total_amount",
    )
    for field_name in deterministic_fields:
        value = getattr(deterministic_offer, field_name)
        if value is not None and value != "":
            data[field_name] = value
    if deterministic_offer.source_pages:
        data["source_pages"] = sorted(set(vlm_offer.source_pages + deterministic_offer.source_pages))

    deterministic_by_row = {
        str(item.row_number): item
        for item in deterministic_offer.items
        if item.row_number is not None
    }
    vlm_by_row = {
        str(item.row_number): item
        for item in vlm_offer.items
        if item.row_number is not None
    }
    if deterministic_by_row:
        merged_items = []
        for row_number, deterministic_item in deterministic_by_row.items():
            vlm_item = vlm_by_row.get(row_number)
            item_data = vlm_item.model_dump(mode="python") if vlm_item else {}
            for field_name in (
                "row_number",
                "name",
                "unit",
                "quantity",
                "quantity_raw",
                "unit_price",
                "unit_price_raw",
                "total_price",
                "total_price_raw",
                "vat_rate",
                "vat_text",
                "notes",
                "evidence_text",
            ):
                value = getattr(deterministic_item, field_name)
                if value is not None and value != []:
                    item_data[field_name] = value
            if not item_data.get("trademark") and deterministic_item.trademark:
                item_data["trademark"] = deterministic_item.trademark
            merged_items.append(CommercialOfferItem.model_validate(item_data))
        data["items"] = merged_items

    warnings = list(dict.fromkeys(vlm_offer.parser_warnings + deterministic_offer.parser_warnings))
    if not vlm_offer.items and deterministic_offer.items:
        warnings.append("Позиции КП восстановлены из текстового слоя PDF.")
    data["parser_warnings"] = warnings
    return CommercialOfferSchema.model_validate(data)


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
