from __future__ import annotations

import pytest
import requests

from services.procurement_reference_registry import (
    KTRUNotFoundError,
    ProcurementReferenceRegistry,
)


def _skip_on_network_error(exc: Exception) -> None:
    pytest.skip(f"Сетевой тест пропущен: {exc}")


@pytest.mark.parametrize(
    "ktru_code",
    [
        "31.01.12.160-00000005",
        "01.11.32.000-00000002",
        "31.01.12.150-00000003",
    ],
)
def test_ktru_common_info_exists(
    registry: ProcurementReferenceRegistry,
    ktru_code: str,
) -> None:
    try:
        payload = registry.get_ktru_common_info(ktru_code)
    except requests.RequestException as exc:
        _skip_on_network_error(exc)
        return

    assert payload["ktru_code"] == ktru_code
    assert payload["url"].endswith(f"itemId={ktru_code}")
    assert isinstance(payload.get("html"), str)
    assert payload["html"]
    assert isinstance(payload.get("short_description"), list)


def test_ktru_common_info_not_exists(
    registry: ProcurementReferenceRegistry,
) -> None:
    ktru_code = "01.11.32.777-00000002"

    try:
        with pytest.raises(KTRUNotFoundError):
            registry.get_ktru_common_info(ktru_code)
    except requests.RequestException as exc:
        _skip_on_network_error(exc)


def test_check_ktru_not_found_message(
    registry: ProcurementReferenceRegistry,
) -> None:
    ktru_code = "01.11.32.777-00000002"

    try:
        res = registry.check_ktru(ktru_code)
    except requests.RequestException as exc:
        _skip_on_network_error(exc)
        return

    assert res.found is False
    assert res.exact_ktru_match is False
    assert res.ktru_code is None
    assert res.payload is None
    assert res.common_info_url is not None
    assert res.message == f"Не удалось найти карточку КТРУ {ktru_code}\n"


def test_check_ktru_without_name(
    registry: ProcurementReferenceRegistry,
) -> None:
    ktru_code = "01.11.32.000-00000002"

    try:
        res = registry.check_ktru(ktru_code)
    except requests.RequestException as exc:
        _skip_on_network_error(exc)
        return

    assert res.found is True
    assert res.exact_ktru_match is True
    assert res.ktru_code == ktru_code
    assert res.reference_name == "Зерно ржи"
    assert res.okpd2_code == "01.11.32"
    assert "Наименование: Зерно ржи" in res.message


@pytest.mark.parametrize(
    "ktru_code",
    [
        "31.09.13.190-00000007",
        "31.09.13.190-00000002",
    ],
)
def test_check_ktru_excluded_from_catalog(
    registry: ProcurementReferenceRegistry,
    ktru_code: str,
) -> None:
    try:
        res = registry.check_ktru(ktru_code)
    except requests.RequestException as exc:
        _skip_on_network_error(exc)
        return

    section_pairs = res.payload["section_pairs"]

    assert res.found is False
    assert res.exact_ktru_match is True
    assert res.ktru_code == ktru_code
    assert section_pairs.get("Дата исключения позиции КТРУ") == "23.11.2020"
    assert "исключено из каталога" in res.message.lower()
    assert "23.11.2020" in res.message