from __future__ import annotations

import hashlib
import re
from pathlib import Path

from summary_model.classification import DocumentClassifier
from summary_model.domain.models import DocumentIR, DocumentType, InputDocument
from summary_model.extraction_models import (
    CommercialOfferSchema,
    ContractSpecificationItem,
    ContractDraftSchema,
    DocumentEnvelope,
    ExplanatoryNoteSchema,
    ExtractionDocumentType,
    MoneyValue,
    NmckItem,
    NmckJustificationSchema,
    PercentValue,
    PriceSource,
    ProcurementPackageExtraction,
    PurchaseDescriptionSchema,
    PurchaseItem,
    PurchaseItemCharacteristic,
    PurchaseRequestSchema,
    RawField,
    RequestAttachment,
    ScheduleApplicationSchema,
    SecurityValue,
    SupplierPrice,
    TermValue,
)
from summary_model.ingestion import read_docx
from summary_model.tables import ParsedTable, extract_tables
from summary_model.tables.utils import (
    KTRU_RE,
    OKPD2_RE,
    clean_text,
    extract_money,
    is_empty_value,
    is_negative_value,
    normalize_document_title,
    normalize_key,
    parse_decimal,
    unique_codes,
)


TYPE_MAP: dict[DocumentType, ExtractionDocumentType] = {
    DocumentType.REQUEST: "purchase_request",
    DocumentType.PLAN: "schedule_application",
    DocumentType.ONMCK: "nmck_justification",
    DocumentType.OOZ: "purchase_description",
    DocumentType.CONTRACT: "contract_draft",
    DocumentType.EXPLANATORY_NOTE: "explanatory_note",
    DocumentType.COMMERCIAL_OFFER: "commercial_offer",
    DocumentType.UNKNOWN: "unknown",
}


def extract_package(documents: list[InputDocument]) -> ProcurementPackageExtraction:
    classifier = DocumentClassifier()
    files: list[DocumentEnvelope] = []
    parsed_by_document: list[tuple[InputDocument, DocumentIR, DocumentType, list[ParsedTable]]] = []
    package = ProcurementPackageExtraction(package_id=_package_id(documents))

    for document in documents:
        ir = read_docx(document.path)
        decision = classifier.classify(ir, document.type_hint)
        parsed_tables = extract_tables(ir, decision.document_type)
        parsed_by_document.append((document, ir, decision.document_type, parsed_tables))
        files.append(_envelope(document, ir, decision.document_type, decision.confidence, parsed_tables))

    package.files = files
    package.tables = [
        {
            "document_id": ir.document_id,
            "file_name": ir.file_name,
            **table.model_dump(mode="json"),
        }
        for _, ir, _, tables in parsed_by_document
        for table in tables
    ]

    for _, ir, document_type, tables in parsed_by_document:
        if document_type == DocumentType.PLAN:
            package.schedule_application = _schedule_application(ir, tables)
        elif document_type == DocumentType.REQUEST:
            package.purchase_request = _purchase_request(ir, tables)
        elif document_type == DocumentType.ONMCK:
            package.nmck_justification = _nmck_justification(ir, tables)
        elif document_type == DocumentType.OOZ:
            package.purchase_description = _purchase_description(ir, tables)
        elif document_type == DocumentType.CONTRACT:
            package.contract_draft = _contract_draft(ir, tables)
        elif document_type == DocumentType.EXPLANATORY_NOTE:
            package.explanatory_note = _explanatory_note(ir, tables)
        elif document_type == DocumentType.COMMERCIAL_OFFER:
            package.commercial_offers.append(_commercial_offer(ir, tables))

    package.commercial_offers_found_count = len(package.commercial_offers)
    package.commercial_offers_missing = (
        package.commercial_offers_found_count < package.commercial_offers_required_count
    )
    if package.commercial_offers_missing:
        package.package_warnings.append(
            "Commercial offers are missing or fewer than the required count."
        )
    return package


