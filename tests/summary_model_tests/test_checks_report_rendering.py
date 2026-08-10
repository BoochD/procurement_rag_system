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
                        "plan_regime": {
                            "field_code": "17.2",
                            "regime": "ограничение",
                            "status": "confirmed",
                            "table_id": "table_02",
                            "position": "198",
                        },
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
    assert "| Сервер | 26.20.14.000-00000189<br>26.20.14.000 | Прил. №2, поз. 198: специальное ограничение; 17.2 подтверждён | 2 | ПРЕДУПРЕЖДЕНИЕ |" in text
    assert "<b>Сервер</b>" in text
    assert "Дополнительные характеристики: RAID 0; ещё 1 характеристика." in text
    assert "Обоснование из ООЗ: Совместимость" in text


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
                "quantity_unit_rows": [
                    {
                        "item": "Сервер",
                        "nmck": "4 шт.",
                        "ooz": "4 шт.",
                        "offer_1": "4 шт.",
                        "offer_2": "4 шт.",
                        "offer_3": "4 шт.",
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
    assert "| Позиция | ОНМЦК | ООЗ | КП №1 | КП №2 | КП №3 | Статус |" in text
    assert "| Сервер | 4 шт. | 4 шт. | 4 шт. | 4 шт. | 4 шт. | ОК |" in text
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
                        "plan_regime": {
                            "field_code": "17.2",
                            "regime": "ограничение",
                            "status": "confirmed",
                            "table_id": "table_02",
                            "position": "198",
                        },
                        "justification": {"status": "found", "source": "ooz"},
                        "decision": "restricted",
                    },
                    {
                        "item": "Программное обеспечение",
                        "ktru_code": "58.29.11.000-00000003",
                        "characteristic": "Централизованное управление",
                        "okpd_rule": {"code": "58.29.31.000"},
                        "plan_regime": {
                            "regime": "запрет",
                            "status": "not_required",
                            "table_id": "table_01",
                            "position": "146",
                        },
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

    assert "| Сервер | 26.20.14.000-00000189<br>26.20.14.120 | Прил. №2, поз. 198: специальное ограничение; 17.2 подтверждён | 1 | ПРЕДУПРЕЖДЕНИЕ |" in text
    assert "| Программное обеспечение | 58.29.11.000-00000003<br>58.29.31.000 | Прил. №1, поз. 146: специальный запрет не применяется | 1 | ОК |" in text


def test_report_compacts_ktru_values_and_keeps_successful_matches_visible():
    report = _report(
        _check_result(
            "manual.ktru.characteristics",
            "КТРУ-характеристики",
            "failed",
            "Найдены ошибки в значениях или обязательных характеристиках КТРУ.",
            details={
                "summary_lines": ["проверено характеристик: 2", "отсутствующих обязательных: 1"],
                "characteristic_rows": [
                    {
                        "item_name": "Сервер",
                        "ktru_code": "26.20.14.000-00000189",
                        "characteristic_name": "Аппаратная поддержка виртуализации",
                        "status": "passed",
                        "ooz_value": "Да",
                        "ktru_allowed_values": ["Да", "Нет", "Опционально"],
                    },
                    {
                        "item_name": "Программное обеспечение",
                        "ktru_code": "58.29.11.000-00000003",
                        "characteristic_name": "Способ предоставления",
                        "status": "failed",
                        "message": "обязательная характеристика не найдена в ООЗ",
                    },
                ],
            },
        )
    )

    text = build_checks_report_text(report)

    assert "Способ предоставления" in text
    assert "обязательная характеристика не найдена в ООЗ" in text
    assert "Аппаратная поддержка виртуализации" in text
    assert "Значение допустимо в КТРУ" in text
    assert "Допустимые значения КТРУ" not in text


def test_report_localizes_public_technical_terms_and_hides_service_rows():
    report = _report(
        _check_result(
            "manual.national_regime_1875",
            "Национальный режим / ПП №1875",
            "warning",
            "Проверены OKPD2 и KTRU.",
            details={
                "matches": [
                    {"message": "- 26.20.14.120: table_02 позиция 198"},
                ]
            },
        ),
        _check_result(
            "manual.ktru.additional",
            "Дополнительные характеристики КТРУ",
            "passed",
            "Обоснование найдено.",
            details={
                "assessments": [
                    {
                        "item": "Программное обеспечение",
                        "ktru_code": "58.29.11.000-00000003",
                        "characteristic": "Дополнительные характеристики **",
                        "decision": "allowed",
                        "justification": {"status": "found", "source": "ooz"},
                    },
                    {
                        "item": "Программное обеспечение",
                        "ktru_code": "58.29.11.000-00000003",
                        "characteristic": "Централизованное управление",
                        "decision": "allowed",
                        "justification": {"status": "found", "source": "ooz"},
                    },
                ],
                "additional_rows": [
                    {"value": "Дополнительные характеристики **"},
                    {"value": "Наличие"},
                ],
            },
        ),
    )

    text = build_checks_report_text(report)

    assert "5) Смысловая и ручная проверка:" in text
    assert "ОКПД2 и КТРУ" in text
    assert "приложение №2 позиция 198" in text
    assert "17.2: confirmed" not in text
    assert "| Программное обеспечение | 58.29.11.000-00000003<br>не найден | режим не подтверждён | 1 | ОК |" in text


def test_report_renders_onmck_minimum_prices_as_readable_blocks():
    report = _report(
        _check_result(
            "strict.onmck.min_price",
            "Минимальная цена ОНМЦК",
            "passed",
            "Минимальные цены ОНМЦК проверены.",
            details={
                "price_rows": [
                    {
                        "item": "Сервер*",
                        "quantity": "4",
                        "unit": "шт.",
                        "selected": "10245000",
                        "minimum_source": "Исполнитель 3 (письмо № 3)",
                        "suppliers": [
                            {"label": "Исполнитель 1", "price": "10300000"},
                            {"label": "Исполнитель 2", "price": "10470000"},
                            {"label": "Исполнитель 3", "price": "10245000"},
                        ],
                        "variation_coefficient": "1.13%",
                        "status": "passed",
                    }
                ]
            },
        )
    )

    text = build_checks_report_text(report)

    assert "<b>Сервер*</b>; количество <b>4</b> шт." in text
    assert "Выбранная минимальная цена: <b>10 245 000,00 руб.</b>" in text
    assert "- Исполнитель 1: <b>10 300 000,00 руб.</b>" in text
    assert "- Исполнитель 2: <b>10 470 000,00 руб.</b>" in text
    assert "- Исполнитель 3: <b>10 245 000,00 руб.</b>" in text
    assert "Коэффициент вариации: <b>1.13%</b>" in text
    assert "Итог: <b>ОК</b>" in text
    assert "8) Сравнение цен услуг поставщиков в ОНМЦК:" not in text


def test_report_renders_onmck_arithmetic_with_formula_and_kopecks():
    report = _report(
        _check_result(
            "strict.onmck.arithmetic",
            "Арифметика ОНМЦК",
            "passed",
            "Арифметика ОНМЦК проверена.",
            details={
                "arithmetic_rows": [
                    {
                        "item": "Сервер",
                        "quantity": "4",
                        "unit": "шт.",
                        "unit_price": "10245000",
                        "calculated": "40980000",
                        "declared": "40980000",
                        "status": "passed",
                    }
                ],
                "row_sum": "40980000",
                "onmck_total": "40980000",
                "plan_nmck": "40980000",
            },
        )
    )

    text = build_checks_report_text(report)

    assert "<b>Сервер</b>, количество <b>4</b> шт." in text
    assert "Цена за единицу: <b>10 245 000,00 руб.</b>" in text
    assert "Расчёт: <b>10 245 000,00 руб.</b> × <b>4</b> = <b>40 980 000,00 руб.</b>" in text
    assert "Стоимость в ОНМЦК: <b>40 980 000,00 руб.</b>" in text
    assert "Сумма строк: <b>40 980 000,00 руб.</b>" in text


def test_report_prefers_successful_semantic_checks_over_duplicate_strict_rows():
    report = _report(
        _check_result(
            "strict.plan.subject",
            "Предмет закупки",
            "warning",
            "Строгое текстовое сравнение требует проверки.",
        ),
        _check_result(
            "semantic.subject",
            "Предмет закупки",
            "failed",
            "Смысловое расхождение подтверждено.",
        ),
        _check_result(
            "strict.plan.warranty",
            "Гарантийные требования",
            "warning",
            "Формулировки гарантий отличаются.",
        ),
        _check_result(
            "semantic.warranty",
            "Гарантии",
            "passed",
            "Ссылка контракта на ООЗ подтверждает совпадение.",
        ),
    )

    text = build_checks_report_text(report)

    assert "Строгое текстовое сравнение" not in text
    assert "Формулировки гарантий отличаются" not in text
    assert "Смысловое расхождение подтверждено" in text
    assert "Ссылка контракта на ООЗ подтверждает совпадение" in text
    assert "Ошибок: 1. Предупреждений: 0." in text


def test_report_prefers_meaningful_manual_semantic_result_over_duplicate_strict_row():
    report = _report(
        _check_result(
            "strict.plan.warranty",
            "Гарантийные требования",
            "warning",
            "Строгое сравнение гарантий требует проверки.",
        ),
        _check_result(
            "semantic.warranty",
            "Гарантии",
            "manual_review",
            "В проекте контракта найдена только ссылка на ООЗ.",
        ),
    )

    text = build_checks_report_text(report)

    assert "Строгое сравнение гарантий" not in text
    assert text.count("В проекте контракта найдена только ссылка на ООЗ") == 1
    assert "Предупреждений: 0. Требуют проверки: 1." in text


def test_report_keeps_strict_result_when_semantic_call_is_unavailable():
    report = _report(
        _check_result(
            "strict.plan.warranty",
            "Гарантийные требования",
            "warning",
            "Строгое сравнение гарантий требует проверки.",
        ),
        _check_result(
            "semantic.warranty",
            "Гарантии",
            "manual_review",
            "Semantic LLM check не выполнен: Connection error.",
        ),
    )

    text = build_checks_report_text(report)

    assert text.count("Строгое сравнение гарантий требует проверки") == 1
    assert "Semantic LLM check не выполнен" not in text
    assert "Предупреждений: 1. Требуют проверки: 0." in text


def test_report_explains_non_passing_commercial_offer_criteria():
    report = _report(
        _check_result(
            "manual.commercial_offers.onmck",
            "Сверка КП с ОНМЦК",
            "manual_review",
            "Часть строк требует проверки.",
            details={
                "criteria": [
                    {
                        "key": "quantity",
                        "label": "Количество ТРУ",
                        "status": "manual_review",
                        "issues": ["Сервер: в КП №2 количество не распознано"],
                    },
                    {
                        "key": "subject",
                        "label": "Соответствие предмета закупки ООЗ",
                        "status": "failed",
                        "issues": ["КП №3: предмет относится к другой закупке"],
                    },
                ]
            },
        )
    )

    text = build_checks_report_text(report)

    assert "Количество ТРУ — <b>ТРЕБУЕТ ПРОВЕРКИ</b>." in text
    assert "Сервер: в КП №2 количество не распознано" in text
    assert "КП №3: предмет относится к другой закупке" in text


def test_report_renders_vat_formula_inside_commercial_offer_section():
    report = _report(
        _check_result(
            "manual.commercial_offers.content",
            "Проверка КП",
            "passed",
            "КП распознаны.",
            details={
                "offer_summaries": [{"label": "КП №1", "items_count": 1}],
                "vat_criterion": {
                    "key": "vat",
                    "label": "Правильность расчёта НДС",
                    "status": "passed",
                    "issues": [],
                    "calculations": [
                        {
                            "label": "КП №1",
                            "base": "10000",
                            "rate_fraction": "0.22",
                            "calculated": "2200",
                            "declared": "2200",
                        }
                    ],
                },
            },
        )
    )

    text = build_checks_report_text(report)

    assert "6) Коммерческие предложения:" in text
    assert "Правильность расчёта НДС — <b>ОК</b>." in text
    assert "10 000,00 руб.</b> × 0,22 = <b>2 200,00 руб." in text
