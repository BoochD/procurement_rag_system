from __future__ import annotations

from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


SCHEMA_VERSION = "4.0.0"


class DocumentType(str, Enum):
    PLAN = "plan"
    REQUEST = "request"
    COMMERCIAL_OFFER = "commercial_offer"
    ONMCK = "onmck"
    OOZ = "ooz"
    CONTRACT = "contract"
    EXPLANATORY_NOTE = "explanatory_note"
    UNKNOWN = "unknown"


class FindingSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    MANUAL_REVIEW = "manual_review"


class FindingStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    UNCERTAIN = "uncertain"


class InputDocument(BaseModel):
    path: Path
    type_hint: DocumentType | None = None
    display_name: str | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


class Evidence(BaseModel):
    document_id: str
    block_id: str
    table_id: str | None = None
    row: int | None = None
    column: int | None = None
    quote: str


class ExtractedValue(BaseModel):
    raw_value: Any = None
    normalized_value: Any = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[Evidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class TableColumnIR(BaseModel):
    index: int
    alias: str
    header_path: list[str] = Field(default_factory=list)


class TableRowIR(BaseModel):
    row_id: str
    row: int
    values: dict[str, str] = Field(default_factory=dict)
    spans: dict[str, tuple[int, int]] = Field(default_factory=dict)


class TableIR(BaseModel):
    table_id: str
    title: str | None = None
    context_before: list[str] = Field(default_factory=list)
    context_after: list[str] = Field(default_factory=list)
    row_count: int
    columns: list[TableColumnIR] = Field(default_factory=list)
    rows: list[TableRowIR] = Field(default_factory=list)
    header_rows: list[int] = Field(default_factory=list)
    kind: Literal[
        "key_value",
        "item_list",
        "characteristics",
        "supplier_matrix",
        "specification",
        "unknown",
    ] = "unknown"

    @property
    def column_count(self) -> int:
        return len(self.columns)

    def header_labels(self) -> list[str]:
        return [" / ".join(column.header_path) for column in self.columns]

    def matrix(self) -> list[list[str]]:
        result = [["" for _ in range(self.column_count)] for _ in range(self.row_count)]
        alias_to_column = {
            column.alias: column.index
            for column in self.columns
        }
        for source_row in self.rows:
            for alias, text in source_row.values.items():
                column = alias_to_column[alias]
                row_span, column_span = source_row.spans.get(alias, (1, 1))
                for row in range(
                    source_row.row,
                    min(source_row.row + row_span, self.row_count),
                ):
                    for target_column in range(
                        column,
                        min(column + column_span, self.column_count),
                    ):
                        result[row][target_column] = text
        return result

    def origin_value(self, row: int, column: int) -> str | None:
        alias = self.columns[column].alias
        for source_row in self.rows:
            if source_row.row != row:
                continue
            return source_row.values.get(alias)
        return None

    def span_at_origin(self, row: int, column: int) -> tuple[int, int]:
        alias = self.columns[column].alias
        for source_row in self.rows:
            if source_row.row == row:
                return source_row.spans.get(alias, (1, 1))
        return (1, 1)


class DocumentBlockIR(BaseModel):
    block_id: str
    order: int
    type: Literal["paragraph", "table", "image", "page_break"]
    text: str | None = None
    table: TableIR | None = None
    page: int | None = None


class DocumentIR(BaseModel):
    document_id: str
    file_name: str
    media_type: Literal["docx", "pdf", "image"]
    blocks: list[DocumentBlockIR] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Money(BaseModel):
    amount: Decimal | None = None
    currency: str | None = "RUB"
    vat_rate: Decimal | None = None
    vat_included: bool | None = None


class Period(BaseModel):
    raw_text: str
    value: int | None = None
    unit: Literal["calendar_day", "working_day", "month", "date_range", "unknown"] = "unknown"
    anchor_event: str | None = None


class DeliveryTermsExtraction(BaseModel):
    delivery_periods: list[ExtractedValue] = Field(default_factory=list)
    delivery_places: list[ExtractedValue] = Field(default_factory=list)
    unresolved_fields: list[str] = Field(default_factory=list)


class ItemCharacteristic(BaseModel):
    name: ExtractedValue
    value: ExtractedValue
    unit: ExtractedValue | None = None
    is_additional: bool | None = None


class ProcurementItem(BaseModel):
    item_id: str
    name: ExtractedValue
    okpd2: list[ExtractedValue] = Field(default_factory=list)
    ktru: list[ExtractedValue] = Field(default_factory=list)
    quantity: ExtractedValue | None = None
    unit: ExtractedValue | None = None
    unit_price: ExtractedValue | None = None
    total_price: ExtractedValue | None = None
    characteristics: list[ItemCharacteristic] = Field(default_factory=list)


class ExecutionStage(BaseModel):
    name: str | None = None
    start: ExtractedValue | None = None
    end: ExtractedValue | None = None
    period: ExtractedValue | None = None
    amount: ExtractedValue | None = None


class SecurityTerms(BaseModel):
    application_security: ExtractedValue | None = None
    contract_security: ExtractedValue | None = None
    warranty_security: ExtractedValue | None = None


class SmpTerms(BaseModel):
    preference_enabled: bool | None = None
    subcontracting_required: bool | None = None
    subcontracting_percent: Decimal | None = None
    sonko_applies: bool | None = None


class NationalRegimeTerms(BaseModel):
    prohibitions: list[ExtractedValue] = Field(default_factory=list)
    restrictions: list[ExtractedValue] = Field(default_factory=list)
    advantages: list[ExtractedValue] = Field(default_factory=list)
    pp1875_fields_completed: bool | None = None


class DocumentSummary(BaseModel):
    document_id: str = ""
    display_name: str = ""
    detected_type: DocumentType = DocumentType.UNKNOWN
    classification_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    classification_evidence: list[Evidence] = Field(default_factory=list)
    extraction_warnings: list[str] = Field(default_factory=list)
    unresolved_fields: list[str] = Field(default_factory=list)


class PlanRequestSummary(DocumentSummary):
    subject: ExtractedValue | None = None
    items: list[ProcurementItem] = Field(default_factory=list)
    nmck: list[ExtractedValue] = Field(default_factory=list)
    procurement_method: ExtractedValue | None = None
    single_supplier_basis: ExtractedValue | None = None
    delivery_places: list[ExtractedValue] = Field(default_factory=list)
    delivery_periods: list[ExtractedValue] = Field(default_factory=list)
    contract_execution_periods: list[ExtractedValue] = Field(default_factory=list)
    execution_stages: list[ExecutionStage] = Field(default_factory=list)
    funding_source: ExtractedValue | None = None
    kbk: list[ExtractedValue] = Field(default_factory=list)
    security: SecurityTerms = Field(default_factory=SecurityTerms)
    national_regime: NationalRegimeTerms = Field(default_factory=NationalRegimeTerms)
    smp_terms: SmpTerms = Field(default_factory=SmpTerms)
    additional_participant_requirements: ExtractedValue | None = None
    all_required_rows_completed: bool | None = None


class ProcurementRequestSummary(DocumentSummary):
    subject: ExtractedValue | None = None
    nmck: list[ExtractedValue] = Field(default_factory=list)
    procurement_method: ExtractedValue | None = None
    single_supplier_basis: ExtractedValue | None = None
    delivery_places: list[ExtractedValue] = Field(default_factory=list)
    delivery_periods: list[ExtractedValue] = Field(default_factory=list)
    execution_stages: list[ExecutionStage] = Field(default_factory=list)
    attachments: list[ExtractedValue] = Field(default_factory=list)
    funding_source: ExtractedValue | None = None


class CommercialOfferSummary(DocumentSummary):
    supplier_name: ExtractedValue | None = None
    inn: ExtractedValue | None = None
    kpp: ExtractedValue | None = None
    requisites_present: bool = False
    outgoing_number: ExtractedValue | None = None
    offer_date: ExtractedValue | None = None
    subject: ExtractedValue | None = None
    items: list[ProcurementItem] = Field(default_factory=list)
    subtotal: list[ExtractedValue] = Field(default_factory=list)
    vat: list[ExtractedValue] = Field(default_factory=list)
    total: list[ExtractedValue] = Field(default_factory=list)
    delivery_places: list[ExtractedValue] = Field(default_factory=list)
    delivery_periods: list[ExtractedValue] = Field(default_factory=list)
    advance_payment: ExtractedValue | None = None
    validity_period: ExtractedValue | None = None
    ocr_used: bool = False
    ocr_warnings: list[str] = Field(default_factory=list)


class SupplierPrice(BaseModel):
    supplier_ref: str
    unit_price: ExtractedValue
    total_price: ExtractedValue | None = None


class OnmckItem(BaseModel):
    item: ProcurementItem
    supplier_prices: list[SupplierPrice] = Field(default_factory=list)
    selected_unit_price: ExtractedValue | None = None
    calculated_total: ExtractedValue | None = None
    minimum_unit_price: ExtractedValue | None = None
    variation_coefficient: Decimal | None = None


class OnmckSummary(DocumentSummary):
    pricing_method: ExtractedValue | None = None
    subject: ExtractedValue | None = None
    source_offers: list[ExtractedValue] = Field(default_factory=list)
    items: list[OnmckItem] = Field(default_factory=list)
    nmck: list[ExtractedValue] = Field(default_factory=list)
    calculation_notes: list[ExtractedValue] = Field(default_factory=list)


class OozSummary(DocumentSummary):
    subject: ExtractedValue | None = None
    delivery_places: list[ExtractedValue] = Field(default_factory=list)
    delivery_periods: list[ExtractedValue] = Field(default_factory=list)
    execution_stages: list[ExecutionStage] = Field(default_factory=list)
    items: list[ProcurementItem] = Field(default_factory=list)
    warranty_terms: list[ExtractedValue] = Field(default_factory=list)
    extra_characteristics_justifications: list[ExtractedValue] = Field(default_factory=list)
    trademarks: list[ExtractedValue] = Field(default_factory=list)
    rights_transfer_required: bool | None = None
    rights_transfer_documents: list[ExtractedValue] = Field(default_factory=list)


class ContractSummary(DocumentSummary):
    contract_number: ExtractedValue | None = None
    subject: ExtractedValue | None = None
    price: list[ExtractedValue] = Field(default_factory=list)
    vat_terms: list[ExtractedValue] = Field(default_factory=list)
    funding_source: ExtractedValue | None = None
    items: list[ProcurementItem] = Field(default_factory=list)
    delivery_places: list[ExtractedValue] = Field(default_factory=list)
    delivery_periods: list[ExtractedValue] = Field(default_factory=list)
    execution_periods: list[ExtractedValue] = Field(default_factory=list)
    execution_stages: list[ExecutionStage] = Field(default_factory=list)
    warranty_terms: list[ExtractedValue] = Field(default_factory=list)
    security: SecurityTerms = Field(default_factory=SecurityTerms)
    smp_terms: SmpTerms = Field(default_factory=SmpTerms)
    penalties: list[ExtractedValue] = Field(default_factory=list)
    applications: list[ExtractedValue] = Field(default_factory=list)
    typical_contract_reference: ExtractedValue | None = None
    smp_typical_terms_present: bool | None = None
    treasury_or_bank_support: ExtractedValue | None = None
    rights_transfer_terms: list[ExtractedValue] = Field(default_factory=list)


class ExplanatoryNoteSummary(DocumentSummary):
    subject: ExtractedValue | None = None
    procurement_goal: ExtractedValue | None = None
    procurement_method: ExtractedValue | None = None
    single_supplier_basis: ExtractedValue | None = None
    nmck: list[ExtractedValue] = Field(default_factory=list)
    delivery_places: list[ExtractedValue] = Field(default_factory=list)
    delivery_periods: list[ExtractedValue] = Field(default_factory=list)
    justification: ExtractedValue | None = None


AnyDocumentSummary = (
    PlanRequestSummary
    | ProcurementRequestSummary
    | CommercialOfferSummary
    | OnmckSummary
    | OozSummary
    | ContractSummary
    | ExplanatoryNoteSummary
    | DocumentSummary
)


class ProcurementPackage(BaseModel):
    plan: PlanRequestSummary | None = None
    request: ProcurementRequestSummary | None = None
    commercial_offers: list[CommercialOfferSummary] = Field(default_factory=list)
    onmck: OnmckSummary | None = None
    ooz: OozSummary | None = None
    contract: ContractSummary | None = None
    explanatory_note: ExplanatoryNoteSummary | None = None
    unknown_documents: list[DocumentSummary] = Field(default_factory=list)
    package_warnings: list[str] = Field(default_factory=list)


class Finding(BaseModel):
    rule_id: str
    severity: FindingSeverity = Field(
        description="Impact level: info, warning, error, or manual_review. Never use passed/failed here."
    )
    status: FindingStatus = Field(
        description="Check result: passed, failed, skipped, or uncertain."
    )
    title: str
    message: str
    documents: list[str] = Field(default_factory=list)
    expected: Any = None
    actual: Any = None
    evidence: list[Evidence] = Field(default_factory=list)
    source: Literal["deterministic", "external", "llm"]

    @field_validator("severity", mode="before")
    @classmethod
    def normalize_status_used_as_severity(cls, value):
        return {
            "passed": FindingSeverity.INFO,
            "failed": FindingSeverity.ERROR,
            "uncertain": FindingSeverity.MANUAL_REVIEW,
            "skipped": FindingSeverity.INFO,
        }.get(value, value)


class AnalyzerResult(BaseModel):
    analyzer: Literal[
        "items_consistency",
        "delivery_and_finance",
        "legal_and_completeness",
    ]
    findings: list[Finding] = Field(default_factory=list)
    coverage: list[str] = Field(default_factory=list)


class PipelineResult(BaseModel):
    package_id: str
    documents: list[AnyDocumentSummary]
    package: ProcurementPackage
    findings: list[Finding]
    report_text: str
    warnings: list[str] = Field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    run_metrics: dict[str, Any] = Field(default_factory=dict)
    document_labels: dict[str, str] = Field(default_factory=dict)
