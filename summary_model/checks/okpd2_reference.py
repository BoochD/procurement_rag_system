"""Local official OKPD2 names used by plan-name validation."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from summary_model.checks.normalization import normalize_code


_REFERENCE_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "parsed_tables" / "okpd2_official.json"
)


@lru_cache(maxsize=1)
def official_okpd2_names() -> dict[str, str]:
    """Return official code-to-name pairs bundled with the application."""
    try:
        payload = json.loads(_REFERENCE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    codes = payload.get("codes") if isinstance(payload, dict) else None
    if not isinstance(codes, dict):
        return {}
    return {
        normalize_code(code): str(name).strip()
        for code, name in codes.items()
        if normalize_code(code) and str(name).strip()
    }


def official_okpd2_name(code: str | None) -> tuple[str | None, str | None]:
    """Find an exact official name, allowing only an explicit trailing .000 alias."""
    normalized = normalize_code(code)
    names = official_okpd2_names()
    if normalized in names:
        return normalized, names[normalized]
    while normalized.endswith(".000"):
        normalized = normalized[:-4]
        if normalized in names:
            return normalized, names[normalized]
    return None, None
