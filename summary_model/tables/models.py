from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ParsedTableType = Literal[
    "schedule_application_table",
    "request_attachments_table",
    "nmck_calculation_table",
    "ooz_items_table",
    "contract_specification_table",
    "contract_stages_table",
    "contract_attachments_table",
    "signature_table",
    "ignored_table",
    "generic_table",
    "unknown",
]

LogicalRowType = Literal[
    "header",
    "item",
    "characteristic",
    "key_value",
    "total",
    "note",
    "section",
    "unknown",
]


class HeaderPath(BaseModel):
    col_index: int
    parts: list[str] = Field(default_factory=list)
    normalized_name: str | None = None


class LogicalTableRow(BaseModel):
    table_id: str
    row_index: int
    row_type: LogicalRowType = "unknown"
    parent_row_index: int | None = None
    parent_item_number: str | None = None
    cells_by_col: dict[int, str | None] = Field(default_factory=dict)
    cells_by_header: dict[str, str | None] = Field(default_factory=dict)
    raw_text: str = ""
    confidence: float = 0.0
    warnings: list[str] = Field(default_factory=list)


class ParsedTable(BaseModel):
    table_id: str
    block_id: str
    table_index: int
    document_type_hint: str | None = None
    table_type: ParsedTableType = "unknown"
    row_count: int
    col_count: int
    title: str | None = None
    header_rows: list[int] = Field(default_factory=list)
    header_paths: list[HeaderPath] = Field(default_factory=list)
    logical_rows: list[LogicalTableRow] = Field(default_factory=list)
    compact_markdown: str = ""
    compact_json: dict[str, Any] = Field(default_factory=dict)
    parser_warnings: list[str] = Field(default_factory=list)
