from __future__ import annotations

import re

from summary_model.domain.models import TableIR
from summary_model.tables.models import ParsedTable
from summary_model.tables.utils import clean_text
from summary_model.vlm_lab.models import VlmTableCandidate, VlmTableRole


def table_role(table: ParsedTable) -> VlmTableRole:
    if table.table_type == "additional_characteristics_justification_table":
        return "additional_characteristics_justification"
    if table.table_type == "ooz_items_table":
        return "purchase_description"
    if table.table_type == "contract_stages_table":
        return "contract_stages"
    if table.table_type in {"nmck_calculation_table", "nmck_staged_calculation_table"}:
        return "nmck_calculation"
    if table.table_type == "contract_specification_table":
        return "contract_specification"
    if table.table_type in {"request_attachments_table", "contract_attachments_table"}:
        return "attachments"
    inferred = _infer_role_from_content(table)
    if inferred is not None:
        return inferred
    if table.table_type == "generic_table":
        return "generic"
    return "unknown"


def rank_table_candidates(
    tables: list[ParsedTable],
    *,
    target_role: VlmTableRole | None = None,
    query: str | None = None,
) -> list[VlmTableCandidate]:
    candidates = [
        _candidate(table, target_role=target_role, query=query)
        for table in tables
        if table.table_type not in {"signature_table", "ignored_table"}
    ]
    return sorted(
        candidates,
        key=lambda item: (item.confidence, item.complexity_score, item.row_count),
        reverse=True,
    )


def _candidate(
    table: ParsedTable,
    *,
    target_role: VlmTableRole | None,
    query: str | None,
) -> VlmTableCandidate:
    role = table_role(table)
    reasons: list[str] = []
    score = 0

    if target_role and role == target_role:
        score += 100
        reasons.append(f"role matches target {target_role}")
    elif target_role and role in {"generic", "unknown"}:
        score += 10
        reasons.append("fallback table kept as possible VLM candidate")

    title = clean_text(table.title)
    text = " ".join(
        [
            title,
            table.table_type,
            table.compact_markdown[:3000],
            " ".join(table.parser_warnings),
        ]
    ).casefold()
    if query:
        matched = _query_overlap(query, text)
        if matched:
            score += min(30, matched * 8)
            reasons.append(f"query overlap: {matched}")

    complexity = table_complexity_score(table)
    if complexity >= 40:
        reasons.append("complex table")
    if table.parser_warnings:
        reasons.append("parser warnings")

    confidence = min(0.99, (score + min(complexity, 50)) / 160)
    return VlmTableCandidate(
        table_id=table.table_id,
        block_id=table.block_id,
        table_index=table.table_index,
        table_type=table.table_type,
        title=title or None,
        role=role,
        row_count=table.row_count,
        col_count=table.col_count,
        complexity_score=complexity,
        confidence=round(confidence, 3),
        reasons=reasons,
        parser_warnings=list(table.parser_warnings),
    )


def table_complexity_score(table: ParsedTable) -> int:
    score = 0
    if table.row_count >= 40:
        score += 20
    if table.row_count >= 80:
        score += 20
    if table.col_count >= 8:
        score += 10
    if table.header_rows and len(table.header_rows) > 1:
        score += 15
    if table.parser_warnings:
        score += 20
    compact = table.compact_json or {}
    fallback_count = len(compact.get("fallback_rows") or compact.get("rows") or [])
    if fallback_count:
        score += min(30, fallback_count * 3)
    if _has_suspicious_items(compact):
        score += 30
    if len(table.compact_markdown) > 15_000:
        score += 15
    if len(table.compact_markdown) > 30_000:
        score += 15
    return score


def _infer_role_from_content(table: ParsedTable) -> VlmTableRole | None:
    text = " ".join(
        [
            clean_text(table.title),
            table.table_type,
            table.compact_markdown[:6000],
            " ".join(table.parser_warnings),
        ]
    ).casefold()
    if not text:
        return None
    if justification_candidate_reasons(table):
        return "additional_characteristics_justification"
    has_price = any(marker in text for marker in ("цена", "стоимость", "сумма", "ндс"))
    if "приложени" in text and any(
        marker in text
        for marker in (
            "описание объекта",
            "спецификац",
            "акт",
            "пояснитель",
            "проект контракта",
            "заявка",
        )
    ):
        return "attachments"
    if "этап" in text and any(
        marker in text
        for marker in (
            "срок",
            "период",
            "результат",
            "стоимость этапа",
            "цена этапа",
            "сумма этапа",
        )
    ):
        return "contract_stages"
    if any(marker in text for marker in ("поставщик", "исполнитель")) and any(
        marker in text
        for marker in (
            "минимальная цена",
            "цена за ед",
            "цена за единицу",
            "стоимость товаров",
            "цена контракта",
        )
    ):
        return "nmck_calculation"
    if "спецификац" in text and has_price and any(
        marker in text for marker in ("количество", "кол-во", "ед. изм", "единица")
    ):
        return "contract_specification"
    if any(marker in text for marker in ("ктру", "окпд", "характеристик")) and any(
        marker in text
        for marker in (
            "наименование",
            "значение",
            "требования",
            "количество",
            "единица",
        )
    ):
        return "purchase_description"
    return None


def justification_candidate_reasons(
    table: ParsedTable,
    source: TableIR | None = None,
) -> list[str]:
    title_context = " ".join(
        part
        for part in (
            clean_text(table.title),
            " ".join(source.context_before[-4:]) if source is not None else "",
        )
        if part
    ).casefold()
    headers = " ".join(
        source.header_labels() if source is not None else [
            " ".join(path.parts) for path in table.header_paths
        ]
    ).casefold()
    body = table.compact_markdown[:8000].casefold()

    subject_markers = ("дополнительн", "характеристик", "потребительск", "свойств")
    reasons: list[str] = []
    if "обоснован" in title_context and any(marker in title_context for marker in subject_markers):
        reasons.append("title/context identifies additional-characteristic justification")
    if (
        "обоснован" in headers
        and any(marker in headers for marker in subject_markers)
    ):
        reasons.append("header pair contains characteristic and justification columns")
    if re.search(
        r"обоснован\w*\s+(?:применения|включения|необходимости)\s+дополнительн\w*\s+характеристик",
        f"{title_context} {headers} {body}",
    ):
        reasons.append("explicit additional-characteristic justification phrase")
    return list(dict.fromkeys(reasons))


def _has_suspicious_items(compact: dict) -> bool:
    items = compact.get("items") or []
    if not items:
        return False
    suspicious = 0
    for item in items:
        name = clean_text(item.get("name"))
        quantity = clean_text(item.get("quantity_raw"))
        has_code = bool(item.get("okpd2_code") or item.get("ktru_code"))
        if len(name) > 600 or len(quantity) > 80:
            suspicious += 1
        if name.casefold() in {"наименование", "наименование товара"}:
            suspicious += 1
        if not has_code and not clean_text(item.get("unit")) and len(name) > 250:
            suspicious += 1
    return suspicious > 0


def _query_overlap(query: str, text: str) -> int:
    tokens = {
        token
        for token in re.findall(r"[a-zа-яё0-9]{3,}", query.casefold())
        if token not in {"таблица", "документ", "приложение"}
    }
    return sum(1 for token in tokens if token in text)
