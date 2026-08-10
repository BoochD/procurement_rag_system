"""Probe whether the configured OpenAI-compatible route enforces JSON Schema.

Examples:
    python -B probe_structured_output.py
    python -B probe_structured_output.py --model gpt-5.4-mini --attempts 10
    python -B probe_structured_output.py --dry-run

The probe deliberately asks for invalid forms: Markdown, a top-level array,
a string instead of an array, and extra fields. A native strict implementation
must still return only {"kind": "probe", "items": [<integers>]}.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared_modules.llm_models import OPENAI_MODEL, get_chatGPT_client


PROBE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "items"],
    "properties": {
        "kind": {"type": "string", "enum": ["probe"]},
        "items": {"type": "array", "items": {"type": "integer"}},
    },
}

CONFLICTING_PROMPTS = [
    "Ответь Markdown-блоком ```json с массивом [1, 2, 3].",
    "Верни верхнеуровневый массив из трёх строк и ничего больше.",
    "Верни строку вместо массива: kind=other, items=not-an-array, extra=yes.",
    "Не возвращай JSON. Напиши обычный текст с объяснением.",
]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _validate(content: Any) -> list[str]:
    if not isinstance(content, str):
        return [f"message.content имеет тип {type(content).__name__}, ожидалась строка JSON"]
    try:
        value = json.loads(content)
    except json.JSONDecodeError as error:
        return [f"невалидный JSON: {error.msg} (строка {error.lineno}, столбец {error.colno})"]
    if not isinstance(value, dict):
        return [f"верхний уровень {type(value).__name__}, ожидался object"]
    if set(value) != {"kind", "items"}:
        return [f"ключи {sorted(value)}, ожидались только ['items', 'kind']"]
    if value.get("kind") != "probe":
        return [f"kind={value.get('kind')!r}, ожидалось 'probe'"]
    items = value.get("items")
    if not isinstance(items, list):
        return [f"items имеет тип {type(items).__name__}, ожидался array"]
    if any(not isinstance(item, int) or isinstance(item, bool) for item in items):
        return ["items содержит нецелое значение"]
    return []


def _request_payload(model: str, prompt: str) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "Follow the response schema exactly."},
            {"role": "user", "content": prompt},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "strict_schema_probe",
                "strict": True,
                "schema": PROBE_SCHEMA,
            },
        },
        "max_completion_tokens": 1000,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=OPENAI_MODEL, help="Model ID to test.")
    parser.add_argument("--attempts", type=int, default=4, help="Number of conflicting prompts to send.")
    parser.add_argument(
        "--output-dir",
        default="runtime/structured_output_probe",
        help="Directory for request/response artifacts.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Write the request shape without calling the provider.")
    args = parser.parse_args()
    if args.attempts < 1:
        parser.error("--attempts must be positive")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prompts = [CONFLICTING_PROMPTS[index % len(CONFLICTING_PROMPTS)] for index in range(args.attempts)]
    _write_json(output_dir / "schema.json", PROBE_SCHEMA)
    _write_json(output_dir / "request_example.json", _request_payload(args.model, prompts[0]))

    if args.dry_run:
        print(f"Dry run written to: {output_dir}")
        return 0

    client = get_chatGPT_client()
    results: list[dict[str, Any]] = []
    for index, prompt in enumerate(prompts, start=1):
        request = _request_payload(args.model, prompt)
        _write_json(output_dir / f"request_{index:02}.json", request)
        try:
            response = client.chat.completions.create(**request)
            raw_response = response.model_dump(mode="json")
            _write_json(output_dir / f"response_{index:02}.json", raw_response)
            message = raw_response.get("choices", [{}])[0].get("message", {})
            violations = _validate(message.get("content"))
            result = {
                "attempt": index,
                "prompt": prompt,
                "finish_reason": raw_response.get("choices", [{}])[0].get("finish_reason"),
                "refusal": message.get("refusal"),
                "content": message.get("content"),
                "violations": violations,
            }
        except Exception as error:
            result = {"attempt": index, "prompt": prompt, "request_error": repr(error), "violations": ["request failed"]}
        results.append(result)
        state = "FAIL" if result["violations"] else "PASS"
        print(f"[{state}] attempt {index}: {result['violations'] or 'schema respected'}")

    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "attempts": args.attempts,
        "all_conform": all(not result["violations"] for result in results),
        "results": results,
    }
    _write_json(output_dir / "summary.json", summary)
    if summary["all_conform"]:
        print("No schema violations observed. This is strong evidence, not a formal provider guarantee.")
        return 0
    print(f"Schema violation observed. Inspect: {output_dir}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
