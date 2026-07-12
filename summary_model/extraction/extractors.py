from __future__ import annotations

import asyncio
from copy import deepcopy

from summary_model.classification.document_classifier import ClassificationDecision
from summary_model.domain.models import (
    AnyDocumentSummary,
    ContractSummary,
    DeliveryTermsExtraction,
    DocumentIR,
    DocumentSummary,
    DocumentType,
)
from .heuristics import KTRU_RE, OKPD_RE, SUMMARY_TYPES, heuristic_summary
from .llm_client import StructuredLLMClient, render_ir_chunks, render_ir_for_llm
from .prompts import DOCUMENT_PROMPTS


def _apply_metadata(
    summary: DocumentSummary,
    ir: DocumentIR,
    display_name: str,
    decision: ClassificationDecision,
) -> DocumentSummary:
    summary.document_id = ir.document_id
    summary.display_name = display_name
    summary.detected_type = decision.document_type
    summary.classification_confidence = decision.confidence
    summary.classification_evidence = decision.evidence
    summary.extraction_warnings.extend(decision.warnings)
    return summary


def _empty(value) -> bool:
    if value is None or value == "" or value == []:
        return True
    if hasattr(value, "model_dump"):
        return all(_empty(item) for item in value.model_dump().values())
    return False


def _merge_summaries(parts: list[DocumentSummary]) -> DocumentSummary:
    merged = parts[0].model_copy(deep=True)
    metadata = {
        "document_id",
        "display_name",
        "detected_type",
        "classification_confidence",
        "classification_evidence",
    }
    for part in parts[1:]:
        for field_name in type(merged).model_fields:
            if field_name in metadata:
                continue
            current = getattr(merged, field_name)
            candidate = getattr(part, field_name)
            if isinstance(current, list) and isinstance(candidate, list):
                known = {
                    item.model_dump_json() if hasattr(item, "model_dump_json") else repr(item)
                    for item in current
                }
                for item in candidate:
                    key = item.model_dump_json() if hasattr(item, "model_dump_json") else repr(item)
                    if key not in known:
                        current.append(item)
                        known.add(key)
            elif _empty(current) and not _empty(candidate):
                setattr(merged, field_name, candidate)
    unresolved = set(field for part in parts for field in part.unresolved_fields)
    merged.unresolved_fields = sorted(
        field
        for field in unresolved
        if _empty(getattr(merged, field.split(".", 1)[0], None))
    )
    return merged


def _merge_contracts(parts: list[ContractSummary]) -> ContractSummary:
    return _merge_summaries(parts)  # type: ignore[return-value]


def _evidence_key(value) -> tuple:
    return tuple(
        (
            item.document_id,
            item.block_id,
            item.table_id,
            item.row,
            item.column,
        )
        for item in value.evidence
    )


def _sanitize_item_codes(summary: DocumentSummary) -> None:
    for wrapper in getattr(summary, "items", []):
        item = getattr(wrapper, "item", wrapper)
        source_values = [*item.okpd2, *item.ktru]
        okpd_values = []
        ktru_values = []
        seen_okpd: set[tuple] = set()
        seen_ktru: set[tuple] = set()
        for value in source_values:
            text = str(value.normalized_value or value.raw_value or "")
            ktru_codes = KTRU_RE.findall(text)
            okpd_codes = OKPD_RE.findall(KTRU_RE.sub(" ", text))
            for code, target, seen in (
                *((code, ktru_values, seen_ktru) for code in ktru_codes),
                *((code, okpd_values, seen_okpd) for code in okpd_codes),
            ):
                key = (code, _evidence_key(value))
                if key in seen:
                    continue
                cleaned = value.model_copy(deep=True)
                if not cleaned.raw_value or code not in str(cleaned.raw_value):
                    cleaned.raw_value = code
                cleaned.normalized_value = code
                target.append(cleaned)
                seen.add(key)
        item.okpd2 = okpd_values
        item.ktru = ktru_values


def _enrich_table_items(summary: DocumentSummary, fallback: DocumentSummary) -> None:
    if summary.detected_type not in {
        DocumentType.CONTRACT,
        DocumentType.OOZ,
        DocumentType.ONMCK,
        DocumentType.PLAN,
    }:
        return
    summary.items = deepcopy(fallback.items)


def _finalize_summary(
    summary: DocumentSummary,
    fallback: DocumentSummary,
) -> DocumentSummary:
    _enrich_table_items(summary, fallback)
    _sanitize_item_codes(summary)
    return summary


def _needs_delivery_repair(summary: DocumentSummary) -> bool:
    return (
        hasattr(summary, "delivery_periods")
        and hasattr(summary, "delivery_places")
        and (
            not getattr(summary, "delivery_periods")
            or not getattr(summary, "delivery_places")
        )
    )


def _merge_delivery_terms(
    summary: DocumentSummary,
    repair: DeliveryTermsExtraction,
) -> None:
    for field_name in ("delivery_periods", "delivery_places"):
        current = getattr(summary, field_name)
        known = {value.model_dump_json() for value in current}
        for value in getattr(repair, field_name):
            key = value.model_dump_json()
            if key not in known:
                current.append(value)
                known.add(key)
        if current:
            summary.unresolved_fields = [
                field
                for field in summary.unresolved_fields
                if field != field_name
            ]


def _delivery_payload(ir: DocumentIR, document_type: DocumentType) -> str:
    return render_ir_for_llm(
        ir,
        include_tables=document_type in {
            DocumentType.PLAN,
            DocumentType.REQUEST,
        },
        max_chars=120_000,
    )


