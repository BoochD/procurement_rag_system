from __future__ import annotations

from decimal import Decimal

import hashlib
import re
from datetime import date
from pathlib import Path
from typing import Callable

from summary_model.classification import DocumentClassifier
from summary_model.domain.models import DocumentIR, DocumentType, InputDocument
from summary_model.extraction_models import (
    CommercialOfferItem,
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
    CodeReference,
    PriceSource,
    ProcurementStage,
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


TableRepairer = Callable[[DocumentIR, DocumentType, list[ParsedTable]], list[ParsedTable]]


class RequiredDocumentExtractionError(RuntimeError):
    """Raised when the mandatory plan document cannot be parsed."""


def extract_package(
    documents: list[InputDocument],
    *,
    table_repairer: TableRepairer | None = None,
    continue_on_document_error: bool = False,
) -> ProcurementPackageExtraction:
    classifier = DocumentClassifier()
    files: list[DocumentEnvelope] = []
    parsed_by_document: list[tuple[InputDocument, DocumentIR, DocumentType, list[ParsedTable]]] = []
    package = ProcurementPackageExtraction(
        package_id=_package_id(documents, tolerate_read_errors=continue_on_document_error)
    )

    for document in documents:
        try:
            ir = read_docx(document.path)
            decision = classifier.classify(ir, document.type_hint)
            parsed_tables = extract_tables(ir, decision.document_type)
            if table_repairer is not None:
                parsed_tables = table_repairer(ir, decision.document_type, parsed_tables)
        except Exception as error:
            if not continue_on_document_error:
                raise
            if document.type_hint == DocumentType.PLAN:
                raise RequiredDocumentExtractionError(
                    f"Не удалось прочитать обязательную заявку в план-график: {document.path.name}."
                ) from error
            warning = (
                f"{document.path.name}: DOCX parsing failed: "
                f"{type(error).__name__}: {error}"
            )
            package.package_warnings.append(warning)
            files.append(
                DocumentEnvelope(
                    file_name=document.path.name,
                    file_path=str(document.path),
                    document_type="unknown",
                    confidence=0.0,
                    parser_warnings=[warning],
                )
            )
            continue
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

    for document, ir, document_type, tables in parsed_by_document:
        try:
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
        except Exception as error:
            if not continue_on_document_error:
                raise
            if document.type_hint == DocumentType.PLAN or document_type == DocumentType.PLAN:
                raise RequiredDocumentExtractionError(
                    f"Не удалось прочитать обязательную заявку в план-график: {document.path.name}."
                ) from error
            warning = (
                f"{document.path.name}: schema extraction failed: "
                f"{type(error).__name__}: {error}"
            )
            package.package_warnings.append(warning)
            _mark_document_failed(files, document, warning)

    package.commercial_offers_found_count = len(package.commercial_offers)
    package.commercial_offers_missing = (
        package.commercial_offers_found_count < package.commercial_offers_required_count
    )
    if package.commercial_offers_missing:
        package.package_warnings.append(
            "Commercial offers are missing or fewer than the required count."
        )
    return package


def _package_id(
    documents: list[InputDocument],
    *,
    tolerate_read_errors: bool = False,
) -> str:
    digest = hashlib.sha256()
    for document in sorted(documents, key=lambda item: str(item.path)):
        path = Path(document.path)
        digest.update(path.name.encode("utf-8"))
        try:
            digest.update(path.read_bytes())
        except OSError:
            if not tolerate_read_errors:
                raise
            digest.update(f"unreadable:{path.name}".encode("utf-8"))
    return f"extraction-{digest.hexdigest()[:16]}"


def _mark_document_failed(
    files: list[DocumentEnvelope],
    document: InputDocument,
    warning: str,
) -> None:
    for envelope in files:
        if envelope.file_path != str(document.path):
            continue
        envelope.document_type = "unknown"
        envelope.confidence = 0.0
        envelope.parser_warnings.append(warning)
        return


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
    seen: set[tuple[str, str]] = set()
    for table in tables:
        if table.table_type in {"schedule_application_table", "generic_table"}:
            for field in table.compact_json.get("raw_fields", []):
                _append_raw_field(result, seen, table, field)
        if table.document_type_hint == DocumentType.PLAN.value:
            for row in table.logical_rows:
                if row.row_type != "key_value":
                    continue
                _append_raw_field(
                    result,
                    seen,
                    table,
                    {
                        "key": row.cells_by_header.get("key"),
                        "value": row.cells_by_header.get("value"),
                        "row_index": row.row_index,
                    },
                )
    return result


def _append_raw_field(
    result: list[RawField],
    seen: set[tuple[str, str]],
    table: ParsedTable,
    field: dict,
) -> None:
    key = clean_text(field.get("key"))
    value = clean_text(field.get("value")) or None
    if not key:
        return
    normalized_key = normalize_key(key)
    dedupe_key = (normalized_key, clean_text(value).casefold())
    if dedupe_key in seen:
        return
    seen.add(dedupe_key)
    result.append(
        RawField(
            key=key,
            value=value,
            normalized_key=normalized_key,
            is_empty=is_empty_value(value),
            is_negative_value=is_negative_value(value),
            evidence=f"{table.table_id}:r{field.get('row_index')}",
        )
    )


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


def _money_value_from_raw(raw: str | None) -> MoneyValue | None:
    raw = clean_text(raw)
    if not raw:
        return None
    _, amount = extract_money(raw)
    if amount is None:
        return None
    return MoneyValue(raw=raw, amount=amount)


def _contract_price_value(
    text: str,
    specification_items: list[ContractSpecificationItem],
) -> MoneyValue | None:
    totals = [item.total_price for item in specification_items if item.total_price is not None]
    if totals:
        amount = sum(totals)
        raw_values = [
            item.raw_total_price
            for item in specification_items
            if item.raw_total_price
        ]
        raw = "; ".join(raw_values) if raw_values else str(amount)
        return MoneyValue(raw=raw, amount=amount)

    patterns = (
        r"итого\s+к\s+оплате\s*:\s*([^\n\r]+?руб[^\n\r]*)",
        r"цена\s+контракта\s+составляет\s*([^\n\r]+?руб[^\n\r]*)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        raw = clean_text(match.group(1))
        if _looks_like_money_range(raw):
            continue
        value = _money_value_from_raw(raw)
        if value is not None:
            return value
    return None


def _looks_like_money_range(text: str | None) -> bool:
    text = clean_text(text).casefold()
    if not text:
        return False
    return bool(re.search(r"\bот\b.{0,80}\bдо\b", text))


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


def _date_from_text(text: str | None) -> date | None:
    text = clean_text(text)
    if not text:
        return None
    match = re.search(r"\b(\d{2})\.(\d{2})\.(\d{4})\b", text)
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _explicit_stage_start_date(text: str | None) -> date | None:
    """A stage can start on contract signing, which is not a calendar date."""
    source = clean_text(text)
    if not source:
        return None
    match = re.search(r"\bс\s+(\d{2}\.\d{2}\.\d{4})\b", source, flags=re.IGNORECASE)
    return _date_from_text(match.group(1)) if match else None


def _stage_from_payload(table: ParsedTable, payload: dict) -> ProcurementStage:
    price = _money_value_from_raw(payload.get("price_raw"))
    service_term_text = clean_text(payload.get("service_term_text")) or None
    execution_end_text = clean_text(payload.get("execution_end_text")) or None
    start_text = clean_text(payload.get("start_text")) or None
    raw_stage_number = clean_text(payload.get("stage_number")) or None
    raw_stage_name = clean_text(payload.get("stage_name")) or None
    if raw_stage_name and not service_term_text:
        embedded_term = re.search(
            r"\(\s*\d+\s*этап\s*,\s*([^)]+)\)",
            raw_stage_name,
            flags=re.IGNORECASE,
        )
        if embedded_term:
            service_term_text = clean_text(embedded_term.group(1))
    stage_name = _clean_stage_name(raw_stage_name)
    return ProcurementStage(
        stage_number=_clean_stage_number(raw_stage_number),
        stage_name=stage_name,
        result_text=clean_text(payload.get("result_text")) or None,
        start_text=start_text,
        service_term_text=service_term_text,
        service_start_date=_date_from_text(service_term_text) or _date_from_text(start_text),
        service_end_date=_last_date_from_text(service_term_text),
        execution_end_date=_date_from_text(execution_end_text),
        price=price,
        quantity_text=clean_text(payload.get("quantity_text")) or None,
        evidence=f"{table.table_id}:r{payload.get('row_index')}",
        parser_warnings=payload.get("warnings", []),
    )


def _clean_stage_number(text: str | None) -> str | None:
    text = clean_text(text)
    if not text:
        return None
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return text
    return match.group(0).rstrip(".")


def _clean_stage_name(text: str | None) -> str | None:
    text = clean_text(text)
    if not text:
        return None
    text = re.sub(r"^\d+(?:\.\d+)?[.)]?\s*", "", text)
    text = re.sub(r"\(\s*\d+\s*этап[^)]*\)", "", text, flags=re.IGNORECASE)
    return clean_text(text) or None


def _last_date_from_text(text: str | None) -> date | None:
    text = clean_text(text)
    if not text:
        return None
    matches = list(re.finditer(r"\b(\d{2})\.(\d{2})\.(\d{4})\b", text))
    if not matches:
        return None
    day, month, year = (int(part) for part in matches[-1].groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _is_valid_stage(stage: ProcurementStage) -> bool:
    if (
        stage.service_term_text
        or stage.service_start_date
        or stage.service_end_date
        or stage.execution_end_date
        or stage.price
        or stage.quantity_text
        or stage.result_text
    ):
        return True
    if stage.stage_name:
        text = clean_text(stage.stage_name)
        if text and not is_negative_value(text) and not is_empty_value(text):
            lowered = text.casefold()
            if "этап" in lowered and not re.search(r"\b\d+\s*этап\b", lowered):
                return False
            return True
    return False


def _stages_from_tables(tables: list[ParsedTable]) -> list[ProcurementStage]:
    stages: list[ProcurementStage] = []
    seen: set[tuple[str, str, str, str]] = set()
    for table in tables:
        if table.table_type not in {"contract_stages_table", "nmck_staged_calculation_table"}:
            continue
        for payload in table.compact_json.get("stages", []):
            stage = _stage_from_payload(table, payload)
            if not _is_valid_stage(stage):
                continue
            key = (
                stage.stage_number or "",
                clean_text(stage.stage_name).casefold(),
                clean_text(stage.service_term_text).casefold(),
                clean_text(stage.price.raw if stage.price else "").casefold(),
            )
            if key in seen:
                continue
            seen.add(key)
            stages.append(stage)
    return stages


def _stage_fragments(text: str | None) -> list[tuple[str, str]]:
    text = clean_text(text)
    if not text:
        return []
    matches = list(re.finditer(r"(\d+)\s*этап\s*[-:]", text, flags=re.IGNORECASE))
    result: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        fragment = clean_text(text[match.end() : end].strip(" ;."))
        tail = re.search(
            r"\b(?:товар(?:ы)?|неисключительные|пользовательские права)\b",
            fragment,
            flags=re.IGNORECASE,
        )
        if tail:
            fragment = clean_text(fragment[: tail.start()].strip(" ;."))
        if fragment:
            result.append((match.group(1), fragment))
    return result


def _stages_from_schedule_fields(fields: list[RawField]) -> list[ProcurementStage]:
    delivery_text = _field_value(
        fields,
        "сроки поставки товара",
        "срок поставки",
        "срок оказания",
        "срок выполнения",
    )
    quantity_text = _field_value(fields, "количество")
    delivery_by_number = dict(_stage_fragments(delivery_text))
    quantity_by_number = dict(_stage_fragments(quantity_text))
    stage_numbers = sorted(
        set(delivery_by_number) | set(quantity_by_number),
        key=lambda value: int(value) if value.isdigit() else 9999,
    )
    stages: list[ProcurementStage] = []
    for number in stage_numbers:
        service_term_text = delivery_by_number.get(number)
        quantity = quantity_by_number.get(number)
        stages.append(
            ProcurementStage(
                stage_number=number,
                stage_name=f"{number} этап",
                service_term_text=service_term_text,
                service_start_date=_explicit_stage_start_date(service_term_text),
                service_end_date=_last_date_from_text(service_term_text),
                quantity_text=quantity,
                evidence="schedule_application:raw_fields",
                parser_warnings=[
                    "Stage inferred from schedule application text fields, not a physical stage table."
                ],
            )
        )
    return stages


def _merge_schedule_stages(
    table_stages: list[ProcurementStage],
    field_stages: list[ProcurementStage],
) -> list[ProcurementStage]:
    """Keep table evidence but fill sparse plan stages from named plan fields."""
    by_number = {
        clean_text(stage.stage_number): stage.model_copy(deep=True)
        for stage in table_stages
        if clean_text(stage.stage_number)
    }
    for field_stage in field_stages:
        number = clean_text(field_stage.stage_number)
        if not number:
            continue
        existing = by_number.get(number)
        if existing is None:
            by_number[number] = field_stage
            continue
        for field_name in (
            "stage_name",
            "service_term_text",
            "service_start_date",
            "service_end_date",
            "execution_end_date",
            "quantity_text",
        ):
            if getattr(existing, field_name) in (None, ""):
                setattr(existing, field_name, getattr(field_stage, field_name))
        existing.parser_warnings = list(dict.fromkeys([
            *existing.parser_warnings,
            *field_stage.parser_warnings,
        ]))
    return sorted(
        by_number.values(),
        key=lambda stage: int(stage.stage_number) if str(stage.stage_number or "").isdigit() else 9999,
    )


def _bool_from_text(text: str | None) -> bool | None:
    text = clean_text(text).casefold()
    if not text:
        return None
    if is_negative_value(text) or text.startswith("отсутств"):
        return False
    if any(marker in text for marker in ("да", "установлено", "предусмотрен", "требуется")):
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
    # Section numbers such as ``8.1`` must not become security amounts.
    money = None
    if not is_not_required and re.search(r"(?:руб(?:л|\.|\b)|₽)", text, flags=re.IGNORECASE):
        money = _money_value(text)
    source_match = re.search(r"(?:п(?:ункт)?\.?\s*)?(\d+(?:\.\d+){1,2})\b", text, flags=re.IGNORECASE)
    return SecurityValue(
        raw=text,
        source_reference=(f"п. {source_match.group(1)}" if source_match else None),
        value_percent=parse_decimal(percent.group(1)) if percent else None,
        value_amount=money.amount if money else None,
        is_not_required=is_not_required,
    )


def _contract_smp_sonko_clause(text: str) -> str | None:
    lines = [clean_text(line) for line in text.splitlines() if clean_text(line)]
    best_window: str | None = None
    best_score = -1
    for index, line in enumerate(lines):
        window = " ".join(lines[index : index + 4])
        lowered = window.casefold()
        has_smp = (
            "смп" in lowered
            or "сонко" in lowered
            or "субъектов малого предпринимательства" in lowered
            or "социально ориентированных" in lowered
        )
        has_subcontract = any(
            marker in lowered
            for marker in ("субподряд", "соисполн", "привлеч")
        )
        if not has_smp or not has_subcontract:
            continue
        window_lowered = window.casefold()
        score = 0
        if "привлечь к исполнению" in window_lowered:
            score += 4
        if "привлеч" in window_lowered:
            score += 2
        if "соисполн" in window_lowered or "субподряд" in window_lowered:
            score += 2
        if _percent_from_text(window) is not None:
            score += 3
        if re.search(r"\b5\.\d+", window):
            score += 1
        if score > best_score:
            best_score = score
            best_window = window
    return best_window[:1200] if best_window else None


def _contract_smp_sonko_required(text: str | None) -> bool | None:
    text = clean_text(text).casefold()
    if not text:
        return None
    if is_negative_value(text) or any(
        marker in text
        for marker in (
            "не предусмотр",
            "не установлен",
            "не требует",
            "отсутств",
            "нет обязанности",
            "не обязан",
        )
    ):
        return False
    if any(
        marker in text
        for marker in (
            "обязан",
            "обязатель",
            "должен привлеч",
            "требуется привлеч",
            "привлечь к исполнению",
        )
    ):
        return True
    return None


def _percent_from_text(text: str | None) -> Decimal | None:
    text = clean_text(text)
    if not text:
        return None
    if re.fullmatch(r"\d+(?:[,.]\d+)?", text):
        return parse_decimal(text)
    match = re.search(
        r"(\d+(?:[,.]\d+)?)\s*(?:\([^)]*\)\s*)?(?:%|процент)",
        text,
        flags=re.IGNORECASE,
    )
    return parse_decimal(match.group(1)) if match else None


def _code_references_from_text(
    text: str | None,
    *,
    role: str,
    evidence: str,
) -> list[CodeReference]:
    raw_text = str(text or "").replace("\r", "\n")
    if not clean_text(raw_text):
        return []
    code_pattern = rf"(?:{KTRU_RE.pattern}|{OKPD2_RE.pattern})"
    pattern = re.compile(
        rf"({code_pattern})\s*[–—-]?\s*(.*?)(?=(?:{code_pattern})|[;\n]|$)",
        flags=re.IGNORECASE,
    )
    result: list[CodeReference] = []
    seen: set[str] = set()
    for match in pattern.finditer(raw_text):
        code = match.group(1)
        if code in seen:
            continue
        seen.add(code)
        name = clean_text(match.group(2)).rstrip(".;") or None
        code_type = "ktru" if "-" in code else "okpd2"
        resolved_role = _role_from_code_name(name, default=role)
        result.append(
            CodeReference(
                code_type=code_type,
                code=code,
                name=name,
                role=resolved_role,  # type: ignore[arg-type]
                raw_text=clean_text(match.group(0)),
                evidence=evidence,
            )
        )
    return result


def _role_from_code_name(name: str | None, *, default: str) -> str:
    lowered = clean_text(name).casefold()
    if any(marker in lowered for marker in ("программ", "неисключительн", "пользовательск", "лицензи")):
        return "software_rights"
    return default


def _purchase_item_from_code_reference(reference: CodeReference) -> PurchaseItem:
    return PurchaseItem(
        name=reference.name,
        okpd2_code=reference.code if reference.code_type == "okpd2" else None,
        ktru_code=reference.code if reference.code_type == "ktru" else None,
        evidence=reference.evidence,
        notes=[f"role:{reference.role}"],
    )


def _schedule_code_roles(
    fields: list[RawField],
    *,
    subject_text: str | None,
) -> tuple[list[CodeReference], list[PurchaseItem]]:
    code_text = _field_value(fields, "код окпд")
    if not code_text:
        return [], []
    marker = re.search(
        r"товар(?:ы)?\s+и\s+неисключительные.*?поставляем",
        code_text,
        flags=re.IGNORECASE,
    )
    if marker:
        subject_refs = _code_references_from_text(
            code_text[: marker.start()],
            role="service",
            evidence="schedule_application:okpd2_field",
        )
        included_refs = _code_references_from_text(
            code_text[marker.start() :],
            role="goods",
            evidence="schedule_application:included_goods_field",
        )
        return subject_refs, [_purchase_item_from_code_reference(item) for item in included_refs]

    refs = _code_references_from_text(
        code_text,
        role="service" if _looks_like_service_subject(subject_text) else "goods",
        evidence="schedule_application:okpd2_field",
    )
    if _looks_like_service_subject(subject_text):
        return refs, []
    return [], [_purchase_item_from_code_reference(item) for item in refs]


def _looks_like_service_subject(text: str | None) -> bool:
    lowered = clean_text(text).casefold()
    return bool(lowered and any(marker in lowered for marker in ("услуг", "работ", "оказан", "выполнен")))


def _subject_codes_from_document_text(text: str, *, evidence: str) -> list[CodeReference]:
    result: list[CodeReference] = []
    lines = [clean_text(line) for line in text.splitlines()]
    for index, line in enumerate(lines):
        cleaned = clean_text(line)
        lowered = cleaned.casefold()
        if "окпд" not in lowered and "ктру" not in lowered:
            continue
        block_lines = [cleaned]
        found_code_after_marker = bool(KTRU_RE.search(cleaned) or OKPD2_RE.search(cleaned))
        for next_line in lines[index + 1 : index + 12]:
            if not clean_text(next_line):
                if found_code_after_marker:
                    break
                continue
            if KTRU_RE.search(next_line) or OKPD2_RE.search(next_line):
                found_code_after_marker = True
                block_lines.append(next_line)
                continue
            if found_code_after_marker:
                break
        result.extend(
            _code_references_from_text(
                "\n".join(block_lines),
                role="service",
                evidence=evidence,
            )
        )
    return _dedupe_code_references(result)


def _link_codes_to_items_from_text(
    items: list[PurchaseItem],
    references: list[CodeReference],
) -> None:
    if not items or not references:
        return
    for item in items:
        item_name = clean_text(item.name)
        if not item_name:
            continue
        matches = [
            reference
            for reference in references
            if reference.name and _names_match(item_name, reference.name)
        ]
        if not matches:
            continue
        ktru_matches = [match for match in matches if match.code_type == "ktru"]
        okpd2_matches = [match for match in matches if match.code_type == "okpd2"]
        if not item.ktru_code and len(ktru_matches) == 1:
            item.ktru_code = ktru_matches[0].code
            item.parser_warnings.append(
                "КТРУ проставлен из plain text документа; в таблице отдельной колонки с кодом нет."
            )
        if not item.okpd2_code and len(okpd2_matches) == 1:
            item.okpd2_code = okpd2_matches[0].code
            item.parser_warnings.append(
                "ОКПД2 проставлен из plain text документа; в таблице отдельной колонки с кодом нет."
            )
        if (len(ktru_matches) > 1 or len(okpd2_matches) > 1) and item.parser_warnings is not None:
            item.parser_warnings.append(
                "Для позиции найдено несколько похожих кодов в plain text; код не проставлен автоматически."
            )


def _names_match(left: str, right: str) -> bool:
    left_norm = _name_key(left)
    right_norm = _name_key(right)
    if not left_norm or not right_norm:
        return False
    if left_norm in right_norm or right_norm in left_norm:
        return True
    left_tokens = set(left_norm.split())
    right_tokens = set(right_norm.split())
    if not left_tokens or not right_tokens:
        return False
    overlap = left_tokens & right_tokens
    return len(overlap) >= min(3, len(left_tokens), len(right_tokens))


def _name_key(value: str | None) -> str:
    text = clean_text(value).casefold().replace("ё", "е")
    text = re.sub(r"[^а-яa-z0-9]+", " ", text)
    stop_words = {"для", "и", "или", "по", "на", "в", "с", "со", "из", "к", "у"}
    return " ".join(token for token in text.split() if token not in stop_words)


def _dedupe_code_references(values: list[CodeReference]) -> list[CodeReference]:
    result: list[CodeReference] = []
    seen: set[tuple[str, str, str]] = set()
    for value in values:
        key = (value.code_type, value.code, value.role)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _schedule_application(ir: DocumentIR, tables: list[ParsedTable]) -> ScheduleApplicationSchema:
    fields = _raw_fields(tables)
    raw_stages = _merge_schedule_stages(
        _stages_from_tables(tables),
        _stages_from_schedule_fields(fields),
    )
    stages = [stage for stage in raw_stages if _is_valid_stage(stage)]
    raw_dict = {field.key: field.value for field in fields}
    full_text = _document_text(ir) + "\n" + "\n".join(
        f"{field.key}: {field.value or ''}" for field in fields
    )
    delivery_text = _field_value(
        fields,
        "сроки поставки товара",
        "срок поставки",
        "срок оказания",
        "срок выполнения",
    )
    contract_term_text = _field_value(fields, "срок исполнения контракта")
    smp_raw = _field_value(fields, "преимуществ", "смп")
    subcontract_raw = _field_value(fields, "субподряд", "сонко")
    subcontract_percent_raw = _field_value(fields, "процент", "объем привлечения") or subcontract_raw
    method_raw = _field_value(fields, "способ закупки", "способ определения", "способ выбора")
    subject_text = _field_value(fields, "наименование объекта закупки", "предмет закупки")
    subject_codes, included_goods = _schedule_code_roles(fields, subject_text=subject_text)
    application_security_raw = _field_value(fields, "размер обеспечения заявки", "обеспечение заявки")
    contract_security_raw = _field_value(
        fields,
        "размер обеспечения исполнения контракта",
        "обеспечение исполнения контракта",
    )
    warranty_security_raw = _field_value(
        fields,
        "размер обеспечения гарантийных обязательств",
        "обеспечение гарантийных обязательств",
    )
    national_regime_fields = [
        field
        for field in fields
        if re.match(r"\s*17[._]?\d", field.key or "")
        or any(marker in (field.key or "").casefold() for marker in ("запрет", "ограничен", "преимуществ"))
    ]
    return ScheduleApplicationSchema(
        document_title=_title(ir),
        raw_fields=fields,
        raw_fields_dict=raw_dict,
        empty_fields=[field.key for field in fields if field.is_empty],
        negative_value_fields=[field.key for field in fields if field.is_negative_value],
        purchase_subject=subject_text,
        okpd2_codes=unique_codes(OKPD2_RE, full_text),
        ktru_codes=unique_codes(KTRU_RE, full_text),
        subject_codes=subject_codes,
        nmck=_money_value(_field_value(fields, "начальная", "нмцк", "цена контракта")),
        procurement_method_raw=method_raw,
        procurement_method=_procurement_method(method_raw or full_text),
        single_supplier_basis_text=_field_value(fields, "основание", "единственный поставщик"),
        funding_source_text=_field_value(fields, "источник финансирования"),
        delivery_place=_field_value(fields, "место поставки", "адрес поставки", "место оказания"),
        delivery_term_text=delivery_text,
        delivery_term=_term_value(delivery_text),
        contract_execution_term_text=contract_term_text,
        contract_execution_term=_term_value(contract_term_text),
        included_goods=included_goods,
        stages=stages,
        has_stages=True if stages else _bool_from_text(_field_value(fields, "этапы исполнения")),
        smp_preference_raw=smp_raw,
        smp_preference=_bool_from_text(smp_raw),
        subcontract_smp_sonko_required_raw=subcontract_raw,
        subcontract_smp_sonko_required=_bool_from_text(subcontract_raw),
        subcontract_smp_sonko_percent_raw=subcontract_percent_raw,
        subcontract_smp_sonko_percent=_percent_from_text(subcontract_percent_raw),
        application_security_raw=application_security_raw,
        application_security=_security_value(application_security_raw),
        contract_security_raw=contract_security_raw,
        contract_security=_security_value(contract_security_raw),
        warranty_security_raw=warranty_security_raw,
        warranty_security=_security_value(warranty_security_raw),
        additional_requirements_raw=_field_value(fields, "дополнительные требования"),
        national_regime_raw=_field_value(fields, "национальный режим"),
        national_regime_fields=national_regime_fields,
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
            "В контракте указано приложение 'Описание объекта закупки', "
            "но заполненная таблица описания объекта закупки внутри контракта не найдена."
        )
    if expects_specification and not specification_items:
        warnings.append(
            "В контракте указано приложение 'Спецификация', "
            "но заполненные позиции спецификации не найдены; таблица может быть пустой или шаблонной."
        )
    return warnings


def _purchase_request(ir: DocumentIR, tables: list[ParsedTable]) -> PurchaseRequestSchema:
    text = _document_text(ir)
    nmck = _money_value(text)
    stages = _stages_from_tables(tables)
    stages_text = _line_after_marker(text, "этапы исполнения", "этапность")
    return PurchaseRequestSchema(
        document_title=_title(ir),
        purchase_subject=_line_after_marker(text, "предмет закупки", "объект закупки"),
        nmck=nmck,
        procurement_method_raw=_line_after_marker(text, "способ закупки"),
        procurement_method=_procurement_method(text),
        single_supplier_basis_text=_line_after_marker(text, "основание"),
        delivery_term_text=_line_after_marker(text, "срок поставки", "срок выполнения"),
        delivery_term=_term_value(_line_after_marker(text, "срок поставки", "срок выполнения")),
        stages_text=stages_text,
        has_stages=True if stages else _bool_from_text(stages_text),
        stages=stages,
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


def _date_after_marker(text: str, *markers: str) -> date | None:
    value = _line_after_marker(text, *markers)
    if value is None:
        return None
    match = re.search(r"\b(\d{1,2})[.](\d{1,2})[.](\d{2,4})\b", value)
    if not match:
        return None
    day, month, year = match.groups()
    year_int = int(year)
    if year_int < 100:
        year_int += 2000
    try:
        return date(year_int, int(month), int(day))
    except ValueError:
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


def _explicit_line_value_after_marker(text: str, *markers: str) -> str | None:
    lowered_markers = [marker.casefold() for marker in markers]
    lines = [clean_text(line) for line in text.splitlines() if clean_text(line)]
    for line in lines:
        lowered = line.casefold()
        if not any(marker in lowered for marker in lowered_markers):
            continue
        if ":" not in line:
            continue
        tail = clean_text(line.split(":", 1)[1])
        if tail and not _is_structured_placeholder(tail):
            return tail
    return None


def _section_after_heading(text: str, heading_pattern: str, *, max_chars: int = 1600) -> str | None:
    match = re.search(heading_pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    chunk = text[match.end() : match.end() + max_chars]
    stop = re.search(r"\n\s*\d{1,2}\.\s+[А-ЯЁA-Z]", chunk)
    if stop:
        chunk = chunk[: stop.start()]
    return clean_text(chunk) or None


def _is_structured_placeholder(text: str | None) -> bool:
    lowered = clean_text(text).casefold()
    return bool(lowered and "указывается в структурированном виде" in lowered)


def _contract_funding_source(text: str) -> str | None:
    value = _explicit_line_value_after_marker(text, "источник финансирования")
    if value:
        return value
    return None


def _contract_security_text(text: str) -> str | None:
    lines = [clean_text(line) for line in text.splitlines() if clean_text(line)]
    for line in lines:
        lowered = line.casefold()
        if (
            "размер обеспечения исполнения" in lowered
            and "структурированном виде" in lowered
        ):
            return line
    section = _section_after_heading(
        text,
        r"(?:^|\n)\s*\d+(?:\.\d+)?\.\s*обеспечение исполнения контракта\b",
    )
    if section:
        sentence = _first_sentence_with(section, "обеспечение исполнения", "не предусмотр")
        if sentence:
            return sentence
        size_sentence = _first_sentence_with(section, "размер обеспечения исполнения")
        if size_sentence:
            return size_sentence
        if not _is_structured_placeholder(section):
            return section[:700]
    return _explicit_line_value_after_marker(text, "обеспечение исполнения контракта")


def _contract_warranty_security_text(text: str) -> str | None:
    lines = [clean_text(line) for line in text.splitlines() if clean_text(line)]
    for index, line in enumerate(lines):
        lowered = line.casefold()
        if "обеспечен" not in lowered or "гарантийн" not in lowered:
            continue
        if "структурированном виде" in lowered and (
            "размер" in lowered or "в размере" in lowered
        ):
            return line
        window = " ".join(lines[index : index + 3])
        if any(marker in window.casefold() for marker in ("размер", "не предусмотр", "не установлен")):
            return window[:900]
    return None


def _contract_warranty_text(text: str) -> str | None:
    lines = [clean_text(line) for line in text.splitlines() if clean_text(line)]
    for index, line in enumerate(lines):
        lowered = line.casefold()
        if any(marker in lowered for marker in ("обеспечение гарантий", "устранение недостатков")):
            continue
        if "гарантийн" not in lowered and "гарантия" not in lowered:
            continue
        if any(marker in lowered for marker in ("неустой", "штраф", "пен", "претензи")):
            continue
        window = " ".join(lines[index : index + 3])
        window_lowered = window.casefold()
        if not any(
            marker in window_lowered
            for marker in (
                "гарантийный срок составляет",
                "гарантийный срок на",
                "гарантия составляет",
                "месяц",
                "лет",
                "год",
            )
        ):
            continue
        line_lowered = line.casefold()
        if any(marker in line_lowered for marker in ("гарантийный срок", "гарантия составляет")):
            return line[:1200]
        return window[:1200]
    return None


def _contract_responsibility_section(text: str) -> str | None:
    section = _section_after_heading(
        text,
        r"(?:^|\n)\s*\d+(?:\.\d+)?\.\s*ответственн\w*\s+сторон\b",
        max_chars=12000,
    )
    if section:
        return section
    lines = [clean_text(line) for line in text.splitlines()]
    for index, line in enumerate(lines):
        lowered = line.casefold()
        if "ответственн" not in lowered or "сторон" not in lowered:
            continue
        chunk_lines: list[str] = []
        for next_line in lines[index : index + 80]:
            if chunk_lines and re.match(r"^\d{1,2}\.\s+[А-ЯЁA-Z]", next_line):
                break
            if clean_text(next_line):
                chunk_lines.append(next_line)
        return clean_text("\n".join(chunk_lines)) or None
    return None


def _first_sentence_with(text: str, *markers: str) -> str | None:
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        cleaned = clean_text(sentence)
        lowered = cleaned.casefold()
        if cleaned and all(marker in lowered for marker in markers):
            return cleaned
    return None


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
    stages = _stages_from_tables(tables)
    for table in tables:
        if table.table_type not in {"nmck_calculation_table", "nmck_staged_calculation_table"}:
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
        okpd2_codes=unique_codes(OKPD2_RE, text),
        ktru_codes=unique_codes(KTRU_RE, text),
        subject_codes=_subject_codes_from_document_text(text, evidence="nmck_text:codes"),
        total_amount=_money_value(text),
        total_amount_text=(_money_value(text).raw if _money_value(text) else None),
        price_sources=sources,
        items=items,
        stages=stages,
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
    stages = _stages_from_tables(tables)
    subject_codes = _subject_codes_from_document_text(text, evidence="ooz_text:codes")
    items = _purchase_items_from_tables(tables)
    _link_codes_to_items_from_text(items, subject_codes)
    return PurchaseDescriptionSchema(
        document_title=_title(ir),
        purchase_subject=_line_after_marker(text, "предмет закупки", "объект закупки"),
        okpd2_codes=unique_codes(OKPD2_RE, text),
        ktru_codes=unique_codes(KTRU_RE, text),
        subject_codes=subject_codes,
        delivery_place=_line_after_marker(text, "место поставки", "адрес поставки"),
        delivery_term_text=delivery_text,
        delivery_term=_term_value(delivery_text),
        stages=stages,
        items=items,
        warranty_requirements_text=_line_after_marker(text, "гаранти"),
        additional_characteristics_justification_text=_additional_characteristics_justification(text),
    )


def _additional_characteristics_justification(text: str) -> str | None:
    lines = [clean_text(line) for line in text.splitlines() if clean_text(line)]
    for index, line in enumerate(lines):
        lowered = line.casefold()
        if "обоснован" not in lowered:
            continue
        if any(marker in lowered for marker in ("дополнитель", "характерист")):
            return " ".join(lines[index : index + 4])[:1600]
    return None


def _contract_draft(ir: DocumentIR, tables: list[ParsedTable]) -> ContractDraftSchema:
    text = _document_text(ir)
    delivery_text = _line_after_marker(text, "срок поставки", "срок выполнения")
    contract_execution_text = _line_value_after_marker(text, "срок исполнения контракта")
    contract_security_text = _contract_security_text(text)
    warranty_security_text = _contract_warranty_security_text(text)
    funding_source = _contract_funding_source(text)
    smp_subcontract_text = _contract_smp_sonko_clause(text)
    responsibility_section = _contract_responsibility_section(text)
    description_items = _purchase_items_from_tables(tables)
    subject_codes = _subject_codes_from_document_text(text, evidence="contract_text:codes")
    _link_codes_to_items_from_text(description_items, subject_codes)
    specification_items = _contract_specification_items_from_tables(tables)
    stages = _stages_from_tables(tables)
    table_attachments = _attachments(tables)
    referenced_attachments = _contract_referenced_attachments(text) or table_attachments
    embedded = PurchaseDescriptionSchema(
        stages=stages,
        items=description_items,
        parser_warnings=["Embedded purchase description inferred from contract tables."],
    )
    return ContractDraftSchema(
        document_title=_title(ir),
        contract_number=_line_after_marker(text, "контракт №", "контракт n"),
        subject=_line_after_marker(text, "предмет контракта", "предмет закупки"),
        okpd2_codes=unique_codes(OKPD2_RE, text),
        ktru_codes=unique_codes(KTRU_RE, text),
        subject_codes=subject_codes,
        price=_contract_price_value(text, specification_items),
        funding_source=funding_source,
        delivery_place=_line_after_marker(text, "место поставки", "адрес поставки"),
        delivery_term_text=delivery_text,
        delivery_term=_term_value(delivery_text),
        contract_execution_term_text=contract_execution_text,
        contract_execution_term=_term_value(contract_execution_text),
        stages=stages,
        warranty_text=_contract_warranty_text(text),
        responsibility_section_text=responsibility_section,
        subcontract_smp_sonko_required_raw=smp_subcontract_text,
        subcontract_smp_sonko_required=_contract_smp_sonko_required(smp_subcontract_text),
        subcontract_smp_sonko_percent_raw=smp_subcontract_text,
        subcontract_smp_sonko_percent=_percent_from_text(smp_subcontract_text),
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
        outgoing_date=_date_after_marker(text, "исх", "исходящий"),
        offer_date=_date_after_marker(text, "дата", "от"),
        purchase_subject=_line_after_marker(text, "предмет", "объект закупки"),
        delivery_term_text=_line_after_marker(text, "срок поставки", "срок оказания", "срок выполнения"),
        delivery_place=_line_after_marker(text, "место поставки", "место оказания", "адрес поставки"),
        advance_payment_text=_line_after_marker(text, "аванс", "авансовый"),
        vat_text=_line_after_marker(text, "ндс"),
        items=_commercial_offer_items_from_tables(tables),
        total_amount=_money_value(text),
    )


def _commercial_offer_items_from_tables(tables: list[ParsedTable]) -> list[CommercialOfferItem]:
    items = []
    for item in _purchase_items_from_tables(tables):
        items.append(
            CommercialOfferItem(
                row_number=item.row_number,
                name=item.name,
                okpd2_code=item.okpd2_code,
                ktru_code=item.ktru_code,
                unit=item.unit,
                quantity=item.quantity,
                quantity_raw=item.quantity_raw,
                notes=item.notes,
                evidence_text=item.evidence,
            )
        )
    return items
