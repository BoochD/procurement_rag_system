from __future__ import annotations

import math
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from summary_model.domain.models import TableIR
from summary_model.tables.utils import clean_text


def render_table_image(
    table: TableIR,
    output_path: str | Path,
    *,
    max_width: int = 2600,
    font_size: int = 18,
    header_font_size: int = 18,
) -> dict[str, int | str]:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    font = _font(font_size)
    header_font = _font(header_font_size, bold=True)
    matrix = table.matrix()
    origin_cells = _origin_cells(table)
    col_count = max(1, table.column_count)
    margins = 32
    table_width = max_width - margins * 2
    col_widths = _column_widths(matrix, table.header_labels(), table_width, font_size)

    row_heights = [font_size + 26 for _ in range(max(1, table.row_count))]
    for cell in origin_cells:
        row_index = cell["row"]
        colspan = cell["colspan"]
        rowspan = cell["rowspan"]
        width = sum(col_widths[cell["col"] : cell["col"] + colspan])
        lines = _wrap_cell(clean_text(cell["text"]), width, font_size)
        height = max(1, len(lines)) * (font_size + 7) + 18
        if rowspan <= 1:
            row_heights[row_index] = max(row_heights[row_index], height)
        else:
            per_row = max(font_size + 26, int(height / rowspan) + 4)
            for offset in range(rowspan):
                if row_index + offset < len(row_heights):
                    row_heights[row_index + offset] = max(row_heights[row_index + offset], per_row)

    total_height = margins + sum(row_heights) + margins
    row_tops = []
    y = margins
    for height in row_heights:
        row_tops.append(y)
        y += height

    wrapped_by_cell = []
    for cell in origin_cells:
        width = sum(col_widths[cell["col"] : cell["col"] + cell["colspan"]])
        wrapped_by_cell.append((cell, _wrap_cell(clean_text(cell["text"]), width, font_size)))

    header_background_rows = set(table.header_rows)
    for row_index, row in enumerate(matrix):
        is_header = row_index in table.header_rows
        if is_header:
            header_background_rows.add(row_index)

    image = Image.new("RGB", (max_width, max(total_height, 200)), "white")
    draw = ImageDraw.Draw(image)
    x0 = margins
    title = clean_text(table.title)
    if title:
        draw.text((x0, 8), title[:220], fill=(20, 20, 20), font=header_font)
    for cell, lines in wrapped_by_cell:
        row = cell["row"]
        col = cell["col"]
        x = x0 + sum(col_widths[:col])
        y = row_tops[row]
        width = sum(col_widths[col : col + cell["colspan"]])
        height = sum(row_heights[row : row + cell["rowspan"]])
        fill = (235, 240, 246) if row in header_background_rows else (255, 255, 255)
        draw.rectangle(
            (x, y, x + width, y + height),
            fill=fill,
            outline=(120, 120, 120),
            width=1,
        )
        row_font = header_font if row in header_background_rows else font
        text_y = y + 8
        for line in lines:
            if text_y + font_size > y + height:
                break
            draw.text((x + 8, text_y), line, fill=(10, 10, 10), font=row_font)
            text_y += font_size + 7

    image.save(output)
    return {
        "path": str(output),
        "width": image.width,
        "height": image.height,
        "rows": len(matrix),
        "columns": col_count,
    }


def _origin_cells(table: TableIR) -> list[dict[str, int | str]]:
    alias_to_col = {column.alias: column.index for column in table.columns}
    cells: list[dict[str, int | str]] = []
    occupied: set[tuple[int, int]] = set()
    for row in table.rows:
        for alias, text in row.values.items():
            col = alias_to_col.get(alias)
            if col is None:
                continue
            rowspan, colspan = row.spans.get(alias, (1, 1))
            cells.append(
                {
                    "row": row.row,
                    "col": col,
                    "rowspan": max(1, rowspan),
                    "colspan": max(1, colspan),
                    "text": text,
                }
            )
            for row_offset in range(max(1, rowspan)):
                for col_offset in range(max(1, colspan)):
                    occupied.add((row.row + row_offset, col + col_offset))

    for row_index in range(table.row_count):
        for col_index in range(table.column_count):
            if (row_index, col_index) in occupied:
                continue
            cells.append(
                {
                    "row": row_index,
                    "col": col_index,
                    "rowspan": 1,
                    "colspan": 1,
                    "text": "",
                }
            )
    return sorted(cells, key=lambda item: (int(item["row"]), int(item["col"])))


def _font(size: int, *, bold: bool = False):
    candidates = [
        "arialbd.ttf" if bold else "arial.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _column_widths(rows: list[list[str]], headers: list[str], total_width: int, font_size: int) -> list[int]:
    col_count = max(len(headers), max((len(row) for row in rows), default=0), 1)
    weights = []
    for col in range(col_count):
        values = [headers[col] if col < len(headers) else ""]
        values.extend(row[col] for row in rows[:25] if col < len(row))
        max_len = max((len(clean_text(value)) for value in values), default=1)
        weights.append(max(8, min(42, math.sqrt(max_len) * 7)))
    total_weight = sum(weights) or 1
    widths = [max(120, int(total_width * weight / total_weight)) for weight in weights]
    overflow = sum(widths) - total_width
    if overflow > 0:
        wide_indexes = sorted(range(len(widths)), key=lambda index: widths[index], reverse=True)
        for index in wide_indexes:
            if overflow <= 0:
                break
            reducible = max(0, widths[index] - 120)
            delta = min(reducible, overflow)
            widths[index] -= delta
            overflow -= delta
    return widths


def _wrap_cell(text: str, width: int, font_size: int) -> list[str]:
    if not text:
        return [""]
    chars_per_line = max(10, int(width / (font_size * 0.55)))
    lines: list[str] = []
    for part in text.splitlines() or [text]:
        wrapped = textwrap.wrap(part, width=chars_per_line, break_long_words=False)
        lines.extend(wrapped or [""])
    return lines
