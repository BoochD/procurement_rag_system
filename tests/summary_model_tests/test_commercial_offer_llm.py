from decimal import Decimal
import json
from types import SimpleNamespace

from summary_model.checks import run_checks
from summary_model.checks import commercial_offer_llm
from summary_model.checks.runner import _match_offer_items, _match_offers_to_price_sources
from summary_model.checks.commercial_offer_llm import (
    CommercialOfferMatchDecision,
    _build_payload,
    _has_non_price_support,
    _validate_decisions,
)
from summary_model.commercial_offer_vlm import COMMERCIAL_OFFER_VLM_PROMPT
from summary_model.commercial_offer_lab import run as commercial_offer_lab
from summary_model.extraction_models import (
    CommercialOfferItem,
    CommercialOfferSchema,
    NmckItem,
    NmckJustificationSchema,
    PriceSource,
    ProcurementPackageExtraction,
    SupplierPrice,
)


def _package(*, nmck_items, offer_items) -> ProcurementPackageExtraction:
    return ProcurementPackageExtraction(
        nmck_justification=NmckJustificationSchema(
            price_sources=[PriceSource(source_id="supplier_1", raw_header="Поставщик 1")],
            items=nmck_items,
        ),
        commercial_offers=[CommercialOfferSchema(supplier_name="ООО Тест", items=offer_items)],
    )


def test_commercial_offer_vlm_prompt_requires_footer_term_and_vat_scan():
    assert "отдельный проход по ВСЕМ страницам" in COMMERCIAL_OFFER_VLM_PROMPT
    assert "Срок оказания Услуг: с даты заключения контракта по 21.08.2026" in COMMERCIAL_OFFER_VLM_PROMPT
    assert "в том числе НДС 5%" in COMMERCIAL_OFFER_VLM_PROMPT
    assert "смешанный режим" in COMMERCIAL_OFFER_VLM_PROMPT


def test_payload_contains_offer_once_for_multiple_unmatched_rows():
    package = _package(
        nmck_items=[
            NmckItem(
                name="Услуга типа А",
                supplier_prices=[SupplierPrice(source_id="supplier_1", unit_price=Decimal("10"))],
            ),
            NmckItem(
                name="Услуга типа Б",
                supplier_prices=[SupplierPrice(source_id="supplier_1", unit_price=Decimal("20"))],
            ),
        ],
        offer_items=[
            CommercialOfferItem(row_number="1", name="Монтаж", unit_price=Decimal("10")),
            CommercialOfferItem(row_number="2", name="Настройка", unit_price=Decimal("20")),
        ],
    )

    payload = _build_payload(package)

    assert len(payload["unmatched_rows"]) == 2
    assert len(payload["offers"]) == 1
    assert len(payload["offers"][0]["items"]) == 2
    assert [item["candidate_id"] for item in payload["offers"][0]["items"]] == [
        "supplier_1:item:0",
        "supplier_1:item:1",
    ]


def test_deterministic_matcher_maps_cod_rows_one_to_one_before_llm():
    nmck_items = [
        NmckItem(name="Подготовка технической документации", quantity=1, unit="усл. ед."),
        NmckItem(name="Сервер*", quantity=4, unit="шт"),
        NmckItem(
            name="Комплект расширения системы хранения данных товарный знак: YADRO TATLIN*",
            quantity=1,
            unit="шт",
        ),
        NmckItem(name="Программное обеспечение (тип №1)*", quantity=4, unit="шт"),
        NmckItem(name="Программное обеспечение (тип №2)*", quantity=6, unit="шт"),
        NmckItem(name="Программное обеспечение (тип №3)*", quantity=14, unit="шт"),
        NmckItem(name="Расширение вычислительных мощностей", quantity=1, unit="усл. ед."),
    ]
    offer_items = [
        CommercialOfferItem(name="Подготовка технической документации", quantity=1, unit="усл. ед."),
        CommercialOfferItem(name="Расширение вычислительных мощностей", quantity=1, unit="усл. ед."),
        CommercialOfferItem(name="Сервер YADRO VEGMAN R220 G2", quantity=4, unit="шт"),
        CommercialOfferItem(
            name="Комплект расширения системы хранения данных",
            trademark="YADRO TATLIN",
            quantity=1,
            unit="шт",
        ),
        CommercialOfferItem(name="Передача прав на ПО zVirt", quantity=4, unit="шт"),
        CommercialOfferItem(name="Передача прав на ПО Кибер Бэкап", quantity=6, unit="шт"),
        CommercialOfferItem(name="Передача прав на ПО Кибер Бэкап", quantity=14, unit="шт"),
    ]

    matches, reasons = _match_offer_items(nmck_items, offer_items)

    assert matches == {0: 0, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 1}
    assert all(reasons[index] == "позиция найдена однозначно" for index in range(7))


