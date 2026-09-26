from summary_model.checks.models import CheckResult, ProcurementChecksReport
from summary_model.checks.report import build_checks_report_text


def _result(
    check_id: str,
    title: str,
    status: str,
    report_text: str,
    details: dict,
) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        title=title,
        severity="warning",
        status=status,
        mode="manual_review",
        message=report_text,
        report_text=report_text,
        details=details,
    )


def _report(*results: CheckResult) -> str:
    return build_checks_report_text(
        ProcurementChecksReport.from_results(package_id="synthetic-ktru", results=list(results))
    )


def test_ktru_report_keeps_identity_and_characteristic_evidence_separate():
    text = _report(
        _result(
            "manual.ktru.characteristics",
            "КТРУ-характеристики",
            "manual_review",
            "Часть сведений требует ручной проверки.",
            {
                "item_identity_rows": [
                    {
                        "item_name": "Сервер для виртуализации",
                        "ktru_code": "26.20.14.000-00000189",
                        "ktru_name": "Сервер",
                        "ooz_unit": None,
                        "ktru_unit": "Штука",
                        "name_status": "failed",
                        "unit_status": "manual_review",
                    }
                ],
                "characteristic_rows": [
                    {
                        "item_name": "Сервер для виртуализации",
                        "ktru_code": "26.20.14.000-00000189",
                        "characteristic_name": "Объем памяти",
                        "ooz_value": "128 ГБ",
                        "ooz_unit": "ГБ",
                        "ktru_allowed_values": ["64 ГБ", "256 ГБ"],
                        "ktru_unit": "МБ",
                        "status": "manual_review",
                        "message": "значение требует проверки вручную",
                    }
                ],
            },
        )
    )

    assert "Наименование: ООЗ — Сервер для виртуализации; КТРУ — Сервер; статус — <error>ОШИБКА</error>." in text
    assert "Единица товара: ООЗ — не указана; КТРУ — Штука; статус — <warn>ТРЕБУЕТ ПРОВЕРКИ</warn>." in text
    assert "Значение в ООЗ: 128 ГБ." in text
    assert "Значения, допустимые по КТРУ: 64 ГБ, 256 ГБ." in text
    assert "Единицы измерения: ООЗ — ГБ; КТРУ — МБ." in text
    assert "значение требует проверки вручную" in text


def test_ktru_additional_report_lists_every_actual_characteristic_and_keeps_justification():
    text = _report(
        _result(
            "manual.ktru.additional",
            "Дополнительные характеристики КТРУ",
            "failed",
            "Для характеристик не найдено явное обоснование.",
            {
                "ooz_justification_state": {"found": False, "partial": False},
                "additional_rows": [
                    {"value": "Да"},
                    {"value": "24 месяца"},
                    {"value": "AES-256"},
                ],
                "assessments": [
                    {
                        "item": "Сервер",
                        "ktru_code": "26.20.14.000-00000189",
                        "characteristic": "Поддержка резервного копирования",
                        "decision": "missing_justification",
                        "okpd_rule": {"code": "26.20.14.000", "reason": "режим не подтвержден"},
                        "plan_regime": {"field_code": "17.2", "field_value": "не установлено", "status": "missing"},
                        "justification": {"status": "missing", "source": "none"},
                    },
                    {
                        "item": "Сервер",
                        "ktru_code": "26.20.14.000-00000189",
                        "characteristic": "Расширенная гарантия",
                        "decision": "missing_justification",
                        "okpd_rule": {"code": "26.20.14.000"},
                        "plan_regime": {},
                        "justification": {"status": "missing", "source": "none"},
                    },
                    {
                        "item": "Сервер",
                        "ktru_code": "26.20.14.000-00000189",
                        "characteristic": "Шифрование данных",
                        "decision": "missing_justification",
                        "okpd_rule": {"code": "26.20.14.000"},
                        "plan_regime": {},
                        "justification": {"status": "missing", "source": "none"},
                    },
                ],
            },
        )
    )

    assert "Дополнительные характеристики: Поддержка резервного копирования; Расширенная гарантия; Шифрование данных." in text
    assert "ещё 2 характеристики" not in text
    assert "Полный перечень характеристик сохранён в <b>checks.json</b>." not in text
    assert "ПП №1875: режим не подтвержден" in text
    assert "План-график, поле 17.2: не установлено." in text
    assert "Обоснование в ООЗ не найдено." in text
