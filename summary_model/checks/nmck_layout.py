from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from summary_model.extraction_models import ProcurementPackageExtraction


NmckRowRole = Literal["stage", "stage_item", "product"]
NmckLayoutMode = Literal["empty", "products", "stages", "mixed"]


@dataclass(frozen=True)
class NmckRowLayout:
    mode: NmckLayoutMode
    roles: tuple[NmckRowRole, ...]
    stage_numbers: tuple[str, ...]

    def role_for(self, index: int) -> NmckRowRole:
        return self.roles[index]


def build_nmck_row_layout(package: ProcurementPackageExtraction) -> NmckRowLayout:
    """Classify ONMCK rows once, using extracted structure rather than row names."""
    onmck = package.nmck_justification
    items = list(getattr(onmck, "items", []) or []) if onmck else []
    # The plan flag is not a layout signal: an empty embedded stage table can
    # set has_stages=True. Only stage rows extracted from ONMCK are authoritative.
    stage_numbers = _stage_numbers(getattr(onmck, "stages", []) if onmck else [])
    roles: list[NmckRowRole] = []
    claimed_stage_rows: set[str] = set()
    for item in items:
        parent = nmck_parent_stage_number(
            getattr(item, "parent_stage_number", None),
            getattr(item, "row_number", None),
        )
        row_stage = nmck_top_level_number(getattr(item, "row_number", None))
        if parent and parent in stage_numbers:
            roles.append("stage_item")
        elif row_stage and row_stage in stage_numbers and row_stage not in claimed_stage_rows:
            roles.append("stage")
            claimed_stage_rows.add(row_stage)
        else:
            roles.append("product")

    has_stage_structure = bool(stage_numbers)
    has_products = any(role in {"stage_item", "product"} for role in roles)
    if not items and not stage_numbers:
        mode: NmckLayoutMode = "empty"
    elif has_stage_structure and has_products:
        mode = "mixed"
    elif has_stage_structure:
        mode = "stages"
    else:
        mode = "products"
    return NmckRowLayout(
        mode=mode,
        roles=tuple(roles),
        stage_numbers=tuple(stage_numbers),
    )


def nmck_top_level_number(value: object) -> str | None:
    match = re.fullmatch(r"\s*(\d+)\s*\.?\s*", str(value or ""))
    return match.group(1) if match else None


def nmck_parent_stage_number(explicit: object, row_number: object) -> str | None:
    explicit_number = _first_number(explicit)
    if explicit_number:
        return explicit_number
    match = re.fullmatch(r"\s*(\d+)\s*\.\s*\d+\s*\.?\s*", str(row_number or ""))
    return match.group(1) if match else None


def _stage_numbers(stages: object) -> list[str]:
    result: list[str] = []
    for stage in stages or []:
        number = _first_number(getattr(stage, "stage_number", None))
        if number and number not in result:
            result.append(number)
    return result


def _first_number(value: object) -> str | None:
    match = re.search(r"\d+", str(value or ""))
    return match.group(0) if match else None
