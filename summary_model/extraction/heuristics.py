from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Iterable

from summary_model.domain.models import (
    ContractSummary,
    DocumentIR,
    DocumentSummary,
    DocumentType,
    Evidence,
    ExplanatoryNoteSummary,
    ExtractedValue,
    OozSummary,
    OnmckItem,
    PlanRequestSummary,
    ProcurementItem,
    ProcurementRequestSummary,
    CommercialOfferSummary,
    OnmckSummary,
    ItemCharacteristic,
    SupplierPrice,
)


OKPD_RE = re.compile(r"(?<![\d.])\d{2}\.\d{2}\.\d{2}\.\d{3}(?!-\d{8})(?![\d.])")
KTRU_RE = re.compile(r"(?<![\d.])\d{2}\.\d{2}\.\d{2}\.\d{3}-\d{8}(?!\d)")
MONEY_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[ \u00a0]\d{3})*(?:[,.]\d{2})?)(?:\s*(?:руб(?:лей|ля|ль|\.)?))", re.I)
AMOUNT_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[ \u00a0]\d{3})+(?:[,.]\d{2})?)(?!\d)")
PLAIN_AMOUNT_RE = re.compile(r"(?<!\d)(\d+(?:[ \u00a0]\d{3})*(?:[,.]\d{1,2})?)(?!\d)")


def _normalize_text(value: str) -> str:
    return " ".join((value or "").replace("\xa0", " ").split()).strip()


def _evidence(ir: DocumentIR, block_id: str, quote: str, table_id=None, row=None, column=None):
    return Evidence(
        document_id=ir.document_id,
        block_id=block_id,
        table_id=table_id,
        row=row,
        column=column,
        quote=quote[:500],
    )


def _value(ir: DocumentIR, block_id: str, raw, normalized=None, **location) -> ExtractedValue:
    return ExtractedValue(
        raw_value=raw,
        normalized_value=normalized if normalized is not None else raw,
        confidence=0.85,
        evidence=[_evidence(ir, block_id, str(raw), **location)],
    )


def _paragraphs(ir: DocumentIR) -> list[tuple[str, str]]:
    return [
        (block.block_id, block.text)
        for block in ir.blocks
        if block.type == "paragraph" and block.text
    ]


def _origin_cells(table):
    alias_to_column = {
        column.alias: column.index
        for column in table.columns
    }
    for row in table.rows:
        for alias, text in row.values.items():
            yield text, row.row, alias_to_column[alias]


def _first_matching_line(ir: DocumentIR, markers: Iterable[str]) -> ExtractedValue | None:
    lowered = tuple(marker.lower() for marker in markers)
    paragraphs = _paragraphs(ir)
    for index, (block_id, text) in enumerate(paragraphs):
        if any(marker in text.lower() for marker in lowered):
            value = text.split(":", 1)[1].strip() if ":" in text else text
            if normalize_heading(value) in lowered and index + 1 < len(paragraphs):
                next_block_id, next_text = paragraphs[index + 1]
                return _value(ir, next_block_id, next_text, next_text)
            return _value(ir, block_id, text, value)
    for block in ir.blocks:
        if block.table is None:
            continue
        matrix = block.table.matrix()
        for row_index, row in enumerate(matrix):
            for column_index, cell in enumerate(row):
                if any(marker in cell.lower() for marker in lowered):
                    value = next((candidate for candidate in row[column_index + 1:] if candidate.strip()), "")
                    if value:
                        return _value(
                            ir,
                            block.block_id,
                            value,
                            value,
                            table_id=block.table.table_id,
                            row=row_index,
                            column=column_index + 1,
                        )
    return None


