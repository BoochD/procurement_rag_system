from __future__ import annotations

import json
import re
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any, TypeVar

from pydantic import BaseModel

from summary_model.domain.models import DocumentType
from summary_model.extraction.llm_client import StructuredLLMClient
from summary_model.extraction.llm_prompts import prompt_for_document_type
from summary_model.extraction_models import (
    CommercialOfferSchema,
    ContractDraftSchema,
    ExplanatoryNoteSchema,
    NmckJustificationSchema,
    ProcurementPackageExtraction,
    PurchaseDescriptionSchema,
    PurchaseRequestSchema,
    ScheduleApplicationSchema,
)


T = TypeVar("T", bound=BaseModel)


SCHEMA_BY_DOCUMENT_TYPE: dict[DocumentType, type[BaseModel]] = {
    DocumentType.PLAN: ScheduleApplicationSchema,
    DocumentType.REQUEST: PurchaseRequestSchema,
    DocumentType.ONMCK: NmckJustificationSchema,
    DocumentType.OOZ: PurchaseDescriptionSchema,
    DocumentType.CONTRACT: ContractDraftSchema,
    DocumentType.EXPLANATORY_NOTE: ExplanatoryNoteSchema,
    DocumentType.COMMERCIAL_OFFER: CommercialOfferSchema,
}


def extract_document_schema_with_llm(
    *,
    payload: dict[str, Any],
    document_type: DocumentType,
    deterministic_schema: BaseModel | None,
    llm_client: StructuredLLMClient,
) -> tuple[BaseModel | None, str | None]:
    schema = SCHEMA_BY_DOCUMENT_TYPE.get(document_type)
    if schema is None:
        return deterministic_schema, "LLM extraction skipped: unsupported document type."

    prompt = prompt_for_document_type(document_type)
    result, error = llm_client.extract(
        schema,
        prompt,
        json.dumps(payload, ensure_ascii=False, default=str),
    )
    if error or result is None:
        fallback = _copy_model(deterministic_schema)
        if fallback is not None:
            _append_warning(fallback, _friendly_llm_warning(error))
        return fallback, error

    merged = _merge_with_deterministic_guard(
        llm_result=result,
        deterministic_schema=deterministic_schema,
        document_type=document_type,
    )
    _postprocess_document_schema(merged, deterministic_schema, document_type)
    return merged, None


async def aextract_document_schema_with_llm(
    *,
    payload: dict[str, Any],
    document_type: DocumentType,
    deterministic_schema: BaseModel | None,
    llm_client: StructuredLLMClient,
) -> tuple[BaseModel | None, str | None]:
    schema = SCHEMA_BY_DOCUMENT_TYPE.get(document_type)
    if schema is None:
        return deterministic_schema, "LLM extraction skipped: unsupported document type."

    prompt = prompt_for_document_type(document_type)
    result, error = await llm_client.aextract(
        schema,
        prompt,
        json.dumps(payload, ensure_ascii=False, default=str),
    )
    if error or result is None:
        fallback = _copy_model(deterministic_schema)
        if fallback is not None:
            _append_warning(fallback, _friendly_llm_warning(error))
        return fallback, error

    merged = _merge_with_deterministic_guard(
        llm_result=result,
        deterministic_schema=deterministic_schema,
        document_type=document_type,
    )
    _postprocess_document_schema(merged, deterministic_schema, document_type)
    return merged, None


def apply_llm_document_result(
    package: ProcurementPackageExtraction,
    document_type: DocumentType,
    document_schema: BaseModel | None,
) -> None:
    if document_schema is None:
        return
    if document_type == DocumentType.PLAN and isinstance(document_schema, ScheduleApplicationSchema):
        package.schedule_application = document_schema
    elif document_type == DocumentType.REQUEST and isinstance(document_schema, PurchaseRequestSchema):
        package.purchase_request = document_schema
    elif document_type == DocumentType.ONMCK and isinstance(document_schema, NmckJustificationSchema):
        package.nmck_justification = document_schema
    elif document_type == DocumentType.OOZ and isinstance(document_schema, PurchaseDescriptionSchema):
        package.purchase_description = document_schema
    elif document_type == DocumentType.CONTRACT and isinstance(document_schema, ContractDraftSchema):
        package.contract_draft = document_schema
    elif document_type == DocumentType.EXPLANATORY_NOTE and isinstance(document_schema, ExplanatoryNoteSchema):
        package.explanatory_note = document_schema
    elif document_type == DocumentType.COMMERCIAL_OFFER and isinstance(document_schema, CommercialOfferSchema):
        _replace_or_append_commercial_offer(package, document_schema)


