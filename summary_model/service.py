from __future__ import annotations

import asyncio
import hashlib
import time
from pathlib import Path

from pydantic import BaseModel, Field

from summary_model.analysis import ANALYZER_PROMPT_VERSIONS, run_llm_analyzers
from summary_model.classification import DocumentClassifier
from summary_model.domain.models import (
    DocumentIR,
    DocumentType,
    InputDocument,
    PipelineResult,
    SCHEMA_VERSION,
)
from summary_model.external import ProcurementRegistryAdapter
from summary_model.extraction import DocumentExtractionEngine, StructuredLLMClient
from summary_model.extraction.prompts import PROMPT_VERSIONS
from summary_model.ingestion import read_docx
from summary_model.reporting import build_report_text
from summary_model.validation import assemble_package, merge_findings, validate_package


class PipelineConfig(BaseModel):
    use_llm: bool = True
    use_external_checks: bool = True
    live_ktru: bool = True
    registry_dir: Path = Path("data/parsed_tables")
    detailed_report: bool = True
    llm_concurrency: int = Field(default=3, ge=1, le=10)
    llm_timeout_seconds: float = Field(default=180.0, gt=0)


DOCUMENT_TYPE_LABELS = {
    DocumentType.PLAN: "Заявка в план-график",
    DocumentType.REQUEST: "Обращение",
    DocumentType.COMMERCIAL_OFFER: "Коммерческое предложение",
    DocumentType.ONMCK: "Обоснование НМЦК",
    DocumentType.OOZ: "Описание объекта закупки (ООЗ)",
    DocumentType.CONTRACT: "Проект контракта",
    DocumentType.EXPLANATORY_NOTE: "Пояснительная записка",
    DocumentType.UNKNOWN: "Неизвестный документ",
}


def _package_id(documents: list[InputDocument]) -> str:
    digest = hashlib.sha256()
    for document in sorted(documents, key=lambda item: str(item.path)):
        path = Path(document.path)
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return f"package-{digest.hexdigest()[:16]}"


async def _timed(coroutine):
    started = time.perf_counter()
    result = await coroutine
    return result, round(time.perf_counter() - started, 3)


async def aprocess_package(
    documents: list[InputDocument],
    config: PipelineConfig | None = None,
) -> PipelineResult:
    settings = config or PipelineConfig()
    if not documents:
        raise ValueError("At least one input document is required.")

    total_started = time.perf_counter()
    semaphore = asyncio.Semaphore(settings.llm_concurrency)
    llm_client = (
        StructuredLLMClient(
            semaphore=semaphore,
            timeout_seconds=settings.llm_timeout_seconds,
        )
        if settings.use_llm
        else None
    )
    classifier = DocumentClassifier(llm_client=llm_client)
    extractor = DocumentExtractionEngine(llm_client=llm_client)

    parsing_started = time.perf_counter()
    irs = await asyncio.gather(
        *(asyncio.to_thread(read_docx, Path(document.path)) for document in documents)
    )
    parsing_seconds = round(time.perf_counter() - parsing_started, 3)

    async def extract_document(document: InputDocument, ir: DocumentIR):
        decision = await classifier.aclassify(ir, document.type_hint)
        summary = await extractor.aextract(
            ir,
            decision,
            document.display_name or Path(document.path).name,
        )
        return decision, summary

    extraction_started = time.perf_counter()
    extracted = await asyncio.gather(
        *(
            extract_document(document, ir)
            for document, ir in zip(documents, irs, strict=True)
        )
    )
    extraction_seconds = round(time.perf_counter() - extraction_started, 3)
    summaries = [summary for _, summary in extracted]
    document_labels = {
        summary.document_id: DOCUMENT_TYPE_LABELS[summary.detected_type]
        for summary in summaries
    }
    warnings = [
        warning
        for decision, summary in extracted
        for warning in (*decision.warnings, *summary.extraction_warnings)
    ]
    package = assemble_package(summaries)
    ir_by_document = {ir.document_id: ir for ir in irs}

    deterministic_started = time.perf_counter()
    deterministic_findings = validate_package(package)
    deterministic_seconds = round(time.perf_counter() - deterministic_started, 3)
    analysis_payload_characters: dict[str, int] = {}

    async def external_checks():
        if not settings.use_external_checks:
            return [], []
        try:
            adapter = ProcurementRegistryAdapter(
                settings.registry_dir,
                live_ktru=settings.live_ktru,
            )
            return await asyncio.to_thread(adapter.validate, package), []
        except Exception as error:
            return [], [f"External registry initialization failed: {error}"]

    (analysis_result, analysis_seconds), (
        external_result,
        external_seconds,
    ) = await asyncio.gather(
        _timed(
            run_llm_analyzers(
                package,
                ir_by_document,
                llm_client,
                payload_metrics=analysis_payload_characters,
            )
        ),
        _timed(external_checks()),
    )
    llm_findings, analysis_warnings = analysis_result
    external_findings, external_warnings = external_result
    warnings.extend(analysis_warnings)
    warnings.extend(external_warnings)

    findings = merge_findings(
        external_findings,
        deterministic_findings,
        llm_findings,
    )
    report_text = build_report_text(
        findings,
        package=package,
        detailed=settings.detailed_report,
        document_labels=document_labels,
    )
    run_metrics = {
        "total_seconds": round(time.perf_counter() - total_started, 3),
        "parsing_seconds": parsing_seconds,
        "extraction_seconds": extraction_seconds,
        "deterministic_seconds": deterministic_seconds,
        "analysis_seconds": analysis_seconds,
        "external_seconds": external_seconds,
        "analysis_payload_characters": {
            **analysis_payload_characters,
            "total": sum(analysis_payload_characters.values()),
        },
        "llm": llm_client.metrics() if llm_client else {
            "calls": 0,
            "retries": 0,
            "errors": [],
            "duration_seconds": 0.0,
        },
    }
    return PipelineResult(
        package_id=await asyncio.to_thread(_package_id, documents),
        documents=summaries,
        package=package,
        findings=findings,
        report_text=report_text,
        warnings=list(dict.fromkeys(warnings)),
        schema_version=SCHEMA_VERSION,
        prompt_versions={
            **PROMPT_VERSIONS,
            **{
                f"analysis.{name}": version
                for name, version in ANALYZER_PROMPT_VERSIONS.items()
            },
        },
        run_metrics=run_metrics,
        document_labels=document_labels,
    )


def process_package(
    documents: list[InputDocument],
    config: PipelineConfig | None = None,
) -> PipelineResult:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(aprocess_package(documents, config))
    raise RuntimeError(
        "process_package() cannot run inside an active event loop; "
        "use await aprocess_package()."
    )
