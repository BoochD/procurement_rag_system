from __future__ import annotations

import re

from summary_model.checks.models import CheckResult, ProcurementChecksReport


STATUS_LABELS = {
    "passed": "ОК",
    "failed": "ОШИБКА",
    "warning": "ПРЕДУПРЕЖДЕНИЕ",
    "manual_review": "ТРЕБУЕТ ПРОВЕРКИ",
    "not_applicable": "НЕ ПРИМЕНИМО",
    "skipped": "ПРОПУЩЕНО",
}

DOCUMENT_CHECK_ORDER = [
    "strict.package.purchase_request",
    "strict.package.schedule_application",
    "strict.package.nmck_justification",
    "strict.package.purchase_description",
    "strict.package.contract_draft",
    "strict.package.explanatory_note",
]

INTERNAL_CHECK_ORDER = [
    "strict.request.attachments",
    "strict.schedule.fields",
    "strict.nmck.amounts",
    "strict.onmck.arithmetic",
    "strict.onmck.min_price",
    "strict.codes.okpd2",
    "strict.codes.ktru",
    "strict.funding_source",
    "strict.securities",
    "strict.contract.attachments",
]

SEMANTIC_CHECK_ORDER = [
    "semantic.subject",
    "semantic.delivery_term",
    "semantic.delivery_place",
    "semantic.stages",
    "semantic.warranty",
    "semantic.procurement_method",
    "semantic.smp_preferences",
]

COMMERCIAL_OFFER_CHECKS = {
    "manual.commercial_offers.count",
    "manual.commercial_offers.content",
    "manual.commercial_offers.onmck",
}

SPECIAL_CHECKS = set(DOCUMENT_CHECK_ORDER + INTERNAL_CHECK_ORDER + SEMANTIC_CHECK_ORDER) | {
    "manual.commercial_offers.count",
    "manual.commercial_offers.content",
    "manual.commercial_offers.onmck",
    "manual.ktru.characteristics",
    "manual.ktru.additional",
    "manual.national_regime_1875",
    "strict.onmck.supplier_prices",
}

DOCUMENT_LABELS = {
    "purchase_request": "Обращение о проведении закупки",
    "schedule_application": "Заявка в план-график",
    "nmck_justification": "ОНМЦК",
    "purchase_description": "Описание объекта закупки",
    "contract_draft": "Проект контракта",
    "explanatory_note": "Пояснительная записка",
    "commercial_offer": "Коммерческое предложение",
    "commercial_offers": "коммерческие предложения",
    "files": "загруженные документы",
}

FIELD_LABELS = {
    "amount": "сумма",
    "amounts": "суммы",
    "attachment_kind": "тип приложения",
    "attachments": "приложения",
    "codes": "коды",
    "contract_security": "обеспечение исполнения контракта",
    "commercial_offers_found_count": "количество приложенных КП",
    "commercial_offers_required_count": "требуемое количество КП",
    "delivery_place": "место поставки",
    "delivery_term": "срок поставки",
    "delivery_term_text": "срок поставки",
    "document_type": "тип документа",
    "extra": "лишнее",
    "extra_characteristics": "дополнительные характеристики",
    "fields_compared": "сравниваемые поля",
    "found": "найдено",
    "funding_source": "источник финансирования",
    "has_stages": "наличие этапов",
    "invalid_values": "ошибки значений",
    "is_correct": "корректно",
    "ktru_code": "код КТРУ",
    "listed": "указано в списке",
    "missing": "не найдено",
    "missing_by_document": "не найдено по документам",
    "nmck": "НМЦК",
    "okpd2_code": "код ОКПД2",
    "present": "наличие",
    "procurement_method": "способ закупки",
    "purchase_subject": "предмет закупки",
    "raw_fields": "поля заявки",
    "referenced": "указанные приложения",
    "required": "требуется",
    "schedule_contract_security": "обеспечение исполнения контракта в заявке",
    "schedule_warranty_security": "обеспечение гарантийных обязательств в заявке",
    "single_supplier_basis": "основание единственного поставщика",
    "smp_preference": "преференции СМП/СОНКО",
    "stage_execution_terms": "этапы исполнения",
    "summary_lines": "краткое описание",
    "uploaded": "загружено",
    "warranty_security": "обеспечение гарантийных обязательств",
}

