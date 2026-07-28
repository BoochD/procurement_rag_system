from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from types import UnionType
from typing import Any, Literal, TypeVar, Union, get_args, get_origin

from pydantic import BaseModel, ValidationError

from summary_model.checks.normalization import normalize_decimal


T = TypeVar("T", bound=BaseModel)
RecoveryStatus = Literal["validated", "recovered", "partial", "failed"]


_FIELD_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "CommercialOfferSchema": {
        "supplier_name": ("supplier", "vendor", "supplier_title"),
        "inn": ("supplier_inn", "vendor_inn", "tax_id"),
        "outgoing_number": ("offer_number", "letter_number", "outgoing_no"),
        "outgoing_date": ("letter_date", "outgoing_dt"),
        "offer_date": ("date", "proposal_date"),
        "purchase_subject": ("subject", "procurement_subject"),
        "total_amount": ("total", "grand_total", "offer_total"),
        "items": ("positions", "products", "rows"),
        "source_pages": ("pages",),
        "parser_warnings": ("warnings",),
    },
    "CommercialOfferItem": {
        "row_number": ("number", "row", "position_number"),
        "name": ("product_name", "item_name", "title"),
        "unit": ("uom", "measurement_unit"),
        "quantity": ("count", "qty"),
        "unit_price": ("price", "unit_cost"),
        "total_price": ("sum", "amount", "line_total"),
        "trademark": ("brand",),
        "notes": ("note", "comments"),
        "evidence_text": ("evidence", "source_text"),
    },
    "MoneyValue": {
        "amount": ("value", "sum", "price"),
        "raw": ("text", "raw_value"),
    },
    "VlmPurchaseItem": {
        "row_number": ("number", "position_number"),
        "name": ("product_name", "item_name", "title"),
        "unit": ("uom", "measurement_unit"),
        "quantity_raw": ("quantity", "count", "qty"),
        "notes": ("note", "comments"),
    },
    "VlmStage": {
        "stage_number": ("number", "stage_no"),
        "stage_name": ("name", "title"),
        "service_term_text": ("term", "period", "deadline"),
        "price_raw": ("price", "cost"),
        "quantity_text": ("quantity", "volume"),
        "notes": ("note", "comments"),
    },
    "VlmNmckItem": {
        "row_number": ("number", "position_number"),
        "name": ("product_name", "item_name", "title"),
        "unit": ("uom", "measurement_unit"),
        "quantity_raw": ("quantity", "count", "qty"),
        "selected_min_unit_price_raw": ("minimum_price", "min_price"),
        "row_total_declared_raw": ("total", "line_total"),
        "notes": ("note", "comments"),
    },
}


_RAW_FIELD_BY_FIELD = {
    "quantity": "quantity_raw",
    "unit_price": "unit_price_raw",
    "total_price": "total_price_raw",
    "selected_min_unit_price": "selected_min_unit_price_raw",
    "row_total_declared": "row_total_declared_raw",
    "variation_coefficient": "variation_coefficient_raw",
    "subcontract_smp_sonko_percent": "subcontract_smp_sonko_percent_raw",
    "amount": "raw",
    "value_percent": "raw",
    "value_amount": "raw",
}


