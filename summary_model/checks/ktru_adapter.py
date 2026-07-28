from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from services.procurement_reference_registry import ProcurementReferenceRegistry
from summary_model.checks.additional_characteristics import (
    build_assessments,
    first_justification,
    justification_records,
    justification_state,
    result_status,
)
from summary_model.checks.models import CheckResult
from summary_model.checks.national_regime import resolve_plan_national_regime
from summary_model.checks.normalization import normalize_code, normalize_text
from summary_model.extraction_models import ProcurementPackageExtraction, PurchaseItem


LOOKALIKE_LATIN_TO_CYRILLIC = str.maketrans(
    {
        "A": "А",
        "a": "а",
        "B": "В",
        "C": "С",
        "c": "с",
        "E": "Е",
        "e": "е",
        "H": "Н",
        "K": "К",
        "k": "к",
        "M": "М",
        "O": "О",
        "o": "о",
        "P": "Р",
        "p": "р",
        "T": "Т",
        "X": "Х",
        "x": "х",
        "Y": "У",
        "y": "у",
    }
)


def run_ktru_characteristic_checks(
    package: ProcurementPackageExtraction,
    *,
    registry_dir: Path = Path("data/parsed_tables"),
    registry: Any | None = None,
    fetch_timeout_seconds: int | None = None,
) -> list[CheckResult]:
    registry = registry or ProcurementReferenceRegistry(registry_dir)
    if fetch_timeout_seconds is not None:
        _apply_fetch_timeout(registry, fetch_timeout_seconds)
    items = [
        item
        for item in (package.purchase_description.items if package.purchase_description else [])
        if item.ktru_code
    ]
    if not items:
        return [
            _result(
                "manual.ktru.characteristics",
                "КТРУ-характеристики",
                "manual_review",
                "В ООЗ не найдены позиции с КТРУ для проверки характеристик.",
            ),
            _result(
                "manual.ktru.additional",
                "Дополнительные характеристики КТРУ",
                "manual_review",
                "В ООЗ не найдены позиции с КТРУ для проверки дополнительных характеристик.",
            ),
        ]

    method_label = _procurement_method_label(package)
    method_kind = _resolve_procurement_method(method_label)
    ooz_justifications = justification_records(package.purchase_description)
    ooz_justification_state = justification_state(ooz_justifications)
    legal_cache: dict[str, dict[str, dict[str, Any]]] = {}
    common_cache: dict[str, dict[str, Any] | None] = {}
    item_names_by_ktru: dict[str, list[str]] = {}
    unavailable: list[str] = []
    invalid_values: list[str] = []
    missing_required: list[str] = []
    extra_characteristics: list[str] = []
    forbidden_extra: list[str] = []
    extra_reasons: list[str] = []
    characteristic_rows: list[dict[str, Any]] = []
    additional_rows: list[dict[str, Any]] = []
    checked_characteristics = 0

    for item in items:
        ktru_code = item.ktru_code or ""
        if item.name:
            item_names_by_ktru.setdefault(ktru_code, [])
            _append_unique(item_names_by_ktru[ktru_code], item.name)
        legal_characteristics = _get_legal_characteristics(registry, ktru_code, legal_cache, common_cache)
        if legal_characteristics is None:
            _append_unique(unavailable, ktru_code)
            continue

        legal_lookup, required_names = _build_legal_lookup(legal_characteristics)
        present_required_names: set[str] = set()
        common_info = common_cache.get(ktru_code)
        plan_okpd2_code, plan_okpd2_source = _plan_okpd2_for_item(package, item)
        rule_okpd2_code = item.okpd2_code or plan_okpd2_code
        rule_okpd2_source = (
            "позиция ООЗ"
            if item.okpd2_code
            else plan_okpd2_source
        )
        extra_allowed = _can_add_extra_characteristics(
            registry=registry,
            schedule=package.schedule_application,
            common_info=common_info,
            item_okpd2_code=rule_okpd2_code,
            item_okpd2_source=rule_okpd2_source,
            ktru_code=ktru_code,
            method_kind=method_kind,
            method_label=method_label,
            has_ktru_characteristics=bool(legal_lookup),
        )
        _append_unique(extra_reasons, extra_allowed["reason"])

        for characteristic in item.characteristics:
            if not characteristic.name:
                continue
            legal_item = _lookup_legal_characteristic(legal_lookup, characteristic.name)
            if legal_item is None:
                label = _char_label(item, characteristic.name, characteristic.value)
                _append_unique(extra_characteristics, label)
                additional_rows.append(
                    {
                        "ktru_code": ktru_code,
                        "okpd2_code": item.okpd2_code,
                        "plan_okpd2_code": plan_okpd2_code,
                        "rule_okpd2_code": extra_allowed.get("okpd2_code"),
                        "rule_okpd2_source": extra_allowed.get("okpd2_source"),
                        "item_name": item.name,
                        "item_row_number": item.row_number,
                        "characteristic_name": characteristic.name,
                        "value": characteristic.value,
                        "unit": characteristic.unit,
                        "status": (
                            "failed"
                            if extra_allowed["can_add_extra_characteristics"] is False
                            else "manual_review"
                            if extra_allowed["can_add_extra_characteristics"] is None
                            else "passed"
                        ),
                        "rule_reason": extra_allowed["reason"],
                        "plan_regime": extra_allowed.get("plan_regime"),
                    }
                )
                if extra_allowed["can_add_extra_characteristics"] is False:
                    _append_unique(forbidden_extra, label)
                continue

            checked_characteristics += 1
            legal_key, legal_name, legal_values, _, legal_unit = legal_item
            present_required_names.add(legal_key)
            values = _split_value(characteristic.value)
            bad_values = [value for value in values if not _is_value_allowed(value, legal_values)]
            unit_status = _unit_status(characteristic.unit, legal_unit)
            row_status = "passed"
            if bad_values or unit_status == "failed":
                row_status = "failed"
            characteristic_rows.append(
                {
                    "ktru_code": ktru_code,
                    "item_name": item.name,
                    "characteristic_name": legal_name,
                    "ooz_value": characteristic.value,
                    "ooz_unit": characteristic.unit,
                    "ktru_allowed_values": legal_values,
                    "ktru_unit": legal_unit,
                    "required": legal_key in required_names,
                    "status": row_status,
                    "message": _characteristic_row_message(bad_values, characteristic.unit, legal_unit),
                }
            )
            if bad_values:
                invalid_values.append(f"{ktru_code} / {legal_name}: {', '.join(bad_values)}")

        for legal_key, legal_name in required_names.items():
            if legal_key not in present_required_names:
                missing_required.append(f"{ktru_code} / {legal_name}")
                characteristic_rows.append(
                    {
                        "ktru_code": ktru_code,
                        "item_name": item.name,
                        "characteristic_name": legal_name,
                        "ooz_value": None,
                        "ooz_unit": None,
                        "ktru_allowed_values": [],
                        "ktru_unit": None,
                        "required": True,
                        "status": "failed",
                        "message": "обязательная характеристика не найдена в ООЗ",
                    }
                )

    characteristic_status = "passed"
    characteristic_message = "Характеристики ООЗ соответствуют значениям КТРУ."
    if unavailable:
        characteristic_status = "manual_review"
        characteristic_message = "Часть карточек КТРУ недоступна, проверка характеристик неполная."
    if invalid_values or missing_required:
        characteristic_status = "failed"
        characteristic_message = "Найдены ошибки в значениях или обязательных характеристиках КТРУ."

    assessments = build_assessments(
        additional_rows,
        ooz_state=ooz_justification_state,
        records=ooz_justifications,
    )
    additional_status, additional_message = result_status(assessments, unavailable)
    justification_text = first_justification(ooz_justifications)

    return [
        _result(
            "manual.ktru.characteristics",
            "КТРУ-характеристики",
            characteristic_status,
            characteristic_message,
            {
                "ktru_cards": _ktru_card_summaries(item_names_by_ktru, common_cache, unavailable),
                "checked_characteristics": checked_characteristics,
                "characteristic_rows": characteristic_rows,
                "invalid_values": invalid_values,
                "missing_required": missing_required,
                "unavailable_ktru": unavailable,
                "summary_lines": [
                    f"позиций с КТРУ: {len(items)}",
                    f"уникальных КТРУ: {len({item.ktru_code for item in items if item.ktru_code})}",
                    f"проверено характеристик: {checked_characteristics}",
                    f"ошибок значений: {len(invalid_values)}",
                    f"отсутствующих обязательных: {len(missing_required)}",
                    f"недоступных карточек: {len(unavailable)}",
                    *([f"недоступные КТРУ: {', '.join(unavailable[:5])}"] if unavailable else []),
                ],
            },
        ),
        _result(
            "manual.ktru.additional",
            "Дополнительные характеристики КТРУ",
            additional_status,
            additional_message,
            {
                "extra_characteristics": extra_characteristics,
                "additional_rows": additional_rows,
                "assessments": assessments,
                "forbidden_extra": forbidden_extra,
                "reasons": extra_reasons,
                "justification_text": justification_text,
                "ooz_justification_state": ooz_justification_state,
                "procurement_method": {
                    "kind": method_kind,
                    "evidence": method_label,
                },
                "summary_lines": [
                    f"дополнительных характеристик: {len(extra_characteristics)}",
                    *(
                        ["обоснование дополнительных характеристик: найдено"]
                        if justification_text
                        else ["обоснование дополнительных характеристик: не найдено"]
                        if extra_characteristics
                        else []
                    ),
                    *([f"недоступные КТРУ: {', '.join(unavailable[:5])}"] if unavailable else []),
                    *extra_reasons[:3],
                ],
            },
        ),
    ]


