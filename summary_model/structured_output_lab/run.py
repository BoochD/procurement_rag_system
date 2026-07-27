from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from shared_modules.llm_models import OPENAI_MODEL, get_langchain_openai_chat_model
from summary_model.checks.normalization import normalize_decimal


class DemoOffer(BaseModel):
    supplier_name: str | None = None
    outgoing_date: date | None = None
    unit_price: Decimal | None = None
    vat_rate: Decimal | None = None
    vat_text: str | None = None
    notes: list[str] = Field(default_factory=list)


DEMO_PROMPT = """
Это технический эксперимент по обработке structured output. Верни один объект
строго с этими значениями, не меняя формат значений под JSON schema:

- supplier_name: ООО «Тест»;
- outgoing_date: 28.04.2026;
- unit_price: 10 470 000,00;
- vat_rate: Без НДС;
- notes: одна строка «Тестовая строка без списка».

Не добавляй фактов и не поясняй ответ вне структурированного результата.
""".strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check whether LangChain keeps raw structured output after Pydantic validation fails."
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Make one live LLM request. Without it, only the local normalization demo is written.",
    )
    parser.add_argument(
        "--compare-direct",
        action="store_true",
        help="Make an additional old-style request without include_raw=True.",
    )
    parser.add_argument(
        "--model",
        default=OPENAI_MODEL,
        help="OpenAI-compatible model for this isolated lab run.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("runtime/structured_output_lab"),
        help="Directory for run.json and raw response artifacts.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    target = args.output_dir / _safe_name(args.model)
    target.mkdir(parents=True, exist_ok=True)

    malformed = _deliberately_malformed_payload()
    normalized, normalization_warnings = normalize_demo_payload(malformed)
    local_result = _validate_payload(normalized)
    _write_json(target / "local_input.json", malformed)
    _write_json(target / "local_normalized.json", normalized)

    run: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "live": args.live,
        "local_normalizer": {
            "warnings": normalization_warnings,
            "validated": local_result[0].model_dump(mode="json") if local_result[0] else None,
            "validation_error": local_result[1],
        },
        "artifacts": ["local_input.json", "local_normalized.json"],
    }

    if args.live:
        live = _run_with_raw(args.model)
        run["include_raw"] = live
        _write_json(target / "include_raw.json", live)
        run["artifacts"].append("include_raw.json")

        if args.compare_direct:
            direct = _run_direct(args.model)
            run["direct"] = direct
            _write_json(target / "direct.json", direct)
            run["artifacts"].append("direct.json")

    _write_json(target / "run.json", run)
    print(f"Wrote structured-output lab artifacts to {target}")
    return 0


def _run_with_raw(model_name: str) -> dict[str, Any]:
    model = get_langchain_openai_chat_model(model_name=model_name)
    structured = model.with_structured_output(
        DemoOffer,
        method="function_calling",
        include_raw=True,
    )
    response = structured.invoke(DEMO_PROMPT)
    raw = response.get("raw") if isinstance(response, dict) else None
    parsed = response.get("parsed") if isinstance(response, dict) else None
    parsing_error = response.get("parsing_error") if isinstance(response, dict) else None
    raw_payload = _payload_from_raw_message(raw)
    normalized, warnings = normalize_demo_payload(raw_payload) if raw_payload else ({}, [])
    validated, validation_error = _validate_payload(normalized) if raw_payload else (None, None)
    return {
        "response_type": type(response).__name__,
        "parsed": _json_value(parsed),
        "parsing_error": _error_text(parsing_error),
        "raw_message": _json_value(raw),
        "recovered_payload": raw_payload,
        "normalizer_warnings": warnings,
        "validated_after_normalization": _json_value(validated),
        "validation_error_after_normalization": validation_error,
    }


def _run_direct(model_name: str) -> dict[str, Any]:
    model = get_langchain_openai_chat_model(model_name=model_name)
    structured = model.with_structured_output(DemoOffer, method="function_calling")
    try:
        result = structured.invoke(DEMO_PROMPT)
    except Exception as error:
        return {"raised": True, "error": _error_text(error)}
    return {"raised": False, "result": _json_value(result)}


def normalize_demo_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Minimal deterministic example of the proposed ingress normalizer."""
    normalized = dict(payload)
    warnings: list[str] = []

    raw_date = normalized.get("outgoing_date")
    if isinstance(raw_date, str):
        parsed_date = _parse_russian_date(raw_date)
        if parsed_date is not None:
            normalized["outgoing_date"] = parsed_date
            warnings.append("outgoing_date: формат ДД.ММ.ГГГГ преобразован в date")

    raw_price = normalized.get("unit_price")
    if isinstance(raw_price, str):
        parsed_price = normalize_decimal(raw_price)
        if parsed_price is not None:
            normalized["unit_price"] = parsed_price
            warnings.append("unit_price: строка суммы преобразована в Decimal")

    raw_vat_rate = normalized.get("vat_rate")
    if isinstance(raw_vat_rate, str):
        parsed_vat_rate = normalize_decimal(raw_vat_rate)
        if parsed_vat_rate is not None:
            normalized["vat_rate"] = parsed_vat_rate
        elif "ндс" in raw_vat_rate.casefold():
            normalized["vat_rate"] = None
            normalized.setdefault("vat_text", raw_vat_rate)
            warnings.append("vat_rate: текстовое значение перенесено в vat_text")

    raw_notes = normalized.get("notes")
    if raw_notes is None:
        normalized["notes"] = []
    elif isinstance(raw_notes, str):
        normalized["notes"] = [raw_notes]
        warnings.append("notes: строка преобразована в список")

    return normalized, warnings


def _payload_from_raw_message(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    tool_calls = getattr(raw, "tool_calls", None)
    if isinstance(tool_calls, list) and tool_calls:
        args = tool_calls[0].get("args") if isinstance(tool_calls[0], dict) else None
        if isinstance(args, dict):
            return args

    content = getattr(raw, "content", None)
    if isinstance(content, str):
        payload = _json_object(content)
        if payload is not None:
            return payload

    additional = getattr(raw, "additional_kwargs", None)
    if not isinstance(additional, dict):
        return None
    calls = additional.get("tool_calls")
    if not isinstance(calls, list) or not calls:
        return None
    function = calls[0].get("function") if isinstance(calls[0], dict) else None
    arguments = function.get("arguments") if isinstance(function, dict) else None
    if not isinstance(arguments, str):
        return None
    return _json_object(arguments)


def _json_object(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _validate_payload(payload: dict[str, Any]) -> tuple[DemoOffer | None, str | None]:
    try:
        return DemoOffer.model_validate(payload), None
    except ValidationError as error:
        return None, str(error)


def _parse_russian_date(value: str) -> date | None:
    try:
        return datetime.strptime(value.strip(), "%d.%m.%Y").date()
    except ValueError:
        return None


def _deliberately_malformed_payload() -> dict[str, Any]:
    return {
        "supplier_name": "ООО «Тест»",
        "outgoing_date": "28.04.2026",
        "unit_price": "10 470 000,00",
        "vat_rate": "Без НДС",
        "notes": "Тестовая строка без списка",
    }


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _error_text(error: Any) -> str | None:
    if error is None:
        return None
    return f"{type(error).__name__}: {error}"


def _safe_name(value: str) -> str:
    result = re.sub(r"[^\w.-]+", "_", value, flags=re.UNICODE).strip("_")
    return result[:120] or "model"


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