ATTACHMENT_KIND_LABELS = {
    "purchase_description": "Описание объекта закупки",
    "contract_specification": "Спецификация",
    "acceptance_act_form": "Форма акта приёма-передачи",
    "commercial_offer": "Коммерческое предложение",
    "nmck_justification": "ОНМЦК",
    "schedule_application": "Заявка в план-график",
    "explanatory_note": "Пояснительная записка",
    "other": "другое приложение",
    "unknown": "тип не определён",
}


def build_checks_report_text(report: ProcurementChecksReport) -> str:
    by_id = {result.check_id: result for result in report.results}
    lines = [
        "Результат проверки документов",
        "",
        (
            f"Ошибок: {report.errors_count}. "
            f"Предупреждений: {report.warnings_count}. "
            f"Требуют проверки: {report.manual_review_count}. "
            f"Успешных: {report.passed_count}. "
            f"Пропущено: {report.skipped_count}."
        ),
        "",
        "0) Комплектность пакета",
        "Наличие документов:",
    ]

    lines.extend(_render_document_presence(by_id))
    lines.append("")
    lines.extend(_render_ktru_registry_section(by_id))
    lines.append("")
    lines.extend(_render_pp1875_section(by_id))
    lines.append("")
    lines.extend(_render_internal_section(by_id))
    lines.append("")
    lines.extend(_render_semantic_section(by_id))
    lines.append("")
    lines.extend(_render_commercial_offer_section(by_id))
    lines.append("")
    lines.extend(_render_ktru_characteristics_section(by_id))
    lines.append("")
    lines.extend(_render_supplier_prices_section(by_id))

    leftovers = [result for result in report.results if result.check_id not in SPECIAL_CHECKS]
    if leftovers:
        lines.append("")
        lines.append("Дополнительные проверки")
        for result in leftovers:
            lines.extend(_render_result(result))
    return "\n".join(lines).rstrip() + "\n"


def _render_document_presence(by_id: dict[str, CheckResult]) -> list[str]:
    lines: list[str] = []
    for check_id in DOCUMENT_CHECK_ORDER:
        result = by_id.get(check_id)
        if result is None:
            continue
        title = result.title.replace("Наличие документа:", "").strip()
        label = "НАЙДЕН" if result.status == "passed" else STATUS_LABELS[result.status]
        lines.append(f"- {title} — {label}")
    return lines or ["- не найдено данных о составе пакета"]


def _render_ktru_registry_section(by_id: dict[str, CheckResult]) -> list[str]:
    result = by_id.get("manual.ktru.characteristics")
    lines = ["1) Проверка КТРУ через сервис zakupki.gov.ru:"]
    if result is None:
        lines.append("- не выполнялась")
        return lines
    rendered = _render_ktru_cards(result)
    if rendered:
        lines.extend(rendered)
        return lines
    lines.extend(_render_result(result))
    return lines


def _render_pp1875_section(by_id: dict[str, CheckResult]) -> list[str]:
    result = by_id.get("manual.national_regime_1875")
    lines = ["2) Проверка ОКПД на вхождение в постановление 1875:"]
    if result is None:
        lines.append("- не выполнялась")
        return lines
    rendered = _render_pp1875_matches(result)
    if rendered:
        lines.extend(rendered)
    else:
        lines.extend(_render_result(result))
    return lines


def _render_internal_section(by_id: dict[str, CheckResult]) -> list[str]:
    lines = ["3) Внутренний анализ перечня документов:"]
    for check_id in INTERNAL_CHECK_ORDER:
        result = by_id.get(check_id)
        if result is not None:
            lines.extend(_render_titled_result(result))
            lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def _render_semantic_section(by_id: dict[str, CheckResult]) -> list[str]:
    lines = ["4) Semantic/manual review"]
    for check_id in SEMANTIC_CHECK_ORDER:
        result = by_id.get(check_id)
        if result is not None:
            lines.extend(_render_semantic_result(result))
            lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def _render_commercial_offer_section(by_id: dict[str, CheckResult]) -> list[str]:
    lines = ["5) Коммерческие предложения:"]
    count = by_id.get("manual.commercial_offers.count")
    if count is not None:
        found = count.details.get("found") if count.details else None
        required = count.details.get("required") if count.details else None
        if found == 0:
            lines.append(f"- Коммерческие предложения не приложены. Требуется не менее {required or 3}.")
            return lines
        else:
            lines.extend(_render_result(count))
    for check_id in ("manual.commercial_offers.content", "manual.commercial_offers.onmck"):
        result = by_id.get(check_id)
        if result is not None:
            lines.extend(_render_result(result))
    return lines


