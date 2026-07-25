from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


OKPD2_RE = re.compile(r"(?<![\d.])\d{2}\.\d{2}\.\d{2}\.\d{3}(?!-\d{8})(?![\d.])")
KTRU_RE = re.compile(r"(?<![\d.])\d{2}\.\d{2}\.\d{2}\.\d{3}-\d{8}(?!\d)")

NEGATIVE_VALUES = {
    "-",
    "нет",
    "отсутствует",
    "не предусмотрено",
    "не установлено",
    "не требуется",
}


def clean_text(value: str | None) -> str:
    value = (value or "").replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def normalize_key(value: str | None) -> str:
    text = clean_text(value).casefold()
    text = text.replace("ё", "е")
    text = re.sub(r"[^\w\d]+", "_", text, flags=re.UNICODE)
    return text.strip("_")


def is_negative_value(value: str | None) -> bool:
    return clean_text(value).casefold() in NEGATIVE_VALUES


def is_empty_value(value: str | None) -> bool:
    text = clean_text(value)
    return not text


def parse_decimal(value: str | None) -> Decimal | None:
    text = clean_text(value)
    if not text:
        return None
    match = re.search(r"-?\d[\d\s\xa0]*(?:[,.]\d+)?", text)
    if not match:
        return None
    raw = match.group(0).replace(" ", "").replace("\xa0", "").replace(",", ".")
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None


def extract_money(value: str | None) -> tuple[str | None, Decimal | None]:
    text = clean_text(value)
    if not text:
        return None, None

    ruble_matches = list(
        re.finditer(
            r"(?P<rub>\d[\d\s\xa0]{0,20})(?:[,.](?P<dec>\d{1,2}))?\s*"
            r"(?:\([^)]{0,300}\)\s*)?"
            r"(?:руб(?:\.|лей|ля|ль|лями)?)(?:\s*(?P<kop>\d{1,2})\s*коп(?:\.|еек|ейки)?)?",
            text,
            flags=re.IGNORECASE,
        )
    )
    if ruble_matches:
        match = max(ruble_matches, key=lambda item: len(item.group(0)))
        rubles = match.group("rub").replace(" ", "").replace("\xa0", "")
        kopeks = match.group("dec") or match.group("kop")
        raw = clean_text(match.group(0))
        amount_text = f"{rubles}.{(kopeks or '00')[:2].ljust(2, '0')}"
        try:
            return raw, Decimal(amount_text)
        except (InvalidOperation, ValueError):
            return raw, parse_decimal(rubles)

    matches = [
        match.group(0)
        for match in re.finditer(r"(?<!\d)\d[\d\s\xa0]{0,15}(?:[,.]\d{1,2})?(?!\d)", text)
        if len(match.group(0).replace(" ", "").replace("\xa0", "")) <= 16
    ]
    if not matches:
        return None, None
    raw = max(matches, key=len)
    return raw, parse_decimal(raw)


def unique_codes(pattern: re.Pattern[str], text: str | None) -> list[str]:
    return list(dict.fromkeys(pattern.findall(text or "")))


def normalize_document_title(text: str | None) -> str | None:
    text = clean_text(text)
    return text or None
