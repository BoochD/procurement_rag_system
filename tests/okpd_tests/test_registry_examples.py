from __future__ import annotations

import pytest


def test_registry_find_code_only(registry):
    res = registry.check_okpd2("31.01.12")
    assert res.found is True
    assert res.exact_okpd_match is False
    assert res.matched_okpd2 == "31.01"
    assert res.is_parent_match is True
    assert res.table_id == "table_02"
    assert res.reference_name == "Мебель для офисов и предприятий торговли"
    assert "родительский код 31.01" in res.message.lower()


def test_registry_name_mismatch(registry):
    res = registry.check_okpd2("31.01.12", "СТУЛ")
    assert res.found is True
    assert res.exact_okpd_match is False
    assert res.exact_name_match is False
    assert res.normalized_name_match is False
    assert res.matched_okpd2 == "31.01"
    assert res.reference_name == "Мебель для офисов и предприятий торговли"
    assert "родительский код 31.01" in res.message.lower()

def test_registry_name_mismatch_2(registry):
    res = registry.check_okpd2("26.20.12.110", "Терминалы кассовые, подключаемые к компьютеру или сети передачи данных")
    assert res.found is True
    assert res.exact_okpd_match is False
    assert res.exact_name_match is False
    assert res.normalized_name_match is False
    assert res.matched_okpd2 == "26.20.12"
    assert res.reference_name == "Терминалы кассовые, банкоматы и аналогичное оборудование, подключаемое к компьютеру или сети передачи данных"
    assert "родительский код 26.20.12" in res.message.lower()

def test_registry_exact_name_match(registry):
    res = registry.check_okpd2("31.01.12", "Мебель деревянная для офисов")
    assert res.found is True
    assert res.exact_okpd_match is False
    assert res.exact_name_match is False
    assert res.normalized_name_match is False
    assert res.matched_okpd2 == "31.01"
    assert "родительский код 31.01" in res.message.lower()

def test_registry_exact_name_match_2(registry):
    res = registry.check_okpd2("26.20.13", "Машины вычислительные электронные цифровые, содержащие в одном корпусе центральный процессор и устройство ввода и вывода, объединенные или нет для автоматической обработки данных")
    assert res.found is True
    assert res.exact_okpd_match is True
    assert res.exact_name_match is True
    assert res.normalized_name_match is True
    assert res.matched_okpd2 == "26.20.13"
    assert "код 26.20.13" in res.message.lower()

def test_registry_normalized_name_match(registry):
    res = registry.check_okpd2("31.01.12", "мебель деревянная для офисов")
    assert res.found is True
    assert res.exact_okpd_match is False
    assert res.exact_name_match is False
    assert res.normalized_name_match is False
    assert res.matched_okpd2 == "31.01"
    assert "родительский код 31.01" in res.message.lower()


def test_registry_parent_match_31_01_12_190(registry):
    res = registry.check_okpd2("31.01.12.190", "Мебель офисная деревянная прочая")
    assert res.found is True
    assert res.query_okpd2 == "31.01.12.190"
    assert res.matched_okpd2 == "31.01"
    assert res.is_parent_match is True
    assert res.reference_name == "Мебель для офисов и предприятий торговли"
    assert "родительский код 31.01" in res.message.lower()


def test_registry_parent_match_31_01_12_139(registry):
    res = registry.check_okpd2("31.01.12.139", "Шкафы деревянные прочие")
    assert res.found is True
    assert res.query_okpd2 == "31.01.12.139"
    assert res.matched_okpd2 == "31.01"
    assert res.is_parent_match is True
    assert res.reference_name == "Мебель для офисов и предприятий торговли"
    assert "родительский код 31.01" in res.message.lower()


def test_registry_parent_match_via_allowed_appendix_only(registry):
    res = registry.check_okpd2("31.01.12.110")
    assert res.found is True
    assert res.query_okpd2 == "31.01.12.110"
    assert res.matched_okpd2 == "31.01"
    assert res.is_parent_match is True
    assert res.table_id == "table_02"
    assert res.reference_name == "Мебель для офисов и предприятий торговли"
    assert "родительский код 31.01" in res.message.lower()


def test_registry_parent_match_keeps_full_query_code(registry):
    res = registry.check_okpd2("31.01.155.139")
    assert res.found is True
    assert res.query_okpd2 == "31.01.155.139"
    assert res.matched_okpd2 == "31.01"
    assert res.is_parent_match is True
    assert res.table_id == "table_02"
    assert "31.01.155.139" in res.message
    assert "родительский код 31.01" in res.message.lower()


def test_registry_invalid_code_raises(registry):
    with pytest.raises(ValueError):
        registry.check_okpd2("abc")


def test_find_okpd2_returns_rows(registry):
    rows = registry.find_okpd2("31.01.12")
    assert isinstance(rows, list)
    assert len(rows) >= 1
    assert rows[0]["okpd2"] == "31.01.12"