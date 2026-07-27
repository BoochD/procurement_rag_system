from __future__ import annotations

import json
from copy import deepcopy

from pydantic import BaseModel, Field

from summary_model.checks.models import CheckResult
from summary_model.checks.runner import (
    _check_contract_penalties,
    _fixed_penalty_amount,
    _format_money,
    _has_penalty_words,
    _money_amount,
    _plan_requires_smp_sonko_subcontract,
    _supplier_value_penalty_percent,
)
from summary_model.extraction.llm_client import StructuredLLMClient
from summary_model.extraction_models import PenaltyClause, ProcurementPackageExtraction


class ContractPenaltyLLMResult(BaseModel):
    penalty_clauses: list[PenaltyClause] = Field(default_factory=list)
    peni_clauses: list[PenaltyClause] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


PENALTY_CHECK_PROMPT = """
Ты извлекаешь штрафы и пени только из главы "Ответственность Сторон" проекта контракта.

Нельзя:
- использовать текст вне переданной главы;
- придумывать штрафы, если их нет в тексте;
- менять НМЦК;
- делать внешние проверки.
- сокращать пункты многоточием или возвращать заглушки;
- объединять разные пункты, стороны или виды обязательств в одну запись;
- копировать ожидаемую сумму из payload, если соответствующий порог отсутствует в тексте.

Верни penalty_clauses и peni_clauses.

Классификация:
- party=supplier, obligation_kind=value_obligation: штраф поставщика/исполнителя за неисполнение или ненадлежащее исполнение обязательств, кроме просрочки, если штраф задан процентом от цены контракта/этапа;
- party=supplier, obligation_kind=non_value_obligation: штраф поставщика/исполнителя за обязательства без стоимостного выражения, обычно фиксированная сумма 1000/5000/10000... рублей;
- party=customer: штраф заказчика, обычно фиксированная сумма;
- obligation_kind=delay_peni: пеня/пени за просрочку, формула 1/300 или ключевой ставки;
- obligation_kind=smp_sonko_subcontract: штраф за непривлечение СМП/СОНКО, обычно 5 процентов.

Для каждого пункта:
- raw_text: точная короткая формулировка из главы;
- percent: число процентов, если явно указано;
- amount: применимая к переданной НМЦК сумма рублей, если соответствующая ветка явно указана;
- evidence: номер пункта, если есть.

Если глава есть, но нужный штраф не найден, не выдумывай его.

Примеры поведения:
- При НМЦК 106 312 006 рублей и пункте 7.4 с диапазонами 1000 / 5000 /
  10000 / 100000 рублей верни amount=100000, evidence="п. 7.4" и точный текст
  ветки "100000 рублей, если цена Контракта превышает 100 млн. рублей".
- Если штраф заказчика и штраф исполнителя за нестоимостное обязательство равны
  100000 рублей, верни две отдельные penalty_clauses с разными party и
  obligation_kind.
- Формулу "1/300 действующей ключевой ставки" верни отдельной записью в
  peni_clauses с obligation_kind=delay_peni.
- Штраф 5 процентов за непривлечение СМП/СОНКО верни отдельной записью с
  obligation_kind=smp_sonko_subcontract.
- Если expected содержит сумму, которой нет в переданной главе, не создавай
  запись только ради совпадения с expected.
""".strip()


def run_penalty_llm_checks(
    package: ProcurementPackageExtraction,
    *,
    llm_client: StructuredLLMClient | None = None,
) -> tuple[list[CheckResult] | None, dict[str, object] | None]:
    contract = package.contract_draft
    section = getattr(contract, "responsibility_section_text", None) if contract else None
    if not section or not _has_penalty_words(section):
        return _check_contract_penalties(package), {
            "calls": 0,
            "skipped_reason": (
                "responsibility_section_not_found"
                if not section
                else "penalty_terms_not_found_in_section"
            ),
        }

    client = llm_client or StructuredLLMClient()
    payload = json.dumps(_penalty_payload(package), ensure_ascii=False, default=str)
    result, error = client.extract(ContractPenaltyLLMResult, PENALTY_CHECK_PROMPT, payload)
    metrics = client.metrics()
    if error or result is None:
        fallback = _check_contract_penalties(package)
        for check in fallback:
            check.details["penalty_llm_error"] = error or "LLM вернула пустой результат."
            check.message = (
                "Специализированная LLM-проверка штрафов не выполнена; "
                "требуется ручная проверка раздела ответственности."
            )
            check.status = "manual_review"
            check.severity = "manual_review"
            check.report_text = check.message
        return fallback, metrics

    repaired_package = deepcopy(package)
    if repaired_package.contract_draft is not None:
        repaired_package.contract_draft.penalty_clauses = result.penalty_clauses
        repaired_package.contract_draft.peni_clauses = result.peni_clauses
        for warning in result.warnings:
            if warning not in repaired_package.contract_draft.parser_warnings:
                repaired_package.contract_draft.parser_warnings.append(warning)
    checks = _check_contract_penalties(repaired_package)
    extracted = {
        "penalty_clauses": [item.model_dump(mode="json") for item in result.penalty_clauses],
        "peni_clauses": [item.model_dump(mode="json") for item in result.peni_clauses],
        "warnings": list(result.warnings),
    }
    for check in checks:
        check.details["penalty_llm_extraction"] = extracted
    return checks, metrics


def _penalty_payload(package: ProcurementPackageExtraction) -> dict[str, object]:
    contract = package.contract_draft
    contract_price = _money_amount(getattr(contract, "price", None) if contract else None)
    if contract_price is None:
        contract_price = _money_amount(package.schedule_application.nmck if package.schedule_application else None)
    return {
        "nmck": _format_money(contract_price),
        "has_stages": bool(
            getattr(package.schedule_application, "stages", None)
            or getattr(contract, "stages", None)
        ),
        "expected": {
            "supplier_value_obligation_percent": _optional_decimal_text(
                _supplier_value_penalty_percent(contract_price)
            ),
            "fixed_fine_amount": _format_money(_fixed_penalty_amount(contract_price)),
            "smp_sonko_subcontract_percent": "5"
            if _plan_requires_smp_sonko_subcontract(package)
            else None,
        },
        "schedule_smp_sonko_required": getattr(
            package.schedule_application,
            "subcontract_smp_sonko_required",
            None,
        )
        if package.schedule_application
        else None,
        "responsibility_section_text": getattr(contract, "responsibility_section_text", None)
        if contract
        else None,
    }


def _optional_decimal_text(value) -> str | None:
    return str(value) if value is not None else None
