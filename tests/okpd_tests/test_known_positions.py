from __future__ import annotations


def assert_has_code_and_name(registry, code: str, expected_name: str):
    res = registry.check_okpd2(code)
    assert res.found is True, f"{code} not found"
    assert res.matched_okpd2 == code
    assert res.reference_name == expected_name


def test_known_position_13_2(registry):
    assert_has_code_and_name(registry, "13.2", "Ткани текстильные")


def test_known_position_14_20(registry):
    assert_has_code_and_name(registry, "14.20", "Изделия меховые")


def test_known_position_31_01_12(registry):
    assert_has_code_and_name(registry, "31.01", "Мебель для офисов и предприятий торговли")


def test_known_position_31_09_11(registry):
    assert_has_code_and_name(
        registry,
        "31.09.11",
        "Мебель металлическая, не включенная в другие группировки",
    )


def test_known_position_found_in_expected_table_fragment(registry):
    res = registry.check_okpd2("31.01.12")
    assert res.found is True
    assert "ПРИЛОЖЕНИЕ N 2" in (res.table_title or "")