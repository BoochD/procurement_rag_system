from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from summary_model.checks import run_checks
from summary_model.checks.ktru_adapter import run_ktru_characteristic_checks, run_pp1875_checks
from summary_model.checks.models import ProcurementChecksReport
from summary_model.checks.penalty_llm import run_penalty_llm_checks
from summary_model.checks.report import build_checks_report_text
from summary_model.checks.runner import external_manual_checks_with_replacements
from summary_model.checks.semantic_llm import run_semantic_llm_checks
from summary_model.checks.stage_llm import run_stage_llm_checks
from summary_model.classification import DocumentClassifier
from summary_model.commercial_offer_vlm import CommercialOfferVlmOptions, extract_commercial_offer_with_vlm
from summary_model.domain.models import DocumentType, InputDocument
from summary_model.extraction_models import DocumentEnvelope, ProcurementPackageExtraction
from summary_model.extraction.llm_client import StructuredLLMClient
from summary_model.extraction.llm_document_extractor import (
    aextract_document_schema_with_llm,
    apply_llm_document_result,
)
from summary_model.extraction.llm_payloads import build_document_llm_payload
from summary_model.extraction_pipeline import RequiredDocumentExtractionError, extract_package
from summary_model.ingestion import read_docx
from summary_model.tables import extract_tables
from summary_model.vlm_fallback import VlmFallbackOptions, VlmFallbackRepairer


DOCUMENT_TYPE_HINTS = {
    "plan": DocumentType.PLAN,
    "contract": DocumentType.CONTRACT,
    "ooz": DocumentType.OOZ,
    "zapiska": DocumentType.EXPLANATORY_NOTE,
    "onmck": DocumentType.ONMCK,
    "obrasheniye": DocumentType.REQUEST,
    "commercial_offer": DocumentType.COMMERCIAL_OFFER,
}


@dataclass
class WebPipelineOptions:
    with_llm_extraction: bool = True
    with_semantic_llm: bool = True
    with_ktru: bool = True
    with_vlm_tables: bool = False
    with_vlm_commercial_offers: bool = True
    ktru_timeout_seconds: int = 30
    llm_concurrency: int = 6
    vlm_max_tables_per_document: int = 4
    vlm_max_commercial_offer_pages: int = 8
    vlm_output_dir: Path | None = None


@dataclass
class WebPipelineResult:
    report_text: str
    package_id: str | None
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    package: ProcurementPackageExtraction | None = None
    checks_report: ProcurementChecksReport | None = None


class WebPipelineInputError(ValueError):
    """A user-facing error for a mandatory document that cannot be processed."""


def process_uploaded_documents(
    uploaded_documents: list[dict[str, Any]],
    *,
    options: WebPipelineOptions | None = None,
) -> WebPipelineResult:
    try:
        return asyncio.run(_aprocess_uploaded_documents(uploaded_documents, options=options))
    except RequiredDocumentExtractionError as error:
        raise WebPipelineInputError(str(error)) from error


