from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from pydantic import BaseModel, Field

from summary_model.extraction.llm_client import StructuredLLMClient
from summary_model.extraction.structured_recovery import (
    raw_payload_from_message,
    recover_model,
)
from summary_model.extraction_models import (
    CommercialOfferSchema,
    ContractDraftSchema,
    NmckJustificationSchema,
    PenaltyClause,
    ScheduleApplicationSchema,
)
from summary_model.commercial_offer_vlm import _extract_vlm_offer
from summary_model.vlm_fallback import _parse_response
from summary_model.web_service import _llm_recovery_warnings, _public_vlm_table_warnings


class RequiredRow(BaseModel):
    name: str
    quantity: Decimal | None = None


class RequiredRows(BaseModel):
    rows: list[RequiredRow] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def test_empty_fact_wrapper_becomes_none_without_lossy_recovery():
    recovery = recover_model(
        NmckJustificationSchema,
        {
            "variation_coefficient": {
                "raw_value": None,
                "normalized_value": None,
                "confidence": 0.0,
                "evidence": [],
            }
        },
    )

    assert recovery.status == "recovered"
    assert recovery.value.variation_coefficient is None
    assert recovery.lossy_warnings == []


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


def test_fact_wrappers_and_single_fact_lists_are_recovered_without_data_loss():
    schedule_recovery = recover_model(
        ScheduleApplicationSchema,
        {
            "purchase_subject": {
                "raw_value": "Оказание услуг",
                "normalized_value": "Оказание услуг",
                "confidence": 0.99,
            },
            "smp_preference": {
                "raw_value": "Нет",
                "normalized_value": False,
                "confidence": 0.95,
            },
            "raw_fields": [
                {
                    "key": "НМЦК",
                    "value": "106 312 006,00",
                    "evidence": [
                        {"document_id": "plan", "block_id": "table-1", "row": "r4"}
                    ],
                }
            ],
        },
    )
    contract_recovery = recover_model(
        ContractDraftSchema,
        {
            "delivery_place": [
                {
                    "raw_value": "Место оказания услуг: г. Новосибирск",
                    "normalized_value": "г. Новосибирск",
                    "confidence": 0.9,
                }
            ],
            "delivery_term": [
                {
                    "raw_value": "с даты заключения по 21.08.2026",
                    "normalized_value": {
                        "raw": "с даты заключения по 21.08.2026",
                        "start_event": "date_of_contract",
                        "end_event": "2026-08-21",
                    },
                }
            ],
        },
    )

    assert schedule_recovery.lossy_warnings == []
    assert schedule_recovery.value.purchase_subject == "Оказание услуг"
    assert schedule_recovery.value.smp_preference is False
    assert schedule_recovery.value.raw_fields[0].evidence == "plan:table-1:r4"
    assert contract_recovery.lossy_warnings == []
    assert contract_recovery.value.delivery_place == "г. Новосибирск"
    assert contract_recovery.value.delivery_term.end_event == "2026-08-21"


def test_onmck_fact_wrappers_do_not_drop_items_or_stage_dates():
    recovery = recover_model(
        NmckJustificationSchema,
        {
            "total_amount": {
                "raw_value": "106 312 006,00",
                "normalized_value": {"raw": "106 312 006,00", "amount": "106312006.00"},
            },
            "items": [
                {
                    "row_number": {
                        "raw_value": "2.1",
                        "normalized_value": "2.1",
                        "confidence": 0.95,
                    },
                    "name": "Сервер",
                    "quantity": "4",
                    "is_declared_min_price_correct": {
                        "raw_value": True,
                        "normalized_value": True,
                    },
                    "is_row_total_correct": {
                        "raw_value": True,
                        "normalized_value": True,
                    },
                    "evidence": ["table-1:r5"],
                }
            ],
            "stages": [
                {
                    "stage_number": "1",
                    "service_start_date": {
                        "raw_value": "13.07.2026",
                        "normalized_value": "2026-07-13",
                    },
                    "service_end_date": {
                        "raw_value": "13.07.2026",
                        "normalized_value": "2026-07-13",
                    },
                }
            ],
        },
    )

    assert recovery.lossy_warnings == []
    assert len(recovery.value.items) == 1
    assert recovery.value.items[0].row_number == "2.1"
    assert recovery.value.items[0].is_declared_min_price_correct is True
    assert str(recovery.value.stages[0].service_start_date) == "2026-07-13"


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


def test_web_recovery_warnings_hide_evidence_noise_and_group_real_losses():
    warnings = _llm_recovery_warnings(
        "извлечение документа через LLM",
        {
            "attempts": [
                {
                    "file_name": "contract.docx",
                    "lossy_recovery_warnings": [
                        "items[0].evidence: поле удалено; raw=[{}]",
                        "items[1].evidence: поле удалено; raw=[{}]",
                        "delivery_place: поле удалено; raw=[...]",
                        "delivery_term: поле удалено; raw=[...]",
                    ],
                }
            ]
        },
    )

    assert len(warnings) == 1
    assert "локальный fallback применён к 2 полям/строкам" in warnings[0]
    assert "raw=" not in warnings[0]
    assert "evidence" not in warnings[0]


def test_public_vlm_warnings_hide_expected_empty_contract_specification():
    assert _public_vlm_table_warnings(
        [
            "contract.docx, table 11: спецификация не содержит заполненных товарных позиций.",
            "ooz.docx, table 3: VLM fallback failed: timeout",
        ]
    ) == ["ooz.docx, table 3: VLM fallback failed: timeout"]
