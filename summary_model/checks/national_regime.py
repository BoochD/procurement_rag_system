from __future__ import annotations

import re
from typing import Any

from summary_model.checks.normalization import normalize_code


FIELD_LABELS = {
    "17.1": "Запреты",
    "17.2": "Ограничения",
    "17.3": "Преимущества",
}


def plan_national_regime_fields(schedule: Any | None) -> dict[str, str]:
    if schedule is None:
        return {}
    fields = list(getattr(schedule, "national_regime_fields", []) or [])
    if not fields:
        fields = [
            field
            for field in (getattr(schedule, "raw_fields", []) or [])
            if re.match(r"\s*17[._]?\d", str(getattr(field, "key", "") or ""))
        ]
    result: dict[str, str] = {}
    for field in fields:
        key = str(getattr(field, "key", "") or "")
        match = re.search(r"17[._]?(\d)", key)
        if match:
            result[f"17.{match.group(1)}"] = str(getattr(field, "value", "") or "").strip()
    return result


def plan_okpd2_codes(schedule: Any | None) -> list[str]:
    if schedule is None:
        return []
    result: list[str] = []
    raw_codes = [
        *(getattr(schedule, "okpd2_codes", []) or []),
        *[
            getattr(reference, "code", None)
            for reference in (getattr(schedule, "subject_codes", []) or [])
        ],
        *[
            getattr(item, "okpd2_code", None)
            for item in (getattr(schedule, "included_goods", []) or [])
        ],
    ]
    for raw_code in raw_codes:
        code = normalize_code(raw_code)
        if code and code not in result:
            result.append(code)
    return result


def resolve_plan_national_regime(
    schedule: Any | None,
    registry: Any,
    *,
    codes: list[str] | None = None,
    aliases_by_code: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    fields = plan_national_regime_fields(schedule)
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for raw_code in codes if codes is not None else plan_okpd2_codes(schedule):
        code = normalize_code(raw_code)
        if not code:
            continue
        try:
            result = registry.check_okpd2(code)
        except Exception as error:
            errors.append(f"{code}: {type(error).__name__}: {error}")
            rows.append({
                "code": code,
                "status": "registry_unavailable",
                "field_code": None,
                "field_value": None,
            })
            continue
        if not getattr(result, "found", False):
            rows.append({
                "code": code,
                "status": "not_listed",
                "field_code": None,
                "field_value": None,
                "table_id": None,
                "position": None,
            })
            continue
        table_id = str(getattr(result, "table_id", "") or "")
        field_code = {"table_01": "17.1", "table_02": "17.2"}.get(table_id)
        matched_code = normalize_code(getattr(result, "matched_okpd2", None)) or code
        aliases = [
            normalized
            for value in (aliases_by_code or {}).get(code, [])
            if (normalized := normalize_code(value))
        ]
        field_value = fields.get(field_code or "", "")
        if not field_code:
            status = "ambiguous"
        elif not field_value:
            status = "missing"
        elif national_regime_code_listed(field_value, code, matched_code, *aliases):
            status = "confirmed"
        else:
            status = "missing"
        rows.append({
            "code": code,
            "matched_code": matched_code,
            "field_match_aliases": aliases,
            "table_id": table_id or None,
            "position": getattr(result, "position", None),
            "reference_name": getattr(result, "reference_name", None),
            "field_code": field_code,
            "field_value": field_value or None,
            "regime": "запрет" if field_code == "17.1" else "ограничение" if field_code == "17.2" else None,
            "status": status,
        })
    return {"fields": fields, "rows": rows, "errors": errors}


def national_regime_code_listed(value: str, *expected_codes: str) -> bool:
    normalized_value = normalize_code(value)
    if not normalized_value:
        return False
    value_codes = [normalize_code(code) for code in re.findall(r"\d{2}(?:\.\d{2}){1,3}", value)]
    for expected in expected_codes:
        normalized_expected = normalize_code(expected)
        if not normalized_expected:
            continue
        if normalized_expected in value_codes:
            return True
        compact_expected = normalized_expected.replace(".", "")
        if compact_expected and compact_expected in normalized_value.replace(".", ""):
            return True
    return False
