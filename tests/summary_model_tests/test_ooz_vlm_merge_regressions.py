from summary_model.domain.models import DocumentBlockIR, DocumentIR
from summary_model.tables.models import LogicalTableRow, ParsedTable
from summary_model.tables.utils import KTRU_RE
from summary_model.vlm_fallback import (
    _discard_unseen_item_codes,
    _document_codes,
    _merge_role_result,
)


def _characteristic(row_index, name, value=None, **extra):
    return {"row_index": row_index, "name": name, "value": value, **extra}


def _item(row_index, name, characteristics, **extra):
    return {
        "row_index": row_index,
        "row_number": str(row_index),
        "name": name,
        "ktru_code": "26.20.16.000-00000001",
        "characteristics": characteristics,
        **extra,
    }


def _table(items, logical_rows=()):
    return ParsedTable(
        table_id="table-1",
        block_id="block-1",
        table_index=1,
        table_type="ooz_items_table",
        row_count=20,
        col_count=4,
        compact_json={"items": items},
        logical_rows=list(logical_rows),
    )


def _source_row(row_index, parent_row_index, *, confidence=0.9, warnings=()):
    return LogicalTableRow(
        table_id="table-1",
        row_index=row_index,
        row_type="characteristic",
        parent_row_index=parent_row_index,
        confidence=confidence,
        warnings=list(warnings),
    )


def _merge(base_items, repaired_items, logical_rows=()):
    return _merge_role_result(
        _table(base_items, logical_rows),
        _table(repaired_items),
        "purchase_description",
    ).compact_json["items"]


def test_repeated_lan_and_usb_characteristic_rows_are_preserved():
    characteristics = [
        _characteristic(10, "LAN", "RJ-45"),
        _characteristic(11, "LAN", "RJ-45"),
        _characteristic(12, "USB", "USB 3.0"),
        _characteristic(13, "USB", "USB 3.0"),
    ]
    items = _merge(
        [_item(1, "Маршрутизатор", characteristics)],
        [_item(1, "Маршрутизатор", [])],
        [_source_row(index, 1) for index in range(10, 14)],
    )

    assert [item["row_index"] for item in items[0]["characteristics"]] == [10, 11, 12, 13]
    assert [item["name"] for item in items[0]["characteristics"]] == ["LAN", "LAN", "USB", "USB"]


def test_same_source_characteristic_row_is_not_duplicated():
    items = _merge(
        [_item(1, "Коммутатор", [_characteristic(10, "LAN", "RJ-45")])],
        [_item(1, "Коммутатор", [
            _characteristic(10, "LAN", None),
            _characteristic(10, "LAN", "RJ-45"),
        ])],
        [_source_row(10, 1)],
    )

    assert items[0]["characteristics"] == [_characteristic(10, "LAN", "RJ-45")]


def test_vlm_fills_missing_reliable_characteristic_fields():
    items = _merge(
        [_item(1, "Коммутатор", [_characteristic(10, "Количество портов")])],
        [_item(1, "Коммутатор", [_characteristic(10, None, "24", unit="шт.")])],
        [_source_row(10, 1)],
    )

    assert items[0]["characteristics"] == [
        _characteristic(10, "Количество портов", "24", unit="шт.")
    ]


def test_products_with_same_ktru_code_are_not_merged():
    items = _merge(
        [
            _item(1, "Коммутатор", [_characteristic(10, "LAN", "RJ-45")]),
            _item(2, "Маршрутизатор", [_characteristic(11, "USB", "USB 3.0")]),
        ],
        [_item(1, "Коммутатор", [])],
        [_source_row(10, 1), _source_row(11, 2)],
    )

    assert [item["name"] for item in items] == ["Коммутатор", "Маршрутизатор"]
    assert items[1]["characteristics"] == [_characteristic(11, "USB", "USB 3.0")]


def test_uncertain_characteristic_row_can_be_repaired_by_vlm():
    items = _merge(
        [_item(1, "Коммутатор", [_characteristic(10, "Порты", "неразборчиво", warnings=["ambiguous"])])],
        [_item(1, "Коммутатор", [_characteristic(10, "Количество портов", "24")])],
        [_source_row(10, 1, confidence=0.35, warnings=["ambiguous source row"])],
    )

    assert items[0]["characteristics"] == [_characteristic(10, "Количество портов", "24")]