class DocumentExtractionEngine:
    def __init__(self, llm_client: StructuredLLMClient | None = None) -> None:
        self.llm_client = llm_client

    def _repair_delivery_terms(
        self,
        summary: AnyDocumentSummary,
        ir: DocumentIR,
        document_type: DocumentType,
    ) -> AnyDocumentSummary:
        if self.llm_client is None or not _needs_delivery_repair(summary):
            return summary
        repair, error = self.llm_client.extract(
            DeliveryTermsExtraction,
            DOCUMENT_PROMPTS["delivery_terms"],
            _delivery_payload(ir, document_type),
        )
        if repair is not None:
            _merge_delivery_terms(summary, repair)
        elif error:
            summary.extraction_warnings.append(
                f"Delivery terms repair failed: {error}"
            )
        return summary

    async def _arepair_delivery_terms(
        self,
        summary: AnyDocumentSummary,
        ir: DocumentIR,
        document_type: DocumentType,
    ) -> AnyDocumentSummary:
        if self.llm_client is None or not _needs_delivery_repair(summary):
            return summary
        repair, error = await self.llm_client.aextract(
            DeliveryTermsExtraction,
            DOCUMENT_PROMPTS["delivery_terms"],
            _delivery_payload(ir, document_type),
        )
        if repair is not None:
            _merge_delivery_terms(summary, repair)
        elif error:
            summary.extraction_warnings.append(
                f"Delivery terms repair failed: {error}"
            )
        return summary

    def extract(
        self,
        ir: DocumentIR,
        decision: ClassificationDecision,
        display_name: str,
    ) -> AnyDocumentSummary:
        fallback = heuristic_summary(
            ir,
            decision.document_type,
            display_name,
            decision.confidence,
            decision.evidence,
            decision.warnings,
        )
        schema = SUMMARY_TYPES.get(decision.document_type)
        if self.llm_client is None or schema is None:
            return fallback

        if decision.document_type == DocumentType.CONTRACT:
            parts: list[ContractSummary] = []
            errors: list[str] = []
            for payload in render_ir_chunks(
                ir,
                include_tables=False,
                max_chars=60_000,
            ):
                result, error = self.llm_client.extract(
                    ContractSummary,
                    DOCUMENT_PROMPTS["contract_terms"],
                    payload,
                )
                if result:
                    parts.append(result)
                elif error:
                    errors.append(error)
            if parts:
                result = _merge_contracts(parts)
                result = _apply_metadata(result, ir, display_name, decision)
                result = _finalize_summary(result, fallback)
                return self._repair_delivery_terms(
                    result,
                    ir,
                    decision.document_type,
                )
        else:
            result, error = self.llm_client.extract(
                schema,
                DOCUMENT_PROMPTS[decision.document_type.value],
                render_ir_for_llm(ir),
            )
            if result is not None:
                result = _apply_metadata(result, ir, display_name, decision)
                result = _finalize_summary(result, fallback)
                return self._repair_delivery_terms(
                    result,
                    ir,
                    decision.document_type,
                )
            errors = [error] if error else []

        failed = deepcopy(fallback)
        failed.extraction_warnings.extend(errors)
        failed.unresolved_fields = sorted(set(failed.unresolved_fields + ["llm_extraction"]))
        return self._repair_delivery_terms(
            failed,
            ir,
            decision.document_type,
        )

    async def aextract(
        self,
        ir: DocumentIR,
        decision: ClassificationDecision,
        display_name: str,
    ) -> AnyDocumentSummary:
        fallback = heuristic_summary(
            ir,
            decision.document_type,
            display_name,
            decision.confidence,
            decision.evidence,
            decision.warnings,
        )
        schema = SUMMARY_TYPES.get(decision.document_type)
        if self.llm_client is None or schema is None:
            return fallback

        requests: list[tuple[str, str]] = []
        if decision.document_type == DocumentType.CONTRACT:
            requests.extend(
                (DOCUMENT_PROMPTS["contract_terms"], payload)
                for payload in render_ir_chunks(
                    ir,
                    include_tables=False,
                    max_chars=60_000,
                )
            )
        elif decision.document_type == DocumentType.OOZ:
            requests.extend(
                (DOCUMENT_PROMPTS["ooz"], payload)
                for payload in render_ir_chunks(
                    ir,
                    include_tables=False,
                    max_chars=60_000,
                )
            )
        else:
            requests.extend(
                (DOCUMENT_PROMPTS[decision.document_type.value], payload)
                for payload in render_ir_chunks(ir)
            )

        responses = await asyncio.gather(
            *(
                self.llm_client.aextract(schema, prompt, payload)
                for prompt, payload in requests
            )
        )
        parts = [result for result, _ in responses if result is not None]
        errors = [error for _, error in responses if error]
        if parts:
            result = _merge_summaries(parts)
            result = _apply_metadata(result, ir, display_name, decision)
            result = _finalize_summary(result, fallback)
            return await self._arepair_delivery_terms(
                result,
                ir,
                decision.document_type,
            )

        failed = deepcopy(fallback)
        failed.extraction_warnings.extend(errors)
        failed.unresolved_fields = sorted(
            set(failed.unresolved_fields + ["llm_extraction"])
        )
        _sanitize_item_codes(failed)
        return await self._arepair_delivery_terms(
            failed,
            ir,
            decision.document_type,
        )
