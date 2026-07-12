from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from summary_model.checks import run_checks
from summary_model.checks.ktru_adapter import run_ktru_characteristic_checks, run_pp1875_checks
from summary_model.checks.report import build_checks_report_text
from summary_model.checks.runner import external_manual_checks_with_replacements
from summary_model.checks.semantic_llm import run_semantic_llm_checks
from summary_model.classification import DocumentClassifier
from summary_model.domain.models import DocumentType, InputDocument
from summary_model.extraction.llm_client import StructuredLLMClient
from summary_model.extraction.llm_document_extractor import (
    aextract_document_schema_with_llm,
    apply_llm_document_result,
)
from summary_model.extraction.llm_payloads import build_document_llm_payload
from summary_model.extraction_pipeline import extract_package
from summary_model.ingestion import read_docx
from summary_model.tables import extract_tables


DOCUMENT_TYPE_HINTS = {
    "plan": DocumentType.PLAN,
    "contract": DocumentType.CONTRACT,
    "ooz": DocumentType.OOZ,
    "zapiska": DocumentType.EXPLANATORY_NOTE,
    "onmck": DocumentType.ONMCK,
    "obrasheniye": DocumentType.REQUEST,
}


@dataclass
class WebPipelineOptions:
    with_llm_extraction: bool = True
    with_semantic_llm: bool = True
    with_ktru: bool = True
    ktru_timeout_seconds: int = 30
    llm_concurrency: int = 6


@dataclass
class WebPipelineResult:
    report_text: str
    package_id: str | None
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


def process_uploaded_documents(
    uploaded_documents: list[dict[str, Any]],
    *,
    options: WebPipelineOptions | None = None,
) -> WebPipelineResult:
    return asyncio.run(_aprocess_uploaded_documents(uploaded_documents, options=options))


async def _aprocess_uploaded_documents(
    uploaded_documents: list[dict[str, Any]],
    *,
    options: WebPipelineOptions | None = None,
) -> WebPipelineResult:
    options = options or WebPipelineOptions()
    input_documents = _input_documents(uploaded_documents)
    package = extract_package(input_documents)
    warnings: list[str] = []
    metrics: dict[str, Any] = {}

    if options.with_llm_extraction:
        llm_warnings, llm_metrics = await _apply_llm_extraction(
            package,
            input_documents,
            concurrency=options.llm_concurrency,
        )
        warnings.extend(llm_warnings)
        metrics["document_llm"] = llm_metrics

    semantic_results = None
    if options.with_semantic_llm:
        semantic_results, semantic_metrics = run_semantic_llm_checks(package)
        metrics["semantic_llm"] = semantic_metrics

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
            warnings.append(f"KTRU checks failed: {error}")

    checks_report = run_checks(
        package,
        semantic_results=semantic_results,
        external_results=external_results,
    )
    return WebPipelineResult(
        report_text=build_checks_report_text(checks_report),
        package_id=package.package_id,
        warnings=warnings,
        metrics=metrics,
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


async def _apply_llm_extraction(
    package,
    documents: list[InputDocument],
    *,
    concurrency: int = 6,
) -> tuple[list[str], dict[str, Any]]:
    classifier = DocumentClassifier()
    llm_client = StructuredLLMClient(
        semaphore=asyncio.Semaphore(max(1, concurrency)),
    )
    warnings: list[str] = []
    prepared: list[dict[str, Any]] = []

    for document in documents:
        ir = read_docx(document.path)
        decision = classifier.classify(ir, document.type_hint)
        document_tables = extract_tables(ir, decision.document_type)
        deterministic_schema = _schema_for_document_type(package, decision.document_type)
        payload = build_document_llm_payload(
            ir=ir,
            document_type=decision.document_type,
            tables=document_tables,
            deterministic_schema=deterministic_schema,
        )
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
            warnings.append(f"{item['file_name']}: {result}")
            continue
        if result["error"]:
            warnings.append(f"{result['file_name']}: {result['error']}")
        apply_llm_document_result(package, result["document_type"], result["schema"])

    return warnings, llm_client.metrics()


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
