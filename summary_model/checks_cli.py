from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from summary_model.checks import run_checks
from summary_model.checks.report import build_checks_report_text
from summary_model.checks.ktru_adapter import run_ktru_characteristic_checks, run_pp1875_checks
from summary_model.checks.runner import external_manual_checks_with_replacements
from summary_model.checks.semantic_llm import run_semantic_llm_checks
from summary_model.extraction_models import ProcurementPackageExtraction


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run checks over ProcurementPackageExtraction JSON."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--with-llm",
        action="store_true",
        help="Run one structured LLM call for semantic checks.",
    )
    parser.add_argument(
        "--with-ktru",
        action="store_true",
        help="Run live KTRU characteristic checks through the registry adapter.",
    )
    parser.add_argument(
        "--ktru-timeout",
        type=int,
        default=12,
        help="Per-request timeout in seconds for live KTRU checks.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    package = ProcurementPackageExtraction.model_validate_json(
        args.input.read_text(encoding="utf-8")
    )
    semantic_results = None
    llm_metrics = None
    if args.with_llm:
        semantic_results, llm_metrics = run_semantic_llm_checks(package)
    ktru_results = None
    ktru_error = None
    if args.with_ktru:
        try:
            ktru_results = run_ktru_characteristic_checks(
                package,
                fetch_timeout_seconds=args.ktru_timeout,
            )
            ktru_results.append(run_pp1875_checks(package))
        except Exception as error:
            ktru_error = str(error)
            ktru_results = None
    external_results = (
        external_manual_checks_with_replacements(package, ktru_results)
        if ktru_results is not None
        else None
    )
    report = run_checks(
        package,
        semantic_results=semantic_results,
        external_results=external_results,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "checks.json").write_text(
        report.model_dump_json(indent=2, exclude_none=True),
        encoding="utf-8",
    )
    (args.output_dir / "report.txt").write_text(
        build_checks_report_text(report),
        encoding="utf-8",
    )
    (args.output_dir / "run.json").write_text(
        json.dumps(
            {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "input": str(args.input),
                "package_id": package.package_id,
                "results_count": len(report.results),
                "errors_count": report.errors_count,
                "warnings_count": report.warnings_count,
                "manual_review_count": report.manual_review_count,
                "passed_count": report.passed_count,
                "with_llm": args.with_llm,
                "with_ktru": args.with_ktru,
                "ktru_timeout": args.ktru_timeout if args.with_ktru else None,
                "llm_metrics": llm_metrics,
                "ktru_error": ktru_error,
                "artifacts": ["checks.json", "report.txt", "run.json"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
