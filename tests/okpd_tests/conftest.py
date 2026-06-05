from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.procurement_reference_registry import ProcurementReferenceRegistry


@pytest.fixture(scope="session")
def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def data_dir(project_root: Path) -> Path:
    return project_root / "data"


@pytest.fixture(scope="session")
def parsed_dir(data_dir: Path) -> Path:
    return data_dir / "parsed_tables"


@pytest.fixture(scope="session")
def raw_html_path(data_dir: Path) -> Path:
    return data_dir / "raw_1875.html"


@pytest.fixture(scope="session")
def manifest_path(parsed_dir: Path) -> Path:
    return parsed_dir / "tables_manifest.json"


@pytest.fixture(scope="session")
def index_json_path(parsed_dir: Path) -> Path:
    return parsed_dir / "okpd_index.json"


@pytest.fixture(scope="session")
def sqlite_path(parsed_dir: Path) -> Path:
    return parsed_dir / "pp1875.sqlite"


@pytest.fixture(scope="session")
def manifest(manifest_path: Path) -> dict:
    return json.loads(manifest_path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def index_rows(index_json_path: Path) -> list[dict]:
    return json.loads(index_json_path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def table_files(parsed_dir: Path) -> list[Path]:
    return sorted(parsed_dir.glob("table_*.json"))


@pytest.fixture(scope="session")
def tables_by_id(table_files: list[Path]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for path in table_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        result[payload["table_id"]] = payload
    return result


@pytest.fixture(scope="session")
def registry(parsed_dir: Path) -> ProcurementReferenceRegistry:
    return ProcurementReferenceRegistry(base_dir=parsed_dir)