def _all_matching_lines(ir: DocumentIR, markers: Iterable[str]) -> list[ExtractedValue]:
    lowered = tuple(marker.lower() for marker in markers)
    values: list[ExtractedValue] = []
    for block_id, text in _paragraphs(ir):
        if any(marker in text.lower() for marker in lowered):
            normalized = text.split(":", 1)[1].strip() if ":" in text else text
            values.append(_value(ir, block_id, text, normalized))
    for block in ir.blocks:
        if block.table is None:
            continue
        for row_index, row in enumerate(block.table.matrix()):
            for column_index, cell in enumerate(row):
                if not any(marker in cell.lower() for marker in lowered):
                    continue
                normalized = next(
                    (candidate for candidate in row[column_index + 1:] if candidate.strip()),
                    "",
                )
                if normalized:
                    values.append(
                        _value(
                            ir,
                            block.block_id,
                            normalized,
                            normalized,
                            table_id=block.table.table_id,
                            row=row_index,
                            column=column_index + 1,
                        )
                    )
    return values


def normalize_heading(value: str) -> str:
    return _normalize_text(value).lower().rstrip(":")


def _money(ir: DocumentIR) -> ExtractedValue | None:
    candidates = _paragraphs(ir)
    for block in ir.blocks:
        if block.table:
            for text, _, _ in _origin_cells(block.table):
                candidates.append((block.block_id, text))
    for block_id, text in candidates:
        if not any(marker in text.lower() for marker in ("цена", "нмцк", "цк:")):
            continue
        match = MONEY_RE.search(text) or AMOUNT_RE.search(text)
        if not match:
            continue
        raw = match.group(1)
        try:
            normalized = Decimal(raw.replace(" ", "").replace("\xa0", "").replace(",", "."))
        except InvalidOperation:
            normalized = raw
        return _value(ir, block_id, raw, normalized)
    for block in ir.blocks:
        if block.table is None:
            continue
        for row_index, row in enumerate(block.table.matrix()):
            row_text = " ".join(row)
            if not any(marker in row_text.lower() for marker in ("цена", "нмцк", "цк")):
                continue
            match = MONEY_RE.search(row_text) or AMOUNT_RE.search(row_text)
            if not match:
                continue
            raw = match.group(1)
            try:
                normalized = Decimal(raw.replace(" ", "").replace("\xa0", "").replace(",", "."))
            except InvalidOperation:
                normalized = raw
            return _value(
                ir,
                block.block_id,
                raw,
                normalized,
                table_id=block.table.table_id,
                row=row_index,
            )
    return None


def _money_values(ir: DocumentIR) -> list[ExtractedValue]:
    values: list[ExtractedValue] = []
    for block in ir.blocks:
        candidates: list[tuple[str, int | None, int | None]] = []
        if block.text:
            candidates.append((block.text, None, None))
        if block.table:
            candidates.extend(
                (text, row, column)
                for text, row, column in _origin_cells(block.table)
            )
        for text, row, column in candidates:
            if not any(marker in text.lower() for marker in ("цена", "нмцк", "цк:")):
                continue
            match = MONEY_RE.search(text) or AMOUNT_RE.search(text)
            if not match:
                continue
            raw = match.group(1)
            try:
                normalized = Decimal(raw.replace(" ", "").replace("\xa0", "").replace(",", "."))
            except InvalidOperation:
                normalized = raw
            values.append(
                _value(
                    ir,
                    block.block_id,
                    raw,
                    normalized,
                    table_id=block.table.table_id if block.table else None,
                    row=row,
                    column=column,
                )
            )
        if block.table:
            for row_index, row_values in enumerate(block.table.matrix()):
                marker_index = next(
                    (
                        index
                        for index, cell in enumerate(row_values)
                        if any(marker in cell.lower() for marker in ("цена", "нмцк", "цк:"))
                    ),
                    None,
                )
                if marker_index is None:
                    continue
                for column_index, text in enumerate(row_values[marker_index + 1:], marker_index + 1):
                    match = MONEY_RE.search(text) or AMOUNT_RE.search(text) or PLAIN_AMOUNT_RE.search(text)
                    if not match:
                        continue
                    raw = match.group(1)
                    try:
                        normalized = Decimal(
                            raw.replace(" ", "").replace("\xa0", "").replace(",", ".")
                        )
                    except InvalidOperation:
                        normalized = raw
                    candidate = _value(
                        ir,
                        block.block_id,
                        raw,
                        normalized,
                        table_id=block.table.table_id,
                        row=row_index,
                        column=column_index,
                    )
                    signature = (
                        candidate.evidence[0].block_id,
                        candidate.evidence[0].table_id,
                        candidate.evidence[0].row,
                        candidate.evidence[0].column,
                    )
                    if signature not in {
                        (
                            value.evidence[0].block_id,
                            value.evidence[0].table_id,
                            value.evidence[0].row,
                            value.evidence[0].column,
                        )
                        for value in values
                        if value.evidence
                    }:
                        values.append(candidate)
    return values


