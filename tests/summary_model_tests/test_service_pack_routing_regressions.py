from datetime import date
from decimal import Decimal

import pytest

from summary_model.checks import run_checks
from summary_model.checks.runner import (
    _confirmed_single_service_package,
    _match_offer_items,
    _single_service_offer_mapping_allowed,
)
from summary_model.checks.nmck_layout import build_nmck_row_layout
from summary_model.domain.models import DocumentType, TableColumnIR, TableIR, TableRowIR
from summary_model.extraction_models import (
    CommercialOfferItem,
    CommercialOfferSchema,
    NmckItem,
    NmckJustificationSchema,
    PriceSource,
    ProcurementPackageExtraction,
    ProcurementStage,
    PurchaseDescriptionSchema,
    PurchaseItem,
    ScheduleApplicationSchema,
    SupplierPrice,
)
from summary_model.tables.table_classifier import classify_parsed_table


def _service_package() -> ProcurementPackageExtraction:
    source_date = date(2026, 9, 24)
    return ProcurementPackageExtraction(
        schedule_application=ScheduleApplicationSchema(
            purchase_subject="Оказание услуг по передаче видеопотока",
            okpd2_codes=["63.11.21.000"],
        ),
        purchase_description=PurchaseDescriptionSchema(
            purchase_subject="Оказание услуг по передаче видеопотока",
            aggregate_quantity_text="2208 часов",
        ),
        nmck_justification=NmckJustificationSchema(
            price_sources=[
                PriceSource(
                    source_id="supplier_1",
                    raw_header="КП Лето",
                    supplier_name_raw="Лето",
                    outgoing_letter_number="Л-24",
                    outgoing_letter_date=source_date,
                )
            ],
            items=[
                NmckItem(
                    name="Оказание услуг по передаче видеопотока",
                    quantity=Decimal("2208"),
                    unit="час",
                    supplier_prices=[
                        SupplierPrice(
                            source_id="supplier_1",
                            unit_price=Decimal("100"),
                            row_total=Decimal("220800"),
                        )
                    ],
                    selected_min_unit_price=Decimal("100"),
                )
            ],
        ),
        commercial_offers=[
            CommercialOfferSchema(
                supplier_name="Лето",
                outgoing_number="Л-24",
                outgoing_date=source_date,
                purchase_subject="Оказание услуг по передаче видеопотока",
                items=[
                    CommercialOfferItem(
                        name="Услуга Лето с ошибочным наименованием",
                        quantity=Decimal("1"),
                        unit="кг",
                        unit_price=Decimal("99"),
                        total_price=Decimal("99"),
                    )
                ],
            )
        ],
    )


def _by_id(report):
    return {result.check_id: result for result in report.results}


def test_service_pack_maps_corrupted_single_leto_row_and_reports_mismatches():
    package = _service_package()
    layout = build_nmck_row_layout(package)

    assert _confirmed_single_service_package(package, layout)[0]
    assert _single_service_offer_mapping_allowed(
        package,
        layout,
        package.nmck_justification,
        "supplier_1",
        package.commercial_offers[0],
    )

    checks = _by_id(run_checks(package))
    item_check = checks["strict.onmck.items"]
    offer_check = checks["manual.commercial_offers.onmck"]

    assert item_check.status == "passed"
    assert item_check.details["service_subject_route"] is True
    assert offer_check.details["comparison_rows"][0]["offer_1"] == "99.00"
    failures = offer_check.details["failures"]
    assert any("наименование" in failure for failure in failures)
    assert any("единица" in failure for failure in failures)
    assert any("цена за единицу" in failure for failure in failures)


def test_corrupted_nmck_service_name_and_unit_do_not_block_proven_offer_mapping():
    package = _service_package()
    package.nmck_justification.items[0].name = "Лето"
    package.nmck_justification.items[0].unit = "кг"
    package.commercial_offers[0].items[0].name = package.purchase_description.purchase_subject
    package.commercial_offers[0].items[0].unit = "час"
    checks = _by_id(run_checks(package))
    assert checks["strict.onmck.items"].status == "failed"
    assert checks["manual.commercial_offers.onmck"].details["comparison_rows"][0]["offer_1"] == "99.00"
    assert any("единица" in failure for failure in checks["manual.commercial_offers.onmck"].details["failures"])


