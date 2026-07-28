from __future__ import annotations

import re
from typing import Any

from summary_model.extraction_models import AdditionalCharacteristicsJustification


def justification_records(description: Any | None) -> list[AdditionalCharacteristicsJustification]:
    if description is None:
        return []
    records = list(getattr(description, "additional_characteristics_justifications", []) or [])
    legacy_text = _clean(getattr(description, "additional_characteristics_justification_text", None))
    if records or not legacy_text:
        return records
    return [
        AdditionalCharacteristicsJustification(
            justification_text=legacy_text,
            evidence_text=legacy_text,
            extraction_method="legacy_text",
        )
    ]


def justification_state(
    records: list[AdditionalCharacteristicsJustification],
) -> dict[str, Any]:
    explicit = [record for record in records if _record_text(record)]
    preferred = _preferred_justification(explicit)
    warnings = list(dict.fromkeys(
        warning
        for record in records
        for warning in record.parser_warnings
        if warning
    ))
    return {
        "found": bool(explicit),
        "partial": bool(warnings) or any(not _record_text(record) for record in records),
        "candidate_count": len(records),
        "explicit_count": len(explicit),
        "sources": sorted({
            _clean(record.source_document_title) or _clean(record.source_document_type)
            for record in explicit
            if record.source_document_title or record.source_document_type
        }),
        "quote": _record_text(preferred)[:240] if preferred else None,
        "warnings": warnings,
    }


def first_justification(records: list[AdditionalCharacteristicsJustification]) -> str | None:
    preferred = _preferred_justification([
        record for record in records if _record_text(record)
    ])
    return _record_text(preferred) if preferred else None


def build_assessments(
    rows: list[dict[str, Any]],
    *,
    ooz_state: dict[str, Any],
) -> list[dict[str, Any]]:
    assessments = []
    for row in rows:
        decision, reason = _decision(row.get("status"), ooz_state)
        regime = row.get("plan_regime") or {}
        assessments.append({
            "item": row.get("item_name"),
            "ktru_code": row.get("ktru_code"),
            "characteristic": row.get("characteristic_name"),
            "catalog_status": row.get("status"),
            "okpd_rule": {
                "code": row.get("rule_okpd2_code"),
                "source": row.get("rule_okpd2_source"),
                "reason": row.get("rule_reason"),
            },
            "plan_regime": {
                key: regime.get(key)
                for key in ("field_code", "field_value", "regime", "status")
                if regime.get(key) is not None
            },
            "justification": _justification_reference(ooz_state),
            "decision": decision,
            "reason": reason,
        })
    return assessments


def result_status(
    assessments: list[dict[str, Any]],
    unavailable_ktru: list[str],
) -> tuple[str, str]:
    if not assessments:
        if unavailable_ktru:
            return "manual_review", "Карточки КТРУ недоступны, проверка дополнительных характеристик неполная."
        return "passed", "Дополнительные характеристики КТРУ не обнаружены."
    decisions = {assessment["decision"] for assessment in assessments}
    if "missing_justification" in decisions:
        return "failed", "Для допустимых дополнительных характеристик не найдено явное обоснование в ООЗ."
    if "restricted" in decisions:
        return "warning", "В ПГ подтверждён специальный режим ПП №1875, запрещающий дополнительные характеристики."
    if "manual_review" in decisions or unavailable_ktru:
        return "manual_review", "Часть дополнительных характеристик требует ручной проверки."
    return "passed", "Дополнительные характеристики допустимы и имеют явное обоснование в ООЗ."


def _decision(
    rule_status: str | None,
    ooz_state: dict[str, Any],
) -> tuple[str, str]:
    if rule_status == "failed":
        return "restricted", "Специальный режим ПП №1875 подтверждён в ПГ; обоснование запрет не отменяет."
    if rule_status == "manual_review":
        return "manual_review", "Режим ПП №1875 или исходные сведения определены не полностью."
    if ooz_state["found"]:
        return "allowed", "Дополнительная характеристика допустима; явное обоснование найдено в ООЗ."
    if ooz_state["partial"]:
        return "manual_review", "Таблица-кандидат ООЗ найдена, но её содержимое извлечено не полностью."
    return "missing_justification", "Дополнительная характеристика допустима по коду, но явное обоснование в ООЗ не найдено."


def _justification_reference(
    ooz_state: dict[str, Any],
) -> dict[str, str]:
    if ooz_state["found"]:
        return {"status": "found", "source": "ooz"}
    if ooz_state["partial"]:
        return {"status": "partial", "source": "ooz"}
    return {"status": "missing", "source": "none"}


def _record_text(record: AdditionalCharacteristicsJustification) -> str:
    return _clean(record.justification_text or record.evidence_text)


def _preferred_justification(
    records: list[AdditionalCharacteristicsJustification],
) -> AdditionalCharacteristicsJustification | None:
    return (
        next((record for record in records if record.characteristic_names), None)
        or next((record for record in records if _clean(record.scope_text)), None)
        or next(
            (
                record
                for record in records
                if re.search(
                    r"обоснован\w*\s+(?:применения|включения|необходимости)\s+дополнительн\w*\s+характеристик",
                    _record_text(record),
                    flags=re.IGNORECASE,
                )
            ),
            None,
        )
        or (records[0] if records else None)
    )


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split()).strip()