async def _aprocess_uploaded_documents(
    uploaded_documents: list[dict[str, Any]],
    *,
    options: WebPipelineOptions | None = None,
) -> WebPipelineResult:
    options = options or WebPipelineOptions()
    input_documents = _input_documents(uploaded_documents)
    docx_documents, media_commercial_offers, unsupported_documents = _split_input_documents(input_documents)
    vlm_repairer = VlmFallbackRepairer(
        VlmFallbackOptions(
            enabled=options.with_vlm_tables,
            output_dir=options.vlm_output_dir or Path("runtime/web_vlm_tables"),
            max_tables_per_document=max(1, options.vlm_max_tables_per_document),
        )
    )
    package = extract_package(
        docx_documents,
        table_repairer=vlm_repairer.repair_document_tables if options.with_vlm_tables else None,
        continue_on_document_error=True,
    )
    warnings: list[str] = [
        warning
        for warning in package.package_warnings
        if "DOCX parsing failed:" in warning or "schema extraction failed:" in warning
    ]
    metrics: dict[str, Any] = {
        "vlm_tables": vlm_repairer.metrics if options.with_vlm_tables else {"enabled": False}
    }
    warnings.extend(_public_vlm_table_warnings(vlm_repairer.warnings))
    if unsupported_documents:
        warnings.extend(
            f"{document.path.name}: формат поддерживается только для КП через VLM."
            for document in unsupported_documents
        )

    commercial_offer_metrics = []
    for document in media_commercial_offers:
        result = extract_commercial_offer_with_vlm(
            document.path,
            options=CommercialOfferVlmOptions(
                enabled=options.with_vlm_commercial_offers,
                max_pages=options.vlm_max_commercial_offer_pages,
            ),
        )
        package.commercial_offers.append(result.offer)
        package.files.append(
            DocumentEnvelope(
                file_name=document.path.name,
                file_path=str(document.path),
                document_type="commercial_offer",
                confidence=0.75 if not result.offer.parser_warnings else 0.35,
                parser_warnings=result.offer.parser_warnings,
            )
        )
        commercial_offer_metrics.append(
            {
                "file_name": document.path.name,
                **result.metrics,
            }
        )
        warnings.extend(
            _commercial_offer_recovery_warnings(document.path.name, result.metrics)
        )
    if media_commercial_offers:
        package.commercial_offers_found_count = len(package.commercial_offers)
        package.commercial_offers_missing = (
            package.commercial_offers_found_count < package.commercial_offers_required_count
        )
        metrics["commercial_offer_vlm"] = commercial_offer_metrics

    if options.with_llm_extraction:
        llm_warnings, llm_metrics = await _apply_llm_extraction(
            package,
            docx_documents,
            concurrency=options.llm_concurrency,
            vlm_repairer=vlm_repairer if options.with_vlm_tables else None,
        )
        warnings.extend(llm_warnings)
        metrics["document_llm"] = llm_metrics
        warnings.extend(_llm_recovery_warnings("извлечение документа через LLM", llm_metrics))

    semantic_results = None
    stage_results = None
    penalty_results = None
    if options.with_semantic_llm:
        try:
            semantic_results, semantic_metrics = run_semantic_llm_checks(package)
            metrics["semantic_llm"] = semantic_metrics
            warnings.extend(_llm_recovery_warnings("семантическая LLM-проверка", semantic_metrics))
        except Exception as error:
            warnings.append(_pipeline_warning("semantic LLM", error))
            metrics["semantic_llm"] = {"error": _error_summary(error)}
        try:
            stage_results, stage_metrics = run_stage_llm_checks(package)
            metrics["stage_llm"] = stage_metrics
            warnings.extend(_llm_recovery_warnings("LLM-проверка этапов", stage_metrics))
        except Exception as error:
            warnings.append(_pipeline_warning("stage LLM", error))
            metrics["stage_llm"] = {"error": _error_summary(error)}
        try:
            penalty_results, penalty_metrics = run_penalty_llm_checks(package)
            metrics["penalty_llm"] = penalty_metrics
            warnings.extend(_llm_recovery_warnings("LLM-проверка штрафов", penalty_metrics))
        except Exception as error:
            warnings.append(_pipeline_warning("penalty LLM", error))
            metrics["penalty_llm"] = {"error": _error_summary(error)}

    external_results = None
    if options.with_ktru:
        try:
            ktru_results = run_ktru_characteristic_checks(
                package,
                fetch_timeout_seconds=options.ktru_timeout_seconds,
            )
            ktru_results.append(run_pp1875_checks(package))
            external_results = external_manual_checks_with_replacements(package, ktru_results)
        except Exception as error:
            warnings.append(_pipeline_warning("KTRU checks", error))

    checks_report = run_checks(
        package,
        semantic_results=semantic_results,
        stage_results=stage_results,
        penalty_results=penalty_results,
        external_results=external_results,
    )
    return WebPipelineResult(
        report_text=build_checks_report_text(checks_report),
        package_id=package.package_id,
        warnings=warnings,
        metrics=metrics,
        package=package,
        checks_report=checks_report,
    )