def test_deterministic_matcher_does_not_guess_duplicate_shapes_by_position():
    nmck_items = [
        NmckItem(name="Тип А", quantity=1, unit="шт"),
        NmckItem(name="Тип Б", quantity=1, unit="шт"),
    ]
    offer_items = [
        CommercialOfferItem(name="Неизвестная позиция X", quantity=1, unit="шт"),
        CommercialOfferItem(name="Неизвестная позиция Y", quantity=1, unit="шт"),
    ]

    matches, _reasons = _match_offer_items(nmck_items, offer_items)

    assert matches == {}


def test_deterministic_matcher_uses_verified_supplier_order_for_full_offer():
    nmck_items = [
        NmckItem(
            name="Программное обеспечение (тип №1)",
            quantity=4,
            unit="шт",
            supplier_prices=[SupplierPrice(source_id="supplier_3", unit_price=Decimal("861360"))],
        ),
        NmckItem(
            name="Программное обеспечение (тип №2)",
            quantity=6,
            unit="шт",
            supplier_prices=[SupplierPrice(source_id="supplier_3", unit_price=Decimal("336270.60"))],
        ),
        NmckItem(
            name="Программное обеспечение (тип №3)",
            quantity=14,
            unit="шт",
            supplier_prices=[SupplierPrice(source_id="supplier_3", unit_price=Decimal("197781.60"))],
        ),
    ]
    offer_items = [
        CommercialOfferItem(name="zVirt", quantity=4, unit="шт", unit_price=Decimal("861360")),
        CommercialOfferItem(name="Кибер Бэкап", quantity=6, unit="шт", unit_price=Decimal("336270.60")),
        CommercialOfferItem(name="Кибер Бэкап", quantity=14, unit="шт", unit_price=Decimal("197781.60")),
    ]

    matches, reasons = _match_offer_items(
        nmck_items,
        offer_items,
        source_id="supplier_3",
    )

    assert matches == {0: 0, 1: 1, 2: 2}
    assert all(reasons[index] == "позиция найдена однозначно" for index in range(3))


def test_verified_supplier_order_rejects_price_mismatch():
    nmck_items = [
        NmckItem(
            name="Тип №1",
            quantity=4,
            unit="шт",
            supplier_prices=[SupplierPrice(source_id="supplier_3", unit_price=Decimal("100"))],
        )
    ]
    offer_items = [
        CommercialOfferItem(name="Другое ПО", quantity=4, unit="шт", unit_price=Decimal("101"))
    ]

    matches, _reasons = _match_offer_items(
        nmck_items,
        offer_items,
        source_id="supplier_3",
    )

    assert matches == {}


def test_offer_source_mapping_recovers_source_missing_from_price_sources():
    offers = [
        CommercialOfferSchema(supplier_name="Поставщик 1"),
        CommercialOfferSchema(supplier_name="Поставщик 2"),
        CommercialOfferSchema(supplier_name="Поставщик 3"),
    ]
    price_sources = [
        PriceSource(source_id="supplier_1", raw_header="Поставщик 1"),
        PriceSource(source_id="supplier_2", raw_header="Поставщик 2"),
    ]

    matches, warnings = _match_offers_to_price_sources(
        offers,
        price_sources,
        required_source_ids=["supplier_1", "supplier_2", "supplier_3"],
    )

    assert matches["supplier_3"] is offers[2]
    assert any("Поставщик3" in warning and "по порядку загрузки" in warning for warning in warnings)