def _replace_or_append_commercial_offer(
    package: ProcurementPackageExtraction,
    document_schema: CommercialOfferSchema,
) -> None:
    for index, existing in enumerate(package.commercial_offers):
        if existing.document_title == document_schema.document_title:
            package.commercial_offers[index] = document_schema
            return
    package.commercial_offers.append(document_schema)
    package.commercial_offers_found_count = len(package.commercial_offers)
    package.commercial_offers_missing = (
        package.commercial_offers_found_count < package.commercial_offers_required_count
    )


def _merge_with_deterministic_guard(
    *,
    llm_result: T,
    deterministic_schema: BaseModel | None,
    document_type: DocumentType,
) -> T:
    if deterministic_schema is None:
        return llm_result

    merged = llm_result.model_copy(deep=True)
    warnings: list[str] = []

    if document_type == DocumentType.PLAN:
        _prefer_list(merged, deterministic_schema, "raw_fields")
        _prefer_dict(merged, deterministic_schema, "raw_fields_dict")
        _preserve_code_list(merged, deterministic_schema, "okpd2_codes", warnings)
        _preserve_code_list(merged, deterministic_schema, "ktru_codes", warnings)
        for field_name in (
            "empty_fields",
            "negative_value_fields",
            "subject_codes",
            "stage_deliverables",
            "included_goods",
            "stage_execution_terms",
            "national_regime_fields",
        ):
            _prefer_list(merged, deterministic_schema, field_name)
        for field_name in (
            "purchase_subject",
            "nmck",
            "procurement_method_raw",
            "procurement_method",
            "single_supplier_basis_text",
            "funding_source_text",
            "delivery_place",
            "delivery_term_text",
            "delivery_term",
            "contract_execution_term_text",
            "contract_execution_term",
            "aggregate_quantity_text",
            "has_stages",
            "smp_preference_raw",
            "smp_preference",
            "subcontract_smp_sonko_required_raw",
            "subcontract_smp_sonko_required",
            "subcontract_smp_sonko_percent_raw",
            "subcontract_smp_sonko_percent",
            "application_security_raw",
            "application_security",
            "contract_security_raw",
            "contract_security",
            "warranty_security_raw",
            "warranty_security",
            "additional_requirements_raw",
            "national_regime_raw",
            "additional_participant_requirements_text",
        ):
            _prefer_scalar(merged, deterministic_schema, field_name)
        _prefer_stages(merged, deterministic_schema)
    elif document_type == DocumentType.REQUEST:
        _prefer_list(merged, deterministic_schema, "attachments")
        _prefer_stages(merged, deterministic_schema)
    elif document_type == DocumentType.ONMCK:
        _preserve_code_list(merged, deterministic_schema, "okpd2_codes", warnings)
        _preserve_code_list(merged, deterministic_schema, "ktru_codes", warnings)
        _preserve_list(merged, deterministic_schema, "subject_codes", warnings, warn=False)
        _prefer_list(merged, deterministic_schema, "price_sources")
        _prefer_list(merged, deterministic_schema, "items")
        _prefer_stages(merged, deterministic_schema)
        _prefer_scalar(merged, deterministic_schema, "total_amount")
        _prefer_scalar(merged, deterministic_schema, "total_amount_text")
    elif document_type == DocumentType.OOZ:
        _preserve_code_list(merged, deterministic_schema, "okpd2_codes", warnings)
        _preserve_code_list(merged, deterministic_schema, "ktru_codes", warnings)
        _preserve_list(merged, deterministic_schema, "subject_codes", warnings, warn=False)
        _prefer_list(merged, deterministic_schema, "items")
        _prefer_stages(merged, deterministic_schema)
        _prefer_scalar(merged, deterministic_schema, "purchase_subject")
        _prefer_scalar(merged, deterministic_schema, "warranty_requirements_text")
        _prefer_scalar(merged, deterministic_schema, "warranty_section_text")
        _prefer_list(
            merged,
            deterministic_schema,
            "additional_characteristics_justifications",
        )
        _preserve_scalar(
            merged,
            deterministic_schema,
            "additional_characteristics_justification_text",
            warnings,
            warn=False,
        )
    elif document_type == DocumentType.CONTRACT:
        _preserve_code_list(merged, deterministic_schema, "okpd2_codes", warnings)
        _preserve_code_list(merged, deterministic_schema, "ktru_codes", warnings)
        _preserve_list(merged, deterministic_schema, "subject_codes", warnings, warn=False)
        _prefer_list(merged, deterministic_schema, "items")
        _prefer_list(merged, deterministic_schema, "specification_items")
        _prefer_stages(merged, deterministic_schema)
        _preserve_list(merged, deterministic_schema, "referenced_attachments", warnings, warn=False)
        _preserve_list(merged, deterministic_schema, "actual_attachments", warnings, warn=False)
        _preserve_scalar(merged, deterministic_schema, "contract_execution_term_text", warnings, warn=False)
        _preserve_scalar(merged, deterministic_schema, "contract_execution_term", warnings, warn=False)
        merged.responsibility_section_text = deepcopy(
            getattr(deterministic_schema, "responsibility_section_text", None)
        )
        _preserve_scalar(merged, deterministic_schema, "subcontract_smp_sonko_required_raw", warnings, warn=False)
        _preserve_scalar(merged, deterministic_schema, "subcontract_smp_sonko_required", warnings, warn=False)
        _preserve_scalar(merged, deterministic_schema, "subcontract_smp_sonko_percent_raw", warnings, warn=False)
        _preserve_scalar(merged, deterministic_schema, "subcontract_smp_sonko_percent", warnings, warn=False)
        _preserve_scalar(merged, deterministic_schema, "smp_sonko_standard_terms_text", warnings, warn=False)
        _preserve_scalar(merged, deterministic_schema, "contract_security_raw", warnings, warn=False)
        _preserve_scalar(merged, deterministic_schema, "contract_security", warnings, warn=False)
        _preserve_scalar(merged, deterministic_schema, "warranty_security_raw", warnings, warn=False)
        _preserve_scalar(merged, deterministic_schema, "warranty_security", warnings, warn=False)
        _prefer_deterministic_security(merged, deterministic_schema, "contract_security_raw", "contract_security")
        _prefer_deterministic_security(merged, deterministic_schema, "warranty_security_raw", "warranty_security")
        # Penalties are owned by the dedicated package-level check. The general
        # document LLM must not create a second, weaker interpretation.
        merged.penalty_clauses = deepcopy(
            getattr(deterministic_schema, "penalty_clauses", []) or []
        )
        merged.peni_clauses = deepcopy(
            getattr(deterministic_schema, "peni_clauses", []) or []
        )
        _preserve_embedded_description(merged, deterministic_schema, warnings, warn=False)
    elif document_type == DocumentType.COMMERCIAL_OFFER:
        _preserve_list(merged, deterministic_schema, "items", warnings, warn=False)
        for field_name in (
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
            "total_amount",
        ):
            _preserve_scalar(merged, deterministic_schema, field_name, warnings, warn=False)

    for warning in warnings:
        _append_warning(merged, warning)
    return merged