def _package_id(documents: list[InputDocument]) -> str:
    digest = hashlib.sha256()
    for document in sorted(documents, key=lambda item: str(item.path)):
        path = Path(document.path)
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return f"extraction-{digest.hexdigest()[:16]}"


def _document_text(ir: DocumentIR) -> str:
    return "\n".join(
        block.text
        for block in ir.blocks
        if block.type == "paragraph" and block.text
    )


def _title(ir: DocumentIR) -> str | None:
    for block in ir.blocks:
        if block.type == "paragraph" and block.text:
            return normalize_document_title(block.text)
    return None


def _envelope(
    document: InputDocument,
    ir: DocumentIR,
    document_type: DocumentType,
    confidence: float,
    tables: list[ParsedTable],
) -> DocumentEnvelope:
    text = _document_text(ir)
    return DocumentEnvelope(
        file_name=ir.file_name,
        file_path=str(document.path),
        document_type=TYPE_MAP[document_type],
        document_title=_title(ir),
        confidence=confidence,
        evidence=[table.title for table in tables if table.title][:3],
        parser_warnings=[warning for table in tables for warning in table.parser_warnings],
        extracted_text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None,
        raw_text_preview=text[:1000] if text else None,
    )


def _raw_fields(tables: list[ParsedTable]) -> list[RawField]:
    result = []
    for table in tables:
        if table.table_type not in {"schedule_application_table", "generic_table"}:
            continue
        for field in table.compact_json.get("raw_fields", []):
            key = clean_text(field.get("key"))
            value = clean_text(field.get("value")) or None
            if not key:
                continue
            result.append(
                RawField(
                    key=key,
                    value=value,
                    normalized_key=normalize_key(key),
                    is_empty=is_empty_value(value),
                    is_negative_value=is_negative_value(value),
                    evidence=f"{table.table_id}:r{field.get('row_index')}",
                )
            )
    return result


def _field_value(fields: list[RawField], *markers: str) -> str | None:
    marker_values = [marker.casefold() for marker in markers]
    for field in fields:
        key = field.key.casefold()
        if any(marker in key for marker in marker_values):
            return field.value
    return None


def _money_value(text: str | None) -> MoneyValue | None:
    raw, amount = extract_money(text)
    if raw is None and amount is None:
        return None
    return MoneyValue(raw=text or raw, amount=amount)


def _term_value(text: str | None) -> TermValue | None:
    text = clean_text(text)
    if not text:
        return None
    match = re.search(r"(\d+)\s+(рабоч|календар)", text.casefold())
    day_type = "unknown"
    if match and "рабоч" in match.group(2):
        day_type = "working"
    elif match and "календар" in match.group(2):
        day_type = "calendar"
    return TermValue(
        raw=text,
        days=int(match.group(1)) if match else None,
        day_type=day_type,
        start_event=_infer_start_event(text),
    )


def _infer_start_event(text: str) -> str | None:
    lowered = text.casefold()
    if "со дня, следующего" in lowered and "заключ" in lowered:
        return "next_day_after_contract_signing"
    if "с даты заключ" in lowered or "со дня заключ" in lowered:
        return "contract_signing"
    return None


def _bool_from_text(text: str | None) -> bool | None:
    text = clean_text(text).casefold()
    if not text:
        return None
    if is_negative_value(text):
        return False
    if any(marker in text for marker in ("да", "установлено", "предусмотрено", "требуется")):
        return True
    return None


def _security_value(text: str | None) -> SecurityValue | None:
    text = clean_text(text)
    if not text:
        return None
    lowered = text.casefold()
    is_not_required = is_negative_value(text) or any(
        marker in lowered
        for marker in (
            "не предусмотр",
            "не установлен",
            "не требует",
            "не предостав",
            "отсутств",
        )
    )
    percent = re.search(r"(\d+(?:[,.]\d+)?)\s*%", text)
    money = None if is_not_required else _money_value(text)
    return SecurityValue(
        raw=text,
        value_percent=parse_decimal(percent.group(1)) if percent else None,
        value_amount=money.amount if money else None,
        is_not_required=is_not_required,
    )


