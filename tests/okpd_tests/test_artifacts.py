from __future__ import annotations


def test_raw_html_exists(raw_html_path):
    assert raw_html_path.exists(), f"Raw HTML file not found: {raw_html_path}"
    assert raw_html_path.stat().st_size > 0, "Raw HTML file is empty"


def test_manifest_exists(manifest_path):
    assert manifest_path.exists(), f"Manifest file not found: {manifest_path}"
    assert manifest_path.stat().st_size > 0, "Manifest file is empty"


def test_index_json_exists(index_json_path):
    assert index_json_path.exists(), f"Index JSON file not found: {index_json_path}"
    assert index_json_path.stat().st_size > 0, "Index JSON file is empty"


def test_sqlite_exists(sqlite_path):
    assert sqlite_path.exists(), f"SQLite file not found: {sqlite_path}"
    assert sqlite_path.stat().st_size > 0, "SQLite file is empty"


def test_table_files_exist(table_files):
    assert table_files, "No parsed table JSON files found"