def _prefer_deterministic_security(
    merged: BaseModel,
    deterministic_schema: BaseModel,
    raw_field: str,
    value_field: str,
) -> None:
    """Security clauses selected by explicit headings beat broad LLM excerpts."""
    raw_value = getattr(deterministic_schema, raw_field, None)
    structured_value = getattr(deterministic_schema, value_field, None)
    if raw_value:
        setattr(merged, raw_field, raw_value)
    if structured_value is not None:
        setattr(merged, value_field, structured_value.model_copy(deep=True))


def _copy_model(value: BaseModel | None) -> BaseModel | None:
    return value.model_copy(deep=True) if value is not None else None


def _prefer_scalar(target: BaseModel, source: BaseModel, field_name: str) -> None:
    """Keep a non-empty deterministic value; LLM only fills parser gaps."""
    source_value = getattr(source, field_name, None)
    if _is_missing_value(source_value):
        return
    setattr(target, field_name, deepcopy(source_value))


def _prefer_list(target: BaseModel, source: BaseModel, field_name: str) -> None:
    source_value = list(getattr(source, field_name, []) or [])
    if source_value:
        setattr(target, field_name, deepcopy(source_value))


def _prefer_dict(target: BaseModel, source: BaseModel, field_name: str) -> None:
    source_value = dict(getattr(source, field_name, {}) or {})
    if source_value:
        setattr(target, field_name, deepcopy(source_value))


