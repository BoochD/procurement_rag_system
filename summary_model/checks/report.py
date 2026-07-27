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
    "strict.securities",
    "strict.warranty_security",
    "strict.contract.penalties",
    "strict.smp_sonko_subcontract",
    "strict.contract.attachments",
]

PLAN_REGULATORY_CHECK_ORDER = [
    "strict.application_security",
    "strict.plan.contract_security_limits",
    "strict.plan.warranty_security_limits",
    "strict.plan.national_regime_fields",
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

SPECIAL_CHECKS = set(DOCUMENT_CHECK_ORDER + PLAN_REGULATORY_CHECK_ORDER + INTERNAL_CHECK_ORDER + SEMANTIC_CHECK_ORDER) | {
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
    lines.extend(_render_plan_regulatory_section(by_id))
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
    result = by_id.get("manual.ktru.characteristics")
    lines = ["2) Проверка КТРУ через сервис zakupki.gov.ru:"]
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


def _render_pp1875_section(by_id: dict[str, CheckResult]) -> list[str]:
    result = by_id.get("manual.national_regime_1875")
    lines = ["3) Проверка ОКПД на вхождение в постановление 1875:"]
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
    lines = ["4) Внутренний анализ перечня документов:"]
    for check_id in INTERNAL_CHECK_ORDER:
        result = by_id.get(check_id)
        if result is not None:
            lines.extend(_render_titled_result(result))
            lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def _render_semantic_section(by_id: dict[str, CheckResult]) -> list[str]:
    lines = ["5) Semantic/manual review"]
    for check_id in SEMANTIC_CHECK_ORDER:
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
            return lines
        else:
            lines.extend(_render_result(count))
    content = by_id.get("manual.commercial_offers.content")
    if content is not None:
        lines.extend(_render_commercial_offer_content(content))
    comparison = by_id.get("manual.commercial_offers.onmck")
    if comparison is not None:
        lines.extend(_render_commercial_offer_comparison(comparison))
    return lines


def _render_ktru_characteristics_section(by_id: dict[str, CheckResult]) -> list[str]:
    lines = ["7) Сравнение характеристик из ООЗ с КТРУ на сайте:"]
    characteristics = by_id.get("manual.ktru.characteristics")
    if characteristics is not None:
        if characteristics.status == "passed":
            lines.append("- <ok>Все характеристики ООЗ полностью соответствуют записям КТРУ.</ok>")
        else:
            rendered = _render_ktru_characteristic_rows(characteristics)
            lines.extend(rendered if rendered else _render_result(characteristics))
    additional = by_id.get("manual.ktru.additional")
    if additional is not None and additional.status != "passed":
        lines.append("")
        rendered = _render_ktru_additional_rows(additional)
        if rendered:
            lines.extend(rendered)
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
            lines.extend(str(message).replace(".<ins>", ".\\n<ins>").splitlines())
        else:
            lines.append(str(item.get("code") or item))
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
def _render_semantic_result(result: CheckResult) -> list[str]:
    return _render_titled_result(result)


def _render_stage_table(result: CheckResult) -> list[str]:
    label = STATUS_LABELS[result.status]
    lines = [f"- <b>{_human_text(result.title)}</b> - {label}. {_human_text(result.report_text)}"]
    stage_tables = result.details.get("stage_tables") if result.details else None
    if isinstance(stage_tables, list) and stage_tables:
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
    return lines


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
            lines.append("  <b>Не найдены или не распознаны в КП:</b>")
            for label, fields in unresolved:
                lines.append(f"  - {label}: {', '.join(fields)}.")
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
        lines.append(
            "  <warn>В ОНМЦК не извлечены реквизиты источников; КП сопоставлены "
            "с поставщиками по порядку загрузки.</warn>"
        )
    if isinstance(comparison_rows, list) and comparison_rows:
        lines.extend([
            "",
            "| Позиция | КП №1 | КП №2 | КП №3 | Минимум ОНМЦК | Коэф. вариации | Статус |",
            "| :--- | ---: | ---: | ---: | ---: | ---: | :---: |",
        ])
        for row in comparison_rows:
            if not isinstance(row, dict):
                continue
            lines.append(
                "| {item} | {offer_1} | {offer_2} | {offer_3} | {selected} | {coefficient} | {status} |".format(
                    item=_table_cell(row.get("item")),
                    offer_1=_table_cell(row.get("offer_1") or "—"),
                    offer_2=_table_cell(row.get("offer_2") or "—"),
                    offer_3=_table_cell(row.get("offer_3") or "—"),
                    selected=_table_cell(row.get("selected_min") or "—"),
                    coefficient=_table_cell(row.get("coefficient") or "не рассчитан"),
                    status=STATUS_LABELS.get(str(row.get("status")), str(row.get("status") or "")),
                )
            )
    manual = details.get("manual_review") or []
    failures = details.get("failures") or []
    if isinstance(manual, list) and manual:
        manual_rows = [row for row in comparison_rows or [] if isinstance(row, dict) and row.get("status") == "manual_review"]
        lines.append(f"  Требуют ручной сверки: {len(manual_rows)} позиций. Причины сохранены в checks.json.")
    if isinstance(failures, list) and failures:
        lines.append("  <error>Подтверждённые расхождения:</error>")
        lines.extend(f"  - {_human_text(value)}" for value in failures[:6])
        if len(failures) > 6:
            lines.append(f"  - ещё {len(failures) - 6}; полный список сохранён в checks.json.")
    return lines


def _unique_issue_positions(values: list[object]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = " ".join(str(value).split())
        position = text.split(":", 1)[0] if ":" in text else text
        if position and position not in result:
            result.append(position)
    return result


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
        allowed = row.get("ktru_allowed_values") or []
        legal_unit = row.get("ktru_unit") or "не указана"
        message = row.get("message") or ""
        allowed_text = ", ".join(str(value) for value in allowed) if isinstance(allowed, list) else str(allowed)
        lines.append(f"- <b>{item_name}</b>; КТРУ {ktru_code}; характеристика: {char_name} — {status}")
        lines.append(f"  Значение в ООЗ: {ooz_value}; единица в ООЗ: {ooz_unit}")
        if allowed_text:
            lines.append(f"  Допустимые значения КТРУ: {allowed_text}")
        lines.append(f"  Единица КТРУ: {legal_unit}")
        if message and message != "ОК":
            lines.append(f"  {_human_text(str(message))}")
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def _render_ktru_additional_rows(result: CheckResult) -> list[str]:
    details = result.details or {}
    rows = details.get("additional_rows")
    if not isinstance(rows, list) or not rows:
        return []
    lines = [f"- <b>{_human_text(result.title)}</b> - {STATUS_LABELS[result.status]}. {_human_text(result.report_text)}"]
    justification = details.get("justification_text")
    lines.append(
        f"  - Обоснование включения дополнительных характеристик: "
        f"{justification if justification else 'не найдено'}"
    )
    grouped: dict[tuple[str, str, str, str, str], dict[str, object]] = {}
    rule_reasons: dict[tuple[str, str, str, str], str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = (
            str(row.get("item_name") or "позиция"),
            str(row.get("ktru_code") or "не найден"),
            str(row.get("rule_okpd2_code") or row.get("okpd2_code") or "не найден"),
            str(row.get("characteristic_name") or "характеристика"),
            str(row.get("status") or "manual_review"),
        )
        current = grouped.setdefault(key, {"values": [], "reason": row.get("rule_reason"), "source": row.get("rule_okpd2_source")})
        if row.get("value") and row["value"] not in current["values"]:
            current["values"].append(row["value"])
        reason_key = (key[0], key[1], key[2], key[4])
        if row.get("rule_reason"):
            rule_reasons.setdefault(reason_key, str(row["rule_reason"]))
    lines.extend([
        "", "| Позиция / КТРУ | ОКПД2 для правила | Дополнительная характеристика | Значение ООЗ | Статус |",
        "| :--- | :--- | :--- | :--- | :---: |",
    ])
    for (item_name, ktru_code, okpd2_code, char_name, status_key), item in grouped.items():
        source = item.get("source")
        code_text = f"{okpd2_code}" + (f" ({source})" if source else "")
        lines.append(
            f"| {_table_cell(item_name)} / {ktru_code} | {_table_cell(code_text)} | "
            f"{_table_cell(char_name)} | {_table_cell('; '.join(str(value) for value in item['values']))} | "
            f"{STATUS_LABELS.get(status_key, status_key)} |"
        )
    if rule_reasons:
        lines.extend(["", "<b>Основания применённых правил:</b>"])
        for (item_name, ktru_code, okpd2_code, _status), reason in rule_reasons.items():
            lines.append(
                f"- {item_name}; КТРУ {ktru_code}; ОКПД2 {okpd2_code}: {_human_text(reason)}"
            )
    return lines


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
    replacements = {**DOCUMENT_LABELS, **FIELD_LABELS, **ATTACHMENT_KIND_LABELS}
    for source, target in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        result = re.sub(rf"(?<![\w]){re.escape(source)}(?![\w])", target, result)
    return result
