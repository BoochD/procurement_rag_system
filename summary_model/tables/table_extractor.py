from __future__ import annotations

from summary_model.domain.models import DocumentIR, DocumentType
from summary_model.tables.models import ParsedTable
from summary_model.tables.table_classifier import classify_parsed_table
from summary_model.tables.table_compactor import build_compact_json, build_compact_markdown
from summary_model.tables.table_logical_rows import build_logical_rows, header_paths


def extract_tables(
    ir: DocumentIR,
    document_type_hint: DocumentType | None = None,
) -> list[ParsedTable]:
    result: list[ParsedTable] = []
    table_index = 0
    for block in ir.blocks:
        table = block.table
        if table is None:
            continue
        table_index += 1
        paths = header_paths(table)
        table_type = classify_parsed_table(table, document_type_hint)
        logical_rows = build_logical_rows(table, table_type, paths)
        parsed = ParsedTable(
            table_id=table.table_id,
            block_id=block.block_id,
            table_index=table_index,
            document_type_hint=document_type_hint.value if document_type_hint else None,
            table_type=table_type,
            row_count=table.row_count,
            col_count=table.column_count,
            title=table.title,
            header_rows=table.header_rows,
            header_paths=paths,
            logical_rows=logical_rows,
            parser_warnings=[
                warning
                for row in logical_rows
                for warning in row.warnings
            ],
        )
        parsed.compact_json = build_compact_json(table_type, logical_rows)
        parsed.compact_markdown = build_compact_markdown(parsed)
        result.append(parsed)
    return result

