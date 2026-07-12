from __future__ import annotations

import json
import re
from pathlib import Path

from summary_model.domain.models import DocumentIR
from summary_model.tables.models import ParsedTable


def _safe_name(value: str) -> str:
    value = re.sub(r"[^\w.\-]+", "_", value, flags=re.UNICODE).strip("_")
    return value[:120] or "document"


def export_table_debug(
    output_root: Path,
    ir: DocumentIR,
    parsed_tables: list[ParsedTable],
) -> None:
    target = output_root / "tables" / _safe_name(ir.file_name)
    target.mkdir(parents=True, exist_ok=True)
    table_by_id = {
        block.table.table_id: block.table
        for block in ir.blocks
        if block.table is not None
    }
    for parsed in parsed_tables:
        table = table_by_id.get(parsed.table_id)
        stem = f"table_{parsed.table_index}"
        if table is not None:
            (target / f"{stem}_physical.md").write_text(
                _physical_markdown(table.matrix()),
                encoding="utf-8",
            )
        (target / f"{stem}_logical.json").write_text(
            json.dumps(
                [row.model_dump(mode="json") for row in parsed.logical_rows],
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        (target / f"{stem}_compact.md").write_text(
            parsed.compact_markdown,
            encoding="utf-8",
        )


def _physical_markdown(matrix: list[list[str]]) -> str:
    if not matrix:
        return ""
    width = max(len(row) for row in matrix)
    normalized = [row + [""] * (width - len(row)) for row in matrix]
    lines = [
        "| " + " | ".join(f"c{index}" for index in range(width)) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
    ]
    for row in normalized:
        lines.append("| " + " | ".join(cell.replace("\n", "<br>") for cell in row) + " |")
    return "\n".join(lines)