def _prefer_stages(target: BaseModel, source: BaseModel) -> None:
    if hasattr(source, "has_stages"):
        _prefer_scalar(target, source, "has_stages")
    _prefer_list(target, source, "stages")


def _append_warning(model: BaseModel, warning: str) -> None:
    if not hasattr(model, "parser_warnings"):
        return
    current = list(getattr(model, "parser_warnings") or [])
    if warning not in current:
        current.append(warning)
    setattr(model, "parser_warnings", current)


def _friendly_llm_warning(error: str | None) -> str:
    text = error or ""
    lowered = text.casefold()
    if (
        "input_value=None" in text
        or "returned no result" in lowered
        or "structured output (none)" in lowered
        or "пустой structured output" in lowered
    ):
        return (
            "LLM не вернула структурированный результат; использованы данные "
            "deterministic/table/VLM parser."
        )
    return f"LLM extraction не применена; использованы данные parser fallback. Причина: {text}"


def _preserve_list(
    target: BaseModel,
    source: BaseModel,
    field_name: str,
    warnings: list[str],
    *,
    warn: bool = True,
) -> None:
    source_values = list(getattr(source, field_name, []) or [])
    target_values = list(getattr(target, field_name, []) or [])
    if not source_values:
        return
    if len(target_values) < len(source_values):
        setattr(target, field_name, deepcopy(source_values))
        if warn:
            warnings.append(
                f"LLM output lost parsed field '{field_name}'; deterministic values were restored."
            )


def _preserve_dict(
    target: BaseModel,
    source: BaseModel,
    field_name: str,
    warnings: list[str],
    *,
    warn: bool = True,
) -> None:
    source_value = dict(getattr(source, field_name, {}) or {})
    target_value = dict(getattr(target, field_name, {}) or {})
    if not source_value:
        return
    missing = [key for key in source_value if key not in target_value]
    if missing:
        restored = {**source_value, **target_value}
        setattr(target, field_name, restored)
        if warn:
            warnings.append(
                f"LLM output lost parsed keys in '{field_name}'; deterministic keys were restored."
            )


def _preserve_code_list(
    target: BaseModel,
    source: BaseModel,
    field_name: str,
    warnings: list[str],
) -> None:
    source_values = list(getattr(source, field_name, []) or [])
    target_values = list(getattr(target, field_name, []) or [])
    if not source_values:
        return
    missing = [value for value in source_values if value not in target_values]
    if missing:
        setattr(target, field_name, target_values + missing)
        warnings.append(
            f"LLM output lost parsed codes in '{field_name}'; deterministic codes were restored."
        )


def _preserve_scalar(
    target: BaseModel,
    source: BaseModel,
    field_name: str,
    warnings: list[str],
    *,
    warn: bool = True,
) -> None:
    source_value = getattr(source, field_name, None)
    target_value = getattr(target, field_name, None)
    if source_value is not None and target_value is None:
        setattr(target, field_name, deepcopy(source_value))
        if warn:
            warnings.append(
                f"LLM output lost parsed field '{field_name}'; deterministic value was restored."
            )


