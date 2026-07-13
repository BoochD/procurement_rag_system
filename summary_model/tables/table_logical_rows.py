from __future__ import annotations

import re

from summary_model.domain.models import TableIR
from summary_model.tables.models import HeaderPath, LogicalTableRow, ParsedTableType
from summary_model.tables.utils import (
    KTRU_RE,
    OKPD2_RE,
    clean_text,
    normalize_key,
)


def header_paths(table: TableIR) -> list[HeaderPath]:
    return [
        HeaderPath(
            col_index=column.index,
            parts=column.header_path,
            normalized_name=normalize_header_name(column.header_path),
        )
        for column in table.columns
    ]


def normalize_header_name(path: list[str]) -> str | None:
    text = " ".join(path).casefold()
    if not text:
        return None
    rules = (
        ("row_number", ("№", "номер", "п/п")),
        ("characteristic_unit", ("единица измерения характерист",)),
        ("characteristic_value", ("значение",)),
        ("characteristic_name", ("характеристик",)),
        ("selected_min_unit_price", ("минимальная цена",)),
        ("okpd2_ktru", ("окпд", "ктру")),
        ("okpd2_code", ("окпд",)),
        ("ktru_code", ("ктру",)),
        ("quantity", ("количество",)),
        ("quantity", ("кол-во",)),
        ("quantity", ("кол во",)),
        ("unit", ("единица измерения товара",)),
        ("unit", ("единица измерения",)),
        ("unit", ("ед. изм",)),
        ("unit", ("ед изм",)),
        ("unit_price", ("цена за ед",)),
        ("unit_price", ("цена за единицу",)),
        ("row_total", ("стоимость товаров",)),
        ("row_total", ("стоимость в руб",)),
        ("row_total", ("сумма",)),
        ("row_total", ("цена контракта",)),
        ("row_total", ("всего",)),
        ("vat_rate", ("ставка ндс",)),
        ("vat_amount", ("сумма ндс",)),
        ("name", ("наименование",)),
    )
    for normalized, markers in rules:
        if all(marker in text for marker in markers):
            return normalized
    return normalize_key(text) or None


def _row_dense(table: TableIR, row_index: int) -> list[str]:
    matrix = table.matrix()
    if row_index >= len(matrix):
        return []
    return matrix[row_index]


def _row_origin_values(table: TableIR, row_index: int) -> dict[int, str]:
    alias_to_col = {column.alias: column.index for column in table.columns}
    row = next((candidate for candidate in table.rows if candidate.row == row_index), None)
    if row is None:
        return {}
    return {
        alias_to_col[alias]: text
        for alias, text in row.values.items()
        if clean_text(text)
    }


def _cells_by_col(row: list[str]) -> dict[int, str | None]:
    return {
        index: clean_text(value) or None
        for index, value in enumerate(row)
        if clean_text(value)
    }


def _raw_text(row: list[str]) -> str:
    return " | ".join(value for value in (clean_text(cell) for cell in row) if value)


def _same_cell_text(left: str | None, right: str | None) -> bool:
    return clean_text(left).casefold() == clean_text(right).casefold()


