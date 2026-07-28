from __future__ import annotations

import re

from summary_model.domain.models import DocumentType, TableIR
from summary_model.tables.models import ParsedTableType


def _joined(table: TableIR) -> str:
    parts = [
        table.title or "",
        " ".join(table.context_before),
        " ".join(table.context_after),
        " ".join(table.header_labels()),
    ]
    for row in table.rows:
        parts.extend(row.values.values())
    return " ".join(parts).casefold()


def _has_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _looks_like_signature_table(text: str) -> bool:
    initials = len(re.findall(r"\b[а-яё]+(?:\s+[а-яё]\.){1,2}", text, flags=re.I))
    if initials >= 3 and not _has_any(
        text,
        (
            "окпд",
            "ктру",
            "количество",
            "кол-во",
            "цена",
            "характеристик",
        ),
    ):
        return True
    return _has_any(
        text,
        (
            "подпись",
            "расшифровка",
            "м.п.",
            "мп",
            "должность",
            "заказчик",
            "поставщик",
            "сдал",
            "принял",
        ),
    ) and not _has_any(
        text,
        (
            "окпд",
            "ктру",
            "количество",
            "кол-во",
            "цена товара",
            "цена за ед",
            "характеристик",
        ),
    )


def _looks_like_contract_specification(text: str) -> bool:
    return _has_any(text, ("спецификация", "цена за единицу", "сумма без ндс", "ставка ндс", "всего")) and _has_any(
        text,
        ("цена", "сумма", "ндс", "всего"),
    )


def _looks_like_staged_nmck(text: str) -> bool:
    has_stage_rows = bool(re.search(r"(?:^|\s)\d+\.\s+[^|.]{0,160}этап", text))
    has_child_rows = bool(re.search(r"(?:^|\s)\d+\.\d+\s+", text))
    has_nmck_prices = _has_any(text, ("минимальная цена", "начальная", "цена контракта"))
    return has_stage_rows and has_child_rows and has_nmck_prices


def _looks_like_nmck_matrix(table: TableIR) -> bool:
    """Recognize price-source matrices without relying on a perfect header inference."""
    header_text = " ".join(
        " ".join(row)
        for row in table.matrix()[: min(table.row_count, 4)]
    ).casefold()
    has_sources = bool(re.search(r"(?:поставщик|исполнитель)\s*\d+", header_text))
    has_price_pair = (
        "цена за ед" in header_text
        or "цена за единицу" in header_text
    ) and ("стоимость" in header_text or "сумма" in header_text)
    has_result = "минимальная цена" in header_text or "цена контракта" in header_text
    has_item_columns = "количество" in header_text or "кол-во" in header_text
    return has_sources and has_price_pair and has_result and has_item_columns


def _looks_like_stage_table(table: TableIR, text: str) -> bool:
    """Require stage columns, not just a plan field mentioning stages."""
    header_text = " ".join(table.header_labels()).casefold()
    has_stage_number = bool(
        re.search(r"(?:№|номер)\s*этап", header_text)
        or re.search(r"(?:^|\s)этап(?:\s|$)", header_text)
    )
    detail_groups = (
        ("дата начала", "начало исполнения", "начало этапа"),
        ("срок оказания", "срок выполнения", "срок поставки", "период исполнения"),
        ("дата окончания", "окончание исполнения", "окончание этапа"),
        ("цена этапа", "стоимость этапа", "сумма этапа"),
        ("наименование этапа", "результат выполнения", "результат этапа"),
    )
    detail_count = sum(1 for markers in detail_groups if _has_any(header_text, markers))
    return has_stage_number and detail_count >= 2


def classify_parsed_table(
    table: TableIR,
    document_type: DocumentType | None,
) -> ParsedTableType:
    text = _joined(table)
    if _looks_like_signature_table(text):
        return "signature_table"

    if document_type == DocumentType.PLAN and _has_any(
        text,
        (
            "наименование объекта закупки",
            "код позиции ктру",
            "начальная",
            "план-график",
        ),
    ):
        return "schedule_application_table"
    if document_type == DocumentType.PLAN and table.kind == "key_value":
        return "schedule_application_table"

    if document_type == DocumentType.ONMCK and _looks_like_staged_nmck(text):
        return "nmck_staged_calculation_table"
    if document_type == DocumentType.ONMCK and _looks_like_nmck_matrix(table):
        return "nmck_calculation_table"

    if document_type == DocumentType.OOZ and table.kind in {"characteristics", "item_list"}:
        return "ooz_items_table"

    if document_type in {DocumentType.OOZ, DocumentType.CONTRACT} and _looks_like_stage_table(table, text):
        return "contract_stages_table"

    if document_type == DocumentType.CONTRACT and _looks_like_contract_specification(text):
        return "contract_specification_table"

    if document_type in {
        DocumentType.REQUEST,
        DocumentType.EXPLANATORY_NOTE,
        DocumentType.CONTRACT,
    } and "приложение" in text and any(
        marker in text
        for marker in (
            "заявка",
            "определение цены",
            "обоснование",
            "проект контракта",
            "описание объекта",
            "пояснительная",
            "коммерчес",
        )
    ):
        return (
            "contract_attachments_table"
            if document_type == DocumentType.CONTRACT
            else "request_attachments_table"
        )
    return "generic_table"