def _schedule_application(ir: DocumentIR, tables: list[ParsedTable]) -> ScheduleApplicationSchema:
    fields = _raw_fields(tables)
    raw_dict = {field.key: field.value for field in fields}
    full_text = _document_text(ir) + "\n" + "\n".join(
        f"{field.key}: {field.value or ''}" for field in fields
    )
    delivery_text = _field_value(fields, "срок поставки", "срок выполнения")
    contract_term_text = _field_value(fields, "срок исполнения контракта")
    smp_raw = _field_value(fields, "преимуществ", "смп")
    subcontract_raw = _field_value(fields, "субподряд", "сонко")
    subcontract_percent_raw = _field_value(fields, "процент", "объем привлечения")
    return ScheduleApplicationSchema(
        document_title=_title(ir),
        raw_fields=fields,
        raw_fields_dict=raw_dict,
        empty_fields=[field.key for field in fields if field.is_empty],
        negative_value_fields=[field.key for field in fields if field.is_negative_value],
        purchase_subject=_field_value(fields, "наименование объекта закупки", "предмет закупки"),
        okpd2_codes=unique_codes(OKPD2_RE, full_text),
        ktru_codes=unique_codes(KTRU_RE, full_text),
        nmck=_money_value(_field_value(fields, "начальная", "нмцк", "цена контракта")),
        funding_source_text=_field_value(fields, "источник финансирования"),
        delivery_term_text=delivery_text,
        delivery_term=_term_value(delivery_text),
        contract_execution_term_text=contract_term_text,
        contract_execution_term=_term_value(contract_term_text),
        smp_preference_raw=smp_raw,
        smp_preference=_bool_from_text(smp_raw),
        subcontract_smp_sonko_required_raw=subcontract_raw,
        subcontract_smp_sonko_required=_bool_from_text(subcontract_raw),
        subcontract_smp_sonko_percent_raw=subcontract_percent_raw,
        subcontract_smp_sonko_percent=parse_decimal(subcontract_percent_raw),
        application_security_raw=_field_value(fields, "обеспечение заявки"),
        application_security=_security_value(_field_value(fields, "обеспечение заявки")),
        contract_security_raw=_field_value(fields, "обеспечение исполнения контракта"),
        contract_security=_security_value(_field_value(fields, "обеспечение исполнения контракта")),
        warranty_security_raw=_field_value(fields, "обеспечение гарантий"),
        warranty_security=_security_value(_field_value(fields, "обеспечение гарантий")),
        additional_requirements_raw=_field_value(fields, "дополнительные требования"),
        national_regime_raw=_field_value(fields, "национальный режим"),
    )


def _attachment_type(title: str) -> ExtractionDocumentType:
    lowered = title.casefold()
    if "заявк" in lowered or "план-график" in lowered:
        return "schedule_application"
    if "определение цены" in lowered or "обоснование" in lowered or "нмцк" in lowered:
        return "nmck_justification"
    if "описание объекта" in lowered:
        return "purchase_description"
    if "проект контракта" in lowered or "контракт" in lowered:
        return "contract_draft"
    if "пояснитель" in lowered:
        return "explanatory_note"
    if "коммерчес" in lowered or re.search(r"\bкп\b", lowered):
        return "commercial_offer"
    return "unknown"


def _attachment_kind(title: str) -> str:
    lowered = title.casefold()
    if "описание объекта" in lowered:
        return "purchase_description"
    if "акт" in lowered and ("приема" in lowered or "приём" in lowered or "передач" in lowered):
        return "acceptance_act_form"
    if "спецификац" in lowered:
        return "contract_specification"
    if lowered:
        return "other"
    return "unknown"


def _attachments(tables: list[ParsedTable]) -> list[RequestAttachment]:
    result = []
    for table in tables:
        if table.table_type not in {"request_attachments_table", "contract_attachments_table"}:
            continue
        for index, item in enumerate(table.compact_json.get("attachments", []), start=1):
            title = clean_text(item.get("title_raw"))
            if not title:
                continue
            result.append(
                RequestAttachment(
                    number=str(index),
                    title_raw=title,
                    normalized_document_type=_attachment_type(title),
                    attachment_kind=_attachment_kind(title),
                    evidence=f"{table.table_id}:r{item.get('row_index')}",
                )
            )
    return result