def test_offer_source_mapping_falls_back_when_number_is_truncated():
    offers = [
        CommercialOfferSchema(supplier_name="Поставщик 1"),
        CommercialOfferSchema(
            supplier_name="Поставщик 2",
            outgoing_number="КС-КП 26-140",
        ),
    ]
    price_sources = [
        PriceSource(source_id="supplier_1", raw_header="Поставщик 1"),
        PriceSource(
            source_id="supplier_2",
            raw_header="Поставщик 2",
            outgoing_letter_number="КС-КП",
        ),
    ]

    matches, warnings = _match_offers_to_price_sources(offers, price_sources)

    assert matches["supplier_2"] is offers[1]
    assert any("реквизиты ОНМЦК не дали точного совпадения" in warning for warning in warnings)


def test_non_price_support_accepts_codes_and_unique_quantity_unit():
    coded_nmck = NmckItem(name="Товар", ktru_code="26.20.14.000-00000189")
    coded_offer = CommercialOfferItem(name="Другое название", ktru_code="26.20.14.000-00000189")
    assert _has_non_price_support(coded_nmck, coded_offer, [coded_offer])

    shaped_nmck = NmckItem(name="Комплект типа А", quantity=Decimal("7"), unit="шт")
    shaped_offer = CommercialOfferItem(name="Комплект оборудования", quantity=Decimal("7"), unit="штука")
    other_offer = CommercialOfferItem(name="Другая позиция", quantity=Decimal("2"), unit="шт")
    assert _has_non_price_support(shaped_nmck, shaped_offer, [shaped_offer, other_offer])


def test_validation_rejects_price_only_and_ignores_unrequested_duplicates():
    offer_item = CommercialOfferItem(
        row_number="1",
        name="Несвязанная позиция",
        unit_price=Decimal("100"),
    )
    package = _package(
        nmck_items=[
            NmckItem(
                name="Абстрактная услуга",
                supplier_prices=[SupplierPrice(source_id="supplier_1", unit_price=Decimal("100"))],
            )
        ],
        offer_items=[offer_item],
    )
    payload = _build_payload(package)
    decisions = [
        CommercialOfferMatchDecision(
            nmck_item_index=0,
            source_id="supplier_1",
            candidate_id="supplier_1:item:0",
            offer_item_row_number="1",
            status="confirmed",
        ),
        CommercialOfferMatchDecision(
            nmck_item_index=0,
            source_id="supplier_1",
            candidate_id="supplier_1:item:0",
            offer_item_row_number="1",
            status="confirmed",
        ),
        CommercialOfferMatchDecision(
            nmck_item_index=999,
            source_id="supplier_1",
            candidate_id="supplier_1:item:0",
            offer_item_row_number="1",
            status="confirmed",
        ),
    ]

    validated = _validate_decisions(package, decisions, payload)

    assert len(validated) == 1
    assert validated[0]["status"] == "ambiguous"
    assert "кроме цены" in validated[0]["reason"]


def test_confirmed_match_is_applied_only_to_requested_row():
    package = _package(
        nmck_items=[
            NmckItem(
                name="Комплект типа А",
                quantity=Decimal("4"),
                unit="шт",
                supplier_prices=[SupplierPrice(source_id="supplier_1", unit_price=Decimal("100"))],
                selected_min_unit_price=Decimal("100"),
                row_total_declared=Decimal("400"),
            )
        ],
        offer_items=[
            CommercialOfferItem(
                row_number="7",
                name="Комплект оборудования",
                quantity=Decimal("4"),
                unit="шт",
                unit_price=Decimal("100"),
                total_price=Decimal("400"),
            )
        ],
    )
    results = run_checks(package, commercial_offer_match_results=[{
        "nmck_item_index": 0,
        "source_id": "supplier_1",
        "offer_item_row_number": "7",
        "status": "confirmed",
    }])
    result = next(
        item for item in results.results
        if item.check_id == "manual.commercial_offers.onmck"
    )

    assert result.details["comparison_rows"][0]["offer_1"] == "100.00"


