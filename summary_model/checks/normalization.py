from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ").casefold()
    text = text.replace("ё", "е")
    text = re.sub(r"[«»\"'`]", "", text)
    text = re.sub(r"[^\w\d]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def normalize_code(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def normalize_unit(value: Any) -> str:
    text = normalize_text(value)
    aliases = {
        "штука": "шт",
        "штук": "шт",
        "шт": "шт",
        "шт.": "шт",
        "комплект": "компл",
        "компл": "компл",
    }
    return aliases.get(text, text)


def normalize_decimal(value: Any) -> Decimal | None:
    if isinstance(value, Decimal):
        return value
    if value is None:
        return None
    cleaned = str(value).replace("\xa0", "").replace(" ", "").replace(",", ".")
    cleaned = re.sub(r"[^\d.\-]", "", cleaned)
    if cleaned in {"", "-", ".", "-."}:
        return None
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def normalize_money(value: Any) -> Decimal | None:
    amount = normalize_decimal(_amount(value))
    if amount is None:
        return None
    return amount.quantize(Decimal("0.01"))


def decimal_equal(left: Any, right: Any) -> bool:
    left_decimal = normalize_decimal(left)
    right_decimal = normalize_decimal(right)
    return left_decimal is not None and right_decimal is not None and left_decimal == right_decimal


def money_equal(left: Any, right: Any) -> bool:
    left_decimal = normalize_money(left)
    right_decimal = normalize_money(right)
    return left_decimal is not None and right_decimal is not None and left_decimal == right_decimal


def normalize_attachment_title(value: Any) -> str:
    text = normalize_text(value)
    text = re.sub(r"\bформа\b", "", text)
    return " ".join(text.split())


def _amount(value: Any) -> Any:
    if hasattr(value, "amount"):
        return value.amount
    if isinstance(value, dict):
        return value.get("amount")
    return value
