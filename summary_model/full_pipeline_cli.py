from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from summary_model.web_service import (
    WebPipelineOptions,
    process_uploaded_documents,
)


ROLE_LABELS = {
    "plan": "Заявка в план-график",
    "obrasheniye": "Обращение о проведении закупки",
    "onmck": "Обоснование НМЦК",
    "ooz": "Описание объекта закупки",
    "contract": "Проект контракта",
    "zapiska": "Пояснительная записка",
    "commercial_offer": "Коммерческое предложение",
}

ROLE_ORDER = {
    "plan": 0,
    "obrasheniye": 1,
    "onmck": 2,
    "ooz": 3,
    "contract": 4,
    "zapiska": 5,
    "commercial_offer": 6,
}

MEDIA_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the same summary_model pipeline as the web worker and save "
            "diagnostic artifacts."
        )
    )
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--no-llm-extraction", action="store_true")
    parser.add_argument("--no-semantic-llm", action="store_true")
    parser.add_argument("--no-ktru", action="store_true")
    parser.add_argument("--no-vlm-tables", action="store_true")
    parser.add_argument("--no-vlm-commercial-offers", action="store_true")
    parser.add_argument("--no-vlm-short-documents", action="store_true")
    parser.add_argument(
        "--llm-concurrency",
        type=int,
        default=int(os.getenv("SUMMARY_LLM_CONCURRENCY", "6")),
    )
    parser.add_argument(
        "--ktru-timeout",
        type=int,
        default=int(os.getenv("KTRU_TIMEOUT_SECONDS", "30")),
    )
    parser.add_argument(
        "--vlm-max-tables",
        type=int,
        default=int(os.getenv("SUMMARY_VLM_MAX_TABLES_PER_DOCUMENT", "4")),
    )
    parser.add_argument(
        "--vlm-max-offer-pages",
        type=int,
        default=int(os.getenv("SUMMARY_VLM_MAX_COMMERCIAL_OFFER_PAGES", "8")),
    )
    parser.add_argument(
        "--vlm-max-short-document-pages",
        type=int,
        default=int(os.getenv("SUMMARY_VLM_MAX_SHORT_DOCUMENT_PAGES", "4")),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _load_env()
    _validate_args(args)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    selected, ignored = discover_uploaded_documents(args.input_dir)
    _write_json(
        args.output_dir / "inputs.json",
        {
            "input_dir": str(args.input_dir.resolve()),
            "selected": selected,
            "ignored": ignored,
        },
    )
    if not any(document["key"] == "plan" for document in selected):
        raise SystemExit(
            "Не найдена обязательная заявка в план-график DOCX. "
            "Проверьте имя файла и inputs.json."
        )

    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()
    options = WebPipelineOptions(
        with_llm_extraction=not args.no_llm_extraction,
        with_semantic_llm=not args.no_semantic_llm,
        with_ktru=not args.no_ktru,
        with_vlm_tables=not args.no_vlm_tables,
        with_vlm_commercial_offers=not args.no_vlm_commercial_offers,
        with_vlm_short_documents=not args.no_vlm_short_documents,
        ktru_timeout_seconds=args.ktru_timeout,
        llm_concurrency=args.llm_concurrency,
        vlm_max_tables_per_document=args.vlm_max_tables,
        vlm_max_commercial_offer_pages=args.vlm_max_offer_pages,
        vlm_max_short_document_pages=args.vlm_max_short_document_pages,
        vlm_output_dir=args.output_dir / "vlm_tables",
    )
    run_metadata = {
        "started_at": started_at.isoformat(),
        "input_dir": str(args.input_dir.resolve()),
        "options": _jsonable(options.__dict__),
    }

    try:
        result = process_uploaded_documents(selected, options=options)
    except Exception as error:
        run_metadata.update(
            {
                "status": "failed",
                "duration_seconds": round(time.perf_counter() - started, 3),
                "error": _error_payload(error),
            }
        )
        _write_json(args.output_dir / "run.json", run_metadata)
        _write_json(args.output_dir / "error.json", _error_payload(error))
        raise

    report_with_warnings = result.report_text
    if result.warnings:
        warning_lines = "\n".join(f"- {warning}" for warning in result.warnings)
        report_with_warnings += f"\n\n<b>Технические предупреждения</b>\n{warning_lines}"

    (args.output_dir / "report.txt").write_text(result.report_text, encoding="utf-8")
    (args.output_dir / "report_with_warnings.txt").write_text(
        report_with_warnings,
        encoding="utf-8",
    )
    _write_json(args.output_dir / "warnings.json", result.warnings)
    _write_json(args.output_dir / "metrics.json", result.metrics)
    if result.package is not None:
        _write_json(args.output_dir / "extraction_result.final.json", result.package)
    if result.checks_report is not None:
        _write_json(args.output_dir / "checks.json", result.checks_report)

    run_metadata.update(
        {
            "status": "completed",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": round(time.perf_counter() - started, 3),
            "package_id": result.package_id,
            "selected_files": [document["name"] for document in selected],
            "warnings_count": len(result.warnings),
            "artifacts": [
                "inputs.json",
                "extraction_result.final.json",
                "checks.json",
                "metrics.json",
                "warnings.json",
                "report.txt",
                "report_with_warnings.txt",
            ],
        }
    )
    _write_json(args.output_dir / "run.json", run_metadata)
    print(f"Full pipeline completed: {args.output_dir}")
    print(f"Warnings: {len(result.warnings)}")
    return 0


def discover_uploaded_documents(input_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    candidates: dict[str, list[Path]] = {}
    ignored: list[dict[str, str]] = []

    for path in sorted(input_dir.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_file() or path.name.startswith("~$"):
            continue
        if path.name.casefold().startswith("analysis_result"):
            ignored.append({"name": path.name, "reason": "generated report"})
            continue
        role = detect_document_role(path)
        if role is None:
            ignored.append({"name": path.name, "reason": "document role not recognized"})
            continue
        if path.suffix.casefold() != ".docx" and role not in {
            "commercial_offer",
            "obrasheniye",
            "zapiska",
        }:
            ignored.append(
                {"name": path.name, "reason": "PDF ingestion is supported only for commercial offers, requests, and explanatory notes"}
            )
            continue
        candidates.setdefault(role, []).append(path)

    selected: list[dict[str, Any]] = []
    for role, paths in candidates.items():
        if role == "commercial_offer":
            chosen = paths
        else:
            chosen = [_prefer_docx(paths)]
            for duplicate in paths:
                if duplicate != chosen[0]:
                    ignored.append({"name": duplicate.name, "reason": f"duplicate role: {role}"})
        for index, path in enumerate(chosen, start=1):
            label = ROLE_LABELS[role]
            if role == "commercial_offer":
                label = f"{label} №{index}"
            selected.append(
                {
                    "key": role,
                    "label": label,
                    "name": path.name,
                    "path": str(path.resolve()),
                }
            )

    selected.sort(key=lambda item: (ROLE_ORDER[item["key"]], item["name"].casefold()))
    return selected, ignored


def detect_document_role(path: Path) -> str | None:
    name = path.stem.casefold().replace("ё", "е")
    name = re.sub(r"[_-]+", " ", name)
    if re.search(r"(?:^|[^а-я])кп(?:$|[^а-я])", name):
        return "commercial_offer"
    if (
        "онмцк" in name
        or re.search(r"(?:^|\s)оцк(?:\s|$)", name)
        or "обоснован" in name and "цен" in name
    ):
        return "onmck"
    if "обращен" in name:
        return "obrasheniye"
    if "поясн" in name or "записк" in name:
        return "zapiska"
    if "описан" in name and "закуп" in name or "ооз" in name:
        return "ooz"
    if "контракт" in name:
        return "contract"
    if "план-граф" in name or "план граф" in name or "заявк" in name and "пг" in name:
        return "plan"
    return None


def _prefer_docx(paths: list[Path]) -> Path:
    return sorted(paths, key=lambda path: (path.suffix.casefold() != ".docx", path.name.casefold()))[0]


def _validate_args(args: argparse.Namespace) -> None:
    if not args.input_dir.is_dir():
        raise SystemExit(f"Input directory not found: {args.input_dir}")
    if args.llm_concurrency < 1:
        raise SystemExit("--llm-concurrency must be at least 1.")
    if args.ktru_timeout < 1:
        raise SystemExit("--ktru-timeout must be at least 1.")
    if (
        args.vlm_max_tables < 1
        or args.vlm_max_offer_pages < 1
        or args.vlm_max_short_document_pages < 1
    ):
        raise SystemExit("VLM table/page limits must be at least 1.")


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        return
    load_dotenv(Path("web/.env"))


def _error_payload(error: Exception) -> dict[str, str]:
    return {"type": type(error).__name__, "message": str(error)}


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _write_json(path: Path, value: Any) -> None:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    path.write_text(
        json.dumps(_jsonable(value), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
