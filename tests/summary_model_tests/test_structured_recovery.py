from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from pydantic import BaseModel, Field

from summary_model.extraction.llm_client import StructuredLLMClient
from summary_model.extraction.structured_recovery import (
    raw_payload_from_message,
    recover_model,
)
from summary_model.extraction_models import CommercialOfferSchema, PenaltyClause
from summary_model.commercial_offer_vlm import _extract_vlm_offer
from summary_model.vlm_fallback import _parse_response
from summary_model.web_service import _llm_recovery_warnings


class RequiredRow(BaseModel):
    name: str
    quantity: Decimal | None = None


class RequiredRows(BaseModel):
    rows: list[RequiredRow] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def test_safe_normalization_keeps_strict_commercial_offer():
    recovery = recover_model(
        CommercialOfferSchema,
        {
            "supplier_name": "ООО Ромашка",
            "outgoing_date": "28.04.2026",
            "vat_rate": "Без НДС",
            "items": {
                "name": "Сервер",
                "quantity": "4",
                "unit_price": "10 470 000,00",
                "notes": "Цена указана без НДС",
            },
        },
    )

    assert recovery.status == "recovered"
    assert recovery.lossy_warnings == []
    assert isinstance(recovery.value, CommercialOfferSchema)
    assert str(recovery.value.outgoing_date) == "2026-04-28"
    assert recovery.value.vat_rate is None
    assert recovery.value.vat_text == "Без НДС"
    assert recovery.value.items[0].unit_price == Decimal("10470000.00")
    assert recovery.value.items[0].notes == ["Цена указана без НДС"]
    assert recovery.value.parser_warnings == []


def test_invalid_optional_field_does_not_remove_commercial_offer_item():
    recovery = recover_model(
        CommercialOfferSchema,
        {
            "supplier_name": "ООО Ромашка",
            "items": [
                {
                    "name": "Сервер",
                    "quantity": "четыре плюс-минус",
                    "unit_price": "10 470 000,00",
                }
            ],
        },
    )

    assert recovery.status == "partial"
    assert isinstance(recovery.value, CommercialOfferSchema)
    assert len(recovery.value.items) == 1
    assert recovery.value.items[0].name == "Сервер"
    assert recovery.value.items[0].quantity is None
    assert recovery.value.items[0].quantity_raw == "четыре плюс-минус"
    assert recovery.value.items[0].unit_price == Decimal("10470000.00")
    assert recovery.lossy_warnings
    assert any("items[0].quantity" in warning for warning in recovery.value.parser_warnings)


def test_offer_aliases_and_extended_money_and_date_formats_are_recovered():
    recovery = recover_model(
        CommercialOfferSchema,
        {
            "supplier": "ООО Алиас",
            "supplier_inn": "5400000000",
            "offer_number": "КП-42",
            "letter_date": "№ КП-42 от 27 апреля 2026 г.",
            "grand_total": "107 648 484 рублей 50 копеек",
            "positions": [
                {
                    "position_number": "1",
                    "product_name": "Сервер",
                    "qty": "4",
                    "unit_cost": "1.234.567,89",
                    "line_total": "1,5 млн руб.",
                    "brand": "Тест",
                }
            ],
        },
    )

    assert recovery.status == "recovered"
    assert recovery.lossy_warnings == []
    assert isinstance(recovery.value, CommercialOfferSchema)
    assert recovery.value.supplier_name == "ООО Алиас"
    assert recovery.value.inn == "5400000000"
    assert recovery.value.outgoing_number == "КП-42"
    assert str(recovery.value.outgoing_date) == "2026-04-27"
    assert recovery.value.total_amount.amount == Decimal("107648484.50")
    assert recovery.value.items[0].row_number == "1"
    assert recovery.value.items[0].unit_price == Decimal("1234567.89")
    assert recovery.value.items[0].total_price == Decimal("1500000")
    assert recovery.value.items[0].trademark == "Тест"


def test_penalty_fraction_is_kept_as_basis_not_misread_as_percent():
    recovery = recover_model(
        PenaltyClause,
        {
            "raw_text": "Пеня начисляется в размере одной трехсотой ставки.",
            "percent": "1/300",
        },
    )

    assert recovery.status == "recovered"
    assert isinstance(recovery.value, PenaltyClause)
    assert recovery.value.percent is None
    assert recovery.value.basis == "1/300"


def test_invalid_required_field_drops_only_broken_nested_row():
    recovery = recover_model(
        RequiredRows,
        {
            "rows": [
                {"name": "Сервер", "quantity": "4"},
                {"name": {"unexpected": []}, "quantity": "1"},
            ]
        },
    )

    assert recovery.status == "partial"
    assert isinstance(recovery.value, RequiredRows)
    assert [row.name for row in recovery.value.rows] == ["Сервер"]
    assert any("rows[1]" in warning for warning in recovery.lossy_warnings)
    assert recovery.value.warnings


