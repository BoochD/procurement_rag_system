from __future__ import annotations

from decimal import Decimal
from typing import Any

from summary_model.tables.models import LogicalTableRow, ParsedTable
from summary_model.tables.utils import KTRU_RE, OKPD2_RE, clean_text, parse_decimal


def build_compact_json(table_type: str, rows: list[LogicalTableRow]) -> dict[str, Any]:
    if table_type == "schedule_application_table":
        return _key_value_json(rows)
    if table_type in {"request_attachments_table", "contract_attachments_table"}:
        return _attachments_json(rows)
    if table_type == "ooz_items_table":
        return _items_json(rows)
    if table_type == "nmck_calculation_table":
        return _nmck_json(rows)
    if table_type == "contract_specification_table":
        return _contract_specification_json(rows)
    if table_type in {"signature_table", "ignored_table"}:
        return {"rows": []}
    return {"rows": [_row_payload(row) for row in rows]}


def build_compact_markdown(parsed: ParsedTable) -> str:
    lines = [f"TABLE {parsed.table_type} {parsed.table_id}"]
    if parsed.title:
        lines.append(f"title: {parsed.title}")
    if parsed.table_type == "schedule_application_table":
        for field in parsed.compact_json.get("raw_fields", []):
            lines.append(f"- {field['key']}: {field.get('value') or ''}")
    elif parsed.table_type in {"request_attachments_table", "contract_attachments_table"}:
        lines.append("ATTACHMENTS:")
        for item in parsed.compact_json.get("attachments", []):
            lines.append(f"- {item.get('title_raw')}")
    elif parsed.table_type == "ooz_items_table":
        for item in parsed.compact_json.get("items", []):
            lines.extend(_item_markdown(item))
    elif parsed.table_type == "nmck_calculation_table":
        lines.append("SOURCES:")
        for source in parsed.compact_json.get("price_sources", []):
            lines.append(f"- {source['source_id']}: {source.get('raw_header')}")
        for item in parsed.compact_json.get("items", []):
            lines.extend(_nmck_item_markdown(item))
    elif parsed.table_type == "contract_specification_table":
        for item in parsed.compact_json.get("items", []):
            lines.extend(_specification_item_markdown(item))
        totals = parsed.compact_json.get("totals") or []
        if totals:
            lines.append("")
            lines.append("TOTALS:")
            for total in totals:
                lines.append(f"- {total.get('raw_text')}")
    elif parsed.table_type in {"signature_table", "ignored_table"}:
        lines.append("ignored: true")
    else:
        for row in parsed.logical_rows:
            lines.append(f"ROW r{row.row_index}: {row.raw_text}")
    return "\n".join(lines).strip()


def _row_payload(row: LogicalTableRow) -> dict[str, Any]:
    return {
        "row_index": row.row_index,
        "row_type": row.row_type,
        "parent_row_index": row.parent_row_index,
        "parent_item_number": row.parent_item_number,
        "cells_by_header": row.cells_by_header,
        "raw_text": row.raw_text,
        "warnings": row.warnings,
    }


def _key_value_json(rows: list[LogicalTableRow]) -> dict[str, Any]:
    return {
        "raw_fields": [
            {
                "key": row.cells_by_header.get("key"),
                "value": row.cells_by_header.get("value"),
                "row_index": row.row_index,
                "raw_text": row.raw_text,
            }
            for row in rows
            if row.row_type == "key_value"
        ]
    }


def _attachments_json(rows: list[LogicalTableRow]) -> dict[str, Any]:
    return {
        "attachments": [
            {
                "title_raw": row.cells_by_header.get("attachment") or row.raw_text,
                "row_index": row.row_index,
                "raw_text": row.raw_text,
            }
            for row in rows
            if row.row_type == "item"
        ]
    }