def _header_index(headers: list[str], *markers: str) -> int | None:
    for index, header in enumerate(headers):
        normalized = header.lower()
        if all(marker.lower() in normalized for marker in markers):
            return index
    return None


def _codes(text: str) -> tuple[list[str], list[str]]:
    ktru = list(dict.fromkeys(KTRU_RE.findall(text)))
    without_ktru = KTRU_RE.sub(" ", text)
    okpd = list(dict.fromkeys(OKPD_RE.findall(without_ktru)))
    return okpd, ktru


def _code_values_from_text(
    ir: DocumentIR,
    block_id: str,
    table_id: str,
    row: int,
    column: int,
    text: str,
    code_pattern: str,
) -> list[ExtractedValue]:
    pair_pattern = re.compile(
        rf"({code_pattern})\s*[-–—]\s*(.*?)"
        rf"(?=\s+(?:{code_pattern})\s*[-–—]|$)"
    )
    values = []
    for match in pair_pattern.finditer(text):
        code = match.group(1)
        quote = _normalize_text(match.group(0))
        values.append(
            _value(
                ir,
                block_id,
                quote,
                code,
                table_id=table_id,
                row=row,
                column=column,
            )
        )
    return values


def _plan_item_from_key_value(ir: DocumentIR) -> ProcurementItem | None:
    for block in ir.blocks:
        table = block.table
        if table is None or table.kind != "key_value":
            continue
        matrix = table.matrix()
        fields: dict[str, tuple[int, int, str]] = {}
        for row_index, row in enumerate(matrix):
            for field_column, cell in enumerate(row):
                heading = normalize_heading(cell)
                if not heading:
                    continue
                if any(
                    marker in heading
                    for marker in (
                        "наименование объекта закупки",
                        "код окпд 2",
                        "код позиции ктру",
                        "количество",
                    )
                ):
                    value_column = next(
                        (
                            index
                            for index in range(field_column + 1, len(row))
                            if row[index].strip()
                        ),
                        None,
                    )
                    if value_column is not None:
                        fields[heading] = (
                            row_index,
                            value_column,
                            row[value_column].strip(),
                        )
        subject_entry = next(
            (
                entry
                for heading, entry in fields.items()
                if "наименование объекта закупки" in heading
            ),
            None,
        )
        codes_okpd_entry = next(
            (
                entry
                for heading, entry in fields.items()
                if "код окпд 2" in heading
            ),
            None,
        )
        codes_ktru_entry = next(
            (
                entry
                for heading, entry in fields.items()
                if "код позиции ктру" in heading
            ),
            None,
        )
        quantity_entry = next(
            (
                entry
                for heading, entry in fields.items()
                if heading == "количество"
            ),
            None,
        )
        if not any((subject_entry, codes_okpd_entry, codes_ktru_entry)):
            continue
        name_row, name_column, name = subject_entry or (
            codes_okpd_entry or codes_ktru_entry
        )
        item = ProcurementItem(
            item_id=f"{ir.document_id}-item-plan-total",
            name=_value(
                ir,
                block.block_id,
                name,
                name,
                table_id=table.table_id,
                row=name_row,
                column=name_column,
            ),
        )
        if codes_okpd_entry:
            row, column, text = codes_okpd_entry
            item.okpd2 = _code_values_from_text(
                ir,
                block.block_id,
                table.table_id,
                row,
                column,
                text,
                r"\d{2}\.\d{2}\.\d{2}\.\d{3}",
            )
        if codes_ktru_entry:
            row, column, text = codes_ktru_entry
            item.ktru = _code_values_from_text(
                ir,
                block.block_id,
                table.table_id,
                row,
                column,
                text,
                r"\d{2}\.\d{2}\.\d{2}\.\d{3}-\d{8}",
            )
        if quantity_entry:
            row, column, text = quantity_entry
            match = re.search(r"(\d+(?:[.,]\d+)?)\s*(\S+)?", text)
            normalized = (
                {
                    "value": Decimal(match.group(1).replace(",", ".")),
                    "unit": match.group(2) or None,
                }
                if match
                else text
            )
            item.quantity = _value(
                ir,
                block.block_id,
                text,
                normalized,
                table_id=table.table_id,
                row=row,
                column=column,
            )
            if match and match.group(2):
                item.unit = _value(
                    ir,
                    block.block_id,
                    match.group(2),
                    match.group(2).casefold(),
                    table_id=table.table_id,
                    row=row,
                    column=column,
                )
        return item
    return None