def _request_attachments(ir: DocumentIR, text: str, tables: list[ParsedTable]) -> list[RequestAttachment]:
    table_attachments = _attachments(tables)

    corpus = text + "\n" + "\n".join(
        table.compact_markdown
        for table in tables
        if table.table_type not in {"signature_table", "ignored_table"}
    ) + "\n" + _table_rows_text(ir)
    marker = re.search(r"приложени[ея]\s*:", corpus, flags=re.IGNORECASE)
    fallback = _numbered_attachments_from_chunk(corpus[marker.end() : marker.end() + 1800]) if marker else []
    if fallback and (not table_attachments or len(fallback) > len(table_attachments)):
        return fallback
    return table_attachments


def _table_rows_text(ir: DocumentIR) -> str:
    rows: list[str] = []
    for block in ir.blocks:
        table = block.table
        if table is None:
            continue
        for row in table.rows:
            values = [clean_text(value) for value in row.values.values() if clean_text(value)]
            if values:
                rows.append(" | ".join(values))
    return "\n".join(rows)


def _numbered_attachments_from_chunk(chunk: str) -> list[RequestAttachment]:
    stop = re.search(r"обязательный пакет|с уважением|подпис", chunk, flags=re.IGNORECASE)
    if stop:
        chunk = chunk[: stop.start()]

    result: list[RequestAttachment] = []
    seen_titles: set[str] = set()
    pattern = re.compile(r"(?:^|[\n;\t|])\s*(\d+)\.\s*([^\n;|]+)", flags=re.IGNORECASE)
    for match in pattern.finditer(chunk):
        title = clean_text(match.group(2)).rstrip(".")
        if not title:
            continue
        normalized_title = title.casefold()
        if normalized_title in seen_titles:
            continue
        seen_titles.add(normalized_title)
        result.append(
            RequestAttachment(
                number=match.group(1),
                title_raw=title,
                normalized_document_type=_attachment_type(title),
                attachment_kind=_attachment_kind(title),
                evidence="request_text:attachments",
            )
        )
    if result:
        return result

    cleaned_chunk = re.sub(r"^[\s|:\-]+", "", chunk)
    parts = cleaned_chunk.split(";") if ";" in cleaned_chunk else cleaned_chunk.splitlines()
    for index, part in enumerate(parts, start=1):
        title = clean_text(part).rstrip(".")
        if not title:
            continue
        normalized_title = title.casefold()
        if normalized_title in seen_titles:
            continue
        seen_titles.add(normalized_title)
        result.append(
            RequestAttachment(
                number=str(index),
                title_raw=title,
                normalized_document_type=_attachment_type(title),
                attachment_kind=_attachment_kind(title),
                evidence="request_text:attachments",
            )
        )
    return result


