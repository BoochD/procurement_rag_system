import numpy as np
import re
from typing import List
from shared_modules.parser_functions import DocumentParser, PlanParser
from shared_modules.parser_functions import (
    build_table_header,
    dedupe_merged_cells,
    extract_ktru_block,
    extract_okpd2_entries_from_plain_text,
    get_row_cells,
    normalize_text,
    _clean_keyword_dict,
)
from shared_modules.parser_functions import parse_contracters_onmck_by_row_number


KTRU_ENTRY_RE = re.compile(
    r"(?<![\d.])"
    r"(?P<code>\d{2}\.\d{2}\.\d{2}\.\d{3}-\d{8})"
    r"(?![\d.])"
    r"(?:\s*[-–—]\s*(?P<name>[^\n\r;|]+))?"
)


def _join_non_empty(*parts: str) -> str:
    return "\n".join(part.strip() for part in parts if part and part.strip())


def _normalize_lookup_text(value: str) -> str:
    return normalize_text(value).lstrip("*").strip().lower()


def _extract_ktru_entries_from_plain_text(text: str) -> list[dict[str, str]]:
    entries = []
    for match in KTRU_ENTRY_RE.finditer(text or ""):
        entries.append({
            "code": match.group("code").strip(),
            "name": _normalize_lookup_text(match.group("name") or ""),
        })
    return entries


def _match_ktru_code_by_name(item_name: str, ktru_entries: list[dict[str, str]]) -> str | None:
    normalized_item_name = _normalize_lookup_text(item_name)
    if not normalized_item_name:
        return None

    for entry in ktru_entries:
        entry_name = entry["name"]
        if entry_name and (
            entry_name == normalized_item_name
            or entry_name in normalized_item_name
            or normalized_item_name in entry_name
        ):
            return entry["code"]

    return None


def _append_characteristic(result: dict[str, dict[str, str]], code: str, name: str, value: str) -> None:
    clean_name = normalize_text(name).lstrip("*").strip()
    clean_value = normalize_text(value)
    if not code or not clean_name or not clean_value:
        return

    characteristics = result.setdefault(code, {})
    if clean_name in characteristics and clean_value not in characteristics[clean_name].split("; "):
        characteristics[clean_name] = characteristics[clean_name] + "; " + clean_value
    else:
        characteristics[clean_name] = clean_value


def _extract_ooz_characteristics_fallback(parser_ooz: DocumentParser) -> tuple[dict[str, dict[str, str]], set[str]]:
    plain_text = parser_ooz.extract_clean_text()
    ktru_entries = _extract_ktru_entries_from_plain_text(plain_text)
    result: dict[str, dict[str, str]] = {}

    for table in parser_ooz.doc.tables:
        rows = [get_row_cells(row) for row in table.rows if len(dedupe_merged_cells(row)) > 1]
        if not rows:
            continue

        header, header_rows_count = build_table_header(rows)
        normalized_header = [_normalize_lookup_text(cell) for cell in header]

        name_idx = next(
            (
                idx
                for idx, cell in enumerate(normalized_header)
                if "наименование" in cell
                and "оборудован" not in cell
                and "характерист" not in cell
            ),
            None,
        )
        characteristic_idx = next(
            (
                idx
                for idx, cell in enumerate(normalized_header)
                if "характерист" in cell and "значение" not in cell and "ед." not in cell
            ),
            None,
        )
        value_idx = next(
            (
                idx
                for idx, cell in enumerate(normalized_header)
                if "значение" in cell and "характерист" in cell
            ),
            None,
        )

        if characteristic_idx is None or value_idx is None:
            continue

        for row in rows[header_rows_count:]:
            normalized_row = [normalize_text(cell) for cell in row]
            row_text = " ".join(normalized_row)
            code_match = KTRU_ENTRY_RE.search(row_text)
            code = code_match.group("code") if code_match else None

            if not code and name_idx is not None and name_idx < len(normalized_row):
                code = _match_ktru_code_by_name(normalized_row[name_idx], ktru_entries)

            if not code:
                continue

            if characteristic_idx >= len(normalized_row) or value_idx >= len(normalized_row):
                continue

            _append_characteristic(
                result,
                code,
                normalized_row[characteristic_idx],
                normalized_row[value_idx],
            )

    return result, set(result)


