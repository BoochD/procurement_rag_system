from __future__ import annotations

from collections import defaultdict

from summary_model.domain.models import TableIR


def _header_legend(table: TableIR) -> list[str]:
    if not table.header_rows:
        return [
            "COLUMNS: "
            + " | ".join(
                f"{column.alias}=column_{column.index}"
                for column in table.columns
            )
        ]
    lines: list[str] = []
    row_lookup = {row.row: row for row in table.rows}
    for level, row_index in enumerate(table.header_rows, start=1):
        row = row_lookup.get(row_index)
        if row is None:
            continue
        values: list[tuple[str, str]] = []
        for column in table.columns:
            text = row.values.get(column.alias)
            if not text:
                continue
            _, column_span = row.spans.get(column.alias, (1, 1))
            alias = (
                f"{column.alias}..c{column.index + column_span - 1}"
                if column_span > 1
                else column.alias
            )
            values.append((alias, text))
        if values:
            lines.append(f"HEADER L{level}: {_format_values(values)}")
    return lines


def _format_values(values: list[tuple[str, str]]) -> str:
    return "; ".join(f"{alias}={text}" for alias, text in values)


def _render_key_value_rows(table: TableIR) -> list[str]:
    lines: list[str] = []
    header_rows = set(table.header_rows)
    for row in table.rows:
        if row.row in header_rows:
            continue
        ordered = []
        seen_values: set[str] = set()
        for column in table.columns:
            text = row.values.get(column.alias)
            if not text or text in seen_values:
                continue
            seen_values.add(text)
            ordered.append(text)
        if not ordered:
            continue
        content = (
            f"{ordered[0]} = {'; '.join(ordered[1:])}"
            if len(ordered) > 1
            else ordered[0]
        )
        lines.append(f"ROW r{row.row}: {content}")
    return lines


def render_table_for_llm(
    table: TableIR,
    *,
    block_id: str | None = None,
) -> str:
    title = f' "{table.title}"' if table.title else ""
    block = f" block_id={block_id}" if block_id else ""
    lines = [
        f"TABLE {table.table_id}{block}{title}",
        f"KIND: {table.kind}",
        *_header_legend(table),
    ]
    if table.kind == "key_value":
        lines.extend(["", *_render_key_value_rows(table)])
        return "\n".join(lines)

    header_rows = set(table.header_rows)
    vertical_scopes: dict[tuple[int, int], list[tuple[str, str]]] = defaultdict(list)
    ordinary_rows: dict[int, list[tuple[str, str]]] = defaultdict(list)

    for row in table.rows:
        if row.row in header_rows:
            continue
        for column in table.columns:
            text = row.values.get(column.alias)
            if not text:
                continue
            row_span, column_span = row.spans.get(column.alias, (1, 1))
            alias = (
                f"{column.alias}..c{column.index + column_span - 1}"
                if column_span > 1
                else column.alias
            )
            if row_span > 1:
                vertical_scopes[(row.row, row.row + row_span - 1)].append(
                    (alias, text)
                )
            else:
                ordinary_rows[row.row].append((alias, text))

    scope_ids = {
        interval: f"g{index}"
        for index, interval in enumerate(sorted(vertical_scopes), start=1)
    }
    if vertical_scopes:
        lines.append("")
        for interval in sorted(vertical_scopes):
            start, end = interval
            lines.append(
                f"SCOPE {scope_ids[interval]} rows=r{start}..r{end}: "
                f"{_format_values(vertical_scopes[interval])}"
            )

    if ordinary_rows:
        lines.append("")
    for row_index in sorted(ordinary_rows):
        scopes = [
            scope_id
            for (start, end), scope_id in scope_ids.items()
            if start <= row_index <= end
        ]
        scope_suffix = f" scope={','.join(scopes)}" if scopes else ""
        lines.append(
            f"ROW r{row_index}{scope_suffix}: "
            f"{_format_values(ordinary_rows[row_index])}"
        )
    return "\n".join(lines)
