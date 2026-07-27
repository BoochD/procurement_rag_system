from datetime import date
from decimal import Decimal

from summary_model.structured_output_lab.run import DemoOffer, normalize_demo_payload
from summary_model.structured_output_lab.run import _payload_from_raw_message


def test_normalizer_repairs_common_transport_mutations_before_strict_validation():
    normalized, warnings = normalize_demo_payload(
        {
            "supplier_name": "ООО Тест",
            "outgoing_date": "28.04.2026",
            "unit_price": "10 470 000,00",
            "vat_rate": "Без НДС",
            "notes": "Одна строка",
        }
    )

    result = DemoOffer.model_validate(normalized)

    assert result.outgoing_date == date(2026, 4, 28)
    assert result.unit_price == Decimal("10470000.00")
    assert result.vat_rate is None
    assert result.vat_text == "Без НДС"
    assert result.notes == ["Одна строка"]
    assert len(warnings) == 4


def test_normalizer_keeps_unknown_values_for_strict_validation_to_reject():
    normalized, _ = normalize_demo_payload({"outgoing_date": "когда-нибудь"})

    assert normalized["outgoing_date"] == "когда-нибудь"


def test_raw_content_json_is_recovered_when_provider_does_not_return_tool_calls():
    class RawMessage:
        tool_calls = []
        additional_kwargs = {}
        content = '{"notes": "строка"}'

    assert _payload_from_raw_message(RawMessage()) == {"notes": "строка"}