def _input_documents(uploaded_documents: list[dict[str, Any]]) -> list[InputDocument]:
    result = []
    for document in uploaded_documents:
        key = str(document["key"])
        path = Path(document["path"])
        result.append(
            InputDocument(
                path=path,
                type_hint=DOCUMENT_TYPE_HINTS.get(key),
                display_name=document.get("label") or document.get("name") or path.name,
            )
        )
    return result


def _split_input_documents(
    documents: list[InputDocument],
) -> tuple[list[InputDocument], list[InputDocument], list[InputDocument]]:
    docx_documents: list[InputDocument] = []
    media_commercial_offers: list[InputDocument] = []
    unsupported_documents: list[InputDocument] = []
    for document in documents:
        suffix = document.path.suffix.casefold()
        if suffix == ".docx":
            docx_documents.append(document)
        elif document.type_hint == DocumentType.COMMERCIAL_OFFER and suffix in {
            ".pdf",
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
        }:
            media_commercial_offers.append(document)
        else:
            unsupported_documents.append(document)
    return docx_documents, media_commercial_offers, unsupported_documents


async def _apply_llm_extraction(
    package,
    documents: list[InputDocument],
    *,
    concurrency: int = 6,
    vlm_repairer: VlmFallbackRepairer | None = None,
) -> tuple[list[str], dict[str, Any]]:
    classifier = DocumentClassifier()
    llm_client = StructuredLLMClient(
        semaphore=asyncio.Semaphore(max(1, concurrency)),
    )
    warnings: list[str] = []
    prepared: list[dict[str, Any]] = []

    for document in documents:
        try:
            ir = read_docx(document.path)
            decision = classifier.classify(ir, document.type_hint)
            document_tables = extract_tables(ir, decision.document_type)
            if vlm_repairer is not None:
                document_tables = vlm_repairer.repair_document_tables(
                    ir,
                    decision.document_type,
                    document_tables,
                )
            deterministic_schema = _schema_for_document_type(package, decision.document_type)
            if deterministic_schema is None:
                warnings.append(
                    f"{document.path.name}: LLM preparation skipped: deterministic document schema is unavailable."
                )
                continue
            payload = build_document_llm_payload(
                ir=ir,
                document_type=decision.document_type,
                tables=document_tables,
                deterministic_schema=deterministic_schema,
            )
        except Exception as error:
            warnings.append(
                f"{document.path.name}: LLM preparation failed: {_error_summary(error)}"
            )
            continue
        prepared.append(
            {
                "file_name": ir.file_name,
                "document_type": decision.document_type,
                "payload": payload,
                "deterministic_schema": deterministic_schema,
            }
        )

    async def run_one(item: dict[str, Any]) -> dict[str, Any]:
        llm_schema, error = await aextract_document_schema_with_llm(
            payload=item["payload"],
            document_type=item["document_type"],
            deterministic_schema=item["deterministic_schema"],
            llm_client=llm_client,
        )
        return {
            "file_name": item["file_name"],
            "document_type": item["document_type"],
            "schema": llm_schema,
            "error": error,
        }

    results = await asyncio.gather(
        *(run_one(item) for item in prepared),
        return_exceptions=True,
    )
    for index, result in enumerate(results):
        item = prepared[index]
        if isinstance(result, Exception):
            warnings.append(
                f"{item['file_name']}: LLM extraction failed: {_error_summary(result)}"
            )
            continue
        if result["error"]:
            warnings.append(f"{result['file_name']}: {result['error']}")
        try:
            apply_llm_document_result(package, result["document_type"], result["schema"])
        except Exception as error:
            warnings.append(
                f"{result['file_name']}: LLM result apply failed: {_error_summary(error)}"
            )

    return warnings, llm_client.metrics()


def _pipeline_warning(phase: str, error: Exception) -> str:
    return f"{phase} failed: {_error_summary(error)}"


