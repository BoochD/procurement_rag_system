from __future__ import annotations

import re

from summary_model.domain.models import (
    TableColumnIR,
    TableIR,
    TableRowIR,
)


CODE_RE = re.compile(r"\b\d{2}(?:\.\d{2}){2}\.\d{3}(?:-\d{8})?\b")
MONEY_RE = re.compile(r"\d[\d\s]*(?:[,.]\d{2})")
ORDINAL_RE = re.compile(r"\d+[.)]?")
HEADER_MARKERS = (
    "наименование",
    "характерист",
    "значение",
    "единиц",
    "ед. изм",
    "количество",
    "кол-во",
    "цена",
    "стоимость",
    "сумма",
    "поставщик",
    "исполнитель",
    "окпд",
    "ктру",
    "№",
)


def _clean(value: str) -> str:
    return " ".join((value or "").replace("\xa0", " ").split()).strip()


def _nonempty(row: list[str]) -> list[str]:
    return [_clean(cell) for cell in row if _clean(cell)]


def _looks_like_data(row: list[str]) -> bool:
    values = _nonempty(row)
    if not values:
        return False
    if ORDINAL_RE.fullmatch(values[0]):
        return True
    if CODE_RE.search(values[0]):
        return True
    numeric = sum(bool(re.search(r"\d", value)) for value in values)
    money = sum(bool(MONEY_RE.fullmatch(value)) for value in values)
    return money >= 2 or numeric >= max(3, len(values) // 2 + 1)


def _header_score(row: list[str]) -> int:
    text = " ".join(_nonempty(row)).casefold()
    return sum(marker.casefold() in text for marker in HEADER_MARKERS)


def _looks_like_key_value(matrix: list[list[str]]) -> bool:
    if len(matrix) < 3 or not matrix or len(matrix[0]) > 4:
        return False
    candidates = 0
    labels: list[str] = []
    for row in matrix:
        values = _nonempty(row)
        if len(values) < 2:
            continue
        label = values[0]
        if (
            not ORDINAL_RE.fullmatch(label)
            and not CODE_RE.search(label)
            and not MONEY_RE.fullmatch(label)
        ):
            candidates += 1
            labels.append(label.casefold())
    return candidates >= 3 and len(set(labels)) >= max(2, candidates // 2)


def infer_header_rows(matrix: list[list[str]], max_rows: int = 8) -> list[int]:
    if not matrix or _looks_like_key_value(matrix):
        return []
    rows = [0]
    for index, row in enumerate(matrix[1:max_rows], start=1):
        if _looks_like_data(row):
            break
        if _header_score(row) == 0:
            break
        rows.append(index)
    return rows


def build_header_paths(
    matrix: list[list[str]],
    header_rows: list[int],
    width: int,
) -> list[list[str]]:
    paths: list[list[str]] = []
    for column in range(width):
        path: list[str] = []
        for row_index in header_rows:
            if row_index >= len(matrix) or column >= len(matrix[row_index]):
                continue
            value = _clean(matrix[row_index][column])
            if value and (not path or path[-1] != value):
                path.append(value)
        paths.append(path)
    return paths


def classify_table(
    matrix: list[list[str]],
    header_paths: list[list[str]],
    header_rows: list[int],
) -> str:
    if _looks_like_key_value(matrix):
        return "key_value"
    joined = " ".join(
        value
        for path in header_paths
        for value in path
    ).casefold()
    if (
        "поставщик" in joined or "исполнитель" in joined
    ) and ("цена" in joined or "стоимость" in joined):
        return "supplier_matrix"
    if "характерист" in joined and "значение" in joined:
        return "characteristics"
    if "ндс" in joined and ("цена" in joined or "сумма" in joined):
        return "specification"
    if "наименование" in joined and (
        "количество" in joined or "кол-во" in joined or "ед. изм" in joined
    ):
        return "item_list"
    return "unknown"


def normalize_table(table: TableIR) -> TableIR:
    matrix = table.matrix()
    header_rows = infer_header_rows(matrix)
    header_paths = build_header_paths(matrix, header_rows, table.column_count)
    if not header_rows and _looks_like_key_value(matrix):
        header_paths = [
            ["field"] if index == 0 else [f"value_{index}"]
            for index in range(table.column_count)
        ]
    table.header_rows = header_rows
    table.columns = [
        TableColumnIR(
            index=index,
            alias=f"c{index}",
            header_path=header_paths[index],
        )
        for index in range(table.column_count)
    ]
    table.kind = classify_table(matrix, header_paths, header_rows)
    return table


def build_rows(
    matrix_cells: list[list[tuple[str, object]]],
    table_id: str,
) -> tuple[list[TableColumnIR], list[TableRowIR]]:
    width = max((len(row) for row in matrix_cells), default=0)
    positions: dict[object, list[tuple[int, int]]] = {}
    for row_index, row in enumerate(matrix_cells):
        for column_index, (_, identity) in enumerate(row):
            positions.setdefault(identity, []).append((row_index, column_index))

    rows: dict[int, TableRowIR] = {}
    for identity, coordinates in sorted(
        positions.items(),
        key=lambda item: min(item[1]),
    ):
        origin_row, origin_column = min(coordinates)
        row_span = len({row for row, _ in coordinates})
        column_span = len({column for _, column in coordinates})
        text = _clean(matrix_cells[origin_row][origin_column][0])
        if not text and row_span == 1 and column_span == 1:
            continue
        target = rows.setdefault(
            origin_row,
            TableRowIR(
                row_id=f"{table_id}.r{origin_row}",
                row=origin_row,
            ),
        )
        alias = f"c{origin_column}"
        if text:
            target.values[alias] = text
        if row_span > 1 or column_span > 1:
            target.spans[alias] = (row_span, column_span)

    columns = [
        TableColumnIR(index=index, alias=f"c{index}")
        for index in range(width)
    ]
    return columns, [rows[index] for index in sorted(rows)]
