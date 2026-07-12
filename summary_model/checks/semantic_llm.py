from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from summary_model.checks.models import CheckResult
from summary_model.extraction.llm_client import StructuredLLMClient
from summary_model.extraction_models import ProcurementPackageExtraction


SEMANTIC_CHECK_IDS = {
    "semantic.subject": "Предмет закупки",
    "semantic.delivery_term": "Срок поставки",
    "semantic.delivery_place": "Место поставки",
    "semantic.stages": "Этапы исполнения",
    "semantic.warranty": "Гарантии",
    "semantic.procurement_method": "Способ закупки и основание ЕП",
    "semantic.smp_preferences": "СМП/СОНКО",
}


class SemanticCheckFinding(BaseModel):
    check_id: Literal[
        "semantic.subject",
        "semantic.delivery_term",
        "semantic.delivery_place",
        "semantic.stages",
        "semantic.warranty",
        "semantic.procurement_method",
        "semantic.smp_preferences",
    ]
    status: Literal["passed", "failed", "warning", "manual_review", "skipped"]
    message: str
    compared_values: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class SemanticChecksLLMResult(BaseModel):
    findings: list[SemanticCheckFinding] = Field(default_factory=list)


SEMANTIC_CHECKS_PROMPT = """
Ты проверяешь уже извлечённые структурированные данные закупочного пакета.

Нельзя:
- вызывать внешние реестры;
- пересчитывать арифметику;
- придумывать отсутствующие значения;
- проверять ОКПД2/КТРУ, цены и характеристики товаров.

Нужно вернуть ровно эти semantic checks:
- semantic.subject: предмет закупки;
- semantic.delivery_term: срок поставки;
- semantic.delivery_place: место поставки;
- semantic.stages: этапы исполнения;
- semantic.warranty: гарантии;
- semantic.procurement_method: способ закупки и основание единственного поставщика;
- semantic.smp_preferences: СМП/СОНКО.

Оценивай только согласованность уже извлечённых полей между документами.
Если данных недостаточно, ставь manual_review и коротко назови недостающие поля.
Если формулировки отличаются только регистром, пунктуацией или небольшой
перефразировкой без изменения смысла, это passed.
Для semantic.delivery_term сравнивай только сроки поставки/оказания услуг/выполнения работ.
Не считай срок исполнения Контракта, срок действия Контракта, дату начала/окончания исполнения или общий срок
исполнения Контракта противоречием сроку поставки. Например, "поставка в течение 15 рабочих дней" и
"срок исполнения Контракта 70 календарных дней" описывают разные сущности; при совпадении сроков поставки
между ООЗ и контрактом это passed.
Для semantic.stages отдельно оценивай этапы и общий срок исполнения Контракта.
Если документы явно противоречат друг другу, это failed.
Если есть слабый риск или частично неполные данные, это warning/manual_review.

В compared_values кратко перечисляй найденные значения с конкретным названием
источника, например "Заявка в план-график: ...", "ООЗ: ...",
"Проект контракта: ...". Не используй общий префикс "Документ:".
"""


def run_semantic_llm_checks(
    package: ProcurementPackageExtraction,
    *,
    llm_client: StructuredLLMClient | None = None,
) -> tuple[list[CheckResult], dict[str, object]]:
    client = llm_client or StructuredLLMClient()
    payload = json.dumps(_semantic_payload(package), ensure_ascii=False, default=str)
    result, error = client.extract(
        SemanticChecksLLMResult,
        SEMANTIC_CHECKS_PROMPT,
        payload,
    )
    metrics = client.metrics()
    if error or result is None:
        return _fallback_manual_results(error or "Semantic LLM returned no result."), metrics

    by_id = {item.check_id: item for item in result.findings}
    checks = []
    for check_id, title in SEMANTIC_CHECK_IDS.items():
        finding = by_id.get(check_id)
        if finding is None:
            checks.append(_manual_result(check_id, title, "LLM не вернула результат по этому пункту."))
            continue
        finding = _apply_delivery_term_guard(package, finding)
        checks.append(_to_check_result(finding, title, _semantic_summary_lines(package, check_id)))
    return checks, metrics


