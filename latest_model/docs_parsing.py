import numpy as np
from typing import List
from shared_modules.parser_functions import DocumentParser, PlanParser
from shared_modules.parser_functions import extract_ktru_block, _clean_keyword_dict, _extract_keyword_windows
from shared_modules.parser_functions import parse_contracters_onmck_by_row_number


def _parse_plan_points(plan_path: str) -> List[str]:
    parser_plan = PlanParser(plan_path)
    plan_points = parser_plan.extract_table_kv_from_docx()
    if not plan_points:
        raise ValueError("Не удалось извлечь данные из плана-графика: ТАБЛИЦЫ ПУСТЫ ИЛИ НЕ НАЙДЕНЫ")

    return plan_points


def _parse_contract_characteristics(contract_path: str) -> str:
    """
    Достаёт характеристики товаров из контракта.
    """
    parser_contract = DocumentParser(contract_path)

    table_ktry_names = parser_contract.extract_tables_columns(keywords=["№", "ОКПД", "КТРУ"])

    table_characteristics, ktry_codes = parser_contract.extract_tables_characteristics(
        keywords=["№", "КТРУ", "Наименование характеристики", "Значение характеристики"]
    )

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
        contract_points = _extract_keyword_windows(
            contract_plain_text,
            keywords=["ОКПД"],
            window=window,
        )
        contract_points += extract_ktru_block(contract_plain_text, tail_chars=60, fallback_chars=150)

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

        ooz_points = _extract_keyword_windows(
            ooz_plain_text,
            keywords=["ОКПД"],
            window=window,
        )
        ooz_points = ooz_points + "\n" + extract_ktru_block(ooz_plain_text, tail_chars=30, fallback_chars=150)

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
