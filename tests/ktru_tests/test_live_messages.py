from __future__ import annotations

import pytest
import requests

from services.procurement_reference_registry import ProcurementReferenceRegistry


def _skip_on_network_error(exc: Exception) -> None:
    pytest.skip(f"Сетевой тест пропущен: {exc}")


def test_check_ktru_name_mismatch_message(
    registry: ProcurementReferenceRegistry,
) -> None:
    try:
        res = registry.check_ktru(
            "01.11.32.000-00000002",
            "ОВЁС??",
        )
    except requests.RequestException as exc:
        _skip_on_network_error(exc)
        return

    expected_message = (
        "КТРУ 01.11.32.000-00000002 найден, но наименование отличается от эталонного.\n\n"
        "Ссылка на карточку: https://zakupki.gov.ru/epz/ktru/ktruCard/commonInfo.html?itemId=01.11.32.000-00000002\n\n"
        "Эталонное наименование: Зерно ржи\n"
        "Проверьте соответствует ли ваше наименование 'ОВЁС??'."
    )

    assert res.found is True
    assert res.exact_ktru_match is True
    assert res.exact_name_match is False
    assert res.normalized_name_match is False
    assert res.reference_name == "Зерно ржи"
    assert res.message == expected_message


def test_get_ktru_short_description(
    registry: ProcurementReferenceRegistry,
) -> None:
    try:
        text = registry.get_ktru_short_description("01.11.32.000-00000002")
    except requests.RequestException as exc:
        _skip_on_network_error(exc)
        return

    expected_text = (
        "Наименование: Зерно ржи\n\n"
        "Краткое описание:\n"
        "- Единица измерения: Тонна;^метрическая тонна (1000 кг)"
    )

    assert text == expected_text


def test_check_ktru_exact_name_match(
    registry: ProcurementReferenceRegistry,
) -> None:
    try:
        res = registry.check_ktru("01.11.32.000-00000002", "Зерно ржи")
    except requests.RequestException as exc:
        _skip_on_network_error(exc)
        return

    expected_message = (
        "КТРУ 01.11.32.000-00000002 найден.\n\n"
        "Ссылка на карточку: https://zakupki.gov.ru/epz/ktru/ktruCard/commonInfo.html?itemId=01.11.32.000-00000002\n\n"
        "Наименование совпадает с эталонной записью КТРУ.\n"
        "Наименование: Зерно ржи"
    )

    assert res.found is True
    assert res.exact_name_match is True
    assert res.normalized_name_match is True
    assert res.reference_name == "Зерно ржи"
    assert res.message == expected_message