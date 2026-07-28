from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from summary_model.checks.models import CheckResult
from summary_model.checks.runner import (
    _fixed_penalty_amount,
    _format_money,
    _has_penalty_words,
    _money_amount,
    _plan_requires_smp_sonko_subcontract,
    _supplier_value_penalty_percent,
)
from summary_model.extraction.llm_client import StructuredLLMClient
from summary_model.extraction_models import ProcurementPackageExtraction


class PenaltyCheckFinding(BaseModel):
    label: str
    status: Literal["passed", "failed", "warning", "manual_review"]
    message: str
    evidence: str | None = None
    quote: str | None = None


class ContractPenaltyLLMResult(BaseModel):
    status: Literal["passed", "failed", "warning", "manual_review"]
    message: str
    findings: list[PenaltyCheckFinding] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


PENALTY_CHECK_PROMPT = """
Ты выполняешь готовую проверку штрафов и пеней только по переданной главе
"Ответственность Сторон" проекта контракта.

Нельзя:
- использовать текст вне переданной главы;
- придумывать отсутствующие пункты, суммы или проценты;
- менять НМЦК и рассчитанные ожидания из payload;
- сокращать пункты многоточием или возвращать заглушки;
- объединять разные пункты, стороны или виды обязательств в одну запись.

Сверь текст главы с expected из payload. Верни общий status и короткий message.
Все текстовые поля ответа заполняй только на русском языке.
В findings верни отдельную запись для каждого найденного или отсутствующего вида:
- штраф заказчика;
- штраф поставщика за стоимостное обязательство;
- штраф поставщика за нестоимостное обязательство;
- пеня за просрочку;
- штраф за непривлечение СМП/СОНКО, только если он ожидается.

Поле label должно дословно содержать одно из русских названий выше. Не возвращай
английские идентификаторы вида supplier_value_obligation_percent.

У каждой findings-записи обязательно укажи label, status, message, evidence (номер
пункта) и quote (короткая точная цитата применимой ветки). Если нужной ветки нет,
оставь evidence и quote пустыми, но не придумывай их.

Примеры:
- НМЦК 106 312 006 рублей; в п. 7.4 есть ветки 1000 / 5000 / 10000 /
  100000 рублей. Для штрафа заказчика верни passed, evidence="п. 7.4",
  quote="100000 рублей, если цена Контракта превышает 100 млн. рублей".
- Одинаковые 100000 рублей для штрафа заказчика и для нестоимостного обязательства
  поставщика являются двумя отдельными findings.
- "1/300 действующей ключевой ставки" является отдельной finding для пени.
- Если expected содержит штраф за непривлечение СМП/СОНКО 5%, а в пункте написано
  "штраф в размере 5% объема привлечения", верни passed. Не сравнивай эти 5% с
  отдельным условием о доле привлечения соисполнителей, например 90%.
- Если expected содержит значение, которого нет в тексте, отметь соответствующую
  finding как manual_review или failed, но не копируй ожидаемое значение как цитату.
""".strip()


def run_penalty_llm_checks(
    package: ProcurementPackageExtraction,
    *,
    llm_client: StructuredLLMClient | None = None,
) -> tuple[list[CheckResult], dict[str, object]]:
    contract = package.contract_draft
    section = getattr(contract, "responsibility_section_text", None) if contract else None
    if not section or not _has_penalty_words(section):
        reason = (
            "Раздел ответственности не найден."
            if not section
            else "В найденном разделе ответственности не найдены слова «штраф», «пеня» или «неустойка»."
        )
        return [_manual_result(reason)], {
            "calls": 0,
            "skipped_reason": (
                "responsibility_section_not_found"
                if not section
                else "penalty_terms_not_found_in_section"
            ),
        }

    client = llm_client or StructuredLLMClient()
    payload_data = _penalty_payload(package)
    result, error = _extract_check(
        client,
        ContractPenaltyLLMResult,
        PENALTY_CHECK_PROMPT,
        json.dumps(payload_data, ensure_ascii=False, default=str),
    )
    metrics = client.metrics()
    if error or result is None:
        check = _manual_result(
            "Специализированная LLM-проверка штрафов не выполнена; требуется ручная проверка раздела ответственности."
        )
        check.details["penalty_llm_error"] = error or "LLM вернула пустой результат."
        return [check], metrics

    check = _to_check_result(result, payload_data)
    return [check], metrics


def _extract_check(client, schema, prompt: str, payload: str):
    """Production uses direct compact validation; simple test doubles stay usable."""
    direct = getattr(client, "extract_check", None)
    if callable(direct):
        return direct(schema, prompt, payload)
    return client.extract(schema, prompt, payload)


