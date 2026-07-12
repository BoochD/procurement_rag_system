from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field


ExtractionDocumentType = Literal[
    "purchase_request",
    "schedule_application",
    "nmck_justification",
    "purchase_description",
    "contract_draft",
    "explanatory_note",
    "commercial_offer",
    "unknown",
]


class MoneyValue(BaseModel):
    raw: str | None = None
    amount: Decimal | None = None
    currency: str | None = "RUB"


class PercentValue(BaseModel):
    raw: str | None = None
    value_percent: Decimal | None = None


class SecurityValue(BaseModel):
    raw: str | None = None
    value_percent: Decimal | None = None
    value_amount: Decimal | None = None
    is_not_required: bool | None = None


class TermValue(BaseModel):
    raw: str | None = None
    days: int | None = None
    day_type: Literal["calendar", "working", "unknown"] | None = None
    start_event: str | None = None
    end_event: str | None = None


class StageTerm(BaseModel):
    stage_name: str | None = None
    start_condition: str | None = None
    end_condition: str | None = None
    deadline_text: str | None = None
    deadline_days: int | None = None


class WarrantyTerm(BaseModel):
    item_scope: str | None = None
    warranty_period_text: str | None = None
    warranty_period_months: int | None = None
    warranty_type: Literal[
        "manufacturer",
        "supplier",
        "contractor",
        "unknown",
    ] = "unknown"


class RawField(BaseModel):
    key: str
    value: str | None = None
    normalized_key: str | None = None
    is_empty: bool = False
    is_negative_value: bool = False
    evidence: str | None = None


class DocumentEnvelope(BaseModel):
    file_name: str
    file_path: str | None = None
    document_type: ExtractionDocumentType
    document_title: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    parser_warnings: list[str] = Field(default_factory=list)
    extraction_errors: list[str] = Field(default_factory=list)
    extracted_text_hash: str | None = None
    raw_text_preview: str | None = None


class RequestAttachment(BaseModel):
    number: str | None = None
    title_raw: str
    normalized_document_type: ExtractionDocumentType = "unknown"
    attachment_kind: Literal[
        "purchase_description",
        "acceptance_act_form",
        "contract_specification",
        "other",
        "unknown",
    ] = "unknown"
    evidence: str | None = None


class PriceSource(BaseModel):
    source_id: str
    supplier_name_raw: str | None = None
    outgoing_letter_number: str | None = None
    outgoing_letter_date: date | None = None
    raw_header: str
    evidence: str | None = None


class SupplierPrice(BaseModel):
    source_id: str
    unit_price: Decimal | None = None
    row_total: Decimal | None = None
    raw_unit_price: str | None = None
    raw_row_total: str | None = None


class PurchaseItemCharacteristic(BaseModel):
    name: str | None = None
    value: str | None = None
    unit: str | None = None
    evidence: str | None = None


class PurchaseItem(BaseModel):
    row_number: int | str | None = None
    name: str | None = None
    okpd2_code: str | None = None
    ktru_code: str | None = None
    unit: str | None = None
    quantity: Decimal | None = None
    quantity_raw: str | None = None
    characteristics: list[PurchaseItemCharacteristic] = Field(default_factory=list)
    evidence: str | None = None
    parser_warnings: list[str] = Field(default_factory=list)


class ContractSpecificationItem(BaseModel):
    row_number: int | str | None = None
    name: str | None = None
    description: str | None = None
    unit: str | None = None
    quantity: Decimal | None = None
    quantity_raw: str | None = None
    unit_price_without_vat: Decimal | None = None
    unit_price_with_vat: Decimal | None = None
    total_without_vat: Decimal | None = None
    vat_rate: str | None = None
    vat_amount: Decimal | None = None
    total_price: Decimal | None = None
    raw_unit_price_without_vat: str | None = None
    raw_unit_price_with_vat: str | None = None
    raw_total_without_vat: str | None = None
    raw_vat_amount: str | None = None
    raw_total_price: str | None = None
    evidence: str | None = None
    parser_warnings: list[str] = Field(default_factory=list)


class NmckItem(BaseModel):
    row_number: int | str | None = None
    name: str | None = None
    okpd2_code: str | None = None
    ktru_code: str | None = None
    unit: str | None = None
    quantity: Decimal | None = None
    quantity_raw: str | None = None
    supplier_prices: list[SupplierPrice] = Field(default_factory=list)
    selected_min_unit_price: Decimal | None = None
    selected_min_unit_price_raw: str | None = None
    row_total_declared: Decimal | None = None
    row_total_declared_raw: str | None = None
    row_total_calculated: Decimal | None = None
    calculated_min_unit_price: Decimal | None = None
    min_price_source_id: str | None = None
    is_declared_min_price_correct: bool | None = None
    is_row_total_correct: bool | None = None
    evidence: str | None = None


