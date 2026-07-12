from __future__ import annotations

import json

from summary_model.domain.models import DocumentIR, TableIR


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _table_lines(table: TableIR, indent: str) -> list[str]:
    field_blocks: list[list[str]] = [
        [f'{indent} "table_id":{_json(table.table_id)}'],
    ]
    for name, value in (
        ("title", table.title),
        ("context_before", table.context_before),
        ("context_after", table.context_after),
    ):
        if value:
            field_blocks.append([f'{indent} "{name}":{_json(value)}'])
    field_blocks.append([f'{indent} "row_count":{table.row_count}'])

    field_blocks.append(
        [
            f'{indent} "columns":'
            + _json(
                [
                    column.model_dump(
                        mode="json",
                        exclude_defaults=True,
                        exclude_none=True,
                    )
                    for column in table.columns
                ]
            )
        ]
    )

    row_lines = [f'{indent} "rows":[']
    for row_index, row in enumerate(table.rows):
        comma = "," if row_index < len(table.rows) - 1 else ""
        row_lines.append(
            f"{indent}  "
            + _json(
                row.model_dump(
                    mode="json",
                    exclude_defaults=True,
                    exclude_none=True,
                )
            )
            + comma
        )
    row_lines.append(f"{indent} ]")
    field_blocks.append(row_lines)
    if table.header_rows:
        field_blocks.append(
            [f'{indent} "header_rows":{_json(table.header_rows)}']
        )
    field_blocks.append([f'{indent} "kind":{_json(table.kind)}'])

    lines = [f"{indent}{{"]
    for block_index, block in enumerate(field_blocks):
        if block_index < len(field_blocks) - 1:
            block[-1] += ","
        lines.extend(block)
    lines.append(f"{indent}}}")
    return lines


def document_ir_json(ir: DocumentIR) -> str:
    lines = [
        "{",
        f' "document_id":{_json(ir.document_id)},',
        f' "file_name":{_json(ir.file_name)},',
        f' "media_type":{_json(ir.media_type)},',
        ' "blocks":[',
    ]
    for block_index, block in enumerate(ir.blocks):
        comma = "," if block_index < len(ir.blocks) - 1 else ""
        if block.table is None:
            payload = block.model_dump(
                mode="json",
                exclude_none=True,
                exclude_defaults=True,
            )
            lines.append(f"  {_json(payload)}{comma}")
            continue
        lines.extend(
            [
                "  {",
                f'   "block_id":{_json(block.block_id)},',
                f'   "order":{block.order},',
                f'   "type":{_json(block.type)},',
                '   "table":',
                *_table_lines(block.table, "   "),
                f"  }}{comma}",
            ]
        )
    lines.append(" ]")
    if ir.warnings:
        lines[-1] += ","
        lines.append(f' "warnings":{_json(ir.warnings)}')
    lines.append("}")
    return "\n".join(lines)