def _apply_delivery_term_guard(
    package: ProcurementPackageExtraction,
    finding: SemanticCheckFinding,
) -> SemanticCheckFinding:
    if finding.check_id != "semantic.delivery_term" or finding.status == "passed":
        return finding
    ooz = package.purchase_description
    contract = package.contract_draft
    ooz_term = getattr(ooz, "delivery_term_text", None)
    contract_term = getattr(contract, "delivery_term_text", None)
    if _same_text(ooz_term, contract_term):
        return SemanticCheckFinding(
            check_id=finding.check_id,
            status="passed",
            message=(
                "Срок поставки согласован между ООЗ и проектом контракта. "
                "Общий срок исполнения Контракта относится к другой проверке и не считается расхождением срока поставки."
            ),
            compared_values=finding.compared_values,
            evidence=finding.evidence,
        )
    return finding


def _same_text(left: object, right: object) -> bool:
    if not left or not right:
        return False
    return _semantic_normalize(left) == _semantic_normalize(right)


def _semantic_normalize(value: object) -> str:
    return " ".join(str(value).replace("\xa0", " ").casefold().split())


def _semantic_payload(package: ProcurementPackageExtraction) -> dict[str, object]:
    schedule = package.schedule_application
    request = package.purchase_request
    ooz = package.purchase_description
    contract = package.contract_draft
    note = package.explanatory_note
    return {
        "schedule_application": {
            "purchase_subject": getattr(schedule, "purchase_subject", None),
            "delivery_term_text": getattr(schedule, "delivery_term_text", None),
            "contract_execution_term_text": getattr(schedule, "contract_execution_term_text", None),
            "has_stages": getattr(schedule, "has_stages", None),
            "stage_execution_terms": _dump(getattr(schedule, "stage_execution_terms", [])),
            "smp_preference_raw": getattr(schedule, "smp_preference_raw", None),
            "smp_preference": getattr(schedule, "smp_preference", None),
            "subcontract_smp_sonko_required_raw": getattr(schedule, "subcontract_smp_sonko_required_raw", None),
            "subcontract_smp_sonko_required": getattr(schedule, "subcontract_smp_sonko_required", None),
            "subcontract_smp_sonko_percent": getattr(schedule, "subcontract_smp_sonko_percent", None),
        },
        "purchase_request": {
            "purchase_subject": getattr(request, "purchase_subject", None),
            "procurement_method_raw": getattr(request, "procurement_method_raw", None),
            "procurement_method": getattr(request, "procurement_method", None),
            "single_supplier_basis_text": getattr(request, "single_supplier_basis_text", None),
            "delivery_term_text": getattr(request, "delivery_term_text", None),
            "stages_text": getattr(request, "stages_text", None),
            "has_stages": getattr(request, "has_stages", None),
            "stages": _dump(getattr(request, "stages", [])),
        },
        "purchase_description": {
            "purchase_subject": getattr(ooz, "purchase_subject", None),
            "delivery_place": getattr(ooz, "delivery_place", None),
            "delivery_term_text": getattr(ooz, "delivery_term_text", None),
            "warranty_requirements_text": getattr(ooz, "warranty_requirements_text", None),
        },
        "contract_draft": {
            "subject": getattr(contract, "subject", None),
            "delivery_place": getattr(contract, "delivery_place", None),
            "delivery_term_text": getattr(contract, "delivery_term_text", None),
            "contract_execution_term_text_for_stages_only": getattr(contract, "contract_execution_term_text", None),
            "warranty_text": getattr(contract, "warranty_text", None),
        },
        "explanatory_note": {
            "subject": getattr(note, "subject", None),
            "procurement_method_raw": getattr(note, "procurement_method_raw", None),
            "procurement_method": getattr(note, "procurement_method", None),
            "justification_text": getattr(note, "justification_text", None),
        },
    }


def _dump(value) -> object:
    if isinstance(value, list):
        return [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in value]
    return value


