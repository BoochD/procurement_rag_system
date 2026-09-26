"""Numeric KTRU constraints, with open/closed bounds preserved."""

from __future__ import annotations

import re
from decimal import Decimal


_NUMBER = r"-?\d+(?:[.,]\d+)?"
_SCALAR = re.compile(rf"{_NUMBER}\Z")


def _constraint_text(value: str) -> str:
    text = str(value).casefold().strip().replace("\xa0", " ")
    text = text.replace("≤", "<=").replace("≥", ">=").replace("−", "-")
    for words, operator in (
        ("не менее", ">="), ("не больше", "<="), ("не более", "<="),
        ("не меньше", ">="), ("более", ">"), ("менее", "<"),
    ):
        text = text.replace(words, operator)
    # Only unambiguous thousands grouping; never join arbitrary numbers.
    text = re.sub(r"(?<=\d) (?=\d{3}(?:\D|$))", "", text)
    return text


def looks_numeric(value: str) -> bool:
    text = _constraint_text(value)
    return bool(
        re.match(r"(?:[<>]=?|от\s+)", text)
        or re.fullmatch(r"[-\d\s.,]+", text)
    )


def numeric_interval(value: str) -> tuple[Decimal, bool, Decimal, bool] | None:
    text = _constraint_text(value)
    if _SCALAR.fullmatch(text):
        number = Decimal(text.replace(",", "."))
        return number, True, number, True
    between = re.fullmatch(rf"от\s+({_NUMBER})\s+до\s+({_NUMBER})(?:\s+включительно)?", text)
    if between:
        text = f">= {between[1]}; <= {between[2]}"
    clauses = list(re.finditer(rf"(<=|>=|<|>)\s*({_NUMBER})", text))
    remainder = re.sub(rf"(<=|>=|<|>)\s*{_NUMBER}", "", text)
    if not clauses or re.sub(r"[\s;]+|\bи\b", "", remainder):
        return None
    low, high = Decimal("-Infinity"), Decimal("Infinity")
    low_closed = high_closed = False
    for clause in clauses:
        operator, raw = clause.groups()
        number = Decimal(raw.replace(",", "."))
        closed = "=" in operator
        if operator.startswith(">"):
            if number > low:
                low, low_closed = number, closed
            elif number == low:
                low_closed = low_closed and closed
        else:
            if number < high:
                high, high_closed = number, closed
            elif number == high:
                high_closed = high_closed and closed
    if low > high or (low == high and not (low_closed and high_closed)):
        return None
    return low, low_closed, high, high_closed


def numeric_value_allowed(value: str, allowed_values: list[str]) -> bool | None:
    """OOZ must not admit values outside a recognized catalogue interval."""
    actual = numeric_interval(value)
    if actual is None:
        return None
    unknown = False
    for allowed in allowed_values:
        reference = numeric_interval(allowed)
        if reference is None:
            unknown = True
            continue
        low, low_closed, high, high_closed = actual
        ref_low, ref_low_closed, ref_high, ref_high_closed = reference
        lower_ok = low > ref_low or (low == ref_low and (not low_closed or ref_low_closed))
        upper_ok = high < ref_high or (high == ref_high and (not high_closed or ref_high_closed))
        if lower_ok and upper_ok:
            return True
    return None if unknown or not allowed_values else False
