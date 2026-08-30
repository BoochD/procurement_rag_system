from __future__ import annotations

import json
import re
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
Для semantic.subject сравнивай не только общую близость фраз, но и охват
самостоятельных групп товаров, работ или услуг. Если в одном документе названа
такая группа, а в другом она отсутствует, ставь warning и назови её. Например,
«инструменты, инвентарь и расходные материалы» и «инструменты и расходные
материалы» не являются полностью согласованными формулировками. Passed допустим
только при одинаковом смысловом охвате предмета.
Для semantic.delivery_place адрес сравнивай точно. Разные номера дома, корпуса,
строения или помещения являются подтверждённым расхождением и дают failed;
их нельзя считать отличием форматирования. Например, «д. 001» и «д. 14» — failed.
Не выводи внутренние рассуждения, правила, английские фразы или вопросительные
комментарии. Различия только в пунктуации, сокращениях, «г.о. город», «г.» и
«город», а также повтор одного адреса в ООЗ не являются расхождением.
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

ДЛЯ semantic.warranty:
- сравнивай фактические гарантийные сроки и условия из ООЗ и из секции ООЗ,
  встроенной в проект контракта;
- ссылка вида «требования указаны в ООЗ/Приложении» сама по себе НЕ подтверждает
  совпадение: если фактические условия проекта контракта не извлечены, ставь manual_review;
- passed допустим только когда в обоих документах найдены и согласованы сами сроки/условия;
- если фактические числовые сроки или иные числовые условия различаются, ставь warning,
  даже когда условие контракта строже; это различие надо показать пользователю;
- пример: основной текст контракта ссылается на Приложение № 1, а в переданной
  warranty_section_text этого приложения указаны «12 месяцев на ПНР» и
  «36 месяцев на серверы» — сравни именно эти значения и приведи ссылку на секцию;
- будь лаконичен: перечисляй ключевые цифры и точные ссылки на пункты/разделы/таблицы,
  не цитируй секцию целиком и не пиши стены текста.