def test_unindexed_identical_vlm_characteristic_consumes_source_row():
    items = _merge(
        [_item(1, "Коммутатор", [_characteristic(10, "Цвет", "черный")])],
        [_item(1, "Коммутатор", [_characteristic(None, "Цвет", "черный")])],
        [_source_row(10, 1)],
    )

    assert items[0]["characteristics"] == [_characteristic(10, "Цвет", "черный")]
    assert "parser_warnings" not in items[0]


def test_unindexed_vlm_rows_do_not_invent_extra_repeated_lan_or_usb_rows():
    items = _merge(
        [_item(1, "Коммутатор", [
            _characteristic(10, "LAN", "RJ-45"),
            _characteristic(11, "LAN", "RJ-45"),
            _characteristic(12, "USB", "USB 3.0"),
            _characteristic(13, "USB", "USB 3.0"),
        ])],
        [_item(1, "Коммутатор", [
            _characteristic(None, "LAN", "RJ-45"),
            _characteristic(None, "USB", "USB 3.0"),
        ])],
        [_source_row(index, 1) for index in range(10, 14)],
    )

    assert [characteristic["name"] for characteristic in items[0]["characteristics"]] == [
        "LAN", "USB", "LAN", "USB",
    ]
    assert len(items[0]["characteristics"]) == 4


def test_missing_logical_source_row_is_not_preserved_as_reliable():
    items = _merge(
        [_item(1, "Коммутатор", [_characteristic(10, "Цвет", "черный")])],
        [_item(1, "Коммутатор", [])],
    )

    assert items[0]["characteristics"] == []


def test_conflicting_unindexed_vlm_characteristic_is_retained_with_warning():
    items = _merge(
        [_item(1, "Коммутатор", [_characteristic(10, "Цвет", "черный")])],
        [_item(1, "Коммутатор", [_characteristic(None, "Цвет", "белый")])],
        [_source_row(10, 1)],
    )

    assert [characteristic["value"] for characteristic in items[0]["characteristics"]] == [
        "белый", "черный",
    ]
    assert any("without row_index retained" in warning for warning in items[0]["parser_warnings"])


def test_ambiguous_vlm_item_row_index_does_not_select_first_candidate():
    items = _merge(
        [_item(1, "Коммутатор", [_characteristic(10, "LAN", "RJ-45")])],
        [
            _item(1, "Маршрутизатор", []),
            _item(1, "Точка доступа", []),
        ],
        [_source_row(10, 1)],
    )

    assert [item["name"] for item in items] == [
        "Маршрутизатор",
        "Точка доступа",
        "Коммутатор",
    ]
    assert items[-1]["characteristics"] == [_characteristic(10, "LAN", "RJ-45")]


def test_ktru_whitespace_is_canonicalized_for_source_whitelist():
    canonical = "26.20.16.000-00000001"
    document = DocumentIR(
        document_id="ooz-1",
        file_name="ooz.docx",
        media_type="docx",
        blocks=[DocumentBlockIR(
            block_id="paragraph-1",
            order=1,
            type="paragraph",
            text="КТРУ 26 . 20 . 16 . 000 - 00000001",
        )],
    )
    table = _table([_item(1, "Коммутатор", [])])
    table.compact_json["items"] = [
        {"name": "Коммутатор", "ktru_code": "26\t.20 . 16.000 - 00000001"},
        {"name": "Другой товар", "ktru_code": "26.20.16.000-00000002"},
    ]

    allowed_codes = _document_codes(document, KTRU_RE)
    _discard_unseen_item_codes(
        table,
        allowed_ktru_codes=allowed_codes,
        allowed_okpd2_codes=set(),
    )

    assert allowed_codes == {canonical}
    assert table.compact_json["items"][0]["ktru_code"] == canonical
    assert table.compact_json["items"][1]["ktru_code"] is None
