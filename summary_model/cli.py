from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from summary_model.domain.models import DocumentIR, DocumentType, InputDocument
from summary_model.ingestion import document_ir_json, read_docx
from summary_model.reporting import build_report_docx_bytes
from summary_model.service import PipelineConfig, process_package


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        return
    load_dotenv(Path("web/.env"))


def _load_manifest(path: Path | None) -> dict[str, dict]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "documents" in payload:
        entries = payload["documents"]
        return {entry["file"]: entry for entry in entries}
    if isinstance(payload, dict):
        return {
            name: value if isinstance(value, dict) else {"type_hint": value}
            for name, value in payload.items()
        }
    raise ValueError("Manifest must be an object or contain a documents list.")


def _inputs(input_dir: Path, manifest: dict[str, dict]) -> list[InputDocument]:
    result = []
    for path in sorted(input_dir.glob("*.docx")):
        entry = manifest.get(path.name, {})
        raw_hint = entry.get("type_hint")
        result.append(
            InputDocument(
                path=path,
                type_hint=DocumentType(raw_hint) if raw_hint else None,
                display_name=entry.get("display_name") or path.name,
            )
        )
    return result


def _write_json(path: Path, value, *, compact_model: bool = False) -> None:
    if isinstance(value, BaseModel):
        if compact_model and isinstance(value, DocumentIR):
            path.write_text(document_ir_json(value), encoding="utf-8")
            return
        path.write_text(
            value.model_dump_json(
                indent=1 if compact_model else 2,
                exclude_none=compact_model,
                exclude_defaults=compact_model,
            ),
            encoding="utf-8",
        )
    else:
        if isinstance(value, list):
            value = [
                item.model_dump(mode="json") if isinstance(item, BaseModel) else item
                for item in value
            ]
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the independent summary pipeline.")
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--no-external", action="store_true")
    parser.add_argument("--no-live-ktru", action="store_true")
    parser.add_argument("--brief", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _load_env()
    manifest = _load_manifest(args.manifest)
    documents = _inputs(args.input_dir, manifest)
    if not documents:
        raise SystemExit(f"No DOCX files found in {args.input_dir}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    ir_dir = args.output_dir / "document_ir"
    summary_dir = args.output_dir / "document_summaries"
    ir_dir.mkdir(exist_ok=True)
    summary_dir.mkdir(exist_ok=True)

    for document in documents:
        ir = read_docx(document.path)
        _write_json(
            ir_dir / f"{ir.document_id}.json",
            ir,
            compact_model=True,
        )

    result = process_package(
        documents,
        PipelineConfig(
            use_llm=not args.no_llm,
            use_external_checks=not args.no_external,
            live_ktru=not args.no_live_ktru,
            detailed_report=not args.brief,
        ),
    )
    for summary in result.documents:
        _write_json(summary_dir / f"{summary.document_id}.json", summary)

    _write_json(args.output_dir / "package.json", result.package)
    _write_json(args.output_dir / "findings.json", result.findings)
    (args.output_dir / "report.txt").write_text(result.report_text, encoding="utf-8")
    (args.output_dir / "report.docx").write_bytes(
        build_report_docx_bytes(
            result.findings,
            package=result.package,
            detailed=not args.brief,
            document_labels=result.document_labels,
        )
    )
    _write_json(
        args.output_dir / "run.json",
        {
            "package_id": result.package_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": result.schema_version,
            "prompt_versions": result.prompt_versions,
            "input_files": [document.path.name for document in documents],
            "document_labels": result.document_labels,
            "warnings": result.warnings,
            "metrics": result.run_metrics,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