def test_client_failure_returns_metrics_without_exception(monkeypatch):
    package = _package(
        nmck_items=[
            NmckItem(
                name="Абстрактный тип",
                supplier_prices=[SupplierPrice(source_id="supplier_1", unit_price=Decimal("100"))],
            )
        ],
        offer_items=[CommercialOfferItem(row_number="1", name="Конкретная позиция")],
    )

    class BrokenCompletions:
        def create(self, **_kwargs):
            raise ConnectionError("offline")

    client = SimpleNamespace(chat=SimpleNamespace(completions=BrokenCompletions()))
    monkeypatch.setattr(commercial_offer_llm, "get_chatGPT_client", lambda: client)

    decisions, metrics = commercial_offer_llm.run_commercial_offer_matching_llm(package)

    assert decisions is None
    assert "ConnectionError" in metrics["error"]


def test_matcher_accepts_json_markdown_fence(monkeypatch):
    package = _package(
        nmck_items=[
            NmckItem(
                name="Абстрактный тип",
                supplier_prices=[SupplierPrice(source_id="supplier_1", unit_price=Decimal("100"))],
            )
        ],
        offer_items=[CommercialOfferItem(row_number="1", name="Конкретная позиция")],
    )
    content = json.dumps({
        "decisions": [{
            "nmck_item_index": 0,
            "source_id": "supplier_1",
            "offer_item_row_number": "1",
            "status": "ambiguous",
            "reason": "Недостаточно признаков",
        }],
        "warnings": [],
    }, ensure_ascii=False)
    response = SimpleNamespace(
        usage=None,
        choices=[SimpleNamespace(message=SimpleNamespace(content=f"```json\n{content}\n```"))],
    )
    completions = SimpleNamespace(create=lambda **_kwargs: response)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setattr(commercial_offer_llm, "get_chatGPT_client", lambda: client)

    decisions, metrics = commercial_offer_llm.run_commercial_offer_matching_llm(package)

    assert decisions[0]["status"] == "ambiguous"
    assert "error" not in metrics


def test_matcher_schema_requires_candidate_id_even_when_value_can_be_null():
    schema = CommercialOfferMatchDecision.model_json_schema()

    assert "candidate_id" in schema["required"]
    assert {variant.get("type") for variant in schema["properties"]["candidate_id"]["anyOf"]} == {
        "string",
        "null",
    }


def test_matcher_recovers_top_level_array_alias_and_unique_source(monkeypatch):
    package = _package(
        nmck_items=[
            NmckItem(
                name="Комплект типа А",
                quantity=Decimal("4"),
                unit="шт",
                supplier_prices=[SupplierPrice(source_id="supplier_1", unit_price=Decimal("100"))],
                selected_min_unit_price=Decimal("100"),
                row_total_declared=Decimal("400"),
            )
        ],
        offer_items=[
            CommercialOfferItem(
                row_number="1.2.3",
                name="Комплект оборудования",
                quantity=Decimal("4"),
                unit="шт",
                unit_price=Decimal("100"),
            )
        ],
    )
    content = (
        '```json[{"nmck_item_index":0,"offer_item_index":0,'
        '"status":"confirmed","reason":"Совпали количество и единица"}]```'
    )
    response = SimpleNamespace(
        usage=None,
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_kwargs: response))
    )
    monkeypatch.setattr(commercial_offer_llm, "get_chatGPT_client", lambda: client)

    decisions, metrics = commercial_offer_llm.run_commercial_offer_matching_llm(package)

    assert decisions == [{
        "nmck_item_index": 0,
        "nmck_row_number": None,
        "source_id": "supplier_1",
        "candidate_id": "supplier_1:item:0",
        "offer_item_row_number": "1.2.3",
        "status": "confirmed",
        "evidence": None,
        "reason": "Совпали количество и единица",
    }]
    assert len(metrics["normalization_warnings"]) == 3
    assert "error" not in metrics


