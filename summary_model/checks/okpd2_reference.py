"""Local official OKPD2 names used by plan-name validation."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from summary_model.checks.normalization import normalize_code, normalize_text


_REFERENCE_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "parsed_tables" / "okpd2_official.json"
)


# The official title is more reliable than a document's own subject wording:
# "поставка" is often used in a plan even when the code describes a service.
_SERVICE_NAME_MARKERS = (
    "услуг",
    "работ",
    "деятельност",
)
_SERVICE_NAME_MARKERS_IN_SERVICE_SECTIONS = (
    "аренд",
    "прокат",
    "техническая помощь",
    "транспортирован",
    "перевоз",
    "обслуживан",
    "ремонт",
    "консульт",
    "обучен",
    "сопровожден",
    "предоставлен",
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


def official_okpd2_is_service(code: str | None) -> bool | None:
    """Return whether an official OKPD2 title explicitly describes a service.

    ``None`` means the bundled reference has no title for the code. The second
    marker group is restricted to service-oriented divisions to avoid treating
    goods such as repair materials as services solely because of one word.
    """
    resolved_code, official_name = official_okpd2_name(code)
    if not resolved_code or not official_name:
        return None
    name = normalize_text(official_name)
    if any(marker in name for marker in _SERVICE_NAME_MARKERS):
        return True
    try:
        division = int(resolved_code[:2])
    except ValueError:
        return False
    return division >= 45 and any(
        marker in name for marker in _SERVICE_NAME_MARKERS_IN_SERVICE_SECTIONS
    )
