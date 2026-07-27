from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared_modules.llm_models import OPENAI_VLM_MODEL
from summary_model.checks import run_checks
from summary_model.checks.models import ProcurementChecksReport
from summary_model.checks.report import build_commercial_offer_report_text
from summary_model.commercial_offer_vlm import (
    CommercialOfferVlmOptions,
    extract_commercial_offer_with_vlm,
)
from summary_model.extraction_models import ProcurementPackageExtraction


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract commercial-offer PDFs through the production VLM path."
    )
    parser.add_argument(
        "--input",
        type=Path,
        nargs="+",
        required=True,
        help="One or more commercial-offer PDF/image files.",
    )
    parser.add_argument(
        "--model",
        default=OPENAI_VLM_MODEL,
        help="OpenAI-compatible multimodal model used only for this run.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("runtime/commercial_offer_lab"),
        help="Directory for schemas, metrics and rendered report.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=8,
        help="Maximum PDF pages per commercial offer sent to VLM.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    invalid_inputs = [path for path in args.input if not path.is_file()]
    if invalid_inputs:
        raise SystemExit("Input file not found: " + ", ".join(str(path) for path in invalid_inputs))
    if args.max_pages < 1:
        raise SystemExit("--max-pages must be at least 1.")

    target = args.output_dir / _safe_name(args.model)
    target.mkdir(parents=True, exist_ok=True)
    offers = []
    offer_metrics: list[dict[str, Any]] = []
    for path in args.input:
        result = extract_commercial_offer_with_vlm(
            path,
            options=CommercialOfferVlmOptions(
                enabled=True,
                model=args.model,
                max_pages=args.max_pages,
            ),
        )
        offers.append(result.offer)
        offer_metrics.append({"file_name": path.name, **result.metrics})

    package = ProcurementPackageExtraction(
        commercial_offers=offers,
        commercial_offers_found_count=len(offers),
        commercial_offers_missing=len(offers) < 3,
    )
    checks = run_checks(package)
    commercial_report = ProcurementChecksReport.from_results(
        package_id=package.package_id,
        results=[
            result
            for result in checks.results
            if result.check_id.startswith("manual.commercial_offers.")
        ],
    )

    _write_json(target / "commercial_offers.json", [offer.model_dump(mode="json") for offer in offers])
    _write_json(target / "checks.json", commercial_report.model_dump(mode="json"))
    (target / "report.txt").write_text(
        build_commercial_offer_report_text(commercial_report),
        encoding="utf-8",
    )
    _write_json(
        target / "run.json",
        {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "model": args.model,
            "max_pages": args.max_pages,
            "inputs": [str(path) for path in args.input],
            "offers": offer_metrics,
            "artifacts": ["commercial_offers.json", "checks.json", "report.txt"],
        },
    )
    print(f"Wrote commercial-offer lab artifacts to {target}")
    return 0


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