def _items_from_tables(ir: DocumentIR) -> list[ProcurementItem]:
    result: dict[str, ProcurementItem] = {}
    for block in ir.blocks:
        table = block.table
        if table is None:
            continue
        if table.kind not in {
            "item_list",
            "characteristics",
            "supplier_matrix",
            "specification",
        }:
            continue
        headers = table.header_labels()
        matrix = table.matrix()
        name_index = _header_index(headers, "наименование")
        if name_index is not None and "характерист" in headers[name_index].lower():
            name_index = None
        quantity_index = next(
            (
                index for index, header in enumerate(headers)
                if "количество" in header.lower() or "кол-во" in header.lower()
            ),
            None,
        )
        unit_candidates = [
            index
            for index, header in enumerate(headers)
            if "ед." in header.lower() or "единица" in header.lower()
        ]
        unit_index = next(
            (
                index
                for index in unit_candidates
                if "характерист" not in headers[index].lower()
            ),
            unit_candidates[0] if unit_candidates else None,
        )
        unit_price_index = next(
            (
                index for index, header in enumerate(headers)
                if "цена" in header.lower() and "ед" in header.lower()
            ),
            None,
        )
        total_price_index = next(
            (
                index for index, header in enumerate(headers)
                if "стоимость" in header.lower() or "итого" in header.lower()
            ),
            None,
        )
        characteristic_index = next(
            (
                index for index, header in enumerate(headers)
                if "характерист" in header.lower() and "значение" not in header.lower()
            ),
            None,
        )
        characteristic_value_index = next(
            (
                index for index, header in enumerate(headers)
                if "значение" in header.lower() and "характерист" in header.lower()
            ),
            None,
        )
        code_index = next(
            (
                index for index, header in enumerate(headers)
                if "окпд" in header.lower() or "ктру" in header.lower()
            ),
            None,
        )
        start = max(table.header_rows, default=-1) + 1
        for row_index, row in enumerate(matrix[start:], start=start):
            row_text = " | ".join(row)
            okpd, ktru = _codes(row_text)
            name = row[name_index].strip() if name_index is not None and name_index < len(row) else ""
            if not name and not okpd and not ktru:
                continue
            if normalize_heading(name) in {"наименование", "наименование товара"}:
                continue
            if normalize_heading(name).startswith(("всего", "итого")):
                continue
            position = row[0].strip() if row else ""
            if table.kind == "characteristics" and position:
                key = f"{table.table_id}:position:{position}"
            elif table.kind in {"item_list", "specification", "supplier_matrix"}:
                key = f"{table.table_id}:{row_index}"
            else:
                key = ktru[0] if ktru else (okpd[0] if okpd else name.lower())
            if not key:
                continue
            item = result.get(key)
            if item is None:
                item = ProcurementItem(
                    item_id=f"{ir.document_id}-item-{len(result) + 1}",
                    name=_value(
                        ir, block.block_id, name or row_text, name or row_text,
                        table_id=table.table_id, row=row_index, column=name_index,
                    ),
                )
                result[key] = item
            for code in okpd:
                if code not in [value.normalized_value for value in item.okpd2]:
                    item.okpd2.append(
                        _value(ir, block.block_id, code, code, table_id=table.table_id, row=row_index, column=code_index)
                    )
            for code in ktru:
                if code not in [value.normalized_value for value in item.ktru]:
                    item.ktru.append(
                        _value(ir, block.block_id, code, code, table_id=table.table_id, row=row_index, column=code_index)
                    )
            if quantity_index is not None and quantity_index < len(row) and row[quantity_index].strip():
                item.quantity = _value(
                    ir, block.block_id, row[quantity_index], row[quantity_index],
                    table_id=table.table_id, row=row_index, column=quantity_index,
                )
            if unit_index is not None and unit_index < len(row) and row[unit_index].strip():
                item.unit = _value(
                    ir, block.block_id, row[unit_index], row[unit_index].lower(),
                    table_id=table.table_id, row=row_index, column=unit_index,
                )
            if (
                unit_price_index is not None
                and unit_price_index < len(row)
                and row[unit_price_index].strip()
            ):
                item.unit_price = _value(
                    ir,
                    block.block_id,
                    row[unit_price_index],
                    row[unit_price_index],
                    table_id=table.table_id,
                    row=row_index,
                    column=unit_price_index,
                )
            if (
                total_price_index is not None
                and total_price_index < len(row)
                and row[total_price_index].strip()
            ):
                item.total_price = _value(
                    ir,
                    block.block_id,
                    row[total_price_index],
                    row[total_price_index],
                    table_id=table.table_id,
                    row=row_index,
                    column=total_price_index,
                )
            if (
                characteristic_index is not None
                and characteristic_value_index is not None
                and characteristic_index < len(row)
                and characteristic_value_index < len(row)
                and row[characteristic_index].strip()
            ):
                item.characteristics.append(
                    ItemCharacteristic(
                        name=_value(
                            ir, block.block_id, row[characteristic_index],
                            row[characteristic_index], table_id=table.table_id,
                            row=row_index, column=characteristic_index,
                        ),
                        value=_value(
                            ir, block.block_id, row[characteristic_value_index],
                            row[characteristic_value_index], table_id=table.table_id,
                            row=row_index, column=characteristic_value_index,
                        ),
                    )
                )
    return list(result.values())


