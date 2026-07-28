from summary_model.checks.models import CheckResult, ProcurementChecksReport
from summary_model.checks.report import build_checks_report_text


def _check_result(
    check_id: str,
    title: str,
    status: str,
    report_text: str,
    *,
    details: dict | None = None,
) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        title=title,
        severity="warning" if status == "warning" else "info",
        status=status,
        mode="strict",
        message=report_text,
        report_text=report_text,
        details=details or {},
    )


def _report(*results: CheckResult) -> ProcurementChecksReport:
    return ProcurementChecksReport.from_results(
        package_id="report-rendering-test",
        results=list(results),
    )


def test_report_does_not_dump_raw_debug_payloads():
    report = _report(
        _check_result(
            "debug.raw_payload",
            "Тестовая проверка",
            "warning",
            "Найдены данные, требующие проверки.",
            details={
                "raw_rows": [
                    {
                        "stage_number": "1",
                        "raw_text": "очень длинный технический фрагмент",
                    }
                ],
                "compact_json": {"rows": [{"supplier_prices": [1, 2, 3]}]},
                "required": 1,
            },
        )
    )

    text = build_checks_report_text(report)

    assert "[{" not in text
    assert "stage_number" not in text
    assert "raw_text" not in text
    assert "compact_json" not in text
    assert "supplier_prices" not in text
    assert "требуется: 1" in text


def test_report_keeps_stage_summary_as_readable_table():
    report = _report(
        _check_result(
            "strict.plan.stages",
            "Этапы исполнения",
            "passed",
            "Порядок и сроки этапов согласованы.",
            details={
                "stage_tables": [
                    {
                        "title": "Заявка в план-график (ПГ)",
                        "kind": "standard",
                        "rows": [
                            {
                                "number": "1",
                                "name": "Поставка лицензий",
                                "term": "по 13.07.2026",
                                "quantity": "1 усл. ед.",
                                "price": "Не выделена",
                            }
                        ],
                    },
                    {
                        "title": "Обоснование НМЦК (ОНМЦК)",
                        "kind": "nmck",
                        "rows": [
                            {
                                "number": "1",
                                "name": "Поставка лицензий",
                                "price": "2 000 000.00",
                                "share": "66.67%",
                            }
                        ],
                    },
                ]
            },
        )
    )

    text = build_checks_report_text(report)

    assert "#### 📌 Таблица 1: Заявка в план-график (ПГ)" in text
    assert "| 1 | Поставка лицензий | по 13.07.2026 | 1 усл. ед. | Не выделена |" in text
    assert "#### 📌 Таблица 2: Обоснование НМЦК (ОНМЦК)" in text
    assert "| 1 | Поставка лицензий | 2 000 000.00 | 66.67% |" in text
    assert "[{" not in text


def test_report_uses_russian_labels_for_commercial_offers():
    report = _report(
        _check_result(
            "manual.commercial_offers.content",
            "Проверка КП",
            "manual_review",
            "Коммерческие предложения не приложены. Содержательная проверка КП невозможна.",
            details={"summary_lines": ["КП не приложены."]},
        ),
        _check_result(
            "manual.commercial_offers.onmck",
            "Сверка КП с ОНМЦК",
            "manual_review",
            "Коммерческие предложения не приложены. Сверка КП с ОНМЦК невозможна.",
            details={"summary_lines": ["КП не приложены."]},
        ),
    )

    text = build_checks_report_text(report)

    assert "6) Коммерческие предложения:" in text
    assert "КП не приложены" in text
    assert "commercial_offer" not in text
    assert "manual.commercial" not in text


def test_report_compacts_commercial_offer_fields_and_additional_ktru_values():
    report = _report(
        _check_result(
            "manual.commercial_offers.content",
            "Проверка КП",
            "manual_review",
            "Часть обязательных реквизитов или строк КП не распознана.",
            details={
                "offer_summaries": [
                    {
                        "label": "КП №1",
                        "supplier_name": None,
                        "inn": None,
                        "outgoing_number": "К-033",
                        "outgoing_date": "2026-04-27",
                        "total_amount": "100.00",
                        "items_count": 8,
                        "has_delivery_term": False,
                        "has_delivery_place": False,
                        "has_vat": False,
                        "trademarks": ["YADRO", "DEPO"],
                    }
                ]
            },
        ),
        _check_result(
            "manual.ktru.additional",
            "Дополнительные характеристики КТРУ",
            "warning",
            "Найдены дополнительные характеристики.",
            details={
                "ooz_justification_state": {"found": True, "quote": "Совместимость"},
                "contract_justification_state": {"found": True},
                "assessments": [
                    {
                        "item": "Сервер",
                        "ktru_code": "26.20.14.000-00000189",
                        "characteristic": "RAID 0",
                        "okpd_rule": {"code": "26.20.14.000"},
                        "plan_regime": {"field_code": "17.2", "status": "confirmed"},
                        "justification": {"status": "found", "source": "ooz"},
                        "decision": "restricted",
                    },
                    {
                        "item": "Сервер",
                        "ktru_code": "26.20.14.000-00000189",
                        "characteristic": "RAID 1",
                        "okpd_rule": {"code": "26.20.14.000"},
                        "plan_regime": {"field_code": "17.2", "status": "confirmed"},
                        "justification": {"status": "found", "source": "ooz"},
                        "decision": "restricted",
                    },
                ]
            },
        ),
    )

    text = build_checks_report_text(report)

    assert "| КП №1 | не найден | К-033 / 2026-04-27 | 100.00 | 8 |" in text
    assert "Не указаны в документе либо не распознаны:" in text
    assert "КП №1: поставщик, ИНН, срок, место, НДС." in text
    assert "Распознаны товарные знаки: DEPO, YADRO." in text
    assert "| Сервер / 26.20.14.000-00000189 |" in text
    assert "| 17.2: confirmed | 2 | найдено в ООЗ | ПРЕДУПРЕЖДЕНИЕ |" in text
    assert "Краткая цитата обоснования: Совместимость" in text