@dataclass(frozen=True)
class RecoveryIssue:
    path: str
    message: str
    lossy: bool = False

    def as_text(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message


@dataclass(frozen=True)
class StructuredRecovery:
    value: BaseModel | None
    status: RecoveryStatus
    issues: tuple[RecoveryIssue, ...] = ()
    error: str | None = None

    @property
    def lossy_warnings(self) -> list[str]:
        return [issue.as_text() for issue in self.issues if issue.lossy]

    @property
    def all_warnings(self) -> list[str]:
        return [issue.as_text() for issue in self.issues]


def recover_model(schema: type[T], payload: Any) -> StructuredRecovery:
    if isinstance(payload, schema):
        return StructuredRecovery(value=payload, status="validated")
    if not isinstance(payload, dict):
        return StructuredRecovery(
            value=None,
            status="failed",
            error=f"Ожидался JSON-объект, получено {type(payload).__name__}.",
        )

    normalized, normalization_issues = _normalize_model_payload(schema, payload)
    try:
        value = schema.model_validate(normalized)
    except ValidationError:
        pass
    else:
        status: RecoveryStatus = "recovered" if normalization_issues else "validated"
        value = _attach_lossy_warnings(value, normalization_issues)
        return StructuredRecovery(value=value, status=status, issues=tuple(normalization_issues))

    candidate = deepcopy(normalized)
    issues = list(normalization_issues)
    last_error: ValidationError | None = None
    for _ in range(6):
        try:
            value = schema.model_validate(candidate)
        except ValidationError as error:
            last_error = error
        else:
            value = _attach_lossy_warnings(value, issues)
            return StructuredRecovery(value=value, status="partial", issues=tuple(issues))

        if _remove_invalid_leaves(candidate, last_error, issues):
            continue
        if _drop_invalid_list_items(candidate, last_error, issues):
            continue
        break

    return StructuredRecovery(
        value=None,
        status="failed",
        issues=tuple(issues),
        error=str(last_error) if last_error is not None else "Не удалось провалидировать ответ.",
    )


def raw_payload_from_message(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None

    for attribute in ("tool_calls", "invalid_tool_calls"):
        calls = getattr(raw, attribute, None)
        payload = _payload_from_calls(calls)
        if payload is not None:
            return payload

    content = getattr(raw, "content", None)
    if isinstance(content, str):
        payload = parse_json_object(content)
        if payload is not None:
            return payload

    additional = getattr(raw, "additional_kwargs", None)
    if isinstance(additional, dict):
        payload = _payload_from_calls(additional.get("tool_calls"))
        if payload is not None:
            return payload
    return None


def raw_message_text(raw: Any) -> str:
    payload = raw_payload_from_message(raw)
    if payload is not None:
        return json.dumps(payload, ensure_ascii=False, default=str)
    content = getattr(raw, "content", None)
    return content if isinstance(content, str) else ""


def _normalize_model_payload(
    schema: type[BaseModel],
    payload: dict[str, Any],
    *,
    path: tuple[str | int, ...] = (),
) -> tuple[dict[str, Any], list[RecoveryIssue]]:
    normalized = dict(payload)
    issues: list[RecoveryIssue] = []
    _apply_field_aliases(schema, normalized, path, issues)
    for field_name, field in schema.model_fields.items():
        if field_name not in normalized:
            continue
        field_path = (*path, field_name)
        value, field_issues = _normalize_value(field.annotation, normalized[field_name], field_path)
        normalized[field_name] = value
        issues.extend(field_issues)

    _normalize_semantic_special_cases(schema, normalized, path, issues)
    return normalized, issues


def _apply_field_aliases(
    schema: type[BaseModel],
    data: dict[str, Any],
    path: tuple[str | int, ...],
    issues: list[RecoveryIssue],
) -> None:
    aliases = _FIELD_ALIASES.get(schema.__name__, {})
    for field_name, candidates in aliases.items():
        if field_name not in schema.model_fields or data.get(field_name) not in (None, "", [], {}):
            continue
        for alias in candidates:
            if alias not in data or data.get(alias) in (None, "", [], {}):
                continue
            data[field_name] = data[alias]
            issues.append(
                RecoveryIssue(
                    _format_path((*path, field_name)),
                    f"поле восстановлено из alias {alias}",
                )
            )
            break


def _normalize_value(
    annotation: Any,
    value: Any,
    path: tuple[str | int, ...],
) -> tuple[Any, list[RecoveryIssue]]:
    issues: list[RecoveryIssue] = []
    base = _without_none(annotation)
    origin = get_origin(base)

    if origin is not list:
        value, wrapper_issues = _unwrap_fact_value(base, value, path)
        issues.extend(wrapper_issues)

    if origin is list:
        item_type = get_args(base)[0] if get_args(base) else Any
        if value is None:
            return [], [RecoveryIssue(_format_path(path), "null преобразован в пустой список")]
        if not isinstance(value, list):
            value = [value]
            issues.append(RecoveryIssue(_format_path(path), "одиночное значение преобразовано в список"))
        result = []
        for index, item in enumerate(value):
            normalized_item, item_issues = _normalize_value(item_type, item, (*path, index))
            result.append(normalized_item)
            issues.extend(item_issues)
        return result, issues

    model_type = _base_model_type(base)
    if model_type is not None and isinstance(value, dict):
        normalized_model, model_issues = _normalize_model_payload(model_type, value, path=path)
        return normalized_model, [*issues, *model_issues]
    if model_type is not None and model_type.__name__ == "MoneyValue":
        parsed = _parse_decimal_text(value) if isinstance(value, (str, int, float, Decimal)) else None
        if parsed is not None:
            return {
                "raw": str(value),
                "amount": parsed,
            }, [*issues, RecoveryIssue(_format_path(path), "денежное значение преобразовано в MoneyValue")]

    if base is Decimal and isinstance(value, dict):
        mapped = _text_from_mapping(value)
        if mapped is not None:
            value = mapped
            issues.append(RecoveryIssue(_format_path(path), "число извлечено из объекта LLM"))

    if base is Decimal and isinstance(value, str):
        parsed = _parse_decimal_text(value)
        if parsed is not None:
            issues.append(RecoveryIssue(_format_path(path), "строковое число преобразовано в Decimal"))
            return parsed, issues

    if base is date and isinstance(value, str):
        parsed_date = _parse_date(value)
        if parsed_date is not None and parsed_date.isoformat() != value.strip():
            return parsed_date, [*issues, RecoveryIssue(_format_path(path), "дата преобразована в формат ISO")]

    if base is bool and isinstance(value, str):
        parsed_bool = _parse_bool(value)
        if parsed_bool is not None:
            return parsed_bool, [*issues, RecoveryIssue(_format_path(path), "текстовое значение преобразовано в bool")]

    if base is str and isinstance(value, dict):
        text = _text_from_mapping(value)
        if text is None and path and path[-1] == "evidence":
            text = _evidence_text(value)
        if text is not None:
            return text, [*issues, RecoveryIssue(_format_path(path), "текст извлечён из объекта LLM")]

    return value, issues


def _unwrap_fact_value(
    expected_type: Any,
    value: Any,
    path: tuple[str | int, ...],
) -> tuple[Any, list[RecoveryIssue]]:
    issues: list[RecoveryIssue] = []
    expected_model = _base_model_type(expected_type)
    if expected_model is not None and isinstance(value, dict):
        # Provider metadata may live beside a complete nested model. Keep the
        # model intact instead of mistaking it for a scalar fact wrapper.
        if any(field_name in value for field_name in expected_model.model_fields):
            return value, issues
    if expected_model is not None and {
        "raw_value",
        "normalized_value",
    }.issubset(expected_model.model_fields):
        return value, issues
    if isinstance(value, list):
        if len(value) == 1:
            value = value[0]
            issues.append(
                RecoveryIssue(
                    _format_path(path),
                    "одиночный факт извлечён из списка LLM",
                )
            )
        elif expected_type is str:
            texts = [_text_from_fact(item) for item in value]
            texts = [text for text in texts if text]
            if texts:
                return "; ".join(dict.fromkeys(texts)), [
                    RecoveryIssue(
                        _format_path(path),
                        "текстовые факты из списка LLM объединены в строку",
                    )
                ]
        elif expected_model is not None and expected_model.__name__ == "TermValue":
            candidates = [item for item in value if isinstance(item, dict)]
            if candidates:
                value = candidates[0]
                issues.append(
                    RecoveryIssue(
                        _format_path(path),
                        "из нескольких вариантов срока выбран первый структурированный факт",
                        lossy=True,
                    )
                )

    if not isinstance(value, dict) or not ({"raw_value", "normalized_value"} & value.keys()):
        return value, issues

    normalized = value.get("normalized_value")
    raw = value.get("raw_value")
    candidate = normalized if normalized not in (None, "", [], {}) else raw
    model_type = expected_model
    if (
        model_type is not None
        and isinstance(candidate, dict)
        and "raw" in model_type.model_fields
        and candidate.get("raw") in (None, "")
        and isinstance(raw, (str, int, float, Decimal))
    ):
        candidate = {**candidate, "raw": str(raw)}
    if expected_type is str and isinstance(candidate, (dict, list)):
        candidate = raw if isinstance(raw, (str, int, float, Decimal)) else _text_from_fact(candidate)
    if candidate in (None, "", [], {}):
        return None, [
            *issues,
            RecoveryIssue(
                _format_path(path),
                "пустой fact-wrapper преобразован в null",
            ),
        ]
    issues.append(
        RecoveryIssue(
            _format_path(path),
            "значение извлечено из fact-wrapper LLM",
        )
    )
    return candidate, issues


def _text_from_fact(value: Any) -> str | None:
    if isinstance(value, (str, int, float, Decimal)):
        text = str(value).strip()
        return text or None
    if isinstance(value, dict):
        text = _text_from_mapping(value)
        if text:
            return text
        if {"document_id", "block_id", "table_id", "row"} & value.keys():
            return _evidence_text(value)
    return None


def _evidence_text(value: dict[str, Any]) -> str | None:
    parts = []
    for key in ("document_id", "block_id", "table_id", "row", "column"):
        item = value.get(key)
        if item not in (None, ""):
            parts.append(str(item))
    return ":".join(parts) if parts else None


def _normalize_semantic_special_cases(
    schema: type[BaseModel],
    data: dict[str, Any],
    path: tuple[str | int, ...],
    issues: list[RecoveryIssue],
) -> None:
    vat_rate = data.get("vat_rate")
    if "vat_rate" in schema.model_fields and isinstance(vat_rate, str):
        if normalize_decimal(vat_rate) is None and "ндс" in vat_rate.casefold():
            data["vat_rate"] = None
            if "vat_text" in schema.model_fields and not data.get("vat_text"):
                data["vat_text"] = vat_rate
            issues.append(
                RecoveryIssue(
                    _format_path((*path, "vat_rate")),
                    "текст о НДС перенесён в vat_text; числовая ставка не задана",
                )
            )

    percent = data.get("percent")
    if "percent" in schema.model_fields and isinstance(percent, str) and "/" in percent:
        data["percent"] = None
        if "basis" in schema.model_fields and not data.get("basis"):
            data["basis"] = percent
        issues.append(
            RecoveryIssue(
                _format_path((*path, "percent")),
                "дробная формула перенесена в основание и не считается процентом",
            )
        )


def _remove_invalid_leaves(
    payload: dict[str, Any],
    error: ValidationError,
    issues: list[RecoveryIssue],
) -> bool:
    changed = False
    seen: set[tuple[str | int, ...]] = set()
    for item in error.errors():
        location = tuple(item.get("loc") or ())
        if not location or location in seen or item.get("type") == "missing":
            continue
        seen.add(location)
        parent = _resolve_parent(payload, location)
        leaf = location[-1]
        if not isinstance(parent, dict) or not isinstance(leaf, str) or leaf not in parent:
            continue
        raw_value = parent.pop(leaf)
        raw_field = _preserve_raw_value(parent, leaf, raw_value)
        preserved = f"; сохранено в {raw_field}" if raw_field else ""
        issues.append(
            RecoveryIssue(
                _format_path(location),
                f"невалидное необязательное поле удалено{preserved}; raw={_short_value(raw_value)}",
                lossy=True,
            )
        )
        changed = True
    return changed


def _drop_invalid_list_items(
    payload: dict[str, Any],
    error: ValidationError,
    issues: list[RecoveryIssue],
) -> bool:
    targets: dict[tuple[str | int, ...], set[int]] = {}
    for item in error.errors():
        location = tuple(item.get("loc") or ())
        indexed = [position for position, part in enumerate(location) if isinstance(part, int)]
        if not indexed:
            continue
        position = indexed[-1]
        list_path = location[:position]
        targets.setdefault(list_path, set()).add(location[position])

    changed = False
    for list_path, indices in sorted(targets.items(), key=lambda pair: len(pair[0]), reverse=True):
        container = _resolve_value(payload, list_path)
        if not isinstance(container, list):
            continue
        for index in sorted(indices, reverse=True):
            if not 0 <= index < len(container):
                continue
            raw_value = container.pop(index)
            issues.append(
                RecoveryIssue(
                    _format_path((*list_path, index)),
                    f"строка удалена из-за невалидного обязательного поля; raw={_short_value(raw_value)}",
                    lossy=True,
                )
            )
            changed = True
    return changed


def _preserve_raw_value(parent: dict[str, Any], field_name: str, value: Any) -> str | None:
    raw_field = _RAW_FIELD_BY_FIELD.get(field_name)
    if raw_field is None or parent.get(raw_field) not in (None, ""):
        return None
    parent[raw_field] = str(value)
    return raw_field


def _attach_lossy_warnings(value: T, issues: list[RecoveryIssue]) -> T:
    warnings = [f"Частичное восстановление LLM: {item.as_text()}" for item in issues if item.lossy]
    if not warnings:
        return value
    result = value.model_copy(deep=True)
    for field_name in ("parser_warnings", "extraction_warnings", "warnings"):
        if field_name not in result.__class__.model_fields:
            continue
        current = list(getattr(result, field_name, []) or [])
        setattr(result, field_name, list(dict.fromkeys([*current, *warnings])))
        break
    return result


def _payload_from_calls(calls: Any) -> dict[str, Any] | None:
    if not isinstance(calls, list):
        return None
    for call in calls:
        if not isinstance(call, dict):
            continue
        args = call.get("args")
        if isinstance(args, dict):
            return args
        if isinstance(args, str):
            payload = _json_object(args)
            if payload is not None:
                return payload
        function = call.get("function")
        arguments = function.get("arguments") if isinstance(function, dict) else None
        if isinstance(arguments, str):
            payload = _json_object(arguments)
            if payload is not None:
                return payload
    return None


def parse_json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        repaired = _close_truncated_json(text)
        if repaired is None:
            return None
        try:
            parsed = json.loads(repaired)
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def _json_object(value: str) -> dict[str, Any] | None:
    return parse_json_object(value)


def _close_truncated_json(text: str) -> str | None:
    if not text.startswith("{"):
        return None
    stack: list[str] = []
    in_string = False
    escaped = False
    pairs = {"}": "{", "]": "["}
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if not stack or stack[-1] != pairs[char]:
                return None
            stack.pop()
    if in_string or not stack:
        return None
    closing = {"{": "}", "[": "]"}
    return text + "".join(closing[char] for char in reversed(stack))


def _without_none(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        args = tuple(item for item in get_args(annotation) if item is not type(None))
        if len(args) == 1:
            return args[0]
    return annotation


def _base_model_type(annotation: Any) -> type[BaseModel] | None:
    try:
        return annotation if isinstance(annotation, type) and issubclass(annotation, BaseModel) else None
    except TypeError:
        return None


def _parse_date(value: str) -> date | None:
    text = " ".join(value.strip().split())
    month_names = {
        "января": 1,
        "февраля": 2,
        "марта": 3,
        "апреля": 4,
        "мая": 5,
        "июня": 6,
        "июля": 7,
        "августа": 8,
        "сентября": 9,
        "октября": 10,
        "ноября": 11,
        "декабря": 12,
    }
    words = re.search(r"\b(\d{1,2})\s+([а-яё]+)\s+(\d{4})\b", text, flags=re.IGNORECASE)
    if words and words.group(2).casefold() in month_names:
        try:
            return date(
                int(words.group(3)),
                month_names[words.group(2).casefold()],
                int(words.group(1)),
            )
        except ValueError:
            return None

    match = re.search(
        r"(?<!\d)(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[.\-/]\d{1,2}[.\-/]\d{4})(?!\d)",
        text,
    )
    candidate = match.group(1) if match else text
    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(candidate, pattern).date()
        except ValueError:
            continue
    return None


def _parse_decimal_text(value: Any) -> Decimal | None:
    if isinstance(value, Decimal):
        return value
    if value is None:
        return None
    text = str(value).replace("\xa0", " ").strip().casefold()
    if re.search(r"\b\d+\s*/\s*\d+\b", text):
        return None
    multiplier = Decimal("1")
    if re.search(r"\b(?:млн|миллион(?:а|ов)?)\b", text):
        multiplier = Decimal("1000000")
    elif re.search(r"\b(?:тыс|тысяч(?:а|и)?)\b", text):
        multiplier = Decimal("1000")

    rubles = re.search(
        r"([-+]?\d[\d\s.,]*)\s*(?:руб(?:л(?:ей|я|ь)?|\.)?)"
        r"(?:\s*(\d{1,2})\s*коп(?:еек|ейки|ейка|\.)?)?",
        text,
    )
    numeric = rubles or re.search(r"[-+]?\d[\d\s.,]*", text)
    if not numeric:
        return None
    token = numeric.group(0).replace(" ", "")
    if rubles:
        token = rubles.group(1).replace(" ", "")
    if "," in token:
        token = token.replace(".", "").replace(",", ".")
    elif token.count(".") > 1:
        parts = token.split(".")
        if all(len(part) == 3 for part in parts[1:]):
            token = "".join(parts)
        else:
            token = "".join(parts[:-1]) + "." + parts[-1]
    try:
        amount = Decimal(token)
        amount *= multiplier
        if rubles and rubles.group(2):
            amount += Decimal(rubles.group(2)) / Decimal("100")
        return amount
    except (ArithmeticError, ValueError):
        return normalize_decimal(value)


def _parse_bool(value: str) -> bool | None:
    normalized = " ".join(value.casefold().replace("ё", "е").split())
    if normalized in {"да", "есть", "предусмотрено", "предусмотрена", "true"}:
        return True
    if normalized in {"нет", "отсутствует", "не предусмотрено", "не предусмотрена", "false"}:
        return False
    return None


def _text_from_mapping(value: dict[str, Any]) -> str | None:
    for key in ("raw", "raw_value", "normalized_value", "value", "text", "title", "name"):
        candidate = value.get(key)
        if isinstance(candidate, (str, int, float, Decimal)):
            text = str(candidate).strip()
            if text:
                return text
    return None


def _resolve_parent(payload: Any, location: tuple[str | int, ...]) -> Any:
    return _resolve_value(payload, location[:-1])


def _resolve_value(payload: Any, location: tuple[str | int, ...]) -> Any:
    current = payload
    for part in location:
        if isinstance(part, int) and isinstance(current, list) and 0 <= part < len(current):
            current = current[part]
        elif isinstance(part, str) and isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _format_path(location: tuple[str | int, ...]) -> str:
    result = ""
    for part in location:
        if isinstance(part, int):
            result += f"[{part}]"
        else:
            result += f".{part}" if result else part
    return result


def _short_value(value: Any, limit: int = 180) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
    text = " ".join(text.split())
    return text if len(text) <= limit else f"{text[:limit]}..."