def _render_ktru_characteristics_section(by_id: dict[str, CheckResult]) -> list[str]:
    lines = ["6) Сравнение характеристик из ООЗ с КТРУ на сайте:"]
    for check_id in ("manual.ktru.characteristics", "manual.ktru.additional"):
        result = by_id.get(check_id)
        if result is not None:
            lines.extend(_render_result(result))
    return lines


def _render_supplier_prices_section(by_id: dict[str, CheckResult]) -> list[str]:
    lines = ["7) Сравнение цен услуг поставщиков в ОНМЦК:"]
    result = by_id.get("strict.onmck.supplier_prices")
    if result is None:
        lines.append("- не выполнялось")
        return lines
    summary_lines = result.details.get("summary_lines") if result.details else None
    if isinstance(summary_lines, list) and summary_lines:
        for item in summary_lines:
            lines.extend(_render_supplier_price_line(str(item)))
    else:
        lines.extend(_render_result(result))
    return lines


def _render_pp1875_matches(result: CheckResult) -> list[str]:
    details = result.details or {}
    matches = details.get("matches")
    if not isinstance(matches, list) or not matches:
        return []

    lines = [f"- {_human_text(result.title)} - {STATUS_LABELS[result.status]}. {_human_text(result.report_text)}", ""]
    for item in matches:
        if not isinstance(item, dict):
            continue
        message = item.get("message")
        if message:
            lines.extend(str(message).replace(".<ins>", ".\n<ins>").splitlines())
        else:
            lines.append(str(item.get("code") or item))
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return lines



def _render_ktru_cards(result: CheckResult) -> list[str]:
    cards = result.details.get("ktru_cards") if result.details else None
    if not isinstance(cards, list) or not cards:
        return []
    lines: list[str] = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        code = card.get("code") or "?"
        if card.get("unavailable"):
            lines.append(f"- <warn>КТРУ {code} не удалось получить через zakupki.gov.ru.</warn>")
            lines.append("")
            continue
        lines.append(f"- <ok>КТРУ {code} найден.</ok>")
        if card.get("url"):
            lines.append(f"  Ссылка на товар: {card['url']}")
        reference_name = card.get("reference_name") or "не найдено"
        item_names = card.get("item_names") or []
        if card.get("name_matches"):
            lines.append("  <ok>Наименование совпадает с эталонной записью КТРУ.</ok>")
        else:
            lines.append("  <warn>Наименование отличается от эталонной записи КТРУ или требует проверки.</warn>")
        lines.append(f"  Наименование КТРУ: {reference_name}")
        for item_name in item_names:
            lines.append(f"  Наименование в документах: {item_name}")
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return lines

def _render_semantic_result(result: CheckResult) -> list[str]:
    return _render_titled_result(result)


def _render_titled_result(result: CheckResult) -> list[str]:
    label = STATUS_LABELS[result.status]
    lines = [f"- <b>{_human_text(result.title)}</b> - {label}. {_human_text(result.report_text)}"]
    summary_lines = result.details.get("summary_lines") if result.details else None
    if isinstance(summary_lines, list):
        for item in summary_lines:
            if item:
                lines.append(f"  - {_human_text(str(item))}")
    if result.check_id == "strict.funding_source" and result.details:
        lines.extend(_field_lines(result.details, ["schedule_application", "contract_draft"]))
    if result.check_id == "strict.securities" and result.details:
        lines.extend(_security_lines(result.details))
    if result.check_id == "strict.contract.attachments" and result.details:
        lines.extend(_attachment_lines(result.details))
    return lines