def test_raw_payload_is_read_from_tool_call_and_plain_json_content():
    tool_raw = SimpleNamespace(
        tool_calls=[{"args": {"supplier_name": "ООО Тест"}}],
        invalid_tool_calls=[],
        content="",
        additional_kwargs={},
    )
    content_raw = SimpleNamespace(
        tool_calls=[],
        invalid_tool_calls=[],
        content='{"supplier_name":"ООО Текст"}',
        additional_kwargs={},
    )

    assert raw_payload_from_message(tool_raw) == {"supplier_name": "ООО Тест"}
    assert raw_payload_from_message(content_raw) == {"supplier_name": "ООО Текст"}


def test_structured_client_recovers_raw_response_without_paid_retry():
    class Runner:
        def invoke(self, _prompt):
            raw = SimpleNamespace(
                tool_calls=[
                    {
                        "args": {
                            "supplier_name": "ООО Тест",
                            "items": [
                                {
                                    "name": "Сервер",
                                    "quantity": "не распознано",
                                    "unit_price": "10 470 000,00",
                                }
                            ],
                        }
                    }
                ],
                invalid_tool_calls=[],
                content="",
                additional_kwargs={},
            )
            return {
                "raw": raw,
                "parsed": None,
                "parsing_error": ValueError("quantity is not a decimal"),
            }

    class Model:
        model_name = "fake-raw-model"

        def __init__(self):
            self.include_raw = None
            self.runner = Runner()

        def with_structured_output(self, _schema, *, method, include_raw=False):
            assert method == "function_calling"
            self.include_raw = include_raw
            return self.runner

    model = Model()
    client = StructuredLLMClient(model=model)
    result, error = client.extract(CommercialOfferSchema, "Extract offer", "payload")

    assert error is None
    assert result is not None
    assert result.supplier_name == "ООО Тест"
    assert len(result.items) == 1
    assert result.items[0].quantity is None
    assert model.include_raw is True
    assert client.calls == 1
    assert client.retries == 0
    assert client.metrics()["partial_calls"] == 1
    assert client.metrics()["attempts"][0]["lossy_recovery_warnings"]


def test_commercial_offer_vlm_keeps_offer_when_one_field_is_invalid(monkeypatch):
    response = {
        "choices": [
            {
                "message": {
                    "content": (
                        '{"supplier_name":"ООО Тест","vat_rate":"Без НДС",'
                        '"items":[{"name":"Сервер","quantity":"не распознано",'
                        '"unit_price":"10 470 000,00"}]}'
                    )
                }
            }
        ]
    }
    monkeypatch.setattr(
        "summary_model.commercial_offer_vlm._call_vlm",
        lambda *_args, **_kwargs: response,
    )

    offer, responses, recovery = _extract_vlm_offer(
        [{"page": 1, "mime": "image/png", "data": b"image"}],
        payload={"file_name": "offer.pdf"},
        model="fake",
        file_name="offer.pdf",
    )

    assert responses == [response]
    assert offer.supplier_name == "ООО Тест"
    assert len(offer.items) == 1
    assert offer.items[0].quantity is None
    assert offer.items[0].unit_price == Decimal("10470000.00")
    assert offer.vat_text == "Без НДС"
    assert recovery.status == "partial"
    assert recovery.lossy_warnings


def test_vlm_table_parser_recovers_string_notes_without_losing_item():
    response = {
        "choices": [
            {
                "message": {
                    "content": (
                        '{"items":[{"name":"Сервер","notes":"служебная строка"}],'
                        '"stages":[],"warnings":[]}'
                    )
                }
            }
        ]
    }

    extraction, recovery = _parse_response(
        response,
        role="purchase_description",
        table_title="Требования",
    )

    assert recovery.status == "recovered"
    assert extraction.items[0].name == "Сервер"
    assert extraction.items[0].notes == ["служебная строка"]


def test_web_warning_exposes_only_lossy_recovery():
    warnings = _llm_recovery_warnings(
        "document LLM",
        {
            "attempts": [
                {
                    "file_name": "plan.docx",
                    "recovery_warnings": ["nmck: строковое число преобразовано в Decimal"],
                },
                {
                    "file_name": "offer.docx",
                    "lossy_recovery_warnings": ["items[2].quantity: поле удалено"],
                },
            ]
        },
    )

    assert warnings == [
        "offer.docx: document LLM: ответ LLM восстановлен частично: "
        "items[2].quantity: поле удалено"
    ]
    assert _llm_recovery_warnings("stage LLM", None) == []