class ScheduleApplicationSchema(BaseModel):
    document_type: Literal["schedule_application"] = "schedule_application"
    document_title: str | None = None
    raw_fields: list[RawField] = Field(default_factory=list)
    raw_fields_dict: dict[str, str | None] = Field(default_factory=dict)
    empty_fields: list[str] = Field(default_factory=list)
    negative_value_fields: list[str] = Field(default_factory=list)
    purchase_subject: str | None = None
    okpd2_codes: list[str] = Field(default_factory=list)
    ktru_codes: list[str] = Field(default_factory=list)
    nmck: MoneyValue | None = None
    funding_source_text: str | None = None
    delivery_term_text: str | None = None
    delivery_term: TermValue | None = None
    contract_execution_term_text: str | None = None
    contract_execution_term: TermValue | None = None
    stage_execution_terms: list[StageTerm] = Field(default_factory=list)
    has_stages: bool | None = None
    smp_preference_raw: str | None = None
    smp_preference: bool | None = None
    subcontract_smp_sonko_required_raw: str | None = None
    subcontract_smp_sonko_required: bool | None = None
    subcontract_smp_sonko_percent_raw: str | None = None
    subcontract_smp_sonko_percent: Decimal | None = None
    application_security_raw: str | None = None
    application_security: SecurityValue | None = None
    contract_security_raw: str | None = None
    contract_security: SecurityValue | None = None
    warranty_security_raw: str | None = None
    warranty_security: SecurityValue | None = None
    additional_requirements_raw: str | None = None
    national_regime_raw: str | None = None
    parser_warnings: list[str] = Field(default_factory=list)


class PurchaseRequestSchema(BaseModel):
    document_type: Literal["purchase_request"] = "purchase_request"
    document_title: str | None = None
    request_number: str | None = None
    request_date: date | None = None
    purchase_subject: str | None = None
    nmck: MoneyValue | None = None
    procurement_method_raw: str | None = None
    procurement_method: Literal[
        "single_supplier",
        "auction",
        "competition",
        "request_for_quotations",
        "other",
        "unknown",
    ] | None = None
    single_supplier_basis_text: str | None = None
    delivery_term_text: str | None = None
    delivery_term: TermValue | None = None
    stages_text: str | None = None
    has_stages: bool | None = None
    stages: list[StageTerm] = Field(default_factory=list)
    attachments: list[RequestAttachment] = Field(default_factory=list)
    parser_warnings: list[str] = Field(default_factory=list)


class NmckJustificationSchema(BaseModel):
    document_type: Literal["nmck_justification"] = "nmck_justification"
    document_title: str | None = None
    nmck_method: str | None = None
    purchase_subject: str | None = None
    total_amount: MoneyValue | None = None
    total_amount_text: str | None = None
    price_sources: list[PriceSource] = Field(default_factory=list)
    items: list[NmckItem] = Field(default_factory=list)
    variation_coefficient_raw: str | None = None
    variation_coefficient: Decimal | None = None
    parser_warnings: list[str] = Field(default_factory=list)


class PurchaseDescriptionSchema(BaseModel):
    document_type: Literal["purchase_description"] = "purchase_description"
    document_title: str | None = None
    purchase_subject: str | None = None
    delivery_place: str | None = None
    delivery_term_text: str | None = None
    delivery_term: TermValue | None = None
    items: list[PurchaseItem] = Field(default_factory=list)
    warranty_requirements_text: str | None = None
    parser_warnings: list[str] = Field(default_factory=list)


class ContractDraftSchema(BaseModel):
    document_type: Literal["contract_draft"] = "contract_draft"
    document_title: str | None = None
    contract_number: str | None = None
    subject: str | None = None
    price: MoneyValue | None = None
    funding_source: str | None = None
    delivery_place: str | None = None
    delivery_term_text: str | None = None
    delivery_term: TermValue | None = None
    contract_execution_term_text: str | None = None
    contract_execution_term: TermValue | None = None
    warranty_text: str | None = None
    contract_security_raw: str | None = None
    contract_security: SecurityValue | None = None
    warranty_security_raw: str | None = None
    warranty_security: SecurityValue | None = None
    referenced_attachments: list[RequestAttachment] = Field(default_factory=list)
    actual_attachments: list[RequestAttachment] = Field(default_factory=list)
    embedded_purchase_description: PurchaseDescriptionSchema | None = None
    items: list[PurchaseItem] = Field(default_factory=list)
    specification_items: list[ContractSpecificationItem] = Field(default_factory=list)
    parser_warnings: list[str] = Field(default_factory=list)


class ExplanatoryNoteSchema(BaseModel):
    document_type: Literal["explanatory_note"] = "explanatory_note"
    document_title: str | None = None
    subject: str | None = None
    nmck: MoneyValue | None = None
    procurement_method_raw: str | None = None
    procurement_method: str | None = None
    justification_text: str | None = None
    parser_warnings: list[str] = Field(default_factory=list)


class CommercialOfferSchema(BaseModel):
    document_type: Literal["commercial_offer"] = "commercial_offer"
    document_title: str | None = None
    supplier_name: str | None = None
    inn: str | None = None
    outgoing_number: str | None = None
    offer_date: date | None = None
    items: list[PurchaseItem] = Field(default_factory=list)
    total_amount: MoneyValue | None = None
    parser_warnings: list[str] = Field(default_factory=list)


class ProcurementPackageExtraction(BaseModel):
    schema_version: str = "extraction-1.1.0"
    package_id: str | None = None
    files: list[DocumentEnvelope] = Field(default_factory=list)
    purchase_request: PurchaseRequestSchema | None = None
    schedule_application: ScheduleApplicationSchema | None = None
    nmck_justification: NmckJustificationSchema | None = None
    purchase_description: PurchaseDescriptionSchema | None = None
    contract_draft: ContractDraftSchema | None = None
    explanatory_note: ExplanatoryNoteSchema | None = None
    commercial_offers: list[CommercialOfferSchema] = Field(default_factory=list)
    commercial_offers_found_count: int = 0
    commercial_offers_required_count: int = 3
    commercial_offers_missing: bool = True
    package_warnings: list[str] = Field(default_factory=list)
    tables: list[dict[str, Any]] = Field(default_factory=list)