def _onmck_items(ir: DocumentIR, items: list[ProcurementItem]) -> list[OnmckItem]:
    tables = {
        block.table.table_id: (block.block_id, block.table)
        for block in ir.blocks
        if block.table is not None
    }
    result = []
    for item in items:
        wrapper = OnmckItem(item=item)
        evidence = item.name.evidence[0] if item.name.evidence else None
        if evidence is None or evidence.table_id not in tables or evidence.row is None:
            result.append(wrapper)
            continue
        block_id, table = tables[evidence.table_id]
        matrix = table.matrix()
        row = matrix[evidence.row]
        header_rows = matrix[: evidence.row]
        supplier_columns = []
        for column in range(table.column_count):
            header = " ".join(
                header_row[column]
                for header_row in header_rows
                if column < len(header_row) and header_row[column]
            ).casefold()
            if "поставщик" in header and "цена за ед" in header:
                supplier_columns.append(column)
        for index, column in enumerate(supplier_columns, start=1):
            if column >= len(row) or not row[column].strip():
                continue
            supplier_header = next(
                (
                    header_row[column]
                    for header_row in reversed(header_rows)
                    if column < len(header_row) and "поставщик" in header_row[column].casefold()
                ),
                f"Поставщик {index}",
            )
            total_column = column + 1 if column + 1 < len(row) else None
            wrapper.supplier_prices.append(
                SupplierPrice(
                    supplier_ref=supplier_header,
                    unit_price=_value(
                        ir,
                        block_id,
                        row[column],
                        row[column],
                        table_id=table.table_id,
                        row=evidence.row,
                        column=column,
                    ),
                    total_price=(
                        _value(
                            ir,
                            block_id,
                            row[total_column],
                            row[total_column],
                            table_id=table.table_id,
                            row=evidence.row,
                            column=total_column,
                        )
                        if total_column is not None and row[total_column].strip()
                        else None
                    ),
                )
            )
        selected_column = next(
            (
                column
                for column, header in enumerate(table.header_labels())
                if "минимальная цена" in header.casefold()
            ),
            None,
        )
        if selected_column is not None and row[selected_column].strip():
            wrapper.selected_unit_price = _value(
                ir,
                block_id,
                row[selected_column],
                row[selected_column],
                table_id=table.table_id,
                row=evidence.row,
                column=selected_column,
            )
        total_column = next(
            (
                column
                for column, header in enumerate(table.header_labels())
                if "цена контракта" in header.casefold()
            ),
            None,
        )
        if total_column is not None and row[total_column].strip():
            wrapper.calculated_total = _value(
                ir,
                block_id,
                row[total_column],
                row[total_column],
                table_id=table.table_id,
                row=evidence.row,
                column=total_column,
            )
        result.append(wrapper)
    return result