def test_report_renders_commercial_offer_comparison_as_one_compact_table():
    report = _report(
        _check_result(
            "manual.commercial_offers.onmck",
            "Сверка КП с ОНМЦК",
            "manual_review",
            "Часть строк КП требует ручной проверки.",
            details={
                "source_warnings": ["Поставщик1: КП сопоставлено по порядку загрузки"],
                "comparison_rows": [
                    {
                        "item": "Сервер",
                        "offer_1": "10300000.00",
                        "offer_2": "10470000.00",
                        "offer_3": "10245000.00",
                        "selected_min": "10245000.00",
                        "actual_min": "10245000.00",
                        "coefficient": "0.90%",
                        "status": "passed",
                    }
                ],
                "manual_review": [],
                "failures": [],
            },
        )
    )

    text = build_checks_report_text(report)

    assert "| Позиция | КП №1 | КП №2 | КП №3 | Минимум ОНМЦК | Минимум КП | Коэф. вариации | Статус |" in text
    assert "| Сервер | 10300000.00 | 10470000.00 | 10245000.00 | 10245000.00 | 10245000.00 | 0.90% | ОК |" in text
    assert "выбранная минимальная цена" not in text


def test_report_renders_offer_arithmetic_and_compacts_vlm_warnings():
    report = _report(
        _check_result(
            "manual.commercial_offers.content",
            "Проверка КП",
            "warning",
            "КП требуют проверки.",
            details={
                "offer_summaries": [{
                    "label": "КП №1",
                    "supplier_name": "ООО Тест",
                    "outgoing_number": "42",
                    "outgoing_date": "2026-04-27",
                    "total_amount": "100.00",
                    "items_count": 2,
                    "has_delivery_term": True,
                    "has_delivery_place": False,
                    "has_vat": True,
                    "trademarks": [],
                }],
                "arithmetic_rows": [{
                    "label": "КП №1",
                    "items_count": 2,
                    "checked_rows": 2,
                    "row_errors": 0,
                    "calculated_total": "100.00",
                    "declared_total": "100.00",
                    "status": "passed",
                    "failures": [],
                    "manual_review": [],
                }],
                "parser_warning_groups": [{
                    "label": "КП №1",
                    "warnings": [
                        "Место поставки/оказания услуг в документе не указано.",
                        "Агрегатная итоговая строка КП исключена после проверки арифметики.",
                        "НДС по строкам смешанный.",
                    ],
                }],
            },
        )
    )

    text = build_checks_report_text(report)

    assert "Проверка арифметики КП:" in text
    assert "| КП №1 | 2 из 2 | 0 | 100.00 | 100.00 | ОК |" in text
    assert "Особенности распознавания:" in text
    assert "Агрегатная итоговая строка" in text
    assert "НДС по строкам смешанный" in text
    assert "Место поставки/оказания услуг в документе не указано" not in text


def test_report_keeps_allowed_ktru_rows_detailed_beside_restricted_summary():
    report = _report(
        _check_result(
            "manual.ktru.additional",
            "Дополнительные характеристики КТРУ",
            "warning",
            "Часть характеристик подпадает под подтверждённое ограничение.",
            details={
                "ooz_justification_state": {"found": True, "quote": "Обоснование применения дополнительных характеристик"},
                "assessments": [
                    {
                        "item": "Сервер",
                        "ktru_code": "26.20.14.000-00000189",
                        "characteristic": "RAID 0",
                        "okpd_rule": {"code": "26.20.14.120"},
                        "plan_regime": {"field_code": "17.2", "status": "confirmed"},
                        "justification": {"status": "found", "source": "ooz"},
                        "decision": "restricted",
                    },
                    {
                        "item": "Программное обеспечение",
                        "ktru_code": "58.29.11.000-00000003",
                        "characteristic": "Централизованное управление",
                        "okpd_rule": {"code": "58.29.31.000"},
                        "plan_regime": {"status": "not_required"},
                        "justification": {"status": "found", "source": "ooz"},
                        "decision": "allowed",
                    },
                ],
                "additional_rows": [
                    {"value": "Да", "unit": None},
                    {"value": "Наличие", "unit": "логическое"},
                ],
            },
        )
    )

    text = build_checks_report_text(report)

    assert "| Сервер / 26.20.14.000-00000189 | 26.20.14.120 | 17.2: confirmed | 1 |" in text
    assert "| Программное обеспечение / 58.29.11.000-00000003 | Централизованное управление | Наличие | логическое | найдено в ООЗ | ОК |" in text
