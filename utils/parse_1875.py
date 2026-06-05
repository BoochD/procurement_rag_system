#!/usr/bin/env python3
"""Parse PP RF No. 1875 HTML saved locally and extract normative tables.

Outputs:
- data/parsed_tables/table_XX_<slug>.json   - one file per table
- data/parsed_tables/tables_manifest.json   - summary of all tables
- data/parsed_tables/okpd_index.json        - flattened rows with codes
- data/parsed_tables/pp1875.sqlite          - fast lookup index

Usage:
    python utils/parse_1875.py
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from bs4 import BeautifulSoup, Tag

DEFAULT_INPUT = Path("data/raw_1875.html")
DEFAULT_OUTPUT_DIR = Path("data/parsed_tables")
DEFAULT_SQLITE = DEFAULT_OUTPUT_DIR / "pp1875.sqlite"

LOGGER = logging.getLogger("parse_1875")

# Подходит и для 13.2, и для 14.20, и для 22.29.29.190
OKPD_RE = re.compile(r"\b\d{2}(?:\.\d{1,3}){1,4}\b")
POSITION_RE = re.compile(r"^\d+[\.)]?$")
SPACE_RE = re.compile(r"\s+")


@dataclass
class ParsedRow:
    row_index: int
    position: Optional[str]
    name: str
    okpd2_codes: list[str]
    primary_okpd2: Optional[str]
    raw_code_text: str
    raw_cells: list[str]


@dataclass
class ParsedTable:
    table_id: str
    title: str
    slug: str
    appendix_title: Optional[str]
    heading: Optional[str]
    rows: list[dict[str, Any]]
    html_index: int
    source: str = "pp_1875"


@dataclass
class IndexedRow:
    table_id: str
    table_title: str
    appendix_title: Optional[str]
    row_index: int
    position: Optional[str]
    okpd2: str
    name: str
    raw_code_text: str
    row: dict[str, Any]

def extract_appendix_number(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    match = re.search(r"ПРИЛОЖЕНИЕ\s+N\s+(\d+)", value, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def normalize_text(value: str) -> str:
    return SPACE_RE.sub(" ", value.replace("\xa0", " ")).strip()


def slugify(value: str, appendix_title: Optional[str] = None, table_index: Optional[int] = None) -> str:
    appendix_number = extract_appendix_number(appendix_title)
    if appendix_number:
        return f"appendix_{appendix_number}"

    if value:
        lowered = normalize_text(value).lower()

        if "перечень" in lowered:
            return f"table_{table_index or 0}"
        if "минимальная обязательная доля" in lowered:
            return f"table_{table_index or 0}"

    return f"table_{table_index or 0}"


def read_html(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def iter_candidate_tables(soup: BeautifulSoup) -> Iterable[Tag]:
    # Берём только целевые таблицы документа, а не служебные.
    for table in soup.select("table.primeTab"):
        rows = table.find_all("tr")
        if len(rows) >= 2:
            yield table


def collect_previous_context(table: Tag) -> tuple[Optional[str], Optional[str], str]:
    """
    Ищем ближайшие осмысленные элементы перед таблицей:
    - appendix_title: обычно <p> с 'ПРИЛОЖЕНИЕ N ...'
    - heading: обычно <h3> с названием перечня
    - full_title: их комбинация
    """
    appendix_title: Optional[str] = None
    heading: Optional[str] = None

    current = table
    seen = 0
    while seen < 20:
        current = current.find_previous(["h1", "h2", "h3", "h4", "p", "div"])
        if current is None:
            break
        seen += 1

        text = normalize_text(current.get_text(" ", strip=True))
        if not text:
            continue

        if appendix_title is None and re.search(r"^ПРИЛОЖЕНИЕ\b", text, flags=re.IGNORECASE):
            appendix_title = text
            continue

        if heading is None and current.name in {"h1", "h2", "h3", "h4"}:
            heading = text
            continue

        if heading is None and re.search(r"перечень|список", text, flags=re.IGNORECASE):
            heading = text
            continue

        if appendix_title and heading:
            break

    if appendix_title and heading:
        full_title = f"{appendix_title} — {heading}"
    elif appendix_title:
        full_title = appendix_title
    elif heading:
        full_title = heading
    else:
        full_title = "Таблица"

    return appendix_title, heading, full_title


def is_header_row(tr: Tag) -> bool:
    return bool(tr.find("th"))


def clean_cells(tr: Tag) -> list[str]:
    cells = tr.find_all(["td", "th"])
    return [normalize_text(cell.get_text(" ", strip=True)) for cell in cells if normalize_text(cell.get_text(" ", strip=True))]


def extract_position(value: str) -> Optional[str]:
    value = normalize_text(value)
    if POSITION_RE.fullmatch(value):
        return value.rstrip(").")
    return None


def extract_okpd_codes(value: str) -> list[str]:
    found = OKPD_RE.findall(normalize_text(value))
    # Сохраняем порядок и убираем дубли
    unique: list[str] = []
    seen: set[str] = set()
    for item in found:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def parse_row(tr: Tag, row_index: int) -> Optional[ParsedRow]:
    if is_header_row(tr):
        return None

    cells = clean_cells(tr)
    if len(cells) < 3:
        return None

    # Для этого документа нормальная строка — это:
    # [номер позиции, наименование, код/коды]
    position = extract_position(cells[0])

    # Иногда HTML может содержать лишние служебные строки — фильтруем их.
    if position is None:
        return None

    name = cells[1]
    raw_code_text = " ".join(cells[2:]).strip()
    okpd2_codes = extract_okpd_codes(raw_code_text)

    if not name or not okpd2_codes:
        return None

    return ParsedRow(
        row_index=row_index,
        position=position,
        name=name,
        okpd2_codes=okpd2_codes,
        primary_okpd2=okpd2_codes[0],
        raw_code_text=raw_code_text,
        raw_cells=cells,
    )


def parse_table(table: Tag, table_index: int) -> ParsedTable:
    appendix_title, heading, full_title = collect_previous_context(table)
    slug = slugify(full_title, appendix_title=appendix_title, table_index=table_index)
    parsed_rows: list[dict[str, Any]] = []

    for row_index, tr in enumerate(table.find_all("tr"), start=1):
        parsed_row = parse_row(tr, row_index=row_index)
        if parsed_row is None:
            continue
        parsed_rows.append(asdict(parsed_row))

    return ParsedTable(
        table_id=f"table_{table_index:02d}",
        title=full_title,
        slug=slug,
        appendix_title=appendix_title,
        heading=heading,
        rows=parsed_rows,
        html_index=table_index,
    )


def build_index(tables: list[ParsedTable]) -> list[IndexedRow]:
    index_rows: list[IndexedRow] = []

    for table in tables:
        for row in table.rows:
            okpd2_codes = row.get("okpd2_codes") or []
            name = row.get("name")
            if not name or not okpd2_codes:
                continue

            for okpd2 in okpd2_codes:
                index_rows.append(
                    IndexedRow(
                        table_id=table.table_id,
                        table_title=table.title,
                        appendix_title=table.appendix_title,
                        row_index=row["row_index"],
                        position=row.get("position"),
                        okpd2=okpd2,
                        name=name,
                        raw_code_text=row.get("raw_code_text", ""),
                        row=row,
                    )
                )

    return index_rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_sqlite(db_path: Path, indexed_rows: list[IndexedRow]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        cursor = connection.cursor()
        cursor.executescript(
            """
            DROP TABLE IF EXISTS okpd_index;

            CREATE TABLE okpd_index (
                okpd2 TEXT NOT NULL,
                name TEXT NOT NULL,
                table_id TEXT NOT NULL,
                table_title TEXT NOT NULL,
                appendix_title TEXT,
                row_index INTEGER NOT NULL,
                position TEXT,
                raw_code_text TEXT,
                row_json TEXT NOT NULL
            );

            CREATE INDEX idx_okpd_index_okpd2 ON okpd_index (okpd2);
            CREATE INDEX idx_okpd_index_table_id ON okpd_index (table_id);
            CREATE INDEX idx_okpd_index_name ON okpd_index (name);
            """
        )
        cursor.executemany(
            """
            INSERT INTO okpd_index (
                okpd2, name, table_id, table_title, appendix_title,
                row_index, position, raw_code_text, row_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row.okpd2,
                    row.name,
                    row.table_id,
                    row.table_title,
                    row.appendix_title,
                    row.row_index,
                    row.position,
                    row.raw_code_text,
                    json.dumps(row.row, ensure_ascii=False),
                )
                for row in indexed_rows
            ],
        )
        connection.commit()
    finally:
        connection.close()


