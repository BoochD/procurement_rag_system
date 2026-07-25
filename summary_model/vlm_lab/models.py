from __future__ import annotations

from typing import Literal

from typing import Any

from pydantic import BaseModel, Field, field_validator


VlmTableRole = Literal[
    "purchase_description",
    "contract_stages",
    "nmck_calculation",
    "contract_specification",
    "attachments",
    "generic",
    "unknown",
]


class VlmTableCandidate(BaseModel):
    table_id: str
    block_id: str
    table_index: int
    table_type: str
    title: str | None = None
    role: VlmTableRole = "unknown"
    row_count: int
    col_count: int
    complexity_score: int = 0
    confidence: float = 0.0
    reasons: list[str] = Field(default_factory=list)
    parser_warnings: list[str] = Field(default_factory=list)


class VlmCharacteristic(BaseModel):
    row_index: int | None = None
    name: str | None = None
    value: str | None = None
    unit: str | None = None
    is_additional: bool | None = None
    source_note: str | None = None


class VlmPurchaseItem(BaseModel):
    row_index: int | None = None
    row_number: str | None = None
    name: str | None = None
    description: str | None = None
    okpd2_code: str | None = None
    ktru_code: str | None = None
    unit: str | None = None
    quantity_raw: str | None = None
    unit_price_without_vat_raw: str | None = None
    unit_price_with_vat_raw: str | None = None
    total_without_vat_raw: str | None = None
    vat_rate: str | None = None
    vat_amount_raw: str | None = None
    total_price_raw: str | None = None
    characteristics: list[VlmCharacteristic] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class VlmStage(BaseModel):
    row_index: int | None = None
    stage_number: str | None = None
    stage_name: str | None = None
    result_text: str | None = None
    service_term_text: str | None = None
    execution_end_text: str | None = None
    price_raw: str | None = None
    quantity_text: str | None = None
    notes: list[str] = Field(default_factory=list)


class VlmSupplierPrice(BaseModel):
    supplier_label: str | None = None
    unit_price_raw: str | None = None
    row_total_raw: str | None = None


class VlmNmckItem(BaseModel):
    row_index: int | None = None
    row_number: str | None = None
    parent_stage_number: str | None = None
    name: str | None = None
    unit: str | None = None
    quantity_raw: str | None = None
    supplier_prices: list[VlmSupplierPrice] = Field(default_factory=list)
    selected_min_unit_price_raw: str | None = None
    row_total_declared_raw: str | None = None
    notes: list[str] = Field(default_factory=list)


class VlmTableExtraction(BaseModel):
    schema_version: str = "vlm-table-extraction-0.1.0"
    table_role: VlmTableRole
    table_title: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    items: list[VlmPurchaseItem] = Field(default_factory=list)
    stages: list[VlmStage] = Field(default_factory=list)
    nmck_items: list[VlmNmckItem] = Field(default_factory=list)
    totals: list[str] = Field(default_factory=list)
    attachments: list[str] = Field(default_factory=list)
    unparsed_rows: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("attachments", "unparsed_rows", mode="before")
    @classmethod
    def _stringify_loose_rows(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            value = [value]
        result: list[str] = []
        for item in value:
            if item is None:
                continue
            if isinstance(item, str):
                text = item.strip()
            elif isinstance(item, dict):
                text = _dict_row_text(item)
            elif isinstance(item, list):
                text = " | ".join(str(part).strip() for part in item if str(part).strip())
            else:
                text = str(item).strip()
            if text:
                result.append(text)
        return result


def _dict_row_text(item: dict[str, Any]) -> str:
    title = item.get("title") or item.get("title_raw") or item.get("name")
    number = item.get("number") or item.get("row_number")
    if title:
        return f"{number}. {title}".strip() if number else str(title).strip()
    cells = item.get("cells")
    if isinstance(cells, list):
        return " | ".join(str(cell).strip() for cell in cells if str(cell).strip())
    return " | ".join(
        str(value).strip()
        for value in item.values()
        if value is not None and str(value).strip()
    )
