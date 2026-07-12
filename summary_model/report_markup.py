from __future__ import annotations

import html
import re


STATUS_TAGS = {
    "ОК": "ok",
    "НАЙДЕН": "ok",
    "ПРЕДУПРЕЖДЕНИЕ": "warn",
    "ТРЕБУЕТ ПРОВЕРКИ": "warn",
    "ОШИБКА": "error",
    "НЕ НАЙДЕН": "error",
}


def mark_report_text(report_text: str) -> str:
    lines = []
    for raw_line in (report_text or "").replace("\r\n", "\n").split("\n"):
        escaped = html.escape(raw_line)
        escaped = _mark_statuses(escaped)
        escaped = _restore_allowed_tags(escaped)
        if _is_major_heading(raw_line):
            escaped = f"<big><b>{escaped}</b></big>"
        elif _is_heading(raw_line):
            escaped = f"<b>{escaped}</b>"
        lines.append(escaped)
    return "\n".join(lines)


def _is_major_heading(line: str) -> bool:
    stripped = line.strip()
    return bool(
        stripped
        and (
            stripped == "Результат проверки документов"
            or stripped == "Наличие документов:"
            or re.match(r"^\d+\)", stripped)
        )
    )


def _is_heading(line: str) -> bool:
    stripped = line.strip()
    return bool(
        stripped
        and (
            stripped == "Результат проверки документов"
            or re.match(r"^\d+\)", stripped)
            or stripped.endswith(":") and not stripped.startswith("-")
        )
    )


def _mark_statuses(text: str) -> str:
    for status, tag in STATUS_TAGS.items():
        text = re.sub(
            rf"(?<![\wА-Яа-яЁё]){re.escape(status)}(?![\wА-Яа-яЁё])",
            f"<{tag}>{status}</{tag}>",
            text,
        )
    return text


def _restore_allowed_tags(text: str) -> str:
    for tag in ("b", "u", "ins", "ok", "warn", "error", "big"):
        text = text.replace(f"&lt;{tag}&gt;", f"<{tag}>")
        text = text.replace(f"&lt;/{tag}&gt;", f"</{tag}>")
    return text
