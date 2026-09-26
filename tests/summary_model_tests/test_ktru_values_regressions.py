from decimal import Decimal

import pytest

from summary_model.checks.ktru_adapter import _is_value_allowed, _split_value, _unit_status, run_ktru_characteristic_checks
from summary_model.extraction_models import ProcurementPackageExtraction, PurchaseDescriptionSchema, PurchaseItem, PurchaseItemCharacteristic
from summary_model.tables.utils import KTRU_RE, OKPD2_RE, unique_codes


@pytest.mark.parametrize("value,allowed,expected", [
    ("50", "> 50", False), ("50", ">= 50", True),
    (">= 50", "> 50", False), ("> 50", ">= 50", True),
    ("< 50", "> 50", False), ("-2,5", ">= -3", True),
    ("2,5", "2.5", True), ("25", "2,5", False),
    (">= 10; <= 20", ">= 10; < 20", False),
    ("от 11 до 19", ">= 10; < 20", True),
    ("не менее 50", ">= 50", True),
    ("А47", "А4", False), ("Да", "Нет, Да", False),
    ("4G", "4G", True), ("802.11ac", "802.11ac", True),
    ("4G", "5G", False),
    ("1 000", ">= 1000", True),
    ("> около 50", "> 50", None),
])
def test_characteristic_numeric_constraints(value, allowed, expected):
    assert _is_value_allowed(value, [allowed]) is expected


def test_numeric_conjunction_is_one_value():
    assert _split_value(">= 10; < 20") == [">= 10; < 20"]


def test_unit_aliases_do_not_change_scale():
    assert _unit_status("мм", "Миллиметр") == "passed"
    assert _unit_status("л", "Литр; кубический дециметр") == "passed"
    assert _unit_status("тысяча штук", "Штука") == "failed"
    assert _unit_status("кВт", "Ватт") == "failed"


def test_ktru_whitespace_and_code_boundaries():
    assert unique_codes(KTRU_RE, "31.01. 11.150-00000003; 31.01.11.150-00000003") == ["31.01.11.150-00000003"]
    assert unique_codes(KTRU_RE, "31.01.11.150 - 00000003") == ["31.01.11.150-00000003"]
    assert unique_codes(OKPD2_RE, "31.01.11.150 - 00000003") == []
    assert unique_codes(KTRU_RE, "31.01.\n11.150-00000003") == []
    assert unique_codes(KTRU_RE, "31.01.11.150-000000033") == []


def _check(characteristics, legal):
    class Registry:
        def get_ktru_characteristics_detailed(self, code):
            return legal

        def get_ktru_common_info(self, code):
            return {"name": "Стол", "unit": "Штука"}

        def check_okpd2(self, code):
            return {"found": False}

    package = ProcurementPackageExtraction(purchase_description=PurchaseDescriptionSchema(items=[
        PurchaseItem(name="Стол", ktru_code="31.01.12.000-00000001", unit="шт", quantity=Decimal(1), characteristics=characteristics)
    ]))
    return next(result for result in run_ktru_characteristic_checks(package, registry=Registry()) if result.check_id == "manual.ktru.characteristics")


def test_unknown_obligation_is_not_silent_success():
    result = _check([], {"Цвет": {"values": ["Чёрный"], "required": None}})
    assert result.status == "manual_review"
    assert "обязательность" in result.details["characteristic_rows"][0]["message"]


def test_characteristic_unit_failure_reaches_top_level():
    result = _check([PurchaseItemCharacteristic(name="Толщина", value="10", unit="литр")], {
        "Толщина": {"values": [">= 5"], "required": True, "unit": "миллиметр"}
    })
    assert result.status == "failed"


def test_unknown_value_is_manual_not_false_error():
    result = _check([PurchaseItemCharacteristic(name="Толщина", value="> около 50")], {
        "Толщина": {"values": ["> 50"], "required": False}
    })
    assert result.status == "manual_review"
