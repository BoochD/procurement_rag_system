from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from summary_model.domain.models import DocumentIR, DocumentType
from summary_model.tables import ParsedTable


LLM_PAYLOAD_SCHEMA_VERSION = "llm-payload-1.0.0"

STRUCTURED_TABLE_TYPES = {
    "schedule_application_table",
    "ooz_items_table",
    "nmck_calculation_table",
    "nmck_staged_calculation_table",
    "contract_specification_table",
    "contract_stages_table",
    "request_attachments_table",
    "contract_attachments_table",
}

IGNORED_TABLE_TYPES = {"signature_table", "ignored_table"}


def build_document_llm_payload(
    *,
    ir: DocumentIR,
    document_type: DocumentType,
    tables: list[ParsedTable],
    deterministic_schema: BaseModel | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build compact per-document context for future structured LLM extraction."""

    known_extracted_full = _model_payload(deterministic_schema)
    known_extracted = _known_extracted_for_llm(document_type, known_extracted_full)
    return {
        "schema_version": LLM_PAYLOAD_SCHEMA_VERSION,
        "document": {
            "document_id": ir.document_id,
            "file_name": ir.file_name,
            "document_type": document_type.value,
            "media_type": ir.media_type,
        },
        "plain_text_blocks": [
            {
                "block_id": block.block_id,
                "order": block.order,
                "text": block.text,
            }
            for block in ir.blocks
            if block.type == "paragraph" and block.text
            and _include_plain_text_block(document_type, block.text)
        ],
        "tables": [
            _table_payload(table, known_extracted)
            for table in tables
            if _include_table(table, known_extracted)
        ],
        "known_extracted": known_extracted,
    }


def _include_table(
    table: ParsedTable,
    known_extracted: dict[str, Any] | None,
) -> bool:
    if table.table_type in IGNORED_TABLE_TYPES:
        return False
    if table.table_type not in STRUCTURED_TABLE_TYPES:
        return bool(table.parser_warnings)
    if _table_is_covered_by_known_extracted(table, known_extracted or {}) and _only_vlm_repair_warnings(table):
        return False
    return bool(table.compact_json or table.compact_markdown or table.parser_warnings)


def _table_payload(
    table: ParsedTable,
    known_extracted: dict[str, Any] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "table_id": table.table_id,
        "block_id": table.block_id,
        "table_index": table.table_index,
        "table_type": table.table_type,
        "title": table.title,
        "row_count": table.row_count,
        "col_count": table.col_count,
        "parser_warnings": table.parser_warnings,
    }
    if _should_include_compact_json(table, known_extracted):
        payload["compact_json"] = table.compact_json
    if _needs_markdown_fallback(table):
        payload["compact_markdown"] = table.compact_markdown
    return {key: value for key, value in payload.items() if value not in (None, [], {})}


def _should_include_compact_json(
    table: ParsedTable,
    known_extracted: dict[str, Any] | None,
) -> bool:
    if not table.compact_json:
        return False
    if table.table_type not in STRUCTURED_TABLE_TYPES:
        return True
    if known_extracted is None:
        return True
    if not _table_is_covered_by_known_extracted(table, known_extracted):
        return True
    return bool(table.parser_warnings and not _only_vlm_repair_warnings(table))


def _table_is_covered_by_known_extracted(
    table: ParsedTable,
    known_extracted: dict[str, Any],
) -> bool:
    if table.table_type == "schedule_application_table":
        return bool(known_extracted.get("raw_fields"))
    if table.table_type == "ooz_items_table":
        return bool(known_extracted.get("items"))
    if table.table_type in {"nmck_calculation_table", "nmck_staged_calculation_table"}:
        return bool(known_extracted.get("items") or known_extracted.get("price_sources"))
    if table.table_type == "contract_specification_table":
        return bool(known_extracted.get("specification_items"))
    if table.table_type == "contract_stages_table":
        return bool(known_extracted.get("stages") or known_extracted.get("stage_execution_terms"))
    if table.table_type in {"request_attachments_table", "contract_attachments_table"}:
        return bool(
            known_extracted.get("attachments")
            or known_extracted.get("referenced_attachments")
            or known_extracted.get("actual_attachments")
        )
    return False


def _needs_markdown_fallback(table: ParsedTable) -> bool:
    if table.table_type not in STRUCTURED_TABLE_TYPES:
        return True
    if table.parser_warnings:
        return True
    compact_json = table.compact_json or {}
    if table.table_type == "schedule_application_table":
        return not compact_json.get("raw_fields")
    if table.table_type in {
        "ooz_items_table",
        "nmck_calculation_table",
        "nmck_staged_calculation_table",
        "contract_specification_table",
    }:
        return not compact_json.get("items")
    if table.table_type == "contract_stages_table":
        return not compact_json.get("stages")
    if table.table_type in {"request_attachments_table", "contract_attachments_table"}:
        return not compact_json.get("attachments")
    return False


def _only_vlm_repair_warnings(table: ParsedTable) -> bool:
    warnings = [str(item).strip() for item in table.parser_warnings if str(item).strip()]
    if not warnings:
        return False
    allowed = {
        "Table compact_json repaired by VLM fallback.",
        "Extracted by VLM fallback.",
    }
    return all(item in allowed for item in warnings)


def _model_payload(value: BaseModel | dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    return value


def _known_extracted_for_llm(
    document_type: DocumentType,
    value: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if value is None:
        return None
    if document_type != DocumentType.CONTRACT:
        return value
    compact = dict(value)
    if "embedded_purchase_description" in compact:
        compact.pop("embedded_purchase_description", None)
    for field_name in ("items", "specification_items"):
        compact[field_name] = [
            _compact_item_for_llm(item)
            for item in compact.get(field_name, [])
            if isinstance(item, dict)
        ]
    return compact


def _compact_item_for_llm(item: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "row_number",
        "name",
        "okpd2_code",
        "ktru_code",
        "unit",
        "quantity",
        "quantity_raw",
        "unit_price",
        "total_price",
    )
    return {key: item.get(key) for key in keep if item.get(key) not in (None, [], {})}


def _include_plain_text_block(document_type: DocumentType, text: str) -> bool:
    if document_type != DocumentType.CONTRACT:
        return True
    lowered = text.casefold()
    markers = (
        "предмет",
        "цена",
        "источник финанс",
        "место",
        "срок",
        "этап",
        "гарант",
        "обеспеч",
        "ответствен",
        "штраф",
        "пен",
        "неусто",
        "приложени",
        "смп",
        "сонко",
        "субподряд",
        "соисполн",
        "прием",
        "приём",
    )
    return any(marker in lowered for marker in markers)
