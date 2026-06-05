from __future__ import annotations

import pytest

from services.procurement_reference_registry import ProcurementReferenceRegistry


def test_normalize_ktru_valid() -> None:
    assert (
        ProcurementReferenceRegistry.normalize_ktru("01.11.32.000-00000002")
        == "01.11.32.000-00000002"
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "abc",
        "01.11.32.000",
        "01.11.32.000-2",
        "01.11.32.000-0000000A",
        "1.11.32.000-00000002",
    ],
)
def test_normalize_ktru_invalid(value: str) -> None:
    with pytest.raises(ValueError, match="Некорректный код КТРУ"):
        ProcurementReferenceRegistry.normalize_ktru(value)


def test_build_ktru_url(registry: ProcurementReferenceRegistry) -> None:
    code = "01.11.32.000-00000002"
    url = registry._build_ktru_url("commonInfo", code)

    assert url == (
        "https://zakupki.gov.ru/epz/ktru/ktruCard/commonInfo.html"
        "?itemId=01.11.32.000-00000002"
    )


def test_normalize_text_yo(registry: ProcurementReferenceRegistry) -> None:
    assert registry.normalize_text("ОВЁС") == "овес"
    assert registry.normalize_text("  Зерно   ржи  ") == "зерно ржи"