def _public_vlm_table_warnings(warnings: list[str]) -> list[str]:
    benign_markers = (
        "спецификация не содержит заполненных товарных позиций",
        "спецификация распознана как пустая или шаблонная",
    )
    return [
        warning
        for warning in warnings
        if not any(marker in warning.casefold() for marker in benign_markers)
    ]


def _llm_recovery_warnings(
    phase: str,
    metrics: dict[str, Any] | None,
) -> list[str]:
    if not metrics:
        return []
    grouped: dict[str, list[str]] = {}
    attempts = metrics.get("attempts", [])
    if not isinstance(attempts, list):
        return []
    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        file_name = attempt.get("file_name")
        prefix = f"{file_name}: {phase}" if file_name else phase
        lossy_warnings = attempt.get("lossy_recovery_warnings", [])
        if not isinstance(lossy_warnings, list):
            continue
        for warning in lossy_warnings:
            warning_text = str(warning)
            if _is_evidence_only_recovery(warning_text):
                continue
            grouped.setdefault(prefix, []).append(warning_text)

    result: list[str] = []
    for prefix, raw_warnings in grouped.items():
        warnings = list(dict.fromkeys(raw_warnings))
        if len(warnings) == 1:
            result.append(
                f"{prefix}: ответ LLM восстановлен частично: "
                f"{_short_recovery_warning(warnings[0])}"
            )
            continue
        examples = ", ".join(_recovery_path(warning) for warning in warnings[:3])
        result.append(
            f"{prefix}: ответ LLM восстановлен частично; локальный fallback применён "
            f"к {len(warnings)} полям/строкам ({examples}). Подробности сохранены в метриках."
        )
    return result


def _commercial_offer_recovery_warnings(
    file_name: str,
    metrics: dict[str, Any],
) -> list[str]:
    recovery = metrics.get("structured_recovery")
    if not isinstance(recovery, dict):
        return []
    lossy_warnings = recovery.get("lossy_warnings", [])
    if not isinstance(lossy_warnings, list):
        return []
    warnings = [
        str(warning)
        for warning in lossy_warnings
        if not _is_evidence_only_recovery(str(warning))
    ]
    if not warnings:
        return []
    if len(warnings) == 1:
        return [
            f"{file_name}: VLM-разбор КП восстановлен частично: "
            f"{_short_recovery_warning(warnings[0])}"
        ]
    examples = ", ".join(_recovery_path(warning) for warning in warnings[:3])
    return [
        f"{file_name}: VLM-разбор КП восстановлен частично; локальный fallback применён "
        f"к {len(warnings)} полям/строкам ({examples}). Подробности сохранены в метриках."
    ]


def _is_evidence_only_recovery(warning: str) -> bool:
    path = warning.split(":", 1)[0]
    return path == "evidence" or path.endswith(".evidence")


def _recovery_path(warning: str) -> str:
    return warning.split(":", 1)[0] or "неизвестное поле"


def _short_recovery_warning(warning: str, limit: int = 220) -> str:
    text = warning.split("; raw=", 1)[0].strip()
    return text if len(text) <= limit else f"{text[:limit].rstrip()}..."


def _error_summary(error: Exception) -> str:
    text = " ".join(str(error).split())
    return f"{type(error).__name__}: {text}" if text else type(error).__name__


def _schema_for_document_type(package, document_type: DocumentType) -> BaseModel | None:
    if document_type == DocumentType.PLAN:
        return package.schedule_application
    if document_type == DocumentType.REQUEST:
        return package.purchase_request
    if document_type == DocumentType.ONMCK:
        return package.nmck_justification
    if document_type == DocumentType.OOZ:
        return package.purchase_description
    if document_type == DocumentType.CONTRACT:
        return package.contract_draft
    if document_type == DocumentType.EXPLANATORY_NOTE:
        return package.explanatory_note
    if document_type == DocumentType.COMMERCIAL_OFFER:
        return next(iter(package.commercial_offers), None)
    return None