def _semantic_summary_lines(package: ProcurementPackageExtraction, check_id: str) -> list[str]:
    schedule = package.schedule_application
    request = package.purchase_request
    ooz = package.purchase_description
    contract = package.contract_draft
    note = package.explanatory_note

    values_by_check = {
        "semantic.subject": [
            ("Заявка в план-график", getattr(schedule, "purchase_subject", None)),
            ("Обращение", getattr(request, "purchase_subject", None)),
            ("ООЗ", getattr(ooz, "purchase_subject", None)),
            ("Проект контракта", getattr(contract, "subject", None)),
            ("Пояснительная записка", getattr(note, "subject", None)),
        ],
        "semantic.delivery_term": [
            ("Заявка в план-график", getattr(schedule, "delivery_term_text", None)),
            ("Обращение", getattr(request, "delivery_term_text", None)),
            ("ООЗ", getattr(ooz, "delivery_term_text", None)),
            ("Проект контракта", getattr(contract, "delivery_term_text", None)),
        ],
        "semantic.delivery_place": [
            ("ООЗ", getattr(ooz, "delivery_place", None)),
            ("Проект контракта", getattr(contract, "delivery_place", None)),
        ],
        "semantic.stages": [
            ("Заявка в план-график", _stage_text(getattr(schedule, "has_stages", None), getattr(schedule, "stage_execution_terms", []))),
            ("Обращение", getattr(request, "stages_text", None) or _stage_text(getattr(request, "has_stages", None), getattr(request, "stages", []))),
            ("Проект контракта", getattr(contract, "contract_execution_term_text", None)),
        ],
        "semantic.warranty": [
            ("ООЗ", getattr(ooz, "warranty_requirements_text", None)),
            ("Проект контракта", getattr(contract, "warranty_text", None)),
        ],
        "semantic.procurement_method": [
            ("Обращение", getattr(request, "procurement_method_raw", None) or getattr(request, "procurement_method", None)),
            ("Обращение, основание", getattr(request, "single_supplier_basis_text", None)),
            ("Пояснительная записка", getattr(note, "procurement_method_raw", None) or getattr(note, "procurement_method", None)),
            ("Пояснительная записка, обоснование", getattr(note, "justification_text", None)),
        ],
        "semantic.smp_preferences": [
            ("Заявка в план-график, преференции СМП", getattr(schedule, "smp_preference_raw", None)),
            ("Заявка в план-график, субподряд СМП/СОНКО", getattr(schedule, "subcontract_smp_sonko_required_raw", None)),
        ],
    }
    return [
        f"{label}: {value}"
        for label, value in values_by_check.get(check_id, [])
        if value not in (None, "", [], {})
    ]


def _stage_text(has_stages, stages) -> str | None:
    if stages:
        return json.dumps(_dump(stages), ensure_ascii=False, default=str)
    if has_stages is False:
        return "этапы не предусмотрены"
    if has_stages is True:
        return "этапы указаны, но структурированный список не извлечён"
    return None


def _to_check_result(finding: SemanticCheckFinding, title: str, summary_lines: list[str]) -> CheckResult:
    severity = {
        "passed": "info",
        "failed": "error",
        "warning": "warning",
        "manual_review": "manual_review",
        "skipped": "info",
    }[finding.status]
    return CheckResult(
        check_id=finding.check_id,
        title=title,
        severity=severity,  # type: ignore[arg-type]
        status=finding.status,
        mode="semantic",
        fields_compared=[finding.check_id],
        message=finding.message,
        report_text=finding.message,
        evidence=finding.evidence,
        details={"summary_lines": summary_lines or finding.compared_values},
    )


def _fallback_manual_results(error: str) -> list[CheckResult]:
    return [
        _manual_result(check_id, title, f"Semantic LLM check не выполнен: {error}")
        for check_id, title in SEMANTIC_CHECK_IDS.items()
    ]


def _manual_result(check_id: str, title: str, message: str) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        title=title,
        severity="manual_review",
        status="manual_review",
        mode="semantic",
        fields_compared=[check_id],
        message=message,
        report_text=message,
    )