def _to_check_result(result: ContractPenaltyLLMResult, payload: dict[str, object]) -> CheckResult:
    findings = result.findings
    status = _aggregate_status(result, findings)
    expected_labels = {
        "Штраф заказчика",
        "Штраф поставщика за стоимостное обязательство",
        "Штраф поставщика за нестоимостное обязательство",
        "Пеня за просрочку",
    }
    expected = payload.get("expected")
    if isinstance(expected, dict) and expected.get("smp_sonko_fine_percent"):
        expected_labels.add("Штраф за непривлечение СМП/СОНКО")
    passed_labels = {
        _finding_label(finding.label)
        for finding in findings
        if finding.status == "passed"
    }
    if findings and expected_labels <= passed_labels and all(
        finding.status == "passed" for finding in findings
    ):
        status = "passed"
    severity = {
        "passed": "info",
        "failed": "error",
        "warning": "warning",
        "manual_review": "manual_review",
    }[status]
    lines = _expected_lines(payload)
    for finding in findings:
        status_label = {
            "passed": "ОК",
            "failed": "ОШИБКА",
            "warning": "ПРЕДУПРЕЖДЕНИЕ",
            "manual_review": "ТРЕБУЕТ ПРОВЕРКИ",
        }[finding.status]
        source = f" {finding.evidence}." if finding.evidence else ""
        quote = f" «{_short_text(finding.quote)}" + "»" if finding.quote else ""
        lines.append(
            f"{_finding_label(finding.label)} — {status_label}.{source} "
            f"{_finding_message(finding.status)}{quote}".strip()
        )
    report_message = _result_message(status)
    return CheckResult(
        check_id="strict.contract.penalties",
        title="Штрафы и пени",
        severity=severity,  # type: ignore[arg-type]
        status=status,
        mode="semantic",
        documents=["schedule_application", "contract_draft"],
        fields_compared=[
            "schedule_application.nmck",
            "contract_draft.responsibility_section_text",
        ],
        message=report_message,
        report_text=report_message,
        evidence=[finding.evidence for finding in result.findings if finding.evidence],
        details={
            "summary_lines": lines,
            "penalty_llm_check": result.model_dump(mode="json"),
            "responsibility_section_present": True,
        },
    )


def _manual_result(message: str) -> CheckResult:
    return CheckResult(
        check_id="strict.contract.penalties",
        title="Штрафы и пени",
        severity="manual_review",
        status="manual_review",
        mode="manual_review",
        documents=["schedule_application", "contract_draft"],
        fields_compared=[
            "schedule_application.nmck",
            "contract_draft.responsibility_section_text",
        ],
        message=message,
        report_text=message,
    )


def _expected_lines(payload: dict[str, object]) -> list[str]:
    expected = payload.get("expected")
    expected = expected if isinstance(expected, dict) else {}
    lines = [f"НМЦК для проверки: {payload.get('nmck') or 'не найдена'}."]
    if expected.get("fixed_fine_amount"):
        lines.append(f"Ожидаемый фиксированный штраф: {expected['fixed_fine_amount']} руб.")
    if expected.get("supplier_value_obligation_percent"):
        lines.append(
            "Ожидаемый штраф поставщика за стоимостное обязательство: "
            f"{expected['supplier_value_obligation_percent']}%."
        )
    if expected.get("smp_sonko_fine_percent"):
        lines.append(
            "Ожидаемый штраф за непривлечение СМП/СОНКО: "
            f"{expected['smp_sonko_fine_percent']}%."
        )
    return lines


def _short_text(value: str | None, limit: int = 260) -> str:
    text = " ".join((value or "").split())
    return text if len(text) <= limit else f"{text[:limit - 3].rstrip()}..."


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
            "smp_sonko_fine_percent": "5"
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


def _aggregate_status(
    result: ContractPenaltyLLMResult,
    findings: list[PenaltyCheckFinding] | None = None,
) -> str:
    statuses = [result.status, *(finding.status for finding in findings or result.findings)]
    return max(
        statuses,
        key={"passed": 0, "warning": 1, "manual_review": 2, "failed": 3}.get,
    )


def _result_message(status: str) -> str:
    return {
        "passed": "Условия о штрафах и пенях соответствуют проверяемым нормативным значениям.",
        "warning": "В условиях о штрафах и пенях найдены сведения, требующие внимания.",
        "manual_review": "Часть условий о штрафах и пенях требует ручной проверки.",
        "failed": "В условиях о штрафах и пенях найдены подтверждённые расхождения.",
    }[status]


def _finding_message(status: str) -> str:
    return {
        "passed": "Условие найдено и соответствует ожидаемому значению.",
        "warning": "Условие найдено, но требует дополнительного внимания.",
        "manual_review": "Условие не подтверждено однозначно; требуется ручная проверка.",
        "failed": "Условие не соответствует ожидаемому значению.",
    }[status]


def _finding_label(value: str) -> str:
    normalized = str(value or "").casefold().replace("ё", "е")
    if "smp" in normalized or "sonko" in normalized or "смп" in normalized or "сонко" in normalized:
        return "Штраф за непривлечение СМП/СОНКО"
    if "non_value" in normalized or "нестоимост" in normalized:
        return "Штраф поставщика за нестоимостное обязательство"
    if "customer" in normalized or "заказчик" in normalized:
        return "Штраф заказчика"
    if "delay" in normalized or "просроч" in normalized or "пен" in normalized:
        return "Пеня за просрочку"
    if "supplier_value" in normalized or "стоимост" in normalized:
        return "Штраф поставщика за стоимостное обязательство"
    return "Условие о штрафе или пене"
