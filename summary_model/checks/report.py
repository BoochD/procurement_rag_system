from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

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
    "strict.onmck.items",
    "strict.onmck.stage_prices",
    "strict.codes.okpd2",
    "strict.codes.ktru",
    "strict.plan.subject",
    "strict.plan.delivery_term",
    "strict.plan.delivery_place",
    "strict.plan.contract_execution_term",
    "strict.plan.stages",
    "strict.plan.warranty",
    "strict.funding_source",
    "strict.contract.penalties",
    "strict.smp_sonko_subcontract",
    "strict.smp_sonko_standard_terms",
    "strict.contract.attachments",
]

PLAN_REGULATORY_CHECK_ORDER = [
    "strict.application_security",
    "strict.plan.contract_security_limits",
    "strict.plan.warranty_security_limits",
    "strict.plan.additional_participant_requirements",
    "strict.plan.national_regime_fields",
]

SEMANTIC_CHECK_ORDER = [
    "semantic.subject",
    "semantic.delivery_term",
    "semantic.delivery_place",
    "semantic.warranty",
    "semantic.procurement_method",
    "semantic.smp_preferences",
]

SEMANTIC_REPORT_REPLACEMENTS = {
    "semantic.subject": "strict.plan.subject",
    "semantic.delivery_term": "strict.plan.delivery_term",
    "semantic.delivery_place": "strict.plan.delivery_place",
    "semantic.warranty": "strict.plan.warranty",
}

COMMERCIAL_OFFER_CHECKS = {
    "manual.commercial_offers.count",
    "manual.commercial_offers.content",
    "manual.commercial_offers.onmck",
}