@pytest.mark.parametrize("mutation", ["goods", "ooz_goods", "stages", "mixed", "multiple_services"])
def test_single_service_route_rejects_non_single_service_structure(mutation):
    package = _service_package()
    if mutation == "goods":
        package.schedule_application.included_goods = [PurchaseItem(name="Камера", quantity=1, unit="шт")]
    elif mutation == "ooz_goods":
        package.purchase_description.items = [PurchaseItem(name="Камера 1", quantity=1, unit="шт")]
    elif mutation == "stages":
        package.schedule_application.stages = [ProcurementStage(stage_number="1")]
        package.nmck_justification.stages = [ProcurementStage(stage_number="1")]
    elif mutation == "mixed":
        package.nmck_justification.stages = [ProcurementStage(stage_number="1")]
    else:
        package.nmck_justification.items.append(
            NmckItem(name="Вторая услуга", quantity=1, unit="час")
        )

    layout = build_nmck_row_layout(package)

    assert not _confirmed_single_service_package(package, layout)[0]
    assert not _single_service_offer_mapping_allowed(
        package,
        layout,
        package.nmck_justification,
        "supplier_1",
        package.commercial_offers[0],
    )


def test_named_supplier_and_subject_link_maps_service_row_without_prices():
    package = _service_package()
    package.nmck_justification.price_sources[0].outgoing_letter_date = None
    package.nmck_justification.items[0].supplier_prices[0].unit_price = None
    package.commercial_offers[0].items[0].unit_price = None
    layout = build_nmck_row_layout(package)

    assert _single_service_offer_mapping_allowed(
        package,
        layout,
        package.nmck_justification,
        "supplier_1",
        package.commercial_offers[0],
    )

    offer_check = _by_id(run_checks(package))["manual.commercial_offers.onmck"]
    assert offer_check.details["comparison_rows"][0]["offer_1"] is None
    assert any("цена за единицу" in item for item in offer_check.details["manual_review"])


@pytest.mark.parametrize(
    ("supplier_name", "purchase_subject", "duplicate_source"),
    [
        ("ООО Альфа Бета", "Оказание услуг по передаче видеопотока", False),
        ("ООО Лето", "Оказание услуг", False),
        ("ООО Лето", "Оказание услуг по передаче видеопотока", True),
    ],
)
def test_named_supplier_service_mapping_rejects_ambiguous_identity_and_generic_subject(
    supplier_name,
    purchase_subject,
    duplicate_source,
):
    package = _service_package()
    package.nmck_justification.price_sources[0].outgoing_letter_date = None
    package.commercial_offers[0].supplier_name = supplier_name
    package.commercial_offers[0].purchase_subject = purchase_subject
    if duplicate_source:
        package.nmck_justification.price_sources.append(
            PriceSource(source_id="supplier_2", raw_header="КП Лето", supplier_name_raw="Лето")
        )

    layout = build_nmck_row_layout(package)

    assert not _single_service_offer_mapping_allowed(
        package,
        layout,
        package.nmck_justification,
        "supplier_1",
        package.commercial_offers[0],
    )


def test_camera_location_form_is_ignored_but_camera_goods_table_is_not():
    location_table = TableIR(
        table_id="location",
        title="Адресный план сцен обзора",
        row_count=1,
        columns=[
            TableColumnIR(index=index, alias=f"c{index}", header_path=[header])
            for index, header in enumerate(
                ["Наименование средства видеонаблюдения", "Широта", "Долгота", "Адрес", "RTSP ссылка"]
            )
        ],
        rows=[TableRowIR(row_id="r0", row=0, values={"c0": "Камера 1"})],
        kind="item_list",
    )
    goods_table = TableIR(
        table_id="goods",
        row_count=2,
        columns=[
            TableColumnIR(index=index, alias=f"c{index}", header_path=[header])
            for index, header in enumerate(["Наименование камеры", "Количество", "Цена за единицу"])
        ],
        rows=[TableRowIR(row_id="r0", row=0, values={"c0": "Камера 1", "c1": "1", "c2": "100"})],
        kind="item_list",
    )

    assert classify_parsed_table(location_table, DocumentType.OOZ) == "ignored_table"
    assert classify_parsed_table(goods_table, DocumentType.OOZ) == "ooz_items_table"
    location_table.title = "Перечень закупаемого оборудования"
    location_table.columns.append(TableColumnIR(index=5, alias="c5", header_path=["Количество"]))
    assert classify_parsed_table(location_table, DocumentType.OOZ) == "ooz_items_table"


def test_real_camera_good_does_not_gain_one_row_service_match():
    matches, _reasons = _match_offer_items(
        [NmckItem(name="Камера видеонаблюдения", quantity=1, unit="шт")],
        [CommercialOfferItem(name="Camera1", quantity=1, unit="шт", unit_price=Decimal("100"))],
        source_id="supplier_1",
    )

    assert matches == {}