def _render_supplier_price_line(line: str) -> list[str]:
    parts = [part.strip() for part in line.split(" | ")]
    if len(parts) != 3:
        return [line]
    return [
        f"- {parts[0]}",
        f"  {parts[1]}",
        f"  {parts[2]}",
        "",
    ]


def _unique_codes(value: object) -> set[str]:
    if not isinstance(value, dict):
        return set()
    result: set[str] = set()
    for codes in value.values():
        if isinstance(codes, list):
            result.update(str(code) for code in codes if code)
    return result


def _render_result(result: CheckResult) -> list[str]:
    label = STATUS_LABELS[result.status]
    lines = [f"- {_human_text(result.title)} — {label}. {_human_text(result.report_text)}"]
    summary_lines = result.details.get("summary_lines") if result.details else None
    if isinstance(summary_lines, list):
        for item in summary_lines:
            if item:
                lines.append(f"  - {_human_text(str(item))}")
    if result.check_id == "strict.funding_source" and result.details:
        lines.extend(_field_lines(result.details, ["schedule_application", "contract_draft"]))
    if result.check_id == "strict.securities" and result.details:
        lines.extend(_security_lines(result.details))
    if result.check_id == "strict.contract.attachments" and result.details:
        lines.extend(_attachment_lines(result.details))
    if result.status in {"failed", "warning", "manual_review"} and result.details and not summary_lines:
        compact = _compact_details(result)
        if compact:
            lines.append(f"  Детали: {compact}")
    return lines


def _field_lines(details: dict[str, object], keys: list[str]) -> list[str]:
    return [
        f"  - {_human_label(key)}: {_human_value(details[key])}"
        for key in keys
        if details.get(key)
    ]


def _security_lines(details: dict[str, object]) -> list[str]:
    lines: list[str] = []
    contract_security = details.get("contract_security")
    if isinstance(contract_security, dict) and contract_security.get("raw"):
        lines.append(f"  - Проект контракта: {contract_security['raw']}")
    schedule_contract_security = details.get("schedule_contract_security")
    if isinstance(schedule_contract_security, dict) and schedule_contract_security.get("raw"):
        lines.append(f"  - Заявка в план-график: {schedule_contract_security['raw']}")
    return lines


def _attachment_lines(details: dict[str, object]) -> list[str]:
    referenced = details.get("referenced")
    if not isinstance(referenced, list):
        return []
    lines = []
    for item in referenced:
        if isinstance(item, dict):
            number = item.get("number") or "?"
            title = item.get("title_raw") or "без названия"
            kind = item.get("attachment_kind") or "unknown"
            lines.append(f"  - Приложение №{number}: {title} ({_human_attachment_kind(kind)})")
    return lines


def _compact_details(result: CheckResult) -> str:
    interesting = {
        key: value
        for key, value in result.details.items()
        if key != "summary_lines" and value not in (None, "", [], {}, False)
    }
    if not interesting:
        return ""
    parts = [f"{_human_label(key)}: {_human_value(value)}" for key, value in interesting.items()]
    text = "; ".join(parts)
    return text[:1000] + ("..." if len(text) > 1000 else "")


def _human_attachment_kind(value: object) -> str:
    text = str(value)
    return ATTACHMENT_KIND_LABELS.get(text, _human_text(text))


def _human_label(value: object) -> str:
    text = str(value)
    return DOCUMENT_LABELS.get(text) or FIELD_LABELS.get(text) or ATTACHMENT_KIND_LABELS.get(text) or _human_text(text)


def _human_value(value: object) -> str:
    if isinstance(value, dict):
        parts = [
            f"{_human_label(key)}: {_human_value(item)}"
            for key, item in value.items()
            if item not in (None, "", [], {}, False)
        ]
        return "{" + ", ".join(parts) + "}"
    if isinstance(value, (list, tuple, set)):
        return "[" + ", ".join(_human_value(item) for item in value if item not in (None, "", [], {}, False)) + "]"
    if isinstance(value, bool):
        return "да" if value else "нет"
    return _human_text(str(value))


def _human_text(text: str) -> str:
    result = text
    replacements = {**DOCUMENT_LABELS, **FIELD_LABELS, **ATTACHMENT_KIND_LABELS}
    for source, target in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        result = re.sub(rf"(?<![\w]){re.escape(source)}(?![\w])", target, result)
    return result
