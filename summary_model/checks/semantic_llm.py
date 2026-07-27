from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from summary_model.checks.models import CheckResult
from summary_model.extraction.llm_client import StructuredLLMClient
from summary_model.extraction_models import ProcurementPackageExtraction
from shared_modules.llm_models import OPENAI_NANO_MODEL


SEMANTIC_CHECK_IDS = {
    "semantic.subject": "Предмет закупки",
    "semantic.delivery_term": "Срок поставки",
    "semantic.delivery_place": "Место поставки",
    "semantic.warranty": "Гарантии",
    "semantic.procurement_method": "Способ закупки и основание ЕП",
    "semantic.smp_preferences": "Преференции СМП/СОНКО",
}


class SemanticCheckFinding(BaseModel):
    check_id: Literal[
        "semantic.subject",
        "semantic.delivery_term",
        "semantic.delivery_place",
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
Если документы явно противоречат друг другу, это failed.
Если есть слабый риск или частично неполные данные, это warning/manual_review.

В compared_values кратко перечисляй найденные значения с конкретным названием
источника, например "Заявка в план-график: ...", "ООЗ: ...",
"Проект контракта: ...". Не используй общий префикс "Документ:".

ДЛЯ semantic.warranty: Будь предельно лаконичен! Запрещено цитировать длинные многостраничные технические описания. Выделяй только ключевые цифры и обязательно сохраняй точные ссылки на пункты/разделы/таблицы документа (например: 'ООЗ (п. 1.5, Таб. 1): 12 мес. на ПНР, 36 мес. на серверы'). Не пиши стены текста!
"""


def _apply_warranty_guard(
    finding: SemanticCheckFinding,
) -> SemanticCheckFinding:
    if finding.check_id != "semantic.warranty":
        return finding

    cleaned_values = []
    for val in finding.compared_values:
        s_val = str(val).strip()
        if len(s_val) > 200:
            if ":" in s_val[:60]:
                prefix, rest = s_val.split(":", 1)
                s_val = f"{prefix.strip()}: {rest.strip()[:180]}..."
            else:
                s_val = f"{s_val[:200]}..."
        cleaned_values.append(s_val)

    return SemanticCheckFinding(
        check_id=finding.check_id,
        status=finding.status,
        message=finding.message,
        compared_values=cleaned_values,
        evidence=finding.evidence,
    )


def run_semantic_llm_checks(
    package: ProcurementPackageExtraction,
    *,
    llm_client: StructuredLLMClient | None = None,
) -> tuple[list[CheckResult], dict[str, object]]:
    client = llm_client or StructuredLLMClient(model_name=OPENAI_NANO_MODEL)
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
        finding = _apply_procurement_method_guard(package, finding)
        finding = _apply_smp_preference_guard(package, finding)
        finding = _apply_warranty_guard(finding)
        checks.append(_to_check_result(finding, title, _semantic_summary_lines(package, check_id)))
    return checks, metrics


def _apply_procurement_method_guard(
    package: ProcurementPackageExtraction,
    finding: SemanticCheckFinding,
) -> SemanticCheckFinding:
    if finding.check_id != "semantic.procurement_method":
        return finding

    cleaned_values = []
    for val in finding.compared_values:
        v = str(val)
        v = v.replace("auction", "Электронный аукцион").replace("tender", "Конкурс").replace("request_for_quotations", "Запрос котировок")
        cleaned_values.append(v)

    method_raw = ""
    if package.schedule_application and package.schedule_application.procurement_method_raw:
        method_raw = package.schedule_application.procurement_method_raw.casefold()

    is_auction = "аукцион" in method_raw or "auction" in method_raw or any("аукцион" in v.casefold() for v in cleaned_values)

    if is_auction:
        return SemanticCheckFinding(
            check_id=finding.check_id,
            status="passed",
            message="Способ закупки: Электронный аукцион (конкурентная закупка, обоснование ЕП не требуется).",
            compared_values=cleaned_values,
            evidence=finding.evidence,
        )

    return SemanticCheckFinding(
        check_id=finding.check_id,
        status=finding.status,
        message=finding.message.replace("auction", "Электронный аукцион"),
        compared_values=cleaned_values,
        evidence=finding.evidence,
    )


def _apply_smp_preference_guard(
    package: ProcurementPackageExtraction,
    finding: SemanticCheckFinding,
) -> SemanticCheckFinding:
    """Do not confuse participant preferences with subcontracting duties."""
    if finding.check_id != "semantic.smp_preferences":
        return finding

    schedule = package.schedule_application
    raw = getattr(schedule, "smp_preference_raw", None) if schedule else None
    value = getattr(schedule, "smp_preference", None) if schedule else None
    subcontract_required = (
        getattr(schedule, "subcontract_smp_sonko_required", None) if schedule else None
    )
    subcontract_percent = (
        getattr(schedule, "subcontract_smp_sonko_percent", None) if schedule else None
    )
    compared_values = [f"Заявка в план-график: {raw}"] if raw else []
    if value is False and subcontract_required is True:
        status = "warning"
        message = (
            "Преференции СМП/СОНКО в заявке не установлены, при этом отдельно установлена "
            "обязанность привлечения соисполнителей СМП/СОНКО"
            f"{f' в объёме {subcontract_percent}%' if subcontract_percent is not None else ''}. "
            "Это разные условия; процент и наличие обязанности сверяются отдельной проверкой."
        )
    elif value is False:
        status = "passed"
        message = "Преференции СМП/СОНКО в заявке не установлены."
    elif value is True:
        status = "passed"
        message = "Преференции СМП/СОНКО в заявке установлены."
    else:
        status = "warning"
        message = "Значение преференций СМП/СОНКО в заявке не удалось определить однозначно."
    return SemanticCheckFinding(
        check_id=finding.check_id,
        status=status,
        message=message,
        compared_values=compared_values,
        evidence=finding.evidence,
    )


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
    embedded_contract_subject = (
        getattr(contract.embedded_purchase_description, "purchase_subject", None)
        if contract and contract.embedded_purchase_description
        else None
    )
    note = package.explanatory_note
    return {
        "schedule_application": {
            "purchase_subject": getattr(schedule, "purchase_subject", None),
            "delivery_place": getattr(schedule, "delivery_place", None),
            "delivery_term_text": getattr(schedule, "delivery_term_text", None),
            "contract_execution_term_text": getattr(schedule, "contract_execution_term_text", None),
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
        },
        "purchase_description": {
            "purchase_subject": getattr(ooz, "purchase_subject", None),
            "delivery_place": getattr(ooz, "delivery_place", None),
            "delivery_term_text": getattr(ooz, "delivery_term_text", None),
            "warranty_requirements_text": getattr(ooz, "warranty_requirements_text", None),
        },
        "contract_draft": {
            "subject": embedded_contract_subject or getattr(contract, "subject", None),
            "legal_subject": getattr(contract, "subject", None),
            "delivery_place": getattr(contract, "delivery_place", None),
            "delivery_term_text": getattr(contract, "delivery_term_text", None),
            "warranty_text": getattr(contract, "warranty_text", None),
            "subcontract_smp_sonko_required_raw": getattr(contract, "subcontract_smp_sonko_required_raw", None),
            "subcontract_smp_sonko_required": getattr(contract, "subcontract_smp_sonko_required", None),
            "subcontract_smp_sonko_percent": getattr(contract, "subcontract_smp_sonko_percent", None),
        },
        "explanatory_note": {
            "subject": getattr(note, "subject", None),
            "procurement_method_raw": getattr(note, "procurement_method_raw", None),
            "procurement_method": getattr(note, "procurement_method", None),
            "justification_text": getattr(note, "justification_text", None),
        },
    }


def _semantic_summary_lines(package: ProcurementPackageExtraction, check_id: str) -> list[str]:
    schedule = package.schedule_application
    request = package.purchase_request
    ooz = package.purchase_description
    contract = package.contract_draft
    embedded_contract_subject = (
        getattr(contract.embedded_purchase_description, "purchase_subject", None)
        if contract and contract.embedded_purchase_description
        else None
    )
    note = package.explanatory_note

    values_by_check = {
        "semantic.subject": [
            ("Заявка в план-график", getattr(schedule, "purchase_subject", None)),
            ("Обращение", getattr(request, "purchase_subject", None)),
            ("ООЗ", getattr(ooz, "purchase_subject", None)),
            ("Проект контракта", embedded_contract_subject or getattr(contract, "subject", None)),
            ("Пояснительная записка", getattr(note, "subject", None)),
        ],
        "semantic.delivery_term": [
            ("Заявка в план-график", getattr(schedule, "delivery_term_text", None)),
            ("Обращение", getattr(request, "delivery_term_text", None)),
            ("ООЗ", getattr(ooz, "delivery_term_text", None)),
            ("Проект контракта", getattr(contract, "delivery_term_text", None)),
        ],
        "semantic.delivery_place": [
            ("Заявка в план-график", getattr(schedule, "delivery_place", None)),
            ("ООЗ", getattr(ooz, "delivery_place", None)),
            ("Проект контракта", getattr(contract, "delivery_place", None)),
        ],
        "semantic.warranty": [
            ("ООЗ", getattr(ooz, "warranty_requirements_text", None)),
            ("Проект контракта", getattr(contract, "warranty_text", None)),
        ],
        "semantic.procurement_method": [
            ("Заявка в план-график", getattr(schedule, "procurement_method_raw", None) or getattr(schedule, "procurement_method", None)),
            ("Заявка в план-график, основание", getattr(schedule, "single_supplier_basis_text", None)),
            ("Обращение", getattr(request, "procurement_method_raw", None) or getattr(request, "procurement_method", None)),
            ("Обращение, основание", getattr(request, "single_supplier_basis_text", None)),
            ("Пояснительная записка", getattr(note, "procurement_method_raw", None) or getattr(note, "procurement_method", None)),
            ("Пояснительная записка, обоснование", getattr(note, "justification_text", None)),
        ],
        "semantic.smp_preferences": [
            ("Заявка в план-график", getattr(schedule, "smp_preference_raw", None)),
        ],
    }
    return [
        f"{label}: {value}"
        for label, value in values_by_check.get(check_id, [])
        if value not in (None, "", [], {})
    ]


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