SPECIAL_CHECKS = set(DOCUMENT_CHECK_ORDER + PLAN_REGULATORY_CHECK_ORDER + INTERNAL_CHECK_ORDER + SEMANTIC_CHECK_ORDER) | {
    "manual.commercial_offers.count",
    "manual.commercial_offers.content",
    "manual.commercial_offers.onmck",
    "manual.ktru.plan_registry",
    "manual.ktru.characteristics",
    "manual.ktru.additional",
    "manual.ktru.trademarks",
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
    "contract_price": "цена контракта",
    "commercial_offers_found_count": "количество приложенных КП",
    "commercial_offers_required_count": "требуемое количество КП",
    "delivery_place": "место поставки",
    "delivery_place_mentions": "места поставки",
    "delivery_term": "срок поставки",
    "delivery_term_text": "срок поставки",
    "contract_execution_term": "срок исполнения контракта",
    "contract_execution_term_text": "срок исполнения контракта",
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
    "procurement_method_raw": "способ закупки",
    "purchase_subject": "предмет закупки",
    "raw_fields": "поля заявки",
    "referenced": "указанные приложения",
    "required": "требуется",
    "schedule_contract_security": "обеспечение исполнения контракта в заявке",
    "schedule_warranty_security": "обеспечение гарантийных обязательств в заявке",
    "penalty_clauses": "штрафы",
    "peni_clauses": "пени",
    "expected_supplier_value_percent": "ожидаемый штраф поставщика",
    "expected_fixed_fine": "ожидаемый фиксированный штраф",
    "expected_smp_sonko_percent": "ожидаемый штраф за непривлечение СМП/СОНКО",
    "clauses": "найденные условия",
    "manual_review": "требует проверки",
    "failures": "расхождения",
    "single_supplier_basis": "основание единственного поставщика",
    "single_supplier_basis_text": "основание единственного поставщика",
    "smp_preference": "преференции СМП/СОНКО",
    "subcontract_smp_sonko_required": "обязанность привлечения СМП/СОНКО",
    "subcontract_smp_sonko_required_raw": "обязанность привлечения СМП/СОНКО",
    "subcontract_smp_sonko_percent": "процент привлечения СМП/СОНКО",
    "subcontract_smp_sonko_percent_raw": "процент привлечения СМП/СОНКО",
    "schedule_percent": "процент в заявке",
    "contract_percent": "процент в проекте контракта",
    "stage_execution_terms": "этапы исполнения",
    "stages": "этапы исполнения",
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

TECHNICAL_TEXT_LABELS = {
    "Semantic/manual review": "Смысловая и ручная проверка",
    "OKPD2": "ОКПД2",
    "KTRU": "КТРУ",
    "table_01": "приложение №1",
    "table_02": "приложение №2",
    "confirmed": "подтверждено",
    "ambiguous": "неоднозначно",
    "manual_review": "требует проверки",
    "auction": "электронный аукцион",
    "checks.json": "файле подробных результатов",
    "VLM": "модель визуального распознавания",
    "LLM": "языковая модель",
    "fallback": "резервный разбор",
    "items": "позиции",
    "item": "позиция",
}

RAW_DETAIL_KEYS = {
    "raw",
    "raw_text",
    "raw_rows",
    "logical_rows",
    "compact_json",
    "compact_markdown",
    "cells_by_col",
    "cells_by_header",
}


def build_checks_report_text(report: ProcurementChecksReport) -> str:
    by_id = {result.check_id: result for result in report.results}
    hidden_check_ids = _hidden_strict_check_ids(by_id)
    visible_results = [
        result for result in report.results if result.check_id not in hidden_check_ids
    ]
    lines = [
        "Результат проверки документов",
        "",
        (
            f"Ошибок: {sum(item.status == 'failed' for item in visible_results)}. "
            f"Предупреждений: {sum(item.status == 'warning' for item in visible_results)}. "
            f"Требуют проверки: {sum(item.status == 'manual_review' for item in visible_results)}. "
            f"Успешных: {sum(item.status == 'passed' for item in visible_results)}. "
            f"Пропущено: {sum(item.status == 'skipped' for item in visible_results)}."
        ),
        "",
        "0) Комплектность пакета",
        "Наличие документов:",
    ]

    lines.extend(_render_document_presence(by_id))
    attachment_result = by_id.get("strict.request.attachments")
    if attachment_result is not None:
        lines.extend(_render_titled_result(attachment_result))
    lines.append("")
    lines.extend(_render_plan_regulatory_section(by_id))
    lines.append("")
    lines.extend(_render_ktru_registry_section(by_id))
    lines.append("")
    lines.extend(_render_pp1875_section(by_id))
    lines.append("")
    lines.extend(
        _render_internal_section(
            by_id,
            hidden_check_ids=hidden_check_ids | {"strict.request.attachments"},
        )
    )
    lines.append("")
    lines.extend(_render_semantic_section(by_id, hidden_check_ids=hidden_check_ids))
    lines.append("")
    lines.extend(_render_commercial_offer_section(by_id))
    lines.append("")
    lines.extend(_render_ktru_characteristics_section(by_id))

    leftovers = [result for result in report.results if result.check_id not in SPECIAL_CHECKS]
    if leftovers:
        lines.append("")
        lines.append("Дополнительные проверки")
        for result in leftovers:
            lines.extend(_render_result(result))
    return "\n".join(lines).rstrip() + "\n"


def _hidden_strict_check_ids(by_id: dict[str, CheckResult]) -> set[str]:
    hidden = {"strict.onmck.supplier_prices"}
    for semantic_id, strict_id in SEMANTIC_REPORT_REPLACEMENTS.items():
        semantic = by_id.get(semantic_id)
        strict = by_id.get(strict_id)
        if semantic is None:
            continue
        if _semantic_result_unavailable(semantic):
            if strict is not None:
                hidden.add(semantic_id)
        else:
            hidden.add(strict_id)
    return hidden


def _semantic_result_unavailable(result: CheckResult) -> bool:
    message = " ".join(str(result.message or result.report_text or "").casefold().split())
    return (
        "semantic llm check не выполнен" in message
        or "llm не вернула результат" in message
    )


def build_commercial_offer_report_text(report: ProcurementChecksReport) -> str:
    """Render commercial-offer checks with the production report formatting."""
    by_id = {result.check_id: result for result in report.results}
    lines = ["Результат извлечения коммерческих предложений"]
    lines.extend(_render_commercial_offer_section(by_id))
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


def _render_plan_regulatory_section(by_id: dict[str, CheckResult]) -> list[str]:
    lines = ["1) Нормативные проверки заявки в план-график:"]
    for check_id in PLAN_REGULATORY_CHECK_ORDER:
        result = by_id.get(check_id)
        if result is not None:
            lines.extend(_render_titled_result(result))
            lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def _render_ktru_registry_section(by_id: dict[str, CheckResult]) -> list[str]:
    result = by_id.get("manual.ktru.plan_registry") or by_id.get("manual.ktru.characteristics")
    lines = ["2) Проверка кодов КТРУ из плана-графика через сервис zakupki.gov.ru:"]
    if result is None:
        lines.append("- не выполнялась")
        return lines
    rendered = _render_ktru_cards(result)
    if rendered:
        lines.extend(rendered)
        return lines
    lines.extend(_render_result(result))
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
        if card.get("not_found"):
            lines.append(f"- <error>КТРУ {code} не найден в каталоге zakupki.gov.ru.</error>")
            lines.append("")
            continue
        if card.get("unavailable"):
            lines.append(f"- <warn>КТРУ {code} не удалось получить через zakupki.gov.ru.</warn>")
            lines.append("")
            continue
        lines.append(f"- <ok>КТРУ {code} найден.</ok>")
        if card.get("url"):
            lines.append(f"  Ссылка на товар: {card['url']}")
        reference_name = card.get("reference_name") or "не найдено"
        item_names = card.get("item_names") or []
        if not item_names:
            lines.append("  Наименование в заявке в план-график не извлечено; карточка проверена по коду.")
        elif card.get("name_matches"):
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


def _render_pp1875_section(by_id: dict[str, CheckResult]) -> list[str]:
    result = by_id.get("manual.national_regime_1875")
    lines = ["3) Проверка ОКПД2 на вхождение в постановление 1875:"]
    if result is None:
        lines.append("- не выполнялась")
        return lines
    rendered = _render_pp1875_matches(result)
    if rendered:
        lines.extend(rendered)
    else:
        lines.extend(_render_result(result))
    return lines


def _render_internal_section(
    by_id: dict[str, CheckResult],
    *,
    hidden_check_ids: set[str] | None = None,
) -> list[str]:
    lines = ["4) Внутренний анализ перечня документов:"]
    for check_id in INTERNAL_CHECK_ORDER:
        if check_id in (hidden_check_ids or set()):
            continue
        result = by_id.get(check_id)
        if result is not None:
            if check_id == "strict.nmck.amounts":
                lines.extend(["", "<b>Проверки ОНМЦК:</b>", ""])
            lines.extend(_render_titled_result(result))
            lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def _render_semantic_section(
    by_id: dict[str, CheckResult],
    *,
    hidden_check_ids: set[str] | None = None,
) -> list[str]:
    lines = ["5) Смысловая и ручная проверка:"]
    for check_id in SEMANTIC_CHECK_ORDER:
        if check_id in (hidden_check_ids or set()):
            continue
        result = by_id.get(check_id)
        if result is not None:
            lines.extend(_render_semantic_result(result))
            lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def _render_commercial_offer_section(by_id: dict[str, CheckResult]) -> list[str]:
    lines = ["6) Коммерческие предложения:"]
    count = by_id.get("manual.commercial_offers.count")
    if count is not None:
        found = count.details.get("found") if count.details else None
        required = count.details.get("required") if count.details else None
        if found == 0:
            lines.append(f"- Коммерческие предложения не приложены. Требуется не менее {required or 3}.")
            lines.append(
                "- Сверка наименований, цен, количества, единиц измерения, реквизитов и НДС с КП не выполнена: "
                "в пакете нет документов КП."
            )
            lines.extend(_render_trademark_table(by_id.get("manual.ktru.trademarks")))
            return lines
        else:
            lines.extend(_render_result(count))
    content = by_id.get("manual.commercial_offers.content")
    if content is not None:
        lines.extend(_render_commercial_offer_content(content))
    comparison = by_id.get("manual.commercial_offers.onmck")
    lines.extend(_render_commercial_offer_criteria(content, comparison))
    if comparison is not None:
        lines.extend(_render_commercial_offer_comparison(comparison))
    lines.extend(_render_trademark_table(by_id.get("manual.ktru.trademarks")))
    return lines


def _render_ktru_characteristics_section(by_id: dict[str, CheckResult]) -> list[str]:
    lines = ["7) Сравнение характеристик из ООЗ с КТРУ на сайте:"]
    characteristics = by_id.get("manual.ktru.characteristics")
    if characteristics is not None:
        if characteristics.status == "passed":
            lines.append(
                "- <ok>Обязательные характеристики и единицы их измерения в ООЗ соответствуют записям КТРУ; "
                "дополнительные характеристики проверены отдельно ниже.</ok>"
            )
        else:
            rendered = _render_ktru_characteristic_rows(characteristics)
            lines.extend(rendered if rendered else _render_result(characteristics))
    additional = by_id.get("manual.ktru.additional")
    if additional is not None:
        lines.append("")
        rendered = _render_ktru_additional_rows(additional)
        if rendered:
            lines.extend(rendered)
    return lines


def _render_commercial_offer_criteria(
    content: CheckResult | None,
    comparison: CheckResult | None,
) -> list[str]:
    criteria = []
    if comparison and isinstance(comparison.details.get("criteria"), list):
        criteria.extend(comparison.details["criteria"])
    if content and isinstance(content.details.get("total_criterion"), dict):
        criteria.append(content.details["total_criterion"])
    if content and isinstance(content.details.get("vat_criterion"), dict):
        criteria.append(content.details["vat_criterion"])
    if not criteria:
        return []
    lines = ["", "- <b>Проверки КП по ООЗ и ОНМЦК:</b>"]
    for criterion in criteria:
        if not isinstance(criterion, dict):
            continue
        status = str(criterion.get("status") or "manual_review")
        label = _human_text(str(criterion.get("label") or "Проверка"))
        lines.append(f"  - {label} — <b>{STATUS_LABELS.get(status, status)}</b>.")
        calculations = criterion.get("calculations")
        if criterion.get("key") == "vat" and isinstance(calculations, list):
            for calculation in calculations:
                if not isinstance(calculation, dict):
                    continue
                calculation_label = _human_text(str(calculation.get("label") or "КП"))
                if calculation.get("note"):
                    lines.append(f"    - {calculation_label}: {_human_text(str(calculation['note']))}")
                elif calculation.get("base") is not None:
                    lines.append(
                        f"    - {calculation_label}: база без НДС <b>{_report_money(calculation['base'])}</b> "
                        f"× {str(calculation.get('rate_fraction') or '').replace('.', ',')} = "
                        f"<b>{_report_money(calculation.get('calculated'))}</b>; "
                        f"в КП указано <b>{_report_money(calculation.get('declared'))}</b>."
                    )
        issues = criterion.get("issues")
        if status != "passed" and isinstance(issues, list):
            unique_issues = list(dict.fromkeys(
                _human_text(str(issue)) for issue in issues if str(issue).strip()
            ))
            for issue in unique_issues[:5]:
                lines.append(f"    - {issue}")
            if len(unique_issues) > 5:
                lines.append(
                    f"    - Ещё причин: {len(unique_issues) - 5}; полный список сохранён в checks.json."
                )
    return lines


def _render_trademark_table(result: CheckResult | None) -> list[str]:
    rows = result.details.get("trademarks") if result and result.details else None
    if not isinstance(rows, list) or not rows:
        return []
    lines = [
        "",
        "- <b>Товарные знаки и их обоснование</b> — без правовой оценки.",
        "",
        "| Позиция | Товарный знак | Обоснование товарного знака |",
        "| :--- | :--- | :---: |",
    ]
    for row in rows:
        if not isinstance(row, dict):
            continue
        justification = "найдено" if row.get("justification_found") else "не найдено"
        item_name = row.get("item_name") or "позиция"
        lines.append(
            f"| {_table_cell(_human_text(str(item_name)))} | "
            f"{_table_cell(_human_text(str(row.get('trademark'))))} | {justification} |"
        )
    return lines


def _render_supplier_prices_section(by_id: dict[str, CheckResult]) -> list[str]:
    lines = ["8) Сравнение цен услуг поставщиков в ОНМЦК:"]
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
            lines.extend(_human_text(str(message)).replace(".<ins>", ".\\n<ins>").splitlines())
        else:
            lines.append(str(item.get("code") or item))
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def _render_semantic_result(result: CheckResult) -> list[str]:
    return _render_titled_result(result)


def _render_stage_table(result: CheckResult) -> list[str]:
    label = STATUS_LABELS[result.status]
    lines = [f"- <b>{_human_text(result.title)}</b> - {label}. {_human_text(result.report_text)}"]
    details = result.details or {}
    stage_tables = result.details.get("stage_tables") if result.details else None
    if isinstance(stage_tables, list) and stage_tables:
        internal_differences = details.get("internal_differences")
        if isinstance(internal_differences, list) and internal_differences:
            lines.extend(["", "<b>Внутреннее несоответствие ПГ:</b>"])
            lines.extend(f"- {_human_text(str(item))}" for item in internal_differences)
        comparison_differences = details.get("comparison_differences")
        if isinstance(comparison_differences, list) and comparison_differences:
            lines.extend(["", "<b>Сверка этапов с документами:</b>"])
            lines.extend(f"- {_human_text(str(item))}" for item in comparison_differences)
        for index, table in enumerate(stage_tables, start=1):
            if not isinstance(table, dict) or not isinstance(table.get("rows"), list):
                continue
            title = _human_text(str(table.get("title") or "Документ"))
            kind = table.get("kind")
            lines.extend(["", f"#### 📌 Таблица {index}: {title}"])
            if kind == "nmck":
                lines.extend([
                    "| № этапа | Наименование этапа | Стоимость этапа | Доля от НМЦК |",
                    "| :---: | :--- | :---: | :---: |",
                ])
                for row in table["rows"]:
                    lines.append(
                        "| {number} | {name} | {price} | {share} |".format(
                            number=_table_cell(row.get("number")),
                            name=_table_cell(row.get("name")),
                            price=_table_cell(row.get("price")),
                            share=_table_cell(row.get("share")),
                        )
                    )
            else:
                lines.extend([
                    "| № этапа | Наименование этапа | Срок исполнения | Объём | Стоимость |",
                    "| :---: | :--- | :--- | :---: | :---: |",
                ])
                for row in table["rows"]:
                    lines.append(
                        "| {number} | {name} | {term} | {quantity} | {price} |".format(
                            number=_table_cell(row.get("number")),
                            name=_table_cell(row.get("name")),
                            term=_table_cell(row.get("term")),
                            quantity=_table_cell(row.get("quantity")),
                            price=_table_cell(row.get("price")),
                        )
                    )
        return lines

    summary_lines = result.details.get("summary_lines") if result.details else None
    clean_lines = []
    if isinstance(summary_lines, list):
        for item in summary_lines:
            s_item = str(item).strip()
            if s_item.startswith("[{") or s_item.startswith("{"):
                continue
            clean_lines.append(s_item)

    if clean_lines:
        lines.append("")
        lines.append("| Документ | Согласованность и сроки этапов |")
        lines.append("|---|---|")
        for line in clean_lines:
            if ":" in line:
                doc, rest = line.split(":", 1)
                lines.append(f"| <doc>{doc.strip()}</doc> | {rest.strip()} |")
            else:
                lines.append(f"| <doc>Сверка</doc> | {line} |")
        lines.append("")
    return lines


DOC_PATTERNS = [
    (re.compile(r"^\s*(Заявка в план-график|План-график)\s*:", re.IGNORECASE), "Заявка в план-график"),
    (re.compile(r"^\s*(Описание объекта закупки \(ООЗ\)|Описание объекта закупки|ООЗ)\s*:", re.IGNORECASE), "Описание объекта закупки (ООЗ)"),
    (re.compile(r"^\s*(Обращение о проведении закупки|Обращение)\s*:", re.IGNORECASE), "Обращение о проведении закупки"),
    (re.compile(r"^\s*(Проект контракта|Контракт)\s*:", re.IGNORECASE), "Проект контракта"),
    (re.compile(r"^\s*(Обоснование НМЦК|ОНМЦК)\s*:", re.IGNORECASE), "Обоснование НМЦК"),
    (re.compile(r"^\s*(Пояснительная записка)\s*:", re.IGNORECASE), "Пояснительная записка"),
]


def _wrap_doc_badges(text: str) -> str:
    if "<doc>" in text or not text:
        return text
    for pattern, label in DOC_PATTERNS:
        if pattern.search(text):
            return pattern.sub(f"<doc>{label}</doc>:", text, count=1)
    if ":" in text:
        prefix, rest = text.split(":", 1)
        prefix_lower = prefix.strip().lower()
        for doc_key, doc_label in DOCUMENT_LABELS.items():
            if doc_label.lower() in prefix_lower or doc_key.lower() in prefix_lower:
                return f"<doc>{doc_label}</doc>: {rest.strip()}"
    return text


def _render_titled_result(result: CheckResult) -> list[str]:
    if result.check_id == "strict.plan.stages":
        return _render_stage_table(result)
    if result.check_id == "strict.onmck.min_price":
        return _render_onmck_min_price(result)
    if result.check_id == "strict.onmck.arithmetic":
        return _render_onmck_arithmetic(result)
    label = STATUS_LABELS[result.status]
    lines = [f"- <b>{_human_text(result.title)}</b> - {label}. {_human_text(result.report_text)}"]
    summary_lines = result.details.get("summary_lines") if result.details else None
    if isinstance(summary_lines, list):
        for item in summary_lines:
            if item:
                text = _wrap_doc_badges(_human_text(str(item)))
                lines.append(f"  - {text}")
    if result.check_id == "strict.funding_source" and result.details:
        lines.extend(_field_lines(result.details, ["schedule_application", "contract_draft"]))
    if result.check_id == "strict.securities" and result.details:
        lines.extend(_security_lines(result.details))
    if result.check_id == "strict.contract.attachments" and result.details:
        lines.extend(_attachment_lines(result.details))
    if result.check_id == "strict.request.attachments" and result.details:
        missing_attachments = result.details.get("missing_attachments")
        if isinstance(missing_attachments, list):
            for title in missing_attachments:
                if title:
                    lines.append(
                        f"  - В обращении указано приложение, но файл не загружен: {_human_text(str(title))}."
                    )
    return lines


def _render_onmck_min_price(result: CheckResult) -> list[str]:
    lines = [
        f"- <b>{_human_text(result.title)}</b> - {STATUS_LABELS[result.status]}. "
        f"{_human_text(result.report_text)}"
    ]
    rows = result.details.get("price_rows") if result.details else None
    if not isinstance(rows, list) or not rows:
        return _render_titled_result_without_special_case(result)
    for row in rows:
        if not isinstance(row, dict):
            continue
        quantity = ""
        if row.get("quantity"):
            quantity = f"; количество <b>{row['quantity']}</b>"
            if row.get("unit"):
                quantity += f" {row['unit']}"
        lines.append("")
        lines.append(f"  - <b>{_human_text(str(row.get('item') or 'Позиция'))}</b>{quantity}")
        lines.append(
            "    Выбранная минимальная цена: "
            f"<b>{_report_money(row.get('selected'))}</b> "
            f"({_human_text(str(row.get('minimum_source') or 'поставщик не определён'))})"
        )
        lines.append("    Цены поставщиков:")
        for supplier in row.get("suppliers") or []:
            if not isinstance(supplier, dict):
                continue
            price = supplier.get("price")
            price_text = (
                _report_money(price)
                if price not in (None, "")
                else _human_text(str(supplier.get("raw_price") or "не указана"))
            )
            if supplier.get("derived_from_total"):
                price_text += " (рассчитано по стоимости)"
            lines.append(
                f"    - {_human_text(str(supplier.get('label') or 'Поставщик'))}: "
                f"<b>{price_text}</b>"
            )
        coefficient = row.get("variation_coefficient")
        lines.append(
            "    Коэффициент вариации: "
            f"<b>{_human_text(str(coefficient)) if coefficient else 'не рассчитан'}</b>"
        )
        lines.append(
            "    Итог: "
            f"<b>{STATUS_LABELS.get(str(row.get('status')), 'ТРЕБУЕТ ПРОВЕРКИ')}</b>"
        )
        if row.get("issue"):
            lines.append(f"    Причина: {_human_text(str(row['issue']))}")
    return lines


def _render_onmck_arithmetic(result: CheckResult) -> list[str]:
    lines = [
        f"- <b>{_human_text(result.title)}</b> - {STATUS_LABELS[result.status]}. "
        f"{_human_text(result.report_text)}"
    ]
    details = result.details or {}
    rows = details.get("arithmetic_rows")
    if not isinstance(rows, list) or not rows:
        return _render_titled_result_without_special_case(result)
    for row in rows:
        if not isinstance(row, dict):
            continue
        quantity = str(row.get("quantity") or "не найдено")
        unit = f" {row['unit']}" if row.get("unit") else ""
        unit_price = _report_money(row.get("unit_price"))
        calculated = _report_money(row.get("calculated"))
        declared = _report_money(row.get("declared"))
        lines.append("")
        lines.append(
            f"  - <b>{_human_text(str(row.get('item') or 'Позиция'))}</b>, "
            f"количество <b>{quantity}</b>{unit}"
        )
        lines.append(f"    Цена за единицу: <b>{unit_price}</b>")
        if row.get("calculated") is not None:
            lines.append(
                f"    Расчёт: <b>{unit_price}</b> × <b>{quantity}</b> = "
                f"<b>{calculated}</b>"
            )
        else:
            lines.append("    Расчёт: не выполнен, недостаточно данных")
        lines.append(f"    Стоимость в ОНМЦК: <b>{declared}</b>")
        lines.append(
            "    Итог: "
            f"<b>{STATUS_LABELS.get(str(row.get('status')), 'ТРЕБУЕТ ПРОВЕРКИ')}</b>"
        )
    lines.extend([
        "",
        f"  Сумма строк: <b>{_report_money(details.get('row_sum'))}</b>",
        f"  Итог ОНМЦК: <b>{_report_money(details.get('onmck_total'))}</b>",
        f"  НМЦК в заявке: <b>{_report_money(details.get('plan_nmck'))}</b>",
    ])
    failed_items = details.get("failed_items")
    incomplete_items = details.get("incomplete_items")
    if isinstance(failed_items, list) and failed_items:
        lines.append("")
        lines.append("  <b>Расхождения в расчёте:</b>")
        lines.extend(f"  - <error>{_human_text(str(item))}</error>" for item in failed_items if item)
    if isinstance(incomplete_items, list) and incomplete_items:
        lines.append("")
        lines.append("  <b>Требуют проверки:</b>")
        lines.extend(f"  - <warn>{_human_text(str(item))}</warn>" for item in incomplete_items if item)
    return lines


def _render_titled_result_without_special_case(result: CheckResult) -> list[str]:
    label = STATUS_LABELS[result.status]
    lines = [f"- <b>{_human_text(result.title)}</b> - {label}. {_human_text(result.report_text)}"]
    summary_lines = result.details.get("summary_lines") if result.details else None
    if isinstance(summary_lines, list):
        for item in summary_lines:
            if item:
                lines.append(f"  - {_wrap_doc_badges(_human_text(str(item)))}")
    return lines


def _report_money(value: object) -> str:
    if value in (None, ""):
        return "не найдено"
    try:
        amount = Decimal(str(value).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return _human_text(str(value))
    formatted = f"{amount:,.2f}".replace(",", " ").replace(".", ",")
    return f"{formatted} руб."


def _render_commercial_offer_content(result: CheckResult) -> list[str]:
    lines = [f"- <b>{_human_text(result.title)}</b> — {STATUS_LABELS[result.status]}. {_human_text(result.report_text)}"]
    offers = result.details.get("offer_summaries") if result.details else None
    if not isinstance(offers, list) or not offers:
        return _render_result(result)
    if isinstance(offers, list) and offers:
        lines.extend([
            "", "| КП | Поставщик | Исходящий номер / дата | Сумма | Позиций |",
            "| :--- | :--- | :--- | :---: | :---: |",
        ])
        for offer in offers:
            if not isinstance(offer, dict):
                continue
            number_date = " / ".join(
                part for part in (str(offer.get("outgoing_number") or "не найден"), str(offer.get("outgoing_date") or "не найдена"))
            )
            lines.append(
                "| {label} | {supplier} | {number_date} | {total} | {items} |".format(
                    label=_table_cell(offer.get("label")),
                    supplier=_table_cell(offer.get("supplier_name") or "не найден"),
                    number_date=_table_cell(number_date),
                    total=_table_cell(offer.get("total_amount")),
                    items=_table_cell(offer.get("items_count")),
                )
            )
        unresolved = []
        for offer in offers:
            if not isinstance(offer, dict):
                continue
            fields = []
            if not offer.get("supplier_name"):
                fields.append("поставщик")
            if not offer.get("inn"):
                fields.append("ИНН")
            if not offer.get("has_delivery_term"):
                fields.append("срок")
            if not offer.get("has_delivery_place"):
                fields.append("место")
            if not offer.get("has_vat"):
                fields.append("НДС")
            if fields:
                unresolved.append((str(offer.get("label") or "КП"), fields))
        if unresolved:
            lines.append("")
            lines.append("  <b>Не указаны в документе либо не распознаны:</b>")
            for label, fields in unresolved:
                lines.append(f"  - {label}: {', '.join(fields)}.")

        arithmetic_rows = result.details.get("arithmetic_rows") if result.details else None
        if isinstance(arithmetic_rows, list) and arithmetic_rows:
            lines.extend([
                "",
                "  <b>Проверка арифметики КП:</b>",
                "",
                "| КП | Проверено строк | Ошибок строк | Сумма строк | Итог КП | Статус |",
                "| :--- | ---: | ---: | ---: | ---: | :---: |",
            ])
            for row in arithmetic_rows:
                if not isinstance(row, dict):
                    continue
                lines.append(
                    "| {label} | {checked} из {count} | {errors} | {calculated} | {declared} | {status} |".format(
                        label=_table_cell(row.get("label")),
                        checked=_table_cell(row.get("checked_rows")),
                        count=_table_cell(row.get("items_count")),
                        errors=_table_cell(row.get("row_errors")),
                        calculated=_table_cell(row.get("calculated_total") or "не рассчитана"),
                        declared=_table_cell(row.get("declared_total") or "не найден"),
                        status=STATUS_LABELS.get(str(row.get("status")), str(row.get("status") or "")),
                    )
                )
            arithmetic_issues = [
                str(issue)
                for row in arithmetic_rows if isinstance(row, dict)
                for issue in [*(row.get("failures") or []), *(row.get("manual_review") or [])]
            ]
            if arithmetic_issues:
                lines.append("")
                lines.append("  <b>Замечания по арифметике:</b>")
                lines.extend(f"  - {_human_text(issue)}" for issue in arithmetic_issues[:6])
                if len(arithmetic_issues) > 6:
                    lines.append(
                        f"  - ещё {len(arithmetic_issues) - 6}; полный список сохранён в файле подробных результатов."
                    )

        warning_groups = result.details.get("parser_warning_groups") if result.details else None
        compact_warnings, total_warning_count = _compact_commercial_offer_warnings(warning_groups)
        if compact_warnings:
            lines.append("")
            lines.append("  <b>Особенности распознавания:</b>")
            for label, warning_text in compact_warnings:
                lines.append(f"  - <b>{_human_text(label)}</b>: <warn>{_human_text(warning_text)}</warn>")
            if total_warning_count > len(compact_warnings):
                lines.append(
                    f"  - показаны основные замечания; полный список ({total_warning_count}) сохранён в файле подробных результатов."
                )
        trademarks = sorted({
            trademark
            for offer in offers if isinstance(offer, dict)
            for trademark in (offer.get("trademarks") or [])
            if trademark
        })
        if trademarks:
            lines.append("  Распознаны товарные знаки: " + ", ".join(trademarks) + ".")
    return lines


def _render_commercial_offer_comparison(result: CheckResult) -> list[str]:
    lines = ["", f"- <b>{_human_text(result.title)}</b> — {STATUS_LABELS[result.status]}. {_human_text(result.report_text)}"]
    details = result.details or {}
    comparison_rows = details.get("comparison_rows")
    if not comparison_rows and not details.get("manual_review") and not details.get("failures"):
        return ["", *_render_result(result)]
    source_warnings = details.get("source_warnings")
    if isinstance(source_warnings, list) and source_warnings:
        lines.extend(f"  <warn>{_human_text(str(warning))}</warn>" for warning in source_warnings[:3])
    source_reference_rows = details.get("source_reference_rows")
    if isinstance(source_reference_rows, list) and source_reference_rows:
        lines.extend([
            "",
            "  <b>Реквизиты и суммы КП в ОНМЦК:</b>",
            "",
            "| Источник | КП: номер / дата | ОНМЦК: номер / дата | Сумма КП | Сумма ОНМЦК | Статус |",
            "| :--- | :--- | :--- | ---: | ---: | :---: |",
        ])
        for row in source_reference_rows:
            if not isinstance(row, dict):
                continue
            lines.append(
                "| {source} | {offer} | {nmck} | {offer_total} | {nmck_total} | {status} |".format(
                    source=_table_cell(row.get("source")),
                    offer=_table_cell(row.get("offer_requisites")),
                    nmck=_table_cell(row.get("nmck_requisites")),
                    offer_total=_table_cell(row.get("offer_total") or "—"),
                    nmck_total=_table_cell(row.get("nmck_total") or "—"),
                    status=STATUS_LABELS.get(str(row.get("status")), str(row.get("status") or "")),
                )
            )
    if isinstance(comparison_rows, list) and comparison_rows:
        lines.extend([
            "",
            "| Позиция | КП №1 | КП №2 | КП №3 | Минимум ОНМЦК | Минимум КП | Коэф. вариации | Статус |",
            "| :--- | ---: | ---: | ---: | ---: | ---: | ---: | :---: |",
        ])
        for row in comparison_rows:
            if not isinstance(row, dict):
                continue
            lines.append(
                "| {item} | {offer_1} | {offer_2} | {offer_3} | {selected} | {actual} | {coefficient} | {status} |".format(
                    item=_table_cell(row.get("item")),
                    offer_1=_table_cell(row.get("offer_1") or "—"),
                    offer_2=_table_cell(row.get("offer_2") or "—"),
                    offer_3=_table_cell(row.get("offer_3") or "—"),
                    selected=_table_cell(row.get("selected_min") or "—"),
                    actual=_table_cell(row.get("actual_min") or "—"),
                    coefficient=_table_cell(row.get("coefficient") or "не рассчитан"),
                    status=STATUS_LABELS.get(str(row.get("status")), str(row.get("status") or "")),
                )
            )
    quantity_unit_rows = details.get("quantity_unit_rows")
    if isinstance(comparison_rows, list) and comparison_rows and isinstance(quantity_unit_rows, list):
        lines.extend([
            "",
            "  <b>Цена за единицу и количество:</b>",
            "",
            "| Позиция | ОНМЦК №1 | КП №1 | ОНМЦК №2 | КП №2 | ОНМЦК №3 | КП №3 | Статус |",
            "| :--- | ---: | ---: | ---: | ---: | ---: | ---: | :---: |",
        ])
        for index, price_row in enumerate(comparison_rows):
            if not isinstance(price_row, dict):
                continue
            quantity_row = quantity_unit_rows[index] if index < len(quantity_unit_rows) else {}
            if not isinstance(quantity_row, dict):
                quantity_row = {}
            lines.append(
                "| {item} | {nmck_1} | {offer_1} | {nmck_2} | {offer_2} | {nmck_3} | {offer_3} | {status} |".format(
                    item=_table_cell(price_row.get("item")),
                    nmck_1=_unit_price_with_quantity(price_row.get("nmck_1"), price_row.get("nmck_quantity") or quantity_row.get("nmck")),
                    offer_1=_unit_price_with_quantity(price_row.get("offer_1"), price_row.get("offer_1_quantity") or quantity_row.get("offer_1")),
                    nmck_2=_unit_price_with_quantity(price_row.get("nmck_2"), price_row.get("nmck_quantity") or quantity_row.get("nmck")),
                    offer_2=_unit_price_with_quantity(price_row.get("offer_2"), price_row.get("offer_2_quantity") or quantity_row.get("offer_2")),
                    nmck_3=_unit_price_with_quantity(price_row.get("nmck_3"), price_row.get("nmck_quantity") or quantity_row.get("nmck")),
                    offer_3=_unit_price_with_quantity(price_row.get("offer_3"), price_row.get("offer_3_quantity") or quantity_row.get("offer_3")),
                    status=STATUS_LABELS.get(str(price_row.get("status")), str(price_row.get("status") or "")),
                )
            )
    if isinstance(quantity_unit_rows, list) and quantity_unit_rows:
        lines.extend([
            "",
            "  <b>Количество и единицы измерения:</b>",
            "",
            "| Позиция | ОНМЦК | ООЗ | КП №1 | КП №2 | КП №3 | Статус |",
            "| :--- | :---: | :---: | :---: | :---: | :---: | :---: |",
        ])
        for row in quantity_unit_rows:
            if not isinstance(row, dict):
                continue
            lines.append(
                "| {item} | {nmck} | {ooz} | {offer_1} | {offer_2} | {offer_3} | {status} |".format(
                    item=_table_cell(row.get("item")),
                    nmck=_table_cell(row.get("nmck")),
                    ooz=_table_cell(row.get("ooz")),
                    offer_1=_table_cell(row.get("offer_1") or "—"),
                    offer_2=_table_cell(row.get("offer_2") or "—"),
                    offer_3=_table_cell(row.get("offer_3") or "—"),
                    status=STATUS_LABELS.get(str(row.get("status")), str(row.get("status") or "")),
                )
            )
    manual = details.get("manual_review") or []
    failures = details.get("failures") or []
    criteria = details.get("criteria") or []
    quantity_unit_issues = {
        str(issue)
        for criterion in criteria
        if isinstance(criterion, dict) and criterion.get("key") in {"quantity", "unit"}
        for issue in (criterion.get("issues") or [])
    }
    manual_to_render = [value for value in manual if str(value) not in quantity_unit_issues]
    failures_to_render = [value for value in failures if str(value) not in quantity_unit_issues]
    if isinstance(manual_to_render, list) and manual_to_render:
        lines.append("  <b>Дополнительные причины ручной сверки:</b>")
        for reason in list(dict.fromkeys(_human_text(str(value)) for value in manual_to_render if value))[:5]:
            lines.append(f"  - {reason}")
        if len(set(str(value) for value in manual_to_render if value)) > 5:
            lines.append("  - Остальные причины сохранены в файле подробных результатов.")
    if isinstance(failures_to_render, list) and failures_to_render:
        lines.append("  <error>Подтверждённые расхождения:</error>")
        lines.extend(f"  - {_human_text(value)}" for value in failures_to_render[:6])
        if len(failures_to_render) > 6:
            lines.append(f"  - ещё {len(failures_to_render) - 6}; полный список сохранён в файле подробных результатов.")
    return lines


def _compact_commercial_offer_warnings(
    groups: object,
) -> tuple[list[tuple[str, str]], int]:
    if not isinstance(groups, list):
        return [], 0
    result: list[tuple[str, str]] = []
    total = 0
    for group in groups:
        if not isinstance(group, dict):
            continue
        label = str(group.get("label") or "КП")
        warnings = [str(value) for value in (group.get("warnings") or []) if value]
        total += len(warnings)
        useful = [warning for warning in warnings if not _is_repeated_offer_absence(warning)]
        useful.sort(key=_commercial_offer_warning_priority)
        if useful:
            compact = "; ".join(_short_warning(warning) for warning in useful[:3])
            result.append((label, compact))
    return result, total


def _is_repeated_offer_absence(value: str) -> bool:
    normalized = value.casefold().replace("ё", "е")
    return any(
        marker in normalized
        for marker in (
            "место поставки/оказания услуг не указ",
            "место поставки/оказания услуг в документе не указ",
            "авансовый платеж в документе не указан",
            "авансовый платеж не указан явно",
            "явный авансовый платеж",
        )
    )


def _commercial_offer_warning_priority(value: str) -> int:
    normalized = value.casefold().replace("ё", "е")
    priorities = (
        ("vlm не вернула", "jsondecode", "ошиб"),
        ("агрегат", "итоговая строка"),
        ("продолж", "обрез", "перенес", "строк"),
        ("ндс",),
    )
    for index, markers in enumerate(priorities):
        if any(marker in normalized for marker in markers):
            return index
    return len(priorities)


def _short_warning(value: str, limit: int = 260) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else f"{text[:limit].rstrip()}..."


def _unique_issue_positions(values: list[object]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = " ".join(str(value).split())
        position = text.split(":", 1)[0] if ":" in text else text
        if position and position not in result:
            result.append(position)
    return result


def _unit_price_with_quantity(price: object, quantity: object) -> str:
    if price in (None, "", "—") or quantity in (None, "", "—"):
        return "—"
    return f"{_table_cell(price)} × {_table_cell(quantity)}"


def _table_cell(value: object) -> str:
    display = "не указано" if value is None or value == "" else value
    return " ".join(str(display).replace("|", "/").split())


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


def _render_ktru_characteristic_rows(result: CheckResult) -> list[str]:
    details = result.details or {}
    rows = details.get("characteristic_rows")
    if not isinstance(rows, list) or not rows:
        return []
    lines = [f"- <b>{_human_text(result.title)}</b> - {STATUS_LABELS[result.status]}. {_human_text(result.report_text)}"]
    summary_lines = details.get("summary_lines")
    if isinstance(summary_lines, list):
        for item in summary_lines:
            if item:
                lines.append(f"  - {_human_text(str(item))}")
    identity_rows = details.get("item_identity_rows")
    if isinstance(identity_rows, list):
        for row in identity_rows:
            if not isinstance(row, dict) or row.get("status") in {"passed", "not_checked"}:
                continue
            item_name = row.get("item_name") or "позиция"
            code = row.get("ktru_code") or "КТРУ не найден"
            if row.get("name_status") in {"failed", "manual_review"}:
                lines.append(
                    f"  - <b>{_human_text(str(item_name))}</b>; КТРУ {_human_text(str(code))}: "
                    f"наименование в КТРУ — {_human_text(str(row.get('ktru_name') or 'не найдено'))}."
                )
            if row.get("unit_status") in {"failed", "manual_review"}:
                lines.append(
                    f"  - <b>{_human_text(str(item_name))}</b>; единица товара: "
                    f"ООЗ — {_human_text(str(row.get('ooz_unit') or 'не указана'))}; "
                    f"КТРУ — {_human_text(str(row.get('ktru_unit') or 'не найдена'))}."
                )
    lines.append("")
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = STATUS_LABELS.get(str(row.get("status")), str(row.get("status") or ""))
        ktru_code = row.get("ktru_code") or "КТРУ не найден"
        item_name = row.get("item_name") or "позиция"
        char_name = row.get("characteristic_name") or "характеристика"
        ooz_value = row.get("ooz_value") or "не найдено"
        ooz_unit = row.get("ooz_unit") or "не указана"
        legal_unit = row.get("ktru_unit") or "не указана"
        message = row.get("message") or ""
        lines.append(
            f"  - <b>{_human_text(str(item_name))}</b>; КТРУ {_human_text(str(ktru_code))}; "
            f"характеристика: {_human_text(str(char_name))} — <b>{status}</b>."
        )
        if str(row.get("status") or "") == "passed":
            lines.append(
                f"    В ООЗ: {_human_text(str(ooz_value))}; единица: "
                f"{_human_text(str(ooz_unit))}. Значение допустимо в КТРУ."
            )
        else:
            if message and message != "ОК":
                lines.append(f"    {_human_text(str(message))}.")
            similar_name = row.get("similar_ooz_characteristic")
            if similar_name:
                lines.append(
                    "    Возможно, в ООЗ допущена ошибка в наименовании: "
                    f"«{_human_text(str(similar_name))}»."
                )
            if ooz_value != "не найдено" or ooz_unit != "не указана" or legal_unit != "не указана":
                lines.append(
                    f"    В ООЗ: {_human_text(str(ooz_value))}; единица: "
                    f"{_human_text(str(ooz_unit))}. В КТРУ: {_human_text(str(legal_unit))}."
                )
    return lines


def _render_ktru_additional_rows(result: CheckResult) -> list[str]:
    details = result.details or {}
    assessments = details.get("assessments")
    if not isinstance(assessments, list) or not assessments:
        return []
    lines = [f"- <b>{_human_text(result.title)}</b> - {STATUS_LABELS[result.status]}. {_human_text(result.report_text)}"]
    ooz_state = details.get("ooz_justification_state") if isinstance(details.get("ooz_justification_state"), dict) else {}
    decision_labels = {
        "allowed": "ОК",
        "restricted": "ПРЕДУПРЕЖДЕНИЕ",
        "missing_justification": "ОШИБКА",
        "manual_review": "ТРЕБУЕТ ПРОВЕРКИ",
    }
    if ooz_state.get("found"):
        lines.append("  - Обоснование дополнительных характеристик найдено в ООЗ.")
    elif ooz_state.get("partial"):
        lines.append("  - Таблица обоснований найдена в ООЗ, но извлечена не полностью.")
    else:
        lines.append("  - <note>Явное обоснование дополнительных характеристик в ООЗ не найдено.</note>")

    rows = details.get("additional_rows")
    rows = rows if isinstance(rows, list) else []
    indexed_assessments = [
        (assessment, rows[index] if index < len(rows) and isinstance(rows[index], dict) else {})
        for index, assessment in enumerate(assessments)
        if isinstance(assessment, dict)
    ]
    grouped: dict[tuple[str, str, str], dict[str, object]] = {}
    decision_rank = {"allowed": 0, "manual_review": 1, "missing_justification": 2, "restricted": 3}
    for assessment, row in indexed_assessments:
        okpd_rule = assessment.get("okpd_rule") if isinstance(assessment.get("okpd_rule"), dict) else {}
        plan_regime = assessment.get("plan_regime") if isinstance(assessment.get("plan_regime"), dict) else {}
        justification = assessment.get("justification") if isinstance(assessment.get("justification"), dict) else {}
        key = (
            str(assessment.get("item") or "позиция"),
            str(assessment.get("ktru_code") or "не найден"),
            str(okpd_rule.get("code") or "не найден"),
        )
        current = grouped.setdefault(key, {
            "count": 0,
            "sample_characteristic": None,
            "decision": assessment.get("decision") or "manual_review",
            "field_code": plan_regime.get("field_code"),
            "field_value": plan_regime.get("field_value"),
            "regime": plan_regime.get("regime"),
            "regime_status": plan_regime.get("status"),
            "table_id": plan_regime.get("table_id"),
            "position": plan_regime.get("position"),
            "rule_reason": okpd_rule.get("reason") or assessment.get("reason"),
            "justification_status": justification.get("status"),
            "justification_source": justification.get("source"),
            "quote": justification.get("quote"),
            "units": [],
        })
        if _is_reportable_additional_characteristic(assessment, row):
            current["count"] = int(current["count"]) + 1
            if not current.get("sample_characteristic"):
                current["sample_characteristic"] = _human_text(
                    str(assessment.get("characteristic") or "")
                ).strip()
        if decision_rank.get(str(assessment.get("decision")), 1) > decision_rank.get(str(current["decision"]), 1):
            current["decision"] = assessment.get("decision")
        for field_name, value in (("rule_reason", okpd_rule.get("reason") or assessment.get("reason")), ("quote", justification.get("quote"))):
            if value and not current.get(field_name):
                current[field_name] = value

    if grouped:
        lines.extend([
            "", "| Позиция | КТРУ / ОКПД2 | ПП №1875 и режим ПГ | Доп. характеристик | Итог |",
            "| :--- | :--- | :--- | :---: | :---: |",
        ])
        for (item_name, ktru_code, okpd2_code), item in grouped.items():
            regime = _compact_pp1875_regime(item)
            count = int(item["count"]) or 1
            lines.append(
                f"| {_table_cell(item_name)} | "
                f"{_table_cell(ktru_code)}<br>{_table_cell(okpd2_code)} | "
                f"{_table_cell(regime)} | {count} | "
                f"{decision_labels.get(str(item['decision']), 'ТРЕБУЕТ ПРОВЕРКИ')} |"
            )
        for (item_name, ktru_code, okpd2_code), item in grouped.items():
            count = int(item["count"]) or 1
            sample = _human_text(str(item.get("sample_characteristic") or "")).strip()
            rule_reason = _human_text(str(item.get("rule_reason") or "")).strip()
            fallback_quote = ooz_state.get("quote") if len(grouped) == 1 else None
            quote = _human_text(str(item.get("quote") or fallback_quote or "")).strip()
            source = item.get("justification_source")
            justification_status = item.get("justification_status")

            lines.extend(["", f"<b>{_human_text(item_name)}</b>"])
            lines.append(f"  - КТРУ: <b>{_human_text(ktru_code)}</b>; ОКПД2: <b>{_human_text(okpd2_code)}</b>.")
            if sample and count > 1:
                remaining = count - 1
                lines.append(
                    f"  - Дополнительные характеристики: {_human_text(sample)}; "
                    f"ещё {remaining} {_characteristic_word(remaining)}."
                )
            elif sample:
                lines.append(f"  - Дополнительная характеристика: {_human_text(sample)}.")
            else:
                lines.append(f"  - Дополнительных характеристик: <b>{count}</b>.")
            if count > 5:
                lines.append("  - Полный перечень характеристик сохранён в <b>checks.json</b>.")
            if rule_reason:
                lines.append(f"  - ПП №1875: {_human_text(rule_reason)}")
            if item.get("field_value"):
                lines.append(
                    f"  - План-график, поле {item.get('field_code') or '17.1/17.2'}: "
                    f"{_human_text(str(item['field_value']))}."
                )
            if quote:
                lines.append(f"  - Обоснование из ООЗ: {_human_text(quote[:240])}")
            elif source == "ooz" or justification_status == "partial":
                lines.append("  - Обоснование найдено в ООЗ, но не привязано к этой позиции.")
            else:
                lines.append("  - Обоснование в ООЗ не найдено.")
            lines.append(
                "  - Итог: <b>"
                f"{decision_labels.get(str(item['decision']), 'ТРЕБУЕТ ПРОВЕРКИ')}"
                "</b>."
            )
    return lines


def _characteristic_word(count: int) -> str:
    if 11 <= count % 100 <= 14:
        return "характеристик"
    last_digit = count % 10
    if last_digit == 1:
        return "характеристика"
    if 2 <= last_digit <= 4:
        return "характеристики"
    return "характеристик"


def _compact_pp1875_regime(item: dict[str, object]) -> str:
    table_id = str(item.get("table_id") or "")
    appendix = {"table_01": "Прил. №1", "table_02": "Прил. №2"}.get(table_id)
    position = str(item.get("position") or "")
    field_code = str(item.get("field_code") or "")
    status = str(item.get("regime_status") or "")
    regime = str(item.get("regime") or "")

    if not appendix:
        status_label = {
            "confirmed": "подтверждён",
            "not_required": "не требуется",
            "missing": "не заполнен",
            "ambiguous": "неоднозначен",
            "registry_unavailable": "реестр недоступен",
        }.get(status, "режим не подтверждён")
        return f"{field_code}: {status_label}" if field_code else status_label
    position_text = f", поз. {position}" if position else ""
    special = _is_special_pp1875_position(table_id, position)
    if not special:
        return f"{appendix}{position_text}: специальный запрет не применяется"
    regime_label = regime or ("запрет" if table_id == "table_01" else "ограничение")
    adjective = "специальное" if regime_label == "ограничение" else "специальный"
    status_label = {
        "confirmed": "подтверждён",
        "missing": "не подтверждён",
        "ambiguous": "неоднозначен",
        "registry_unavailable": "не проверен",
    }.get(status, "не подтверждён")
    field_text = f"; {field_code} {status_label}" if field_code else ""
    return f"{appendix}{position_text}: {adjective} {regime_label}{field_text}"


def _is_special_pp1875_position(table_id: str, position: str) -> bool:
    match = re.search(r"\d+", position)
    if not match:
        return False
    number = int(match.group(0))
    if table_id == "table_01":
        return number in {25, 26, 32}
    return table_id == "table_02" and 191 <= number <= 361


def _is_reportable_additional_characteristic(
    assessment: dict[str, object],
    row: dict[str, object],
) -> bool:
    """Do not render headings or justification prose as characteristics."""
    name = " ".join(str(assessment.get("characteristic") or "").split())
    value = " ".join(str(row.get("value") or "").split())
    normalized = name.casefold().replace("ё", "е").strip(" *:.;")
    value_normalized = value.casefold().replace("ё", "е").strip(" *:.;")
    if normalized == "дополнительные характеристики" and value_normalized == normalized:
        return False
    if len(name) > 500 or "обоснование применения дополнительных характеристик" in normalized:
        return False
    return True


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
        if key not in {"summary_lines", *RAW_DETAIL_KEYS} and value not in (None, "", [], {}, False)
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
        if _looks_like_raw_payload(value):
            return f"{len(value)} полей; подробности сохранены в checks.json"
        parts = [
            f"{_human_label(key)}: {_human_value(item)}"
            for key, item in value.items()
            if item not in (None, "", [], {}, False)
        ]
        if not parts:
            return ""
        text = "; ".join(parts)
        return text[:500] + ("..." if len(text) > 500 else "")
    if isinstance(value, (list, tuple, set)):
        items = [item for item in value if item not in (None, "", [], {}, False)]
        if not items:
            return ""
        if any(isinstance(item, (dict, list, tuple, set)) for item in items):
            return f"{len(items)} записей; подробности сохранены в checks.json"
        text = ", ".join(_human_value(item) for item in items[:8])
        if len(items) > 8:
            text += f", ещё {len(items) - 8}"
        return text[:500] + ("..." if len(text) > 500 else "")
    if isinstance(value, bool):
        return "да" if value else "нет"
    return _human_text(str(value))


def _looks_like_raw_payload(value: dict[object, object]) -> bool:
    raw_markers = {
        "raw",
        "raw_text",
        "raw_rows",
        "logical_rows",
        "compact_json",
        "compact_markdown",
        "cells_by_col",
        "cells_by_header",
    }
    return any(str(key) in raw_markers for key in value)


def _human_text(text: str) -> str:
    result = text
    replacements = {
        **DOCUMENT_LABELS,
        **FIELD_LABELS,
        **ATTACHMENT_KIND_LABELS,
        **TECHNICAL_TEXT_LABELS,
    }
    for source, target in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        result = re.sub(rf"(?<![\w]){re.escape(source)}(?![\w])", target, result)
    result = re.sub(
        r"(?i)товарный\s+знак\s+товарный\s+знак\s*:",
        "товарный знак:",
        result,
    )
    return result