def run_pp1875_checks(
    package: ProcurementPackageExtraction,
    *,
    registry_dir: Path = Path("data/parsed_tables"),
    registry: Any | None = None,
) -> CheckResult:
    registry = registry or ProcurementReferenceRegistry(registry_dir)
    codes_by_document = _collect_okpd2_codes(package)
    names_by_code = _collect_okpd2_names(package)
    all_codes = sorted({code for codes in codes_by_document.values() for code in codes})
    if not all_codes:
        return _result(
            "manual.national_regime_1875",
            "Национальный режим / ПП №1875",
            "manual_review",
            "ОКПД2-коды не извлечены, локальную проверку ПП №1875 выполнить нельзя.",
            {
                "codes_by_document": codes_by_document,
                "summary_lines": ["ОКПД2-коды не найдены."],
            },
        )

    matched: list[str] = []
    matches: list[dict[str, Any]] = []
    not_matched: list[str] = []
    errors: list[str] = []
    for code in all_codes:
        try:
            query_name = names_by_code.get(code)
            try:
                result = registry.check_okpd2(code, query_name)
            except TypeError:
                result = registry.check_okpd2(code)
        except Exception as error:
            errors.append(f"{code}: {type(error).__name__}: {error}")
            continue
        if getattr(result, "found", False):
            matched.append(
                f"{code}: {getattr(result, 'table_id', None) or '?'}"
                f" позиция {getattr(result, 'position', None) or '?'}"
                f" ({getattr(result, 'matched_okpd2', None) or code})"
            )
            matches.append(
                {
                    "code": code,
                    "query_name": names_by_code.get(code) or getattr(result, "query_name", None),
                    "matched_okpd2": getattr(result, "matched_okpd2", None) or code,
                    "table_id": getattr(result, "table_id", None),
                    "table_title": getattr(result, "table_title", None),
                    "reference_name": getattr(result, "reference_name", None),
                    "position": getattr(result, "position", None),
                    "is_parent_match": bool(getattr(result, "is_parent_match", False)),
                    "message": getattr(result, "message", None),
                }
            )
        else:
            not_matched.append(code)

    if errors:
        status = "manual_review"
        message = "Локальная проверка ПП №1875 выполнена не полностью."
    elif matched:
        status = "warning"
        message = "Часть ОКПД2 найдена в локальных перечнях ПП №1875; нужно учесть национальный режим."
    else:
        status = "passed"
        message = "ОКПД2-коды не найдены в локальных перечнях ПП №1875."

    return _result(
        "manual.national_regime_1875",
        "Национальный режим / ПП №1875",
        status,
        message,
        {
            "codes_by_document": codes_by_document,
            "matched": matched,
            "matches": matches,
            "not_matched": not_matched,
            "errors": errors,
            "summary_lines": [
                f"проверено ОКПД2: {len(all_codes)}",
                f"найдено в ПП №1875: {len(matched)}",
                *matched[:10],
                *([f"ошибок локальной проверки: {len(errors)}"] if errors else []),
            ],
        },
    )


