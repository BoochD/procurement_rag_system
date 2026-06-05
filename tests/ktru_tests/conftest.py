from __future__ import annotations

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
def registry(parsed_dir: Path) -> ProcurementReferenceRegistry:
    return ProcurementReferenceRegistry(base_dir=parsed_dir)