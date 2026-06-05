from __future__ import annotations


EXPECTED_ROW_KEYS = {
    "row_index",
    "position",
    "name",
    "okpd2_codes",
    "primary_okpd2",
    "raw_code_text",
    "raw_cells",
}


def test_manifest_basic_shape(manifest):
    assert isinstance(manifest, dict)
    assert manifest["source"] == "pp_1875"
    assert "table_count" in manifest
    assert "tables" in manifest
    assert isinstance(manifest["tables"], list)
    assert manifest["table_count"] == len(manifest["tables"])


def test_index_rows_not_empty(index_rows):
    assert isinstance(index_rows, list)
    assert len(index_rows) > 0


def test_index_row_shape(index_rows):
    sample = index_rows[0]
    required_keys = {
        "table_id",
        "table_title",
        "row_index",
        "position",
        "okpd2",
        "name",
        "row",
    }
    assert required_keys.issubset(sample.keys())


def test_index_rows_have_required_fields(index_rows):
    for row in index_rows[:100]:
        assert row["table_id"]
        assert row["table_title"]
        assert row["okpd2"]
        assert row["name"]
        assert isinstance(row["row"], dict)


def test_row_schema_is_normalized(index_rows):
    for row in index_rows[:200]:
        payload = row["row"]
        assert set(payload.keys()) == EXPECTED_ROW_KEYS


def test_no_legacy_broken_dynamic_keys(index_rows):
    forbidden_keys = {
        "Ткани текстильные",
        "13.2",
        "Щебень",
        "1.",
    }
    for row in index_rows[:300]:
        payload = row["row"]
        assert forbidden_keys.isdisjoint(payload.keys())


def test_row_okpd2_codes_not_empty(index_rows):
    for row in index_rows[:200]:
        payload = row["row"]
        assert isinstance(payload["okpd2_codes"], list)
        assert len(payload["okpd2_codes"]) >= 1


def test_row_primary_okpd2_consistent(index_rows):
    for row in index_rows[:200]:
        payload = row["row"]
        assert payload["primary_okpd2"] == payload["okpd2_codes"][0]


def test_table_file_shape(table_files):
    for path in table_files:
        payload = __import__("json").loads(path.read_text(encoding="utf-8"))
        assert "table_id" in payload
        assert "title" in payload
        assert "rows" in payload
        assert isinstance(payload["rows"], list)
        assert len(payload["rows"]) > 0