from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from summary_model.checks.models import CheckResult
from summary_model.checks.runner import _check_stages_against_plan, _result
from summary_model.extraction.llm_client import StructuredLLMClient
from summary_model.extraction_models import ProcurementPackageExtraction
from shared_modules.llm_models import OPENAI_NANO_MODEL


class StageLLMResult(BaseModel):
    status: Literal["passed", "failed", "warning", "manual_review"]
    message: str
    summary_lines: list[str] = Field(default_factory=list)


STAGE_CHECK_PROMPT = """
Ты сверяешь только этапы исполнения закупки по уже распарсенным данным.

Правила:
- Заявка в план-график является главным источником.
- Сверяй порядок этапов и сроки этапов.
- Не проверяй цены этапов здесь: цены проверяются отдельным ОНМЦК-пунктом.
- Если этапы в одном документе текстом, а в другом таблицей, сравнивай смысл и даты.
- Если данных недостаточно, ставь manual_review.
- Не выдумывай отсутствующие этапы.

Верни короткий вывод и несколько строк summary_lines вида:
"ПГ: ..."
"ООЗ: ..."
"Проект контракта: ..."
"ОНМЦК: ..."
""".strip()


def run_stage_llm_checks(
    package: ProcurementPackageExtraction,
    *,
    llm_client: StructuredLLMClient | None = None,
) -> tuple[list[CheckResult] | None, dict[str, object] | None]:
    deterministic = _check_stages_against_plan(package)
    if deterministic.status != "manual_review":
        return None, None

    client = llm_client or StructuredLLMClient(model_name=OPENAI_NANO_MODEL)
    payload = json.dumps(_stage_payload(package), ensure_ascii=False, default=str)
    result, error = client.extract(StageLLMResult, STAGE_CHECK_PROMPT, payload)
    metrics = client.metrics()
    if error or result is None:
        return None, metrics

    status = result.status
    message = f"LLM fallback по этапам: {result.message}"
    if status == "passed":
        status = "warning"
        message = (
            "Deterministic-сверка этапов требовала проверки; LLM fallback считает этапы согласованными. "
            "Проверьте вывод ниже."
        )
    return [
        _result(
            check_id="strict.plan.stages",
            title="Этапы исполнения",
            status=status,
            mode="semantic",
            message=message,
            documents=[
                "schedule_application",
                "purchase_description",
                "contract_draft",
                "nmck_justification",
            ],
            fields=[
                "schedule_application.stages",
                "purchase_description.stages",
                "contract_draft.stages",
                "nmck_justification.stages",
            ],
            details={
                "summary_lines": result.summary_lines,
                "deterministic_status": deterministic.status,
                "deterministic_message": deterministic.message,
                "deterministic_summary_lines": (deterministic.details or {}).get("summary_lines", []),
                "stage_tables": (deterministic.details or {}).get("stage_tables", []),
            },
        )
    ], metrics


def _stage_payload(package: ProcurementPackageExtraction) -> dict[str, object]:
    return {
        "schedule_application": _document_stages(package.schedule_application),
        "purchase_request": _document_stages(package.purchase_request),
        "purchase_description": _document_stages(package.purchase_description),
        "contract_draft": _document_stages(package.contract_draft),
        "nmck_justification": _document_stages(package.nmck_justification),
    }


def _document_stages(document) -> dict[str, object]:
    if document is None:
        return {"stages": []}
    return {
        "has_stages": getattr(document, "has_stages", None),
        "stages_text": getattr(document, "stages_text", None),
        "stages": [
            _stage_for_llm(stage)
            for stage in (getattr(document, "stages", []) or [])
        ],
    }


def _stage_for_llm(stage) -> dict[str, object]:
    fields = (
        "stage_number",
        "stage_name",
        "result_text",
        "start_text",
        "service_term_text",
        "service_start_date",
        "service_end_date",
        "execution_end_date",
        "quantity_text",
        "evidence",
    )
    return {
        field_name: value
        for field_name in fields
        if (value := getattr(stage, field_name, None)) is not None
    }