def _parse_plan_points(plan_path: str) -> List[str]:
    parser_plan = PlanParser(plan_path)
    plan_points = parser_plan.extract_table_kv_from_docx()
    if not plan_points:
        raise ValueError("Не удалось извлечь данные из плана-графика: ТАБЛИЦЫ ПУСТЫ ИЛИ НЕ НАЙДЕНЫ")

    return plan_points


def _parse_ooz_characteristic(ooz_path: str) -> tuple[str, dict[str, dict[str, str]], set[str]]:
    """
    Достаёт характеристики товаров из ООЗ для сравнения с КТРУ.
    """
    parser_ooz = DocumentParser(ooz_path)

    table_ktry_names = parser_ooz.extract_tables_columns(keywords=["№", "ОКПД", "КТРУ"])

    table_characteristics, ktry_codes = parser_ooz.extract_tables_characteristics(
        keywords=["№", "КТРУ", "Наименование характеристики", "Значение характеристики"]
    )
    fallback_characteristics, fallback_codes = _extract_ooz_characteristics_fallback(parser_ooz)

    for code, characteristics in fallback_characteristics.items():
        table_characteristics.setdefault(code, {}).update(characteristics)
    ktry_codes = set(ktry_codes) | fallback_codes

    ktry_codes = {code for code in ktry_codes if len(code.split("-")) > 1}
    table_characteristics = {ktry_code: table_characteristics[ktry_code] for ktry_code in ktry_codes}

    return table_ktry_names, table_characteristics, ktry_codes


def _parse_contract_points(contract_path: str, window: int = 100) -> str:
    """
    Достаёт КТРУ, ОКПД и количество товаров из контракта.
    """
    parser_contract = DocumentParser(contract_path)
    ktru_okpd = parser_contract.extract_table_cells_by_keyword(["ОКПД", "КТРУ"])
    contract_points = _clean_keyword_dict(ktru_okpd)

    if not contract_points or len(contract_points) < 20:
        contract_plain_text = parser_contract.extract_clean_text().strip()
        contract_points = _join_non_empty(
            extract_okpd2_entries_from_plain_text(contract_plain_text),
            extract_ktru_block(contract_plain_text, tail_chars=60, fallback_chars=150),
        )

        table_contract_points = parser_contract.extract_tables_columns(keywords=["№", "ОКПД", "КТРУ"])
        table_contract_points_amount = parser_contract.extract_tables_columns(
            keywords=["№", "Наименование товара", "Количество"]
        )
        table_contract_points_amount_2 = parser_contract.extract_tables_columns(
            keywords=["Наименование продукции", "Кол-во"]
        )
        table_contract_points_amount_3 = parser_contract.extract_tables_columns(
            keywords=["Наименование,", "Количество"]
        )

        if table_contract_points:
            contract_points = contract_points + "\n" + table_contract_points
        if table_contract_points_amount:
            contract_points = contract_points + "\n" + table_contract_points_amount
        if table_contract_points_amount_2:
            contract_points = contract_points + "\n" + table_contract_points_amount_2
        if table_contract_points_amount_3:
            contract_points = contract_points + "\n" + table_contract_points_amount_3
        if not contract_points:
            contract_points = "В контракте не найдены КТРУ и ОКПД"
    else:
        table_contract_points_amount_3 = parser_contract.extract_tables_columns(
            keywords=["Наименование,", "Количество"]
        )
        if table_contract_points_amount_3:
            contract_points = contract_points + "\n" + table_contract_points_amount_3

    return contract_points


