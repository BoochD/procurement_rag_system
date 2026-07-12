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
    if document_type == DocumentType.ONMCK and table.kind == "supplier_matrix":
        return "nmck_calculation_table"
    if document_type == DocumentType.OOZ and table.kind in {"characteristics", "item_list"}:
        return "ooz_items_table"
    if document_type == DocumentType.CONTRACT and _looks_like_contract_specification(text):
        return "contract_specification_table"
    if document_type == DocumentType.CONTRACT and table.kind in {
        "characteristics",
        "item_list",
    }:
        return "ooz_items_table"
    if "приложение" in text and any(
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
    if any(marker in text for marker in ("этап", "срок исполнения этап")):
        return "contract_stages_table"
    if table.kind == "key_value":
        return "schedule_application_table" if "план-график" in text else "generic_table"
    if table.kind == "supplier_matrix":
        return "nmck_calculation_table"
    if table.kind == "characteristics":
        return "ooz_items_table"
    if table.kind in {"item_list", "specification"}:
        return "generic_table"
    return "unknown"
