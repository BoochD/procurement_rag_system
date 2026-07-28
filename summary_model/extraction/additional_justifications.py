from __future__ import annotations

from typing import Any

from summary_model.domain.models import DocumentIR, DocumentType
from summary_model.extraction_models import AdditionalCharacteristicsJustification
from summary_model.tables.models import ParsedTable
from summary_model.tables.utils import clean_text
from summary_model.vlm_lab.candidates import justification_candidate_reasons


def collect_additional_justifications(
    ir: DocumentIR,
    document_type: DocumentType,
    tables: list[ParsedTable],
    document_text: str,
) -> tuple[list[AdditionalCharacteristicsJustification], str | None]:
    plain_text = _plain_text_justification(document_text)
    source_tables = {
        block.table.table_id: block.table
        for block in ir.blocks
        if block.table is not None
    }
    records: list[AdditionalCharacteristicsJustification] = []
    for table in tables:
        source = source_tables.get(table.table_id)
        compact = table.compact_json or {}
        if not _is_candidate(table, compact, source):
            continue
        extraction = compact.get("justification_extraction") or {}
        rows = compact.get("additional_characteristics_justifications") or []
        for row in rows:
            record = _record_from_vlm_row(
                row,
                ir=ir,
                document_type=document_type,
                table=table,
                extraction=extraction,
            )
            if record is not None:
                records.append(record)
        if not rows or extraction.get("status") != "complete":
            records.append(_candidate_marker(ir, document_type, table, extraction))

    if plain_text:
        records.append(
            AdditionalCharacteristicsJustification(
                scope_text="Описание объекта закупки",
                justification_text=plain_text,
                evidence_text=plain_text,
                source_document_title=ir.file_name,
                source_document_type=document_type.value,
                extraction_method="deterministic_text",
            )
        )
    records = _deduplicate(records)
    return records, _first_text(records) or plain_text


def _plain_text_justification(text: str) -> str | None:
    lines = [clean_text(line) for line in text.splitlines() if clean_text(line)]
    for index, line in enumerate(lines):
        lowered = line.casefold()
        if "обоснован" in lowered and any(
            marker in lowered for marker in ("дополнитель", "характерист")
        ):
            return " ".join(lines[index : index + 4])[:1600]
    return None


def _is_candidate(table: ParsedTable, compact: dict[str, Any], source: Any) -> bool:
    return bool(
        table.table_type == "additional_characteristics_justification_table"
        or "additional_characteristics_justifications" in compact
        or justification_candidate_reasons(table, source)
    )


def _record_from_vlm_row(
    row: Any,
    *,
    ir: DocumentIR,
    document_type: DocumentType,
    table: ParsedTable,
    extraction: dict[str, Any],
) -> AdditionalCharacteristicsJustification | None:
    if not isinstance(row, dict):
        return None
    warnings = _string_list(row.get("parser_warnings")) + _string_list(extraction.get("warnings"))
    payload = {
        **row,
        **_source_fields(ir, document_type, table),
        "extraction_method": "vlm_table",
        "parser_warnings": list(dict.fromkeys(warnings)),
    }
    try:
        return AdditionalCharacteristicsJustification.model_validate(payload)
    except Exception as error:
        return AdditionalCharacteristicsJustification(
            **_source_fields(ir, document_type, table),
            extraction_method="vlm_table",
            parser_warnings=[f"invalid_justification_row: {type(error).__name__}"],
        )


def _candidate_marker(
    ir: DocumentIR,
    document_type: DocumentType,
    table: ParsedTable,
    extraction: dict[str, Any],
) -> AdditionalCharacteristicsJustification:
    status = str(extraction.get("status") or "contents_not_extracted")
    warnings = [status, *_string_list(extraction.get("warnings")), *table.parser_warnings]
    return AdditionalCharacteristicsJustification(
        scope_text=table.title,
        **_source_fields(ir, document_type, table),
        extraction_method="table_candidate",
        parser_warnings=list(dict.fromkeys(warnings)),
    )


def _source_fields(
    ir: DocumentIR,
    document_type: DocumentType,
    table: ParsedTable,
) -> dict[str, Any]:
    return {
        "source_document_title": ir.file_name,
        "source_document_type": document_type.value,
        "source_table_id": table.table_id,
        "source_table_index": table.table_index,
        "source_table_title": table.title,
    }


def _deduplicate(
    records: list[AdditionalCharacteristicsJustification],
) -> list[AdditionalCharacteristicsJustification]:
    result = []
    seen: set[tuple[str, str, str]] = set()
    for record in records:
        key = tuple(
            clean_text(value).casefold()
            for value in (
                record.source_table_id,
                record.scope_text,
                record.justification_text or record.evidence_text,
            )
        )
        if key not in seen:
            seen.add(key)
            result.append(record)
    return result


def _first_text(records: list[AdditionalCharacteristicsJustification]) -> str | None:
    for record in records:
        text = clean_text(record.justification_text or record.evidence_text)
        if text:
            return text[:1600]
    return None


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return [str(item).strip() for item in values if item is not None and str(item).strip()]