def _contract_referenced_attachments(text: str) -> list[RequestAttachment]:
    result: list[RequestAttachment] = []
    pattern = re.compile(
        r"приложени[ея]\s*№\s*(\d+)\s*[«\"]([^»\"\n;]+)[»\"]",
        flags=re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        title = clean_text(match.group(2))
        if not title:
            continue
        result.append(
            RequestAttachment(
                number=match.group(1),
                title_raw=title,
                normalized_document_type=_attachment_type(title),
                attachment_kind=_attachment_kind(title),
                evidence="contract_text:attachments",
            )
        )
    return result


def _contract_attachment_warnings(
    referenced: list[RequestAttachment],
    description_items: list[PurchaseItem],
    specification_items: list[ContractSpecificationItem],
) -> list[str]:
    warnings: list[str] = []
    expects_description = any(
        item.attachment_kind == "purchase_description"
        for item in referenced
    )
    expects_specification = any(
        item.attachment_kind == "contract_specification"
        for item in referenced
    )
    if expects_description and not description_items:
        warnings.append(
            "Contract references an 'Описание объекта закупки' attachment, "
            "but no embedded purchase-description item table was parsed."
        )
    if expects_specification and not specification_items:
        warnings.append(
            "Contract references a 'Спецификация' attachment, "
            "but no embedded specification item table was parsed."
        )
    return warnings


def _purchase_request(ir: DocumentIR, tables: list[ParsedTable]) -> PurchaseRequestSchema:
    text = _document_text(ir)
    nmck = _money_value(text)
    return PurchaseRequestSchema(
        document_title=_title(ir),
        purchase_subject=_line_after_marker(text, "предмет закупки", "объект закупки"),
        nmck=nmck,
        procurement_method_raw=_line_after_marker(text, "способ закупки"),
        procurement_method=_procurement_method(text),
        single_supplier_basis_text=_line_after_marker(text, "основание"),
        delivery_term_text=_line_after_marker(text, "срок поставки", "срок выполнения"),
        delivery_term=_term_value(_line_after_marker(text, "срок поставки", "срок выполнения")),
        attachments=_request_attachments(ir, text, tables),
    )


def _procurement_method(text: str) -> str | None:
    lowered = text.casefold()
    if "единствен" in lowered:
        return "single_supplier"
    if "аукцион" in lowered:
        return "auction"
    if "конкурс" in lowered:
        return "competition"
    if "котиров" in lowered:
        return "request_for_quotations"
    return None


def _line_after_marker(text: str, *markers: str) -> str | None:
    lowered_markers = [marker.casefold() for marker in markers]
    lines = [clean_text(line) for line in text.splitlines() if clean_text(line)]
    for index, line in enumerate(lines):
        lowered = line.casefold()
        if any(marker in lowered for marker in lowered_markers):
            if ":" in line:
                tail = clean_text(line.split(":", 1)[1])
                if tail:
                    return tail
            if index + 1 < len(lines):
                return lines[index + 1]
            return line
    return None


def _line_value_after_marker(text: str, *markers: str) -> str | None:
    lowered_markers = [marker.casefold() for marker in markers]
    lines = [clean_text(line) for line in text.splitlines() if clean_text(line)]
    for line in lines:
        lowered = line.casefold()
        if any(marker in lowered for marker in lowered_markers) and ":" in line:
            tail = clean_text(line.split(":", 1)[1])
            if tail:
                return tail
    return _line_after_marker(text, *markers)


def _purchase_items_from_tables(tables: list[ParsedTable]) -> list[PurchaseItem]:
    result = []
    for table in tables:
        if table.table_type != "ooz_items_table":
            continue
        for payload in table.compact_json.get("items", []):
            characteristics = [
                PurchaseItemCharacteristic(
                    name=characteristic.get("name"),
                    value=characteristic.get("value"),
                    unit=characteristic.get("unit"),
                    evidence=f"{table.table_id}:r{characteristic.get('row_index')}",
                )
                for characteristic in payload.get("characteristics", [])
            ]
            result.append(
                PurchaseItem(
                    row_number=payload.get("row_number"),
                    name=payload.get("name"),
                    okpd2_code=payload.get("okpd2_code"),
                    ktru_code=payload.get("ktru_code"),
                    unit=payload.get("unit"),
                    quantity=parse_decimal(payload.get("quantity_raw")),
                    quantity_raw=payload.get("quantity_raw"),
                    characteristics=characteristics,
                    evidence=f"{table.table_id}:r{payload.get('row_index')}",
                    parser_warnings=payload.get("parser_warnings", []),
                )
            )
    return result


def _contract_specification_items_from_tables(
    tables: list[ParsedTable],
) -> list[ContractSpecificationItem]:
    result = []
    for table in tables:
        if table.table_type != "contract_specification_table":
            continue
        for payload in table.compact_json.get("items", []):
            name = clean_text(payload.get("name"))
            if not name:
                continue
            result.append(
                ContractSpecificationItem(
                    row_number=payload.get("row_number"),
                    name=name,
                    description=payload.get("description"),
                    unit=payload.get("unit"),
                    quantity=parse_decimal(payload.get("quantity_raw")),
                    quantity_raw=payload.get("quantity_raw"),
                    unit_price_without_vat=parse_decimal(
                        payload.get("raw_unit_price_without_vat")
                    ),
                    unit_price_with_vat=parse_decimal(
                        payload.get("raw_unit_price_with_vat")
                    ),
                    total_without_vat=parse_decimal(payload.get("raw_total_without_vat")),
                    vat_rate=payload.get("vat_rate"),
                    vat_amount=parse_decimal(payload.get("raw_vat_amount")),
                    total_price=parse_decimal(payload.get("raw_total_price")),
                    raw_unit_price_without_vat=payload.get("raw_unit_price_without_vat"),
                    raw_unit_price_with_vat=payload.get("raw_unit_price_with_vat"),
                    raw_total_without_vat=payload.get("raw_total_without_vat"),
                    raw_vat_amount=payload.get("raw_vat_amount"),
                    raw_total_price=payload.get("raw_total_price"),
                    evidence=f"{table.table_id}:r{payload.get('row_index')}",
                )
            )
    return result


def _nmck_justification(ir: DocumentIR, tables: list[ParsedTable]) -> NmckJustificationSchema:
    text = _document_text(ir)
    sources: list[PriceSource] = []
    items: list[NmckItem] = []
    for table in tables:
        if table.table_type != "nmck_calculation_table":
            continue
        for source in table.compact_json.get("price_sources", []):
            raw_header = source.get("raw_header") or source["source_id"]
            sources.append(
                PriceSource(
                    source_id=source["source_id"],
                    supplier_name_raw=raw_header,
                    raw_header=raw_header,
                    evidence=table.table_id,
                )
            )
        for payload in table.compact_json.get("items", []):
            supplier_prices = [
                SupplierPrice(
                    source_id=price["source_id"],
                    unit_price=parse_decimal(price.get("raw_unit_price")),
                    row_total=parse_decimal(price.get("raw_row_total")),
                    raw_unit_price=price.get("raw_unit_price"),
                    raw_row_total=price.get("raw_row_total"),
                )
                for price in payload.get("supplier_prices", [])
            ]
            item = NmckItem(
                row_number=payload.get("row_number"),
                name=payload.get("name"),
                unit=payload.get("unit"),
                quantity=parse_decimal(payload.get("quantity_raw")),
                quantity_raw=payload.get("quantity_raw"),
                supplier_prices=supplier_prices,
                selected_min_unit_price=parse_decimal(payload.get("selected_min_unit_price_raw")),
                selected_min_unit_price_raw=payload.get("selected_min_unit_price_raw"),
                row_total_declared=parse_decimal(payload.get("row_total_declared_raw")),
                row_total_declared_raw=payload.get("row_total_declared_raw"),
                evidence=f"{table.table_id}:r{payload.get('row_index')}",
            )
            _calculate_nmck_item(item)
            items.append(item)
    return NmckJustificationSchema(
        document_title=_title(ir),
        nmck_method=_line_after_marker(text, "метод"),
        purchase_subject=_line_after_marker(text, "предмет закупки", "объект закупки"),
        total_amount=_money_value(text),
        total_amount_text=(_money_value(text).raw if _money_value(text) else None),
        price_sources=sources,
        items=items,
        variation_coefficient_raw=_line_after_marker(text, "коэффициент вариации"),
        variation_coefficient=parse_decimal(_line_after_marker(text, "коэффициент вариации")),
    )


def _calculate_nmck_item(item: NmckItem) -> None:
    prices = [
        price.unit_price
        for price in item.supplier_prices
        if price.unit_price is not None
    ]
    if prices:
        item.calculated_min_unit_price = min(prices)
        for price in item.supplier_prices:
            if price.unit_price == item.calculated_min_unit_price:
                item.min_price_source_id = price.source_id
                break
    if item.selected_min_unit_price is not None and item.calculated_min_unit_price is not None:
        item.is_declared_min_price_correct = (
            item.selected_min_unit_price == item.calculated_min_unit_price
        )
    if item.quantity is not None and item.selected_min_unit_price is not None:
        item.row_total_calculated = item.quantity * item.selected_min_unit_price
    if item.row_total_declared is not None and item.row_total_calculated is not None:
        item.is_row_total_correct = item.row_total_declared == item.row_total_calculated


def _purchase_description(ir: DocumentIR, tables: list[ParsedTable]) -> PurchaseDescriptionSchema:
    text = _document_text(ir)
    delivery_text = _line_after_marker(text, "срок поставки", "срок выполнения")
    return PurchaseDescriptionSchema(
        document_title=_title(ir),
        purchase_subject=_line_after_marker(text, "предмет закупки", "объект закупки"),
        delivery_place=_line_after_marker(text, "место поставки", "адрес поставки"),
        delivery_term_text=delivery_text,
        delivery_term=_term_value(delivery_text),
        items=_purchase_items_from_tables(tables),
        warranty_requirements_text=_line_after_marker(text, "гаранти"),
    )


def _contract_draft(ir: DocumentIR, tables: list[ParsedTable]) -> ContractDraftSchema:
    text = _document_text(ir)
    delivery_text = _line_after_marker(text, "срок поставки", "срок выполнения")
    contract_execution_text = _line_value_after_marker(text, "срок исполнения контракта")
    contract_security_text = _line_after_marker(text, "обеспечение исполнения контракта")
    warranty_security_text = _line_after_marker(text, "обеспечение гарантий")
    description_items = _purchase_items_from_tables(tables)
    specification_items = _contract_specification_items_from_tables(tables)
    table_attachments = _attachments(tables)
    referenced_attachments = _contract_referenced_attachments(text) or table_attachments
    embedded = PurchaseDescriptionSchema(
        items=description_items,
        parser_warnings=["Embedded purchase description inferred from contract tables."],
    )
    return ContractDraftSchema(
        document_title=_title(ir),
        contract_number=_line_after_marker(text, "контракт №", "контракт n"),
        subject=_line_after_marker(text, "предмет контракта", "предмет закупки"),
        price=_money_value(text),
        funding_source=_line_after_marker(text, "источник финансирования"),
        delivery_place=_line_after_marker(text, "место поставки", "адрес поставки"),
        delivery_term_text=delivery_text,
        delivery_term=_term_value(delivery_text),
        contract_execution_term_text=contract_execution_text,
        contract_execution_term=_term_value(contract_execution_text),
        warranty_text=_line_after_marker(text, "гаранти"),
        contract_security_raw=contract_security_text,
        contract_security=_security_value(contract_security_text),
        warranty_security_raw=warranty_security_text,
        warranty_security=_security_value(warranty_security_text),
        referenced_attachments=referenced_attachments,
        actual_attachments=table_attachments,
        embedded_purchase_description=embedded if embedded.items else None,
        items=description_items,
        specification_items=specification_items,
        parser_warnings=_contract_attachment_warnings(
            referenced_attachments,
            description_items,
            specification_items,
        ),
    )


def _explanatory_note(ir: DocumentIR, tables: list[ParsedTable]) -> ExplanatoryNoteSchema:
    text = _document_text(ir)
    return ExplanatoryNoteSchema(
        document_title=_title(ir),
        subject=_line_after_marker(text, "предмет закупки", "объект закупки"),
        nmck=_money_value(text),
        procurement_method_raw=_line_after_marker(text, "способ закупки"),
        procurement_method=_procurement_method(text),
        justification_text=_line_after_marker(text, "обоснование"),
    )


def _commercial_offer(ir: DocumentIR, tables: list[ParsedTable]) -> CommercialOfferSchema:
    text = _document_text(ir)
    return CommercialOfferSchema(
        document_title=_title(ir),
        supplier_name=_line_after_marker(text, "поставщик", "организация"),
        inn=next(iter(re.findall(r"\b\d{10}(?:\d{2})?\b", text)), None),
        outgoing_number=_line_after_marker(text, "исх", "исходящий"),
        items=_purchase_items_from_tables(tables),
        total_amount=_money_value(text),
    )