"""


def _apply_warranty_guard(
    package: ProcurementPackageExtraction,
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

    status = finding.status
    message = finding.message
    contract = package.contract_draft
    embedded = getattr(contract, "embedded_purchase_description", None) if contract else None
    embedded_value = getattr(embedded, "warranty_requirements_text", None)
    contract_value = getattr(contract, "warranty_text", None)
    if status == "passed" and not embedded_value and (
        not contract_value or _warranty_reference_only(contract_value)
    ):
        status = "manual_review"
        message = (
            "В проекте контракта найдена только ссылка на ООЗ; "
            "фактические гарантийные сроки в приложении не извлечены."
        )
    elif status == "passed" and _warranty_numeric_terms(embedded_value or contract_value) != _warranty_numeric_terms(
        getattr(package.purchase_description, "warranty_requirements_text", None)
    ):
        status = "warning"
        message = (
            "Гарантийные сроки или иные числовые условия различаются; "
            "требуется сверка формулировок ООЗ и проекта контракта."
        )

    return SemanticCheckFinding(
        check_id=finding.check_id,
        status=status,
        message=message,
        compared_values=cleaned_values,
        evidence=finding.evidence,
    )


def run_semantic_llm_checks(
    package: ProcurementPackageExtraction,
    *,
    llm_client: StructuredLLMClient | None = None,
) -> tuple[list[CheckResult], dict[str, object]]:
    client = llm_client or StructuredLLMClient(model_name=OPENAI_NANO_MODEL)
    active_checks = dict(SEMANTIC_CHECK_IDS)
    if _plan_has_stages(package):
        active_checks.pop("semantic.delivery_term", None)
    prompt = SEMANTIC_CHECKS_PROMPT
    if "semantic.delivery_term" not in active_checks:
        prompt += "\nВ ПГ есть этапы: semantic.delivery_term не возвращай, сроки проверяет отдельная проверка этапов."
    payload = json.dumps(_semantic_payload(package), ensure_ascii=False, default=str)
    result, error = _extract_check(
        client,
        SemanticChecksLLMResult,
        prompt,
        payload,
    )
    metrics = client.metrics()
    if error or result is None:
        return _fallback_manual_results(error or "Semantic LLM returned no result."), metrics

    by_id = {item.check_id: item for item in result.findings}
    checks = []
    for check_id, title in active_checks.items():
        finding = by_id.get(check_id)
        if finding is None:
            checks.append(_manual_result(check_id, title, "LLM не вернула результат по этому пункту."))
            continue
        finding = _apply_delivery_term_guard(package, finding)
        finding = _apply_delivery_place_guard(package, finding)
        finding = _apply_procurement_method_guard(package, finding)
        finding = _apply_smp_preference_guard(package, finding)
        finding = _apply_subject_guard(package, finding)
        finding = _apply_warranty_guard(package, finding)
        checks.append(_to_check_result(finding, title, _semantic_summary_lines(package, check_id)))
    return checks, metrics


def _apply_delivery_place_guard(
    package: ProcurementPackageExtraction,
    finding: SemanticCheckFinding,
) -> SemanticCheckFinding:
    if finding.check_id != "semantic.delivery_place":
        return finding

    values = [
        ("Заявка в план-график", getattr(package.schedule_application, "delivery_place", None)),
        ("ООЗ", getattr(package.purchase_description, "delivery_place", None)),
        ("Проект контракта", getattr(package.contract_draft, "delivery_place", None)),
    ]
    house_numbers = [
        (label, _house_numbers(value))
        for label, value in values
        if value and _house_numbers(value)
    ]
    if len(house_numbers) < 2:
        return finding

    address_parts = [
        (label, _address_core(value))
        for label, value in values
        if value and _address_core(value) is not None
    ]
    if len(address_parts) >= 2:
        baseline_label, baseline = address_parts[0]
        street_conflicts = [
            f"{label}: {parts[1]}"
            for label, parts in address_parts[1:]
            if baseline[0] == parts[0]
            and baseline[2] == parts[2]
            and baseline[1] != parts[1]
        ]
        if street_conflicts:
            return SemanticCheckFinding(
                check_id=finding.check_id,
                status="failed",
                message=(
                    f"Улица различается: {baseline_label} — {baseline[1]}; "
                    f"{' ; '.join(street_conflicts)}. Адреса не согласованы."
                ),
                compared_values=finding.compared_values,
                evidence=finding.evidence,
            )

    baseline_label, baseline_numbers = house_numbers[0]
    conflicts = [
        f"{label}: {', '.join(sorted(numbers))}"
        for label, numbers in house_numbers[1:]
        if baseline_numbers.isdisjoint(numbers)
    ]
    if not conflicts:
        if _same_address_core([value for _label, value in values if value]):
            return SemanticCheckFinding(
                check_id=finding.check_id,
                status="passed",
                message="Адреса совпадают по городу, улице и номеру дома; различается только формат записи.",
                compared_values=finding.compared_values,
                evidence=finding.evidence,
            )
        return finding

    baseline = ", ".join(sorted(baseline_numbers))
    return SemanticCheckFinding(
        check_id=finding.check_id,
        status="failed",
        message=(
            f"Номер дома различается: {baseline_label} — {baseline}; "
            f"{' ; '.join(conflicts)}. Адреса не согласованы."
        ),
        compared_values=finding.compared_values,
        evidence=finding.evidence,
    )


def _house_numbers(value: object) -> set[str]:
    matches = re.findall(
        r"(?i)(?<![а-яa-z])(?:д(?:ом)?\.?)\s*(\d+[а-яa-z]?)",
        str(value or ""),
    )
    return {
        re.sub(r"^0+(?=\d)", "", match.casefold())
        for match in matches
    }


def _same_address_core(values: list[object]) -> bool:
    streets: set[str] = set()
    cities: set[str] = set()
    for value in values:
        text = str(value or "")
        street_match = re.search(r"(?i)(?:ул[.]?|улица)\s*([^,;.]+)", text)
        city_matches = re.findall(r"(?i)(?:город|г[.]?)\s+([а-яёa-z-]+)", text)
        if not street_match or not city_matches:
            return False
        streets.add(re.sub(r"\s+", " ", street_match.group(1)).strip(" .").casefold())
        cities.add(city_matches[-1].casefold())
    return len(streets) == 1 and len(cities) == 1


def _address_core(value: object) -> tuple[str, str, str] | None:
    text = str(value or "")
    street_match = re.search(r"(?i)(?:ул[.]?|улица)\s*([^,;.]+)", text)
    city_matches = re.findall(r"(?i)(?:город|г[.]?)\s+([а-яёa-z-]+)", text)
    houses = _house_numbers(text)
    if not street_match or not city_matches or len(houses) != 1:
        return None
    street = re.sub(r"\s+", " ", street_match.group(1)).strip(" .").casefold()
    return city_matches[-1].casefold(), street, next(iter(houses))


def _extract_check(client, schema, prompt: str, payload: str):
    """Keep compatibility with small test doubles while production bypasses recovery."""
    direct = getattr(client, "extract_check", None)
    if callable(direct):
        return direct(schema, prompt, payload)
    return client.extract(schema, prompt, payload)


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

    method_values = [
        (
            "Заявка в план-график",
            getattr(package.schedule_application, "procurement_method_raw", None)
            or getattr(package.schedule_application, "procurement_method", None),
        ),
        (
            "Обращение",
            getattr(package.purchase_request, "procurement_method_raw", None)
            or getattr(package.purchase_request, "procurement_method", None),
        ),
        (
            "Пояснительная записка",
            getattr(package.explanatory_note, "procurement_method_raw", None)
            or getattr(package.explanatory_note, "procurement_method", None),
        ),
    ]
    recognized = [
        (label, value, kind)
        for label, value in method_values
        if value and (kind := _procurement_method_kind(value))
    ]
    kinds = {kind for _label, _value, kind in recognized}
    if len(kinds) > 1:
        values_text = "; ".join(f"{label}: {value}" for label, value, _kind in recognized)
        return SemanticCheckFinding(
            check_id=finding.check_id,
            status="failed",
            message=f"Способ закупки различается между документами: {values_text}.",
            compared_values=cleaned_values,
            evidence=finding.evidence,
        )

    if kinds == {"auction"}:
        return SemanticCheckFinding(
            check_id=finding.check_id,
            status="passed",
            message="Способ закупки: Электронный аукцион (конкурентная закупка, обоснование ЕП не требуется).",
            compared_values=cleaned_values,
            evidence=finding.evidence,
        )

    if kinds == {"single_supplier"}:
        evidence = " ".join(str(value or "") for _label, value, _kind in recognized).casefold()
        detail = "прямой договор" if "прямой договор" in evidence else "электронный магазин"
        return SemanticCheckFinding(
            check_id=finding.check_id,
            status="passed",
            message=f"Способ закупки: единственный поставщик ({detail}).",
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


def _procurement_method_kind(value: object) -> str | None:
    text = str(value or "").casefold().replace("ё", "е")
    if "электрон" in text and "магазин" in text:
        return "single_supplier"
    if "прямой договор" in text:
        return "single_supplier"
    if "аукцион" in text or "auction" in text:
        return "auction"
    if "конкурс" in text or "tender" in text:
        return "tender"
    if "котиров" in text or "quotation" in text:
        return "request_for_quotations"
    if "единственн" in text or "single_supplier" in text:
        return "single_supplier"
    return None


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
    if finding.check_id != "semantic.delivery_term":
        return finding
    values = [
        ("Заявка в план-график", getattr(package.schedule_application, "delivery_term", None), getattr(package.schedule_application, "delivery_term_text", None)),
        ("ООЗ", getattr(package.purchase_description, "delivery_term", None), getattr(package.purchase_description, "delivery_term_text", None)),
        ("Проект контракта", getattr(package.contract_draft, "delivery_term", None), getattr(package.contract_draft, "delivery_term_text", None)),
    ]
    signatures = [
        (label, signature)
        for label, term, raw in values
        if (signature := _delivery_term_signature(term, raw)) is not None
    ]
    if len(signatures) >= 2:
        baseline_label, baseline = signatures[0]
        conflicts = [
            f"{label}: {_delivery_term_signature_text(signature)}"
            for label, signature in signatures[1:]
            if signature[0] == baseline[0] and signature != baseline
        ]
        if conflicts:
            return SemanticCheckFinding(
                check_id=finding.check_id,
                status="failed",
                message=(
                    f"Сроки различаются: {baseline_label} — "
                    f"{_delivery_term_signature_text(baseline)}; {'; '.join(conflicts)}."
                ),
                compared_values=finding.compared_values,
                evidence=finding.evidence,
            )
    if finding.status == "passed":
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


def _delivery_term_signature(term: object, raw: object) -> tuple[str, str, str | None] | None:
    days = getattr(term, "days", None)
    day_type = getattr(term, "day_type", None)
    if days is not None:
        return "days", str(days), str(day_type or "unknown")
    text = str(raw or "")
    match = re.search(
        r"(?i)\b(\d+)\s*(?:\([^)]*\)\s*)?(\u043aалендарн\w*|рабоч\w*)\s+дн",
        text,
    )
    if match:
        return "days", match.group(1), "working" if match.group(2).casefold().startswith("рабоч") else "calendar"
    end_dates = re.findall(r"(?i)\b(?:по|до)\s+(\d{1,2}[./]\d{1,2}[./]\d{4})", text)
    if end_dates:
        return "end_date", end_dates[-1].replace("/", "."), None
    return None


def _delivery_term_signature_text(signature: tuple[str, str, str | None]) -> str:
    kind, value, qualifier = signature
    if kind == "end_date":
        return f"по {value}"
    label = "рабочих" if qualifier == "working" else "календарных" if qualifier == "calendar" else "дней"
    return f"{value} {label} дней" if label != "дней" else f"{value} дней"


def _same_text(left: object, right: object) -> bool:
    if not left or not right:
        return False
    return _semantic_normalize(left) == _semantic_normalize(right)


def _semantic_normalize(value: object) -> str:
    return " ".join(str(value).replace("\xa0", " ").casefold().split())


def _warranty_numeric_terms(value: object) -> set[str]:
    return set(re.findall(r"\d+(?:[.,]\d+)?", _semantic_normalize(value)))


_SUBJECT_STOP_TERMS = {
    "закупк",
    "поставк",
    "оказани",
    "выполнен",
    "контракт",
    "государствен",
    "муниципальн",
    "нужд",
    "товар",
    "работ",
    "услуг",
}


def _subject_terms(value: object) -> dict[str, str]:
    words = re.findall(r"[а-яё]{5,}", _semantic_normalize(value))
    return {
        word[:7]: word
        for word in words
        if not any(word.startswith(stop_term) for stop_term in _SUBJECT_STOP_TERMS)
    }


def _apply_subject_guard(
    package: ProcurementPackageExtraction,
    finding: SemanticCheckFinding,
) -> SemanticCheckFinding:
    if finding.check_id != "semantic.subject" or finding.status != "passed":
        return finding

    baseline_terms = _subject_terms(getattr(package.schedule_application, "purchase_subject", None))
    contract = package.contract_draft
    embedded_contract_subject = (
        getattr(contract.embedded_purchase_description, "purchase_subject", None)
        if contract and contract.embedded_purchase_description
        else None
    )
    values = [
        ("Обращение", getattr(package.purchase_request, "purchase_subject", None)),
        ("ООЗ", getattr(package.purchase_description, "purchase_subject", None)),
        ("Проект контракта", embedded_contract_subject or getattr(contract, "subject", None)),
        ("Пояснительная записка", getattr(package.explanatory_note, "subject", None)),
    ]
    gaps = []
    for label, value in values:
        if not value:
            continue
        candidate_terms = _subject_terms(value)
        missing = sorted(set(baseline_terms) - set(candidate_terms))
        if missing:
            gaps.append(f"{label}: не названы {', '.join(baseline_terms[key] for key in missing)}")
    if not gaps:
        return finding
    return SemanticCheckFinding(
        check_id=finding.check_id,
        status="warning",
        message=(
            "Формулировки предмета близки, но охват закупаемых групп различается: "
            + "; ".join(gaps)
            + "."
        ),
        compared_values=finding.compared_values,
        evidence=finding.evidence,
    )


def _warranty_reference_only(value: object) -> bool:
    text = _semantic_normalize(value)
    refers_to_appendix = (
        "указан" in text
        and ("описани" in text or "приложени" in text)
    )
    has_explicit_term = bool(
        re.search(r"\b\d+\s*(?:\([^)]*\)\s*)?(?:месяц|год|лет)", text)
        or re.search(r"\bдо\s+\d{1,2}[./]\d{1,2}[./]\d{2,4}", text)
    )
    return refers_to_appendix and not has_explicit_term


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
    embedded_contract_ooz = (
        contract.embedded_purchase_description
        if contract and contract.embedded_purchase_description
        else None
    )
    embedded_contract_warranty = getattr(
        embedded_contract_ooz,
        "warranty_requirements_text",
        None,
    )
    note = package.explanatory_note
    payload = {
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
            "warranty_section_text": getattr(ooz, "warranty_section_text", None),
        },
        "contract_draft": {
            "subject": embedded_contract_subject or getattr(contract, "subject", None),
            "legal_subject": getattr(contract, "subject", None),
            "delivery_place": getattr(contract, "delivery_place", None),
            "delivery_term_text": getattr(contract, "delivery_term_text", None),
            "warranty_text": embedded_contract_warranty or getattr(contract, "warranty_text", None),
            "warranty_reference_text": getattr(contract, "warranty_text", None),
            "warranty_section_text": getattr(embedded_contract_ooz, "warranty_section_text", None),
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
    if _plan_has_stages(package):
        for document in ("schedule_application", "purchase_request", "purchase_description", "contract_draft"):
            payload[document].pop("delivery_term_text", None)
    return payload


def _plan_has_stages(package: ProcurementPackageExtraction) -> bool:
    schedule = package.schedule_application
    return bool(schedule and getattr(schedule, "stages", []))


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
    embedded_contract_warranty = (
        getattr(contract.embedded_purchase_description, "warranty_requirements_text", None)
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
            ("Проект контракта", embedded_contract_warranty or getattr(contract, "warranty_text", None)),
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