def _parse_ooz_points(ooz_path: str, window: int = 200) -> str:
    parser_ooz = DocumentParser(ooz_path)
    tables_ooz = parser_ooz.extract_table_cells_by_keyword(["ОКПД", "КТРУ"])
    ooz_points = _clean_keyword_dict(tables_ooz)

    if not ooz_points or len(ooz_points) < 20:
        ooz_plain_text = parser_ooz.extract_clean_text().strip()
        ooz_ktry_okpd_table = parser_ooz.extract_tables_columns(keywords=["№", "ОКПД", "КТРУ"])

        ooz_amounts = parser_ooz.extract_tables_columns(keywords=["№", "наименование товара", "количество"])
        if not ooz_amounts:
            ooz_amounts = parser_ooz.extract_tables_columns(keywords=["наименование", "количество"])

        ooz_points = _join_non_empty(
            extract_okpd2_entries_from_plain_text(ooz_plain_text),
            extract_ktru_block(ooz_plain_text, tail_chars=30, fallback_chars=150),
        )

        if ooz_ktry_okpd_table:
            ooz_points = ooz_points + "\n" + ooz_ktry_okpd_table

        if ooz_amounts:
            ooz_points = ooz_points + "\n" + ooz_amounts
    else:
        ooz_amounts = parser_ooz.extract_tables_columns(keywords=["Наименование,", "Количество"])
        if ooz_amounts:
            ooz_points = ooz_points + "\n" + ooz_amounts

    if not ooz_points:
        ooz_points = "В ООЗ не найдены КТРУ или ОКПД"

    return ooz_points


def _parse_zapiska_text(zapiska_path: str) -> str:
    parser_zapiska = DocumentParser(zapiska_path)
    paragraphs_zapiska = parser_zapiska.extract_clean_text()
    tables_zapiska = parser_zapiska.table_to_markdown()
    zapiska_full_text = ("Название: " + paragraphs_zapiska + "\n\n" + tables_zapiska).strip()
    if not zapiska_full_text:
        zapiska_full_text = "Не удалось извлечь данные из записки"

    return zapiska_full_text


def _parse_onmck_text(ONMCK_path: str) -> str:
    parser_onmck = DocumentParser(ONMCK_path)
    table_onmck = parser_onmck.extract_rows_region(keyword="шт")
    table_onmck += "\n" + parser_onmck.extract_rows_region(keyword="к-т")
    if not table_onmck:
        table_onmck = parser_onmck.extract_tables_columns(
            keywords=["наименование товара", "Ед.", "Единиц", "Кол-во", "Количество"]
        )

    return table_onmck


def _parse_onmck_pricies(ONMCK_path: str) -> str:
    pricies = parse_contracters_onmck_by_row_number(ONMCK_path)
    result_lines = []
    error_lines = []

    name_width = max(len(k) for k in pricies)
    var_width = 5
    for k, v in pricies.items():
        mu, std = np.mean(v), np.std(v)
        std = np.sqrt(np.sum(np.square(v - mu) / (len(v) - 1)))

        var_coeff = (100 * std / (mu + 1e-7))
        var_coeff = np.round(var_coeff, 2)

        if var_coeff >= 33:
            result = (
                f"{k:<{name_width}} | "
                + "<error>"
                + f"коэффициент вариации: {var_coeff:>{var_width}}% | "
                + "</error>"
                + f"Цены: {v}"
            )
            error_lines.append(
                "<error>"
                + "Внимание! Коэффициент вариации в 33% превышен | "
                + f"{k:<{name_width}} | Вариация цен поставщиков: {var_coeff}%"
                + "</error>"
            )
        else:
            result = (
                f"{k:<{name_width}} | "
                + "<ok>"
                + f"коэффициент вариации: {var_coeff:>{var_width}}%"
                + "</ok> | "
                + f"Цены: {v}"
            )

        result_lines.append(result)

    return "\n".join(result_lines + error_lines)