def test_matcher_uses_candidate_id_when_visible_row_number_is_missing(monkeypatch):
    package = _package(
        nmck_items=[
            NmckItem(
                name="Комплект типа А",
                quantity=Decimal("4"),
                unit="шт",
                supplier_prices=[SupplierPrice(source_id="supplier_1", unit_price=Decimal("100"))],
                selected_min_unit_price=Decimal("100"),
                row_total_declared=Decimal("400"),
            )
        ],
        offer_items=[
            CommercialOfferItem(
                name="Комплект оборудования",
                quantity=Decimal("4"),
                unit="шт",
                unit_price=Decimal("100"),
            )
        ],
    )
    content = json.dumps({
        "decisions": [{
            "nmck_item_index": 0,
            "source_id": "supplier_1",
            "candidate_id": "supplier_1:item:0",
            "offer_item_row_number": None,
            "status": "confirmed",
            "evidence": "Совпали количество и единица",
        }],
        "warnings": [],
    }, ensure_ascii=False)
    response = SimpleNamespace(
        usage=None,
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_kwargs: response))
    )
    monkeypatch.setattr(commercial_offer_llm, "get_chatGPT_client", lambda: client)

    decisions, metrics = commercial_offer_llm.run_commercial_offer_matching_llm(package)

    assert decisions[0]["status"] == "confirmed"
    assert decisions[0]["candidate_id"] == "supplier_1:item:0"
    assert decisions[0]["offer_item_row_number"] is None
    assert "error" not in metrics

    checks = run_checks(package, commercial_offer_match_results=decisions)
    comparison = next(
        item for item in checks.results
        if item.check_id == "manual.commercial_offers.onmck"
    )
    assert comparison.details["comparison_rows"][0]["offer_1"] == "100.00"


def test_lab_runs_production_matcher_and_writes_debug_artifacts(tmp_path, monkeypatch):
    input_pdf = tmp_path / "offer.pdf"
    input_pdf.write_bytes(b"fake pdf")
    package_path = tmp_path / "package.json"
    source_package = _package(
        nmck_items=[
            NmckItem(
                name="Комплект типа А",
                quantity=Decimal("4"),
                unit="шт",
                supplier_prices=[SupplierPrice(source_id="supplier_1", unit_price=Decimal("100"))],
                selected_min_unit_price=Decimal("100"),
                row_total_declared=Decimal("400"),
            )
        ],
        offer_items=[],
    )
    package_path.write_text(
        source_package.model_dump_json(indent=2),
        encoding="utf-8",
    )
    extracted_offer = CommercialOfferSchema(
        supplier_name="ООО Тест",
        items=[
            CommercialOfferItem(
                name="Комплект оборудования",
                quantity=Decimal("4"),
                unit="шт",
                unit_price=Decimal("100"),
            )
        ],
    )
    monkeypatch.setattr(
        commercial_offer_lab,
        "extract_commercial_offer_with_vlm",
        lambda *_args, **_kwargs: SimpleNamespace(
            offer=extracted_offer,
            metrics={"called": True},
        ),
    )
    captured = {}

    def fake_matcher(package, *, model):
        captured["package"] = package
        captured["model"] = model
        return ([{
            "nmck_item_index": 0,
            "source_id": "supplier_1",
            "candidate_id": "supplier_1:item:0",
            "offer_item_row_number": None,
            "status": "confirmed",
        }], {
            "model": model,
            "called": True,
            "raw_output": '{"decisions":[]}',
            "normalized_response": {"decisions": [], "warnings": []},
        })

    monkeypatch.setattr(
        commercial_offer_lab,
        "run_commercial_offer_matching_llm",
        fake_matcher,
    )
    output_dir = tmp_path / "lab"

    exit_code = commercial_offer_lab.main([
        "--input", str(input_pdf),
        "--package", str(package_path),
        "--model", "vlm-test",
        "--matcher-model", "matcher-test",
        "--output-dir", str(output_dir),
    ])

    target = output_dir / "vlm-test__matcher_matcher-test"
    assert exit_code == 0
    assert captured["model"] == "matcher-test"
    assert captured["package"].commercial_offers[0].supplier_name == "ООО Тест"
    assert (target / "matcher_payload.json").is_file()
    assert (target / "matcher_raw_response.txt").read_text(encoding="utf-8")
    assert json.loads((target / "matcher_decisions.json").read_text(encoding="utf-8"))[0][
        "candidate_id"
    ] == "supplier_1:item:0"
    assert "100.00" in (target / "report.txt").read_text(encoding="utf-8")