def _items_json(rows: list[LogicalTableRow]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    by_parent: dict[int, dict[str, Any]] = {}
    for row in rows:
        if row.row_type == "item":
            payload = {
                "row_number": row.cells_by_header.get("row_number"),
                "name": row.cells_by_header.get("name"),
                "okpd2_code": row.cells_by_header.get("okpd2_code"),
                "ktru_code": row.cells_by_header.get("ktru_code"),
                "unit": row.cells_by_header.get("unit"),
                "quantity_raw": row.cells_by_header.get("quantity"),
                "quantity": _decimal_json(row.cells_by_header.get("quantity")),
                "characteristics": [],
                "row_index": row.row_index,
                "raw_text": row.raw_text,
            }
            code_text = row.raw_text
            payload["okpd2_codes"] = list(dict.fromkeys(OKPD2_RE.findall(code_text)))
            payload["ktru_codes"] = list(dict.fromkeys(KTRU_RE.findall(code_text)))
            items.append(payload)
            by_parent[row.row_index] = payload
        elif row.row_type == "characteristic":
            parent = by_parent.get(row.parent_row_index or -1)
            characteristic = {
                "name": row.cells_by_header.get("characteristic_name"),
                "value": row.cells_by_header.get("characteristic_value"),
                "unit": row.cells_by_header.get("characteristic_unit"),
                "row_index": row.row_index,
                "raw_text": row.raw_text,
                "warnings": row.warnings,
            }
            if parent is None:
                items.append(
                    {
                        "row_number": row.parent_item_number,
                        "name": None,
                        "characteristics": [characteristic],
                        "row_index": row.row_index,
                        "raw_text": row.raw_text,
                        "parser_warnings": ["Characteristic row has no parent item."],
                    }
                )
            else:
                parent["characteristics"].append(characteristic)
    return {"items": items}


def _nmck_json(rows: list[LogicalTableRow]) -> dict[str, Any]:
    source_ids: set[str] = set()
    items = []
    for row in rows:
        by_supplier: dict[str, dict[str, Any]] = {}
        for key, value in row.cells_by_header.items():
            if "." not in key or not value:
                continue
            source_id, field = key.split(".", 1)
            if not source_id.startswith("supplier_"):
                continue
            source_ids.add(source_id)
            supplier = by_supplier.setdefault(source_id, {"source_id": source_id})
            if field == "unit_price":
                supplier["unit_price"] = _decimal_json(value)
                supplier["raw_unit_price"] = value
            elif field == "row_total":
                supplier["row_total"] = _decimal_json(value)
                supplier["raw_row_total"] = value
        supplier_prices = [
            by_supplier[source_id]
            for source_id in sorted(by_supplier, key=_source_sort_key)
        ]
        selected = row.cells_by_header.get("selected_min_unit_price")
        total = row.cells_by_header.get("row_total_declared")
        quantity = row.cells_by_header.get("quantity")
        items.append(
            {
                "row_number": row.cells_by_header.get("row_number"),
                "name": row.cells_by_header.get("name"),
                "unit": row.cells_by_header.get("unit"),
                "quantity_raw": quantity,
                "quantity": _decimal_json(quantity),
                "supplier_prices": supplier_prices,
                "selected_min_unit_price": _decimal_json(selected),
                "selected_min_unit_price_raw": selected,
                "row_total_declared": _decimal_json(total),
                "row_total_declared_raw": total,
                "row_index": row.row_index,
                "raw_text": row.raw_text,
            }
        )
    price_sources = [
        {
            "source_id": source_id,
            "raw_header": _source_label(source_id),
        }
        for source_id in sorted(source_ids, key=_source_sort_key)
    ]
    return {"price_sources": price_sources, "items": items}


def _source_label(source_id: str) -> str:
    if source_id.startswith("supplier_"):
        suffix = source_id.removeprefix("supplier_")
        if suffix.isdigit():
            return f"Поставщик{suffix}"
    return source_id


def _contract_specification_json(rows: list[LogicalTableRow]) -> dict[str, Any]:
    items = []
    totals = []
    for row in rows:
        if row.row_type == "total":
            totals.append(
                {
                    "row_index": row.row_index,
                    "raw_text": row.raw_text,
                }
            )
            continue
        if row.row_type != "item":
            continue
        values = row.cells_by_header
        items.append(
            {
                "row_number": values.get("row_number"),
                "name": values.get("name"),
                "description": values.get("description"),
                "unit": values.get("unit"),
                "quantity_raw": values.get("quantity"),
                "quantity": _decimal_json(values.get("quantity")),
                "unit_price_without_vat": _decimal_json(values.get("unit_price_without_vat")),
                "unit_price_with_vat": _decimal_json(values.get("unit_price_with_vat")),
                "total_without_vat": _decimal_json(values.get("total_without_vat")),
                "vat_rate": values.get("vat_rate"),
                "vat_amount": _decimal_json(values.get("vat_amount")),
                "total_price": _decimal_json(values.get("total_price")),
                "raw_unit_price_without_vat": values.get("unit_price_without_vat"),
                "raw_unit_price_with_vat": values.get("unit_price_with_vat"),
                "raw_total_without_vat": values.get("total_without_vat"),
                "raw_vat_amount": values.get("vat_amount"),
                "raw_total_price": values.get("total_price"),
                "row_index": row.row_index,
                "raw_text": row.raw_text,
            }
        )
    return {"items": items, "totals": totals}


def _source_sort_key(value: str) -> tuple[int, str]:
    try:
        return (int(value.rsplit("_", 1)[1]), value)
    except (IndexError, ValueError):
        return (9999, value)


def _decimal_json(value: str | None) -> Decimal | None:
    return parse_decimal(value)


def _item_markdown(item: dict[str, Any]) -> list[str]:
    lines = [
        "",
        f"ITEM {item.get('row_number') or item.get('row_index')}",
        f"name: {item.get('name') or ''}",
        f"okpd2: {item.get('okpd2_code') or ', '.join(item.get('okpd2_codes') or [])}",
        f"ktru: {item.get('ktru_code') or ', '.join(item.get('ktru_codes') or [])}",
        f"unit: {item.get('unit') or ''}",
        f"quantity: {item.get('quantity_raw') or ''}",
    ]
    characteristics = item.get("characteristics") or []
    if characteristics:
        lines.append("characteristics:")
        for characteristic in characteristics:
            name = clean_text(characteristic.get("name"))
            value = clean_text(characteristic.get("value"))
            unit = clean_text(characteristic.get("unit"))
            suffix = f" {unit}" if unit else ""
            lines.append(f"- {name}: {value}{suffix}")
    return lines


def _nmck_item_markdown(item: dict[str, Any]) -> list[str]:
    lines = _item_markdown(item)
    for price in item.get("supplier_prices") or []:
        lines.append(f"{price['source_id']}_unit_price: {price.get('raw_unit_price') or ''}")
        if price.get("raw_row_total"):
            lines.append(f"{price['source_id']}_row_total: {price.get('raw_row_total')}")
    if item.get("selected_min_unit_price_raw"):
        lines.append(f"selected_min_unit_price: {item['selected_min_unit_price_raw']}")
    if item.get("row_total_declared_raw"):
        lines.append(f"row_total_declared: {item['row_total_declared_raw']}")
    return lines


def _specification_item_markdown(item: dict[str, Any]) -> list[str]:
    lines = [
        "",
        f"SPEC ITEM {item.get('row_number') or item.get('row_index')}",
        f"name: {item.get('name') or ''}",
        f"description: {item.get('description') or ''}",
        f"unit: {item.get('unit') or ''}",
        f"quantity: {item.get('quantity_raw') or ''}",
        f"unit_price_without_vat: {item.get('raw_unit_price_without_vat') or ''}",
        f"unit_price_with_vat: {item.get('raw_unit_price_with_vat') or ''}",
        f"total_price: {item.get('raw_total_price') or ''}",
    ]
    return lines