def _preserve_list_item_fields(
    target: BaseModel,
    source: BaseModel,
    field_name: str,
    item_field_names: list[str],
    warnings: list[str],
) -> None:
    source_items = list(getattr(source, field_name, []) or [])
    target_items = list(getattr(target, field_name, []) or [])
    if not source_items or not target_items:
        return

    restored_fields: set[str] = set()
    source_by_row = {
        str(getattr(item, "row_number", "")).strip(): item
        for item in source_items
        if str(getattr(item, "row_number", "")).strip()
    }
    source_by_identity = {
        _item_identity_key(item): item
        for item in source_items
        if _item_identity_key(item) is not None
    }
    for index, target_item in enumerate(target_items):
        source_item = _matching_source_item(
            target_item=target_item,
            source_items=source_items,
            source_by_row=source_by_row,
            source_by_identity=source_by_identity,
            fallback_index=index,
        )
        if source_item is None:
            continue
        for item_field_name in item_field_names:
            source_value = getattr(source_item, item_field_name, None)
            target_value = getattr(target_item, item_field_name, None)
            if not _is_missing_value(source_value) and _is_missing_value(target_value):
                setattr(target_item, item_field_name, deepcopy(source_value))
                restored_fields.add(item_field_name)
    if restored_fields:
        warnings.append(
            f"LLM output lost parsed item fields in '{field_name}'; restored: {', '.join(sorted(restored_fields))}."
        )


def _preserve_stage_fields(
    target: BaseModel,
    source: BaseModel,
    warnings: list[str],
) -> None:
    source_stages = list(getattr(source, "stages", []) or [])
    target_stages = list(getattr(target, "stages", []) or [])
    if hasattr(source, "has_stages"):
        setattr(target, "has_stages", getattr(source, "has_stages"))
    if not source_stages:
        setattr(target, "stages", [])
        return
    if len(target_stages) < len(source_stages):
        setattr(target, "stages", deepcopy(source_stages))
        warnings.append("LLM output lost parsed stages; deterministic stages were restored.")
        return

    restored_fields: set[str] = set()
    field_names = [
        "stage_number",
        "stage_name",
        "result_text",
        "start_text",
        "service_term_text",
        "service_start_date",
        "service_end_date",
        "execution_end_date",
        "price",
        "quantity_text",
    ]
    for index, source_stage in enumerate(source_stages):
        if index >= len(target_stages):
            break
        target_stage = target_stages[index]
        for field_name in field_names:
            source_value = getattr(source_stage, field_name, None)
            target_value = getattr(target_stage, field_name, None)
            if not _is_missing_value(source_value) and _is_missing_value(target_value):
                setattr(target_stage, field_name, deepcopy(source_value))
                restored_fields.add(field_name)
    if restored_fields:
        warnings.append(
            "LLM output lost parsed stage fields; restored: "
            + ", ".join(sorted(restored_fields))
            + "."
        )


def _matching_source_item(
    *,
    target_item: Any,
    source_items: list[Any],
    source_by_row: dict[str, Any],
    source_by_identity: dict[tuple[str, str, str], Any],
    fallback_index: int,
) -> Any | None:
    row_number = str(getattr(target_item, "row_number", "")).strip()
    if row_number and row_number in source_by_row:
        return source_by_row[row_number]

    identity_key = _item_identity_key(target_item)
    if identity_key is not None and identity_key in source_by_identity:
        return source_by_identity[identity_key]

    if len(source_items) == 1:
        return source_items[0]
    if len(source_items) > fallback_index:
        return source_items[fallback_index]
    return None


def _item_identity_key(item: Any) -> tuple[str, str, str] | None:
    name = _normalize_item_text(getattr(item, "name", None))
    if not name:
        return None
    quantity = _normalize_item_number(getattr(item, "quantity", None) or getattr(item, "quantity_raw", None))
    unit = _normalize_item_text(getattr(item, "unit", None))
    return (name, quantity, unit)