def _dedupe_adjacent_values(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if result and _same_cell_text(result[-1], value):
            continue
        result.append(value)
    return result


def _header_map(paths: list[HeaderPath]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in paths:
        if item.normalized_name and item.normalized_name not in result:
            result[item.normalized_name] = item.col_index
    return result


def _first_index(*values: int | None) -> int | None:
    for value in values:
        if value is not None:
            return value
    return None


def _value(row: list[str], index: int | None) -> str | None:
    if index is None or index >= len(row):
        return None
    return clean_text(row[index]) or None


def _origin_value(table: TableIR, row_index: int, index: int | None) -> str | None:
    if index is None:
        return None
    return _row_origin_values(table, row_index).get(index)


def build_logical_rows(
    table: TableIR,
    table_type: ParsedTableType,
    paths: list[HeaderPath],
) -> list[LogicalTableRow]:
    if table_type == "schedule_application_table":
        return _key_value_rows(table, paths)
    if table_type in {"request_attachments_table", "contract_attachments_table"}:
        return _attachment_rows(table, paths)
    if table_type == "ooz_items_table":
        return _ooz_rows(table, paths)
    if table_type == "nmck_calculation_table":
        return _nmck_rows(table, paths)
    if table_type == "contract_specification_table":
        return _contract_specification_rows(table, paths)
    return _generic_rows(table, paths)


def _key_value_rows(table: TableIR, paths: list[HeaderPath]) -> list[LogicalTableRow]:
    logical: list[LogicalTableRow] = []
    for row_index in range(table.row_count):
        row = _row_dense(table, row_index)
        values = [clean_text(value) for value in row if clean_text(value)]
        if not values:
            continue
        key = values[0]
        value_parts = _dedupe_adjacent_values(values[1:])
        value = "; ".join(value_parts) if value_parts else None
        logical.append(
            LogicalTableRow(
                table_id=table.table_id,
                row_index=row_index,
                row_type="key_value",
                cells_by_col=_cells_by_col(row),
                cells_by_header={
                    "key": key,
                    "value": value,
                },
                raw_text=_raw_text(row),
                confidence=0.9,
            )
        )
    return logical


def _attachment_rows(table: TableIR, paths: list[HeaderPath]) -> list[LogicalTableRow]:
    rows: list[LogicalTableRow] = []
    for row_index in range(table.row_count):
        if row_index in table.header_rows:
            continue
        row = _row_dense(table, row_index)
        text = _raw_text(row)
        if not text:
            continue
        row_type = "item" if any(marker in text.casefold() for marker in (
            "заявка",
            "определение цены",
            "обоснование",
            "описание объекта",
            "проект контракта",
            "пояснительная",
            "коммерчес",
        )) else "unknown"
        rows.append(
            LogicalTableRow(
                table_id=table.table_id,
                row_index=row_index,
                row_type=row_type,
                cells_by_col=_cells_by_col(row),
                cells_by_header={"attachment": text},
                raw_text=text,
                confidence=0.8 if row_type == "item" else 0.4,
            )
        )
    return rows


def _ooz_rows(table: TableIR, paths: list[HeaderPath]) -> list[LogicalTableRow]:
    mapping = _header_map(paths)
    name_index = mapping.get("name")
    code_index = _first_index(
        mapping.get("okpd2_ktru"),
        mapping.get("okpd2_code"),
        mapping.get("ktru_code"),
    )
    quantity_index = mapping.get("quantity")
    unit_index = mapping.get("unit")
    char_name_index = mapping.get("characteristic_name")
    char_value_index = mapping.get("characteristic_value")
    char_unit_index = mapping.get("characteristic_unit")
    row_number_index = mapping.get("row_number")

    logical: list[LogicalTableRow] = []
    current_parent_row: int | None = None
    current_parent_number: str | None = None
    current_parent_key: tuple[str, str, str, str] | None = None
    parent_by_key: dict[tuple[str, str, str, str], tuple[int, str | None]] = {}
    start = max(table.header_rows, default=-1) + 1
    for row_index in range(start, table.row_count):
        row = _row_dense(table, row_index)
        if not _raw_text(row):
            continue
        origin = _row_origin_values(table, row_index)
        origin_name = _origin_value(table, row_index, name_index)
        origin_code = _origin_value(table, row_index, code_index)
        origin_quantity = _origin_value(table, row_index, quantity_index)
        origin_number = _origin_value(table, row_index, row_number_index)
        identity_text = (
            origin_name
            or _value(row, name_index)
            or origin_code
            or _value(row, code_index)
            or ""
        )
        code_text = origin_code or _value(row, code_index) or identity_text
        okpd_match = OKPD2_RE.search(code_text)
        ktru_match = KTRU_RE.search(code_text)
        dense_name = _value(row, name_index) or _name_from_combined_identity(identity_text)
        quantity_value = _value(row, quantity_index)
        row_number = origin_number or (
            clean_text(row[0]) if row and re.fullmatch(r"\d+[.)]?", clean_text(row[0])) else None
        )
        item_key = _item_key(
            row_number,
            dense_name,
            okpd_match.group(0) if okpd_match else None,
            ktru_match.group(0) if ktru_match else None,
            quantity_value,
        )
        has_item_identity = bool(item_key and (dense_name or okpd_match or ktru_match))
        if has_item_identity and item_key:
            existing_parent = parent_by_key.get(item_key)
            if existing_parent is None:
                current_parent_row = row_index
                current_parent_number = row_number
                current_parent_key = item_key
                parent_by_key[item_key] = (row_index, row_number)
                logical.append(
                    LogicalTableRow(
                        table_id=table.table_id,
                        row_index=row_index,
                        row_type="item",
                        parent_item_number=current_parent_number,
                        cells_by_col=_cells_by_col(row),
                        cells_by_header={
                            "row_number": current_parent_number,
                            "name": dense_name,
                            "okpd2_code": okpd_match.group(0) if okpd_match else None,
                            "ktru_code": ktru_match.group(0) if ktru_match else None,
                            "unit": _value(row, unit_index),
                            "quantity": quantity_value,
                        },
                        raw_text=_raw_text(row),
                        confidence=0.88,
                    )
                )
            else:
                current_parent_row, current_parent_number = existing_parent
                current_parent_key = item_key
        elif (origin_name or origin_code or origin_quantity) and not dense_name:
            logical.append(
                LogicalTableRow(
                    table_id=table.table_id,
                    row_index=row_index,
                    row_type="unknown",
                    cells_by_col=_cells_by_col(row),
                    cells_by_header={},
                    raw_text=_raw_text(row),
                    confidence=0.35,
                    warnings=["Row has partial item markers but no reliable item identity."],
                )
            )
        characteristic_name = _value(row, char_name_index)
        characteristic_value = _value(row, char_value_index)
        if characteristic_name and current_parent_row is not None:
            logical.append(
                LogicalTableRow(
                    table_id=table.table_id,
                    row_index=row_index,
                    row_type="characteristic",
                    parent_row_index=current_parent_row,
                    parent_item_number=current_parent_number,
                    cells_by_col=_cells_by_col(row),
                    cells_by_header={
                        "characteristic_name": characteristic_name,
                        "characteristic_value": characteristic_value,
                        "characteristic_unit": _value(row, char_unit_index),
                    },
                    raw_text=_raw_text(row),
                    confidence=0.9,
                )
            )
        elif characteristic_name:
            logical.append(
                LogicalTableRow(
                    table_id=table.table_id,
                    row_index=row_index,
                    row_type="characteristic",
                    cells_by_col=_cells_by_col(row),
                    cells_by_header={
                        "characteristic_name": characteristic_name,
                        "characteristic_value": characteristic_value,
                    },
                    raw_text=_raw_text(row),
                    confidence=0.35,
                    warnings=["Characteristic row could not be attached to an item."],
                )
            )
    return logical


def _name_from_combined_identity(text: str | None) -> str | None:
    text = clean_text(text)
    if not text:
        return None
    code_match = KTRU_RE.search(text) or OKPD2_RE.search(text)
    if code_match:
        before = clean_text(text[: code_match.start()].rstrip(" ,-–—:;"))
        before = re.sub(r"\b(?:ОКПД2?|КТРУ)\b\s*$", "", before, flags=re.I).strip(" ,-–—:;")
        if before:
            return clean_text(before)
        after = clean_text(text[code_match.end() :].lstrip(" ,-–—:;"))
        if after:
            return after
    return text


def _item_key(
    row_number: str | None,
    name: str | None,
    okpd2_code: str | None,
    ktru_code: str | None,
    quantity: str | None,
) -> tuple[str, str, str, str] | None:
    row_number = clean_text(row_number)
    name = clean_text(name).casefold()
    okpd2_code = clean_text(okpd2_code)
    ktru_code = clean_text(ktru_code)
    quantity = clean_text(quantity)
    if not any((name, okpd2_code, ktru_code)):
        return None
    if row_number and re.fullmatch(r"\d+[.)]?", row_number):
        return (row_number, name, okpd2_code, ktru_code)
    return (name, okpd2_code, ktru_code, quantity)


def _nmck_rows(table: TableIR, paths: list[HeaderPath]) -> list[LogicalTableRow]:
    mapping = _header_map(paths)
    name_index = mapping.get("name")
    quantity_index = mapping.get("quantity")
    unit_index = mapping.get("unit")
    selected_index = mapping.get("selected_min_unit_price")
    total_index = _first_index(_first_path_index(paths, "цена контракта"), mapping.get("row_total"))
    row_number_index = mapping.get("row_number") if mapping.get("row_number") is not None else 0
    logical: list[LogicalTableRow] = []
    start = max(table.header_rows, default=-1) + 1
    for row_index in range(start, table.row_count):
        row = _row_dense(table, row_index)
        if not _raw_text(row):
            continue
        name = _value(row, name_index)
        row_text = _raw_text(row)
        if _is_total_label(name) or _is_total_label(row_text):
            continue
        if not name and not (OKPD2_RE.search(row_text) or KTRU_RE.search(row_text)):
            continue
        cells_by_header = {
            "row_number": _value(row, row_number_index),
            "name": name,
            "unit": _value(row, unit_index),
            "quantity": _value(row, quantity_index),
            "selected_min_unit_price": _value(row, selected_index),
            "row_total_declared": _value(row, total_index),
        }
        for path in paths:
            joined = " ".join(path.parts).casefold()
            supplier = _supplier_ref(joined)
            if supplier is None or path.col_index >= len(row):
                continue
            value = clean_text(row[path.col_index])
            if not value:
                continue
            if _is_unit_price_header(joined):
                cells_by_header[f"{supplier}.unit_price"] = value
            elif _is_supplier_total_header(joined):
                cells_by_header[f"{supplier}.row_total"] = value
        logical.append(
            LogicalTableRow(
                table_id=table.table_id,
                row_index=row_index,
                row_type="item",
                cells_by_col=_cells_by_col(row),
                cells_by_header=cells_by_header,
                raw_text=row_text,
                confidence=0.82,
            )
        )
    return logical


def _first_path_index(paths: list[HeaderPath], marker: str) -> int | None:
    marker = marker.casefold()
    for path in paths:
        if marker in " ".join(path.parts).casefold():
            return path.col_index
    return None


def _is_total_label(text: str | None) -> bool:
    text = clean_text(text).casefold()
    return text in {"итого", "всего", "итого:", "всего:"}


def _contract_specification_rows(table: TableIR, paths: list[HeaderPath]) -> list[LogicalTableRow]:
    mapping = _header_map(paths)
    name_index = mapping.get("name")
    unit_index = mapping.get("unit")
    quantity_index = mapping.get("quantity")
    unit_price_columns = [
        path.col_index
        for path in paths
        if path.normalized_name == "unit_price"
    ]
    total_columns = [
        path.col_index
        for path in paths
        if path.normalized_name == "row_total"
    ]
    vat_rate_index = mapping.get("vat_rate")
    vat_amount_index = mapping.get("vat_amount")
    row_number_index = mapping.get("row_number")
    logical: list[LogicalTableRow] = []
    start = max(table.header_rows, default=-1) + 1
    for row_index in range(start, table.row_count):
        row = _row_dense(table, row_index)
        row_text = _raw_text(row)
        if not row_text:
            continue
        if _is_technical_numbering_row(row):
            logical.append(
                LogicalTableRow(
                    table_id=table.table_id,
                    row_index=row_index,
                    row_type="header",
                    cells_by_col=_cells_by_col(row),
                    cells_by_header={},
                    raw_text=row_text,
                    confidence=0.8,
                    warnings=["Technical numbering row ignored as specification item."],
                )
            )
            continue
        lowered = row_text.casefold()
        if lowered.startswith("всего") or "итого" in lowered:
            logical.append(
                LogicalTableRow(
                    table_id=table.table_id,
                    row_index=row_index,
                    row_type="total",
                    cells_by_col=_cells_by_col(row),
                    cells_by_header={"total": row_text},
                    raw_text=row_text,
                    confidence=0.9,
                )
            )
            continue
        name = _value(row, name_index)
        if not name:
            continue
        description_index = 1 if name_index != 1 and len(row) > 1 else None
        description = _value(row, description_index)
        unit = _value(row, unit_index)
        quantity = _value(row, quantity_index)
        unit_price_without_vat = _value(row, unit_price_columns[0] if unit_price_columns else None)
        unit_price_with_vat = _value(row, unit_price_columns[-1] if len(unit_price_columns) > 1 else None)
        total_without_vat = _value(row, total_columns[0] if total_columns else None)
        total_price = _value(row, total_columns[-1] if total_columns else None)
        if not _is_meaningful_specification_item(
            name,
            unit,
            quantity,
            description,
            unit_price_without_vat,
            unit_price_with_vat,
            total_without_vat,
            total_price,
        ):
            logical.append(
                LogicalTableRow(
                    table_id=table.table_id,
                    row_index=row_index,
                    row_type="unknown",
                    cells_by_col=_cells_by_col(row),
                    cells_by_header={},
                    raw_text=row_text,
                    confidence=0.35,
                    warnings=["Specification-like row did not contain enough item data."],
                )
            )
            continue
        cells_by_header = {
            "row_number": _value(row, row_number_index),
            "name": name,
            "description": description,
            "unit": unit,
            "quantity": quantity,
            "unit_price_without_vat": unit_price_without_vat,
            "unit_price_with_vat": unit_price_with_vat,
            "total_without_vat": total_without_vat,
            "total_price": total_price,
            "vat_rate": _value(row, vat_rate_index),
            "vat_amount": _value(row, vat_amount_index),
        }
        logical.append(
            LogicalTableRow(
                table_id=table.table_id,
                row_index=row_index,
                row_type="item",
                cells_by_col=_cells_by_col(row),
                cells_by_header=cells_by_header,
                raw_text=row_text,
                confidence=0.86,
            )
        )
    return logical


def _is_technical_numbering_row(row: list[str]) -> bool:
    values = [clean_text(value) for value in row if clean_text(value)]
    if len(values) < 3:
        return False
    return all(value.isdigit() and int(value) == index for index, value in enumerate(values, start=1))


def _is_meaningful_specification_item(
    name: str | None,
    unit: str | None,
    quantity: str | None,
    description: str | None,
    *money_fields: str | None,
) -> bool:
    name = clean_text(name)
    if not name or name.isdigit() or name in {"-", "—"}:
        return False
    useful_values = [unit, quantity, description, *money_fields]
    return any(clean_text(value) and clean_text(value) not in {"-", "—"} for value in useful_values)


def _supplier_ref(header: str) -> str | None:
    match = re.search(r"(?:поставщик|исполнитель)\s*(\d+)", header)
    if match:
        return f"supplier_{match.group(1)}"
    return None


def _is_unit_price_header(header: str) -> bool:
    return "цена за ед" in header or "цена за единицу" in header


def _is_supplier_total_header(header: str) -> bool:
    return "стоимость товаров" in header or "стоимость" in header or "сумма" in header


def _generic_rows(table: TableIR, paths: list[HeaderPath]) -> list[LogicalTableRow]:
    result: list[LogicalTableRow] = []
    for row_index in range(table.row_count):
        row = _row_dense(table, row_index)
        text = _raw_text(row)
        if not text:
            continue
        result.append(
            LogicalTableRow(
                table_id=table.table_id,
                row_index=row_index,
                row_type="header" if row_index in table.header_rows else "unknown",
                cells_by_col=_cells_by_col(row),
                cells_by_header={},
                raw_text=text,
                confidence=0.5,
            )
        )
    return result
