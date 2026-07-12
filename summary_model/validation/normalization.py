from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from summary_model.domain.models import ExtractedValue


def normalize_text(value: Any) -> str:
    if isinstance(value, ExtractedValue):
        value = value.normalized_value
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ").lower()
    text = re.sub(r"[«»\"'`]", "", text)
    text = re.sub(r"[^\w\d]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def normalize_code(value: Any) -> str:
    if isinstance(value, ExtractedValue):
        value = value.normalized_value
    return re.sub(r"\s+", "", str(value or "")).strip()


def normalize_decimal(value: Any) -> Decimal | None:
    if isinstance(value, ExtractedValue):
        value = value.normalized_value
    if isinstance(value, Decimal):
        return value
    if value is None:
        return None
    cleaned = str(value).replace("\xa0", "").replace(" ", "").replace(",", ".")
    cleaned = re.sub(r"[^\d.\-]", "", cleaned)
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def normalized_values(values: list[ExtractedValue]) -> set[str]:
    return {normalize_code(value) for value in values if normalize_code(value)}


def normalize_unit(value: Any) -> str:
    normalized = normalize_text(value)
    aliases = {
        "штука": "шт",
        "штук": "шт",
        "шт": "шт",
        "комплект": "компл",
        "компл": "компл",
        "усл ед": "усл_ед",
    }
    return aliases.get(normalized, normalized)