def _normalize_item_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _normalize_item_number(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace(",", ".").strip()


def _is_missing_value(value: Any) -> bool:
    return value in (None, "", [], {})


def _preserve_embedded_description(
    target: BaseModel,
    source: BaseModel,
    warnings: list[str],
    *,
    warn: bool = True,
) -> None:
    source_embedded = getattr(source, "embedded_purchase_description", None)
    target_embedded = getattr(target, "embedded_purchase_description", None)
    if source_embedded is None:
        return
    if target_embedded is None:
        setattr(target, "embedded_purchase_description", source_embedded.model_copy(deep=True))
        return
    _prefer_scalar(target_embedded, source_embedded, "purchase_subject")
    _prefer_scalar(target_embedded, source_embedded, "warranty_requirements_text")
    _prefer_scalar(target_embedded, source_embedded, "warranty_section_text")
    _prefer_list(target_embedded, source_embedded, "items")
    _prefer_list(target_embedded, source_embedded, "stages")
    _prefer_list(
        target_embedded,
        source_embedded,
        "additional_characteristics_justifications",
    )
    _prefer_scalar(
        target_embedded,
        source_embedded,
        "additional_characteristics_justification_text",
    )
    source_items = getattr(source_embedded, "items", []) or []
    target_items = getattr(target_embedded, "items", []) if target_embedded is not None else []
    if source_items and len(target_items) < len(source_items):
        setattr(target, "embedded_purchase_description", source_embedded.model_copy(deep=True))
        if warn:
            warnings.append(
                "LLM output lost embedded purchase-description items; deterministic values were restored."
            )


def _postprocess_document_schema(
    target: BaseModel,
    source: BaseModel | None,
    document_type: DocumentType,
) -> None:
    if source is not None:
        _fill_missing_text(target, source, "document_title")
    _clear_empty_money_fields(target)
    _fill_term_text(target, "delivery_term_text", "delivery_term")
    _fill_term_text(target, "contract_execution_term_text", "contract_execution_term")
    if document_type == DocumentType.CONTRACT:
        _fill_contract_smp_percent(target)
    if document_type == DocumentType.REQUEST and hasattr(target, "purchase_subject"):
        subject = getattr(target, "purchase_subject", None)
        cleaned = _clean_purchase_subject(subject)
        if cleaned:
            setattr(target, "purchase_subject", cleaned)


def _fill_contract_smp_percent(target: BaseModel) -> None:
    if getattr(target, "subcontract_smp_sonko_percent", None) is not None:
        return
    raw = (
        getattr(target, "subcontract_smp_sonko_percent_raw", None)
        or getattr(target, "subcontract_smp_sonko_required_raw", None)
    )
    if not isinstance(raw, str):
        return
    match = re.search(
        r"(\d+(?:[,.]\d+)?)\s*(?:\([^)]*\)\s*)?(?:%|процент)",
        raw,
        flags=re.IGNORECASE,
    )
    if not match:
        return
    try:
        target.subcontract_smp_sonko_percent = Decimal(match.group(1).replace(",", "."))
    except InvalidOperation:
        return


def _fill_missing_text(target: BaseModel, source: BaseModel, field_name: str) -> None:
    if not hasattr(target, field_name) or not hasattr(source, field_name):
        return
    if getattr(target, field_name, None):
        return
    source_value = getattr(source, field_name, None)
    if isinstance(source_value, str) and source_value.strip():
        setattr(target, field_name, source_value.strip())


def _fill_term_text(target: BaseModel, text_field: str, value_field: str) -> None:
    if not hasattr(target, text_field) or getattr(target, text_field, None):
        return
    structured = getattr(target, value_field, None)
    raw = getattr(structured, "raw", None)
    if isinstance(raw, str) and raw.strip():
        setattr(target, text_field, raw.strip())


def _clear_empty_money_fields(target: BaseModel) -> None:
    for field_name in ("price", "nmck", "total_amount"):
        if not hasattr(target, field_name):
            continue
        value = getattr(target, field_name, None)
        if value is None:
            continue
        if _is_missing_value(getattr(value, "amount", None)) and _is_missing_value(getattr(value, "raw", None)):
            setattr(target, field_name, None)


def _clean_purchase_subject(value: str | None) -> str | None:
    if not value:
        return value
    text = " ".join(value.split())
    bracket_match = re.search(r"\(([^()]*поставк[^()]*)\)", text, flags=re.IGNORECASE)
    if bracket_match:
        return _sentence_case_subject(bracket_match.group(1))
    lowered = text.casefold()
    prefixes = [
        "обращение о проведении закупки",
        "проведение закупки",
    ]
    for prefix in prefixes:
        if lowered.startswith(prefix) and ":" in text:
            return _sentence_case_subject(text.split(":", 1)[1].strip())
    return _sentence_case_subject(text)


def _sentence_case_subject(value: str) -> str:
    value = value.strip(" .;")
    if not value:
        return value
    return value[0].upper() + value[1:]