def _result(
    check_id: str,
    title: str,
    status: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> CheckResult:
    severity = {
        "passed": "info",
        "failed": "error",
        "warning": "warning",
        "manual_review": "manual_review",
        "not_applicable": "info",
        "skipped": "info",
    }[status]
    return CheckResult(
        check_id=check_id,
        title=title,
        severity=severity,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        mode="manual_review",
        message=message,
        report_text=message,
        fields_compared=["purchase_description.items[].characteristics"],
        details=details or {},
    )


def _collect_okpd2_codes(package: ProcurementPackageExtraction) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {
        "schedule_application": [],
        "purchase_description": [],
        "contract_draft": [],
        "nmck_justification": [],
    }
    if package.schedule_application:
        _extend_unique(result["schedule_application"], getattr(package.schedule_application, "okpd2_codes", []))
        _extend_unique(result["schedule_application"], _okpd2_from_ktru_codes(getattr(package.schedule_application, "ktru_codes", [])))
    if package.purchase_description:
        _extend_unique(result["purchase_description"], _codes_from_items(package.purchase_description.items))
    if package.contract_draft:
        _extend_unique(result["contract_draft"], _codes_from_items(package.contract_draft.items))
        _extend_unique(result["contract_draft"], _codes_from_items(package.contract_draft.specification_items))
    if package.nmck_justification:
        _extend_unique(result["nmck_justification"], _codes_from_items(package.nmck_justification.items))
    return {key: sorted(value) for key, value in result.items()}


def _collect_okpd2_names(package: ProcurementPackageExtraction) -> dict[str, str]:
    names: dict[str, str] = {}
    sources: list[list[Any]] = []
    if package.purchase_description:
        sources.append(list(package.purchase_description.items))
    if package.contract_draft:
        sources.append(list(package.contract_draft.items))
        sources.append(list(package.contract_draft.specification_items))
    if package.nmck_justification:
        sources.append(list(package.nmck_justification.items))

    for items in sources:
        for item in items:
            name = str(getattr(item, "name", "") or "").strip()
            if not name:
                continue
            for code in _codes_from_items([item]):
                names.setdefault(code, name)
    return names


def _ktru_card_summaries(
    item_names_by_ktru: dict[str, list[str]],
    common_cache: dict[str, dict[str, Any] | None],
    unavailable: list[str],
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    unavailable_set = set(unavailable)
    for code in sorted(item_names_by_ktru):
        common_info = common_cache.get(code) or {}
        reference_name = common_info.get("name")
        item_names = item_names_by_ktru.get(code, [])
        normalized_reference = normalize_text(reference_name)
        name_matches = bool(
            normalized_reference
            and any(normalize_text(name) == normalized_reference for name in item_names)
        )
        cards.append(
            {
                "code": code,
                "url": common_info.get("url")
                or f"https://zakupki.gov.ru/epz/ktru/ktruCard/ktru-description.html?itemId={code}",
                "reference_name": reference_name,
                "item_names": item_names,
                "name_matches": name_matches,
                "unavailable": code in unavailable_set or not common_info,
            }
        )
    return cards


def _codes_from_items(items: list[Any]) -> list[str]:
    codes: list[str] = []
    for item in items:
        code = normalize_code(getattr(item, "okpd2_code", None))
        if code:
            _append_unique(codes, code)
        _extend_unique(codes, _okpd2_from_ktru_codes([getattr(item, "ktru_code", None)]))
    return codes


def _okpd2_from_ktru_codes(codes: list[Any]) -> list[str]:
    result: list[str] = []
    for code in codes:
        normalized = normalize_code(code)
        if normalized and len(normalized) >= 21 and normalized[12] == "-":
            _append_unique(result, normalized[:12])
    return result


def _extend_unique(items: list[str], values: list[Any]) -> None:
    for value in values:
        normalized = normalize_code(value)
        if normalized:
            _append_unique(items, normalized)


def _get_legal_characteristics(
    registry: Any,
    ktru_code: str,
    legal_cache: dict[str, dict[str, dict[str, Any]]],
    common_cache: dict[str, dict[str, Any] | None],
) -> dict[str, dict[str, Any]] | None:
    if ktru_code in legal_cache:
        return legal_cache[ktru_code]
    common_cache[ktru_code] = _safe_common_info(registry, ktru_code)
    try:
        legal_cache[ktru_code] = registry.get_ktru_characteristics_detailed(ktru_code)
    except Exception:
        return None
    return legal_cache[ktru_code]


def _apply_fetch_timeout(registry: Any, timeout_seconds: int) -> None:
    fetch = getattr(registry, "_fetch_html", None)
    if fetch is None or getattr(registry, "_summary_model_timeout_wrapped", False):
        return

    def fetch_with_timeout(url: str, timeout: int = 60) -> str:
        return fetch(url, timeout=min(timeout, timeout_seconds))

    registry._fetch_html = fetch_with_timeout
    registry._summary_model_timeout_wrapped = True


def _build_legal_lookup(
    legal_characteristics: dict[str, dict[str, Any]],
) -> tuple[dict[str, tuple[str, str, list[str], bool, str | None]], dict[str, str]]:
    lookup: dict[str, tuple[str, str, list[str], bool, str | None]] = {}
    required_names: dict[str, str] = {}
    for name, payload in legal_characteristics.items():
        values = list(payload.get("values") or [])
        required = bool(payload.get("required"))
        unit = payload.get("unit") or payload.get("okei_unit") or payload.get("measure_unit")
        legal_key = _name_key(name)
        record = (legal_key, name, values, required, unit)
        lookup[legal_key] = record
        lookup[_visual_key(name)] = record
        if required:
            required_names[legal_key] = name
    return lookup, required_names


def _lookup_legal_characteristic(
    legal_lookup: dict[str, tuple[str, str, list[str], bool, str | None]],
    name: str,
) -> tuple[str, str, list[str], bool, str | None] | None:
    return legal_lookup.get(_name_key(name)) or legal_lookup.get(_visual_key(name))


def _procurement_method_label(package: ProcurementPackageExtraction) -> str:
    values = [
        getattr(package.schedule_application, "procurement_method_raw", None) if package.schedule_application else None,
        getattr(package.schedule_application, "single_supplier_basis_text", None) if package.schedule_application else None,
        getattr(package.purchase_request, "procurement_method_raw", None) if package.purchase_request else None,
        getattr(package.purchase_request, "single_supplier_basis_text", None) if package.purchase_request else None,
        getattr(package.explanatory_note, "procurement_method_raw", None) if package.explanatory_note else None,
        getattr(package.explanatory_note, "justification_text", None) if package.explanatory_note else None,
    ]
    return "\n".join(str(value) for value in values if value)


def _resolve_procurement_method(label: str) -> str:
    normalized = normalize_text(label)
    if "часть 12 статьи 93" in normalized or "ч. 12 ст. 93" in normalized:
        return "part_12_article_93"
    if "единственный поставщик" in normalized or "единственным поставщиком" in normalized:
        return "single_supplier"
    if any(marker in normalized for marker in ("электронный аукцион", "запрос котиров", "конкурс")):
        return "competitive"
    return "unknown"


def _safe_common_info(registry: Any, ktru_code: str | None) -> dict[str, Any] | None:
    if not ktru_code:
        return None
    try:
        return registry.get_ktru_common_info(ktru_code)
    except Exception:
        return None


def _plan_okpd2_for_item(
    package: ProcurementPackageExtraction,
    item: PurchaseItem,
) -> tuple[str | None, str | None]:
    """Find one unambiguous plan item that describes the OOZ position.

    The plan is the comparison baseline. Some OOZ tables only carry a KTRU
    code, while the corresponding supplied good in the plan carries an OKPD2
    code. This deliberately refuses to choose when several plan rows fit.
    """
    schedule = package.schedule_application
    if schedule is None:
        return None, None

    candidates = list(getattr(schedule, "included_goods", []) or [])
    exact_ktru = [
        candidate
        for candidate in candidates
        if item.ktru_code
        and normalize_code(getattr(candidate, "ktru_code", None)) == normalize_code(item.ktru_code)
        and normalize_code(getattr(candidate, "okpd2_code", None))
    ]
    if len(exact_ktru) == 1:
        return normalize_code(exact_ktru[0].okpd2_code), "позиция ПГ по КТРУ"

    name_matches = [
        candidate
        for candidate in candidates
        if normalize_code(getattr(candidate, "okpd2_code", None))
        and _purchase_names_match(item.name, getattr(candidate, "name", None))
    ]
    unique_codes = {normalize_code(candidate.okpd2_code) for candidate in name_matches}
    if len(unique_codes) == 1:
        return unique_codes.pop(), "позиция ПГ по наименованию"
    return None, None


def _purchase_names_match(left: str | None, right: str | None) -> bool:
    left_tokens = _purchase_name_tokens(left)
    right_tokens = _purchase_name_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    if left_tokens == right_tokens:
        return True
    for left_token in left_tokens:
        if any(
            left_token == right_token
            or (len(left_token) >= 5 and len(right_token) >= 5 and left_token[:5] == right_token[:5])
            for right_token in right_tokens
        ):
            return True
    return False


def _purchase_name_tokens(value: str | None) -> set[str]:
    ignored = {"товар", "услуга", "услуги", "система", "системы", "данных", "для", "и"}
    return {
        token
        for token in normalize_text(value).split()
        if len(token) >= 4 and token not in ignored
    }


def _can_add_extra_characteristics(
    *,
    registry: Any,
    schedule: Any | None,
    common_info: dict[str, Any] | None,
    item_okpd2_code: str | None,
    item_okpd2_source: str | None,
    ktru_code: str,
    method_kind: str,
    method_label: str,
    has_ktru_characteristics: bool,
) -> dict[str, Any]:
    if not has_ktru_characteristics:
        return {
            "can_add_extra_characteristics": True,
            "reason": "В карточке КТРУ отсутствуют характеристики; дополнительные характеристики допустимы.",
            "okpd2_code": item_okpd2_code or _okpd2_from_ktru(ktru_code),
            "okpd2_source": item_okpd2_source or "префикс КТРУ",
            "plan_regime": None,
        }

    official_candidates = _official_okpd_candidates(common_info)
    okpd_candidates = _okpd_candidates(common_info, item_okpd2_code, ktru_code)
    primary_okpd = official_candidates[0] if len(official_candidates) == 1 else (
        item_okpd2_code or (okpd_candidates[0] if okpd_candidates else None)
    )
    if not primary_okpd:
        return {
            "can_add_extra_characteristics": None,
            "reason": (
                f"Не удалось однозначно определить ОКПД2 для правила дополнительных характеристик "
                f"по КТРУ {ktru_code}."
            ),
            "okpd2_code": None,
            "okpd2_source": None,
            "plan_regime": None,
        }
    
    code_diff_note = ""
    ktru_okpd = _okpd2_from_ktru(ktru_code)
    okpd2_source = "карточка КТРУ" if primary_okpd in official_candidates else (item_okpd2_source or "префикс КТРУ")
    if item_okpd2_code and primary_okpd and item_okpd2_code != primary_okpd:
        code_diff_note = f" Для проверки ПП №1875 использован ОКПД2 {primary_okpd}."

    resolution = resolve_plan_national_regime(
        schedule,
        registry,
        codes=[primary_okpd],
        aliases_by_code={primary_okpd: [item_okpd2_code] if item_okpd2_code else []},
    )
    plan_regime = resolution["rows"][0] if resolution.get("rows") else None
    if resolution.get("errors") or not plan_regime:
        return {
            "can_add_extra_characteristics": None,
            "reason": (
                f"Не удалось проверить ОКПД2 {primary_okpd} по ПП №1875 "
                f"для правила дополнительных характеристик.{code_diff_note}"
            ),
            "okpd2_code": primary_okpd,
            "okpd2_source": okpd2_source,
            "plan_regime": plan_regime,
        }
    if plan_regime.get("status") == "registry_unavailable":
        return {
            "can_add_extra_characteristics": None,
            "reason": (
                f"Локальный реестр ПП №1875 недоступен для ОКПД2 {primary_okpd}; "
                "требуется ручная проверка."
            ),
            "okpd2_code": primary_okpd,
            "okpd2_source": okpd2_source,
            "plan_regime": plan_regime,
        }
    if plan_regime.get("status") == "not_listed":
        return {
            "can_add_extra_characteristics": True,
            "reason": (
                f"ОКПД2 {primary_okpd} не найден в приложениях 1 и 2 ПП №1875; "
                f"дополнительные характеристики допустимы.{code_diff_note}"
            ),
            "okpd2_code": primary_okpd,
            "okpd2_source": okpd2_source,
            "plan_regime": plan_regime,
        }
    if _is_special_pp1875_position(plan_regime):
        registry_note = _pp1875_match_note(plan_regime)
        if plan_regime.get("status") != "confirmed":
            return {
                "can_add_extra_characteristics": None,
                "reason": (
                    f"ОКПД2 {primary_okpd} относится к специальной позиции ПП №1875, "
                    f"но режим не подтверждён в поле ПГ {plan_regime.get('field_code') or '17.1/17.2'}. "
                    f"{registry_note}{code_diff_note}"
                ),
                "okpd2_code": primary_okpd,
                "okpd2_source": okpd2_source,
                "plan_regime": plan_regime,
            }
        return {
            "can_add_extra_characteristics": False,
            "reason": (
                f"По коду ОКПД2 {primary_okpd} специальный режим подтверждён в ПГ; "
                "дополнительные характеристики запрещены. "
                f"{registry_note}{code_diff_note}"
            ),
            "okpd2_code": primary_okpd,
            "okpd2_source": okpd2_source,
            "plan_regime": plan_regime,
        }
    registry_note = _pp1875_match_note(plan_regime)
    return {
        "can_add_extra_characteristics": True,
        "reason": (
            f"ОКПД2 {primary_okpd} не попадает в специальные позиции запрета; "
            f"дополнительные характеристики допустимы. {registry_note}{code_diff_note}"
        ),
        "okpd2_code": primary_okpd,
        "okpd2_source": okpd2_source,
        "plan_regime": plan_regime,
    }


def _okpd_candidates(
    common_info: dict[str, Any] | None,
    item_okpd2_code: str | None,
    ktru_code: str | None,
) -> list[str]:
    candidates = []
    for raw_value in (*_official_okpd_candidates(common_info), item_okpd2_code, _okpd2_from_ktru(ktru_code)):
        if raw_value:
            _append_unique(candidates, raw_value)
    return candidates


def _official_okpd_candidates(common_info: dict[str, Any] | None) -> list[str]:
    candidates: list[str] = []
    if not common_info:
        return candidates
    section_pairs = common_info.get("section_pairs") or {}
    for raw_value in (
        common_info.get("okpd2_code"),
        section_pairs.get("Код по ОКПД2"),
    ):
        if not raw_value:
            continue
        for match in re.findall(r"\d{2}(?:\.\d{1,3}){1,4}", str(raw_value)):
            _append_unique(candidates, match)
    return candidates


def _pp1875_match_note(okpd_result: Any) -> str:
    table_id = _result_value(okpd_result, "table_id")
    appendix = "Приложение №1" if table_id == "table_01" else "Приложение №2" if table_id == "table_02" else "приложение не определено"
    position = _result_value(okpd_result, "position") or "не определена"
    matched_code = _result_value(okpd_result, "matched_okpd2") or "не определён"
    reference_name = _result_value(okpd_result, "reference_name") or "наименование не найдено"
    return (
        f"Основание: {appendix} к ПП №1875, позиция {position}, код перечня {matched_code}, "
        f"«{reference_name}»."
    )


def _clean_char_value(val: str) -> str:
    s = normalize_text(str(val or ""))
    s = re.sub(r"^[\(\[\{\d\.\:\s]+", "", s)
    s = re.sub(r"[\)\]\}\s]+$", "", s)
    return s.strip().casefold()


def _is_value_allowed(value: str, allowed_values: list[str]) -> bool:
    normalized_value = _name_key(value)
    visual_value = _visual_key(value)
    clean_val = _clean_char_value(value)
    value_number = _number(value)
    doc_codes = set(re.findall(r"\b\d{2}\.\d{2}\b", str(value)))

    for allowed in allowed_values:
        normalized_allowed = _name_key(allowed)
        if not normalized_allowed:
            continue
        if normalized_value == normalized_allowed:
            return True
        if visual_value == _visual_key(allowed):
            return True
        if clean_val and clean_val in _clean_char_value(allowed):
            return True
        if doc_codes:
            allowed_codes = set(re.findall(r"\b\d{2}\.\d{2}\b", str(allowed)))
            if doc_codes.intersection(allowed_codes):
                return True
        if _range_match(value, allowed):
            return True
        allowed_number = _number(allowed)
        if value_number is not None and allowed_number is not None and value_number == allowed_number:
            return True
    return False


def _okpd2_from_ktru(ktru_code: str | None) -> str | None:
    match = re.match(r"(\d{2}\.\d{2}\.\d{2}\.\d{3})-", str(ktru_code or ""))
    return match.group(1) if match else None


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split()).strip()


def _name_key(value: Any) -> str:
    return normalize_text(_clean_text(value))


def _visual_key(value: Any) -> str:
    return _clean_text(value).translate(LOOKALIKE_LATIN_TO_CYRILLIC).casefold()


def _split_value(value: Any) -> list[str]:
    text = _clean_text(value)
    return [part.strip() for part in re.split(r"\s*[;\n\r]+\s*", text) if part.strip()]


def _is_special_pp1875_position(okpd_result: Any) -> bool:
    table_id = _result_value(okpd_result, "table_id")
    position = _result_value(okpd_result, "position")
    row = _result_value(okpd_result, "row")
    if position is None and isinstance(row, dict):
        position = row.get("position")
    match = re.search(r"\d+", str(position or ""))
    if not match:
        return False
    position_number = int(match.group(0))
    if table_id == "table_01":
        return position_number in {25, 26, 32}
    if table_id == "table_02":
        return 191 <= position_number <= 361
    return False


def _result_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _number(value: Any) -> float | None:
    text = normalize_text(str(value)).replace(",", ".")
    text = re.sub(r"[^\d.\-]+", "", text)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _clean_char_value(val: str) -> str:
    s = normalize_text(str(val or ""))
    s = re.sub(r"^[\(\[\{\d\.\:\s]+", "", s)
    s = re.sub(r"[\)\]\}\s]+$", "", s)
    return s.strip().casefold()


def _is_value_allowed(value: str, allowed_values: list[str]) -> bool:
    normalized_value = _name_key(value)
    visual_value = _visual_key(value)
    clean_val = _clean_char_value(value)
    value_number = _number(value)
    doc_codes = set(re.findall(r"\b\d{2}\.\d{2}\b", str(value)))

    for allowed in allowed_values:
        normalized_allowed = _name_key(allowed)
        if not normalized_allowed:
            continue
        if normalized_value == normalized_allowed:
            return True
        if visual_value == _visual_key(allowed):
            return True
        if clean_val and clean_val in _clean_char_value(allowed):
            return True
        if doc_codes:
            allowed_codes = set(re.findall(r"\b\d{2}\.\d{2}\b", str(allowed)))
            if doc_codes.intersection(allowed_codes):
                return True
        if _range_match(value, allowed):
            return True
        allowed_number = _number(allowed)
        if value_number is not None and allowed_number is not None and value_number == allowed_number:
            return True
    return False


def _unit_status(ooz_unit: str | None, legal_unit: str | None) -> str:
    if not legal_unit or not ooz_unit:
        return "not_checked"
    return "passed" if _name_key(ooz_unit) == _name_key(legal_unit) else "failed"


def _characteristic_row_message(
    bad_values: list[str],
    ooz_unit: str | None,
    legal_unit: str | None,
) -> str:
    messages = []
    if bad_values:
        messages.append(f"недопустимые значения: {', '.join(bad_values)}")
    if _unit_status(ooz_unit, legal_unit) == "failed":
        messages.append(f"единица измерения отличается: ООЗ '{ooz_unit}', КТРУ '{legal_unit}'")
    return "; ".join(messages) if messages else "ОК"


def _range_match(value: str, allowed: str) -> bool:
    value_number = _number(value)
    if value_number is None:
        return False
    normalized = str(allowed or "").casefold().replace(",", ".").replace("≤", "<=").replace("≥", ">=")
    matches = re.findall(r"(<=|>=|<|>)\s*(-?\d+(?:\.\d+)?)", normalized)
    if not matches:
        return False
    for operator, raw_number in matches:
        border = float(raw_number)
        if operator == "<" and not value_number < border:
            return False
        if operator == "<=" and not value_number <= border:
            return False
        if operator == ">" and not value_number > border:
            return False
        if operator == ">=" and not value_number >= border:
            return False
    return True


def _char_label(item: PurchaseItem, name: str | None, value: str | None) -> str:
    return f"{item.ktru_code} / {item.name or 'позиция'} / {name or 'характеристика'}: {value or ''}"


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)