def run(input_path: Path, output_dir: Path, sqlite_path: Optional[Path]) -> None:
    html = read_html(input_path)
    soup = BeautifulSoup(html, "html.parser")

    tables: list[ParsedTable] = []
    for table_index, table in enumerate(iter_candidate_tables(soup), start=1):
        parsed = parse_table(table, table_index)

        # Пустые таблицы не сохраняем
        if not parsed.rows:
            LOGGER.debug("Skipping empty parsed table %s", parsed.table_id)
            continue

        tables.append(parsed)

        appendix_number = extract_appendix_number(parsed.appendix_title)
        if appendix_number:
            filename = f"{parsed.table_id}_appendix_{appendix_number}.json"
        else:
            filename = f"{parsed.table_id}.json"

        table_file = output_dir / filename
        write_json(table_file, asdict(parsed))
        LOGGER.info("Saved %s", table_file)
    appendix_number = extract_appendix_number(table.appendix_title)
    if appendix_number:
        file_name = f"{table.table_id}_appendix_{appendix_number}.json"
    else:
        file_name = f"{table.table_id}.json"
    manifest = {
        "source": "pp_1875",
        "input_file": str(input_path),
        "table_count": len(tables),
        "tables": [
            {
                "table_id": table.table_id,
                "title": table.title,
                "appendix_title": table.appendix_title,
                "heading": table.heading,
                "slug": table.slug,
                "rows": len(table.rows),
                "file": file_name,
            }
            for table in tables
        ],
    }
    write_json(output_dir / "tables_manifest.json", manifest)

    indexed_rows = build_index(tables)
    write_json(output_dir / "okpd_index.json", [asdict(row) for row in indexed_rows])
    LOGGER.info("Indexed %s OKPD rows", len(indexed_rows))

    if sqlite_path is not None:
        write_sqlite(sqlite_path, indexed_rows)
        LOGGER.info("Saved SQLite index to %s", sqlite_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parse PP 1875 HTML")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Path to raw HTML file")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for parsed tables")
    parser.add_argument(
        "--sqlite",
        default=str(DEFAULT_SQLITE),
        help="Path to SQLite file. Use empty string to disable.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    sqlite_path = Path(args.sqlite) if args.sqlite else None
    run(input_path=Path(args.input), output_dir=Path(args.output_dir), sqlite_path=sqlite_path)