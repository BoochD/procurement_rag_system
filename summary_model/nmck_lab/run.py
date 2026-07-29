from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from summary_model.checks import run_checks
from summary_model.classification import DocumentClassifier
from summary_model.domain.models import DocumentType, InputDocument
from summary_model.extraction_pipeline import extract_package
from summary_model.ingestion import read_docx
from summary_model.vlm_fallback import VlmFallbackOptions, VlmFallbackRepairer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check every NMCK/ONMCK DOCX under a fixture directory."
    )
    parser.add_argument("--input-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--live", action="store_true", help="Use the production VLM table repairer.")
    parser.add_argument("--model", default=None, help="Optional VLM model override for --live.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.input_root.resolve()
    if not root.is_dir():
        raise SystemExit(f"Input root not found: {root}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    classifier = DocumentClassifier()
    candidates, skipped = _discover_nmck_documents(root, classifier)
    repairer = VlmFallbackRepairer(
        VlmFallbackOptions(
            enabled=args.live,
            output_dir=args.output_dir / "vlm_tables",
            model=args.model or VlmFallbackOptions().model,
            force_roles={"nmck_calculation"} if args.live else set(),
        )
    )
    summaries = []
    for path in candidates:
        package = extract_package(
            [InputDocument(path=path, type_hint=DocumentType.ONMCK)],
            table_repairer=repairer.repair_document_tables if args.live else None,
        )
        checks = run_checks(package)
        onmck_checks = [
            result for result in checks.results
            if result.check_id.startswith("strict.onmck.")
        ]
        name = _artifact_name(root, path)
        output_dir = args.output_dir / name
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_json(output_dir / "nmck.json", package.nmck_justification)
        _write_json(output_dir / "tables.json", package.tables)
        _write_json(output_dir / "checks.json", onmck_checks)
        summaries.append(_summary(root, path, package, onmck_checks))

    _write_json(
        args.output_dir / "summary.json",
        {
            "input_root": str(root),
            "live": args.live,
            "documents": summaries,
            "skipped": skipped,
            "vlm_metrics": repairer.metrics,
            "warnings": repairer.warnings,
        },
    )
    print(f"NMCK lab completed: {args.output_dir}")
    print(f"Documents: {len(summaries)}; skipped: {len(skipped)}")
    return 0


def _discover_nmck_documents(
    root: Path,
    classifier: DocumentClassifier,
) -> tuple[list[Path], list[dict[str, str]]]:
    selected: list[Path] = []
    skipped: list[dict[str, str]] = []
    for path in sorted(root.rglob("*.docx")):
        if path.name.startswith("~$"):
            continue
        try:
            decision = classifier.classify(read_docx(path))
        except Exception as error:
            skipped.append({"file": str(path), "reason": f"read failed: {error}"})
            continue
        if decision.document_type == DocumentType.ONMCK:
            selected.append(path)
        elif any(marker in path.stem.casefold() for marker in ("онмцк", "нмцк", "оцк")):
            skipped.append(
                {
                    "file": str(path),
                    "reason": f"name suggests NMCK but classifier returned {decision.document_type.value}",
                }
            )
    return selected, skipped


def _summary(
    root: Path,
    path: Path,
    package: Any,
    checks: list[Any],
) -> dict[str, Any]:
    onmck = package.nmck_justification
    return {
        "file": str(path.relative_to(root)),
        "items": len(onmck.items) if onmck else 0,
        "price_sources": len(onmck.price_sources) if onmck else 0,
        "totals": len(onmck.totals) if onmck else 0,
        "stages": len(onmck.stages) if onmck else 0,
        "total_amount": str(onmck.total_amount.amount) if onmck and onmck.total_amount else None,
        "table_types": [table.get("table_type") for table in package.tables],
        "checks": {result.check_id: result.status for result in checks},
        "warnings": list(getattr(onmck, "parser_warnings", []) or []) if onmck else [],
    }


def _artifact_name(root: Path, path: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    return "__".join(relative.parts)


def _write_json(path: Path, value: Any) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif isinstance(value, list):
        value = [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in value]
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