SUMMARY_TYPES = {
    DocumentType.PLAN: PlanRequestSummary,
    DocumentType.REQUEST: ProcurementRequestSummary,
    DocumentType.COMMERCIAL_OFFER: CommercialOfferSummary,
    DocumentType.ONMCK: OnmckSummary,
    DocumentType.OOZ: OozSummary,
    DocumentType.CONTRACT: ContractSummary,
    DocumentType.EXPLANATORY_NOTE: ExplanatoryNoteSummary,
}


def heuristic_summary(
    ir: DocumentIR,
    document_type: DocumentType,
    display_name: str,
    confidence: float,
    classification_evidence: list[Evidence],
    warnings: list[str],
) -> DocumentSummary:
    schema = SUMMARY_TYPES.get(document_type, DocumentSummary)
    common = dict(
        document_id=ir.document_id,
        display_name=display_name,
        detected_type=document_type,
        classification_confidence=confidence,
        classification_evidence=classification_evidence,
        extraction_warnings=list(warnings),
    )
    if schema is DocumentSummary:
        return schema(**common, unresolved_fields=["document_type"])

    subject = _first_matching_line(
        ir,
        ("наименование закупки", "наименование объекта закупки", "предметом контракта", "описание объекта закупки"),
    )
    delivery_places = _all_matching_lines(ir, ("место поставки", "место оказания"))
    delivery_periods = _all_matching_lines(
        ir,
        ("срок поставки", "сроки поставки", "срок оказания", "сроки оказания"),
    )
    items = _items_from_tables(ir)
    if schema is PlanRequestSummary:
        plan_item = _plan_item_from_key_value(ir)
        if plan_item is not None:
            items = [plan_item]
    money_values = _money_values(ir)

    values = dict(common)
    if "subject" in schema.model_fields:
        values["subject"] = subject
    if "items" in schema.model_fields:
        values["items"] = (
            _onmck_items(ir, items)
            if schema is OnmckSummary
            else items
        )
    if "delivery_places" in schema.model_fields:
        values["delivery_places"] = delivery_places
    if "delivery_periods" in schema.model_fields:
        values["delivery_periods"] = delivery_periods
    if "nmck" in schema.model_fields:
        values["nmck"] = money_values
    if "price" in schema.model_fields:
        values["price"] = money_values
    values["unresolved_fields"] = [
        field for field, value in (
            ("subject", subject),
            ("items", items if "items" in schema.model_fields else True),
        )
        if not value
    ]
    return schema(**values)
