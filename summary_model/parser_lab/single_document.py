from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from summary_model.classification import DocumentClassifier
from summary_model.domain.models import DocumentType, InputDocument
from summary_model.extraction.llm_payloads import build_document_llm_payload
from summary_model.extraction_pipeline import extract_package
from summary_model.ingestion import read_docx
from summary_model.tables import export_table_debug, extract_tables


def _safe_name(value: str) -> str:
    value = re.sub(r"[^\w.\-]+", "_", value, flags=re.UNICODE).strip("_")
    return value[:120] or "document"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, BaseModel):
        path.write_text(value.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
        return
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _schema_for_document_type(package, document_type: DocumentType):
    if document_type == DocumentType.PLAN:
        return package.schedule_application
    if document_type == DocumentType.REQUEST:
        return package.purchase_request
    if document_type == DocumentType.ONMCK:
        return package.nmck_justification
    if document_type == DocumentType.OOZ:
        return package.purchase_description
    if document_type == DocumentType.CONTRACT:
        return package.contract_draft
    if document_type == DocumentType.EXPLANATORY_NOTE:
        return package.explanatory_note
    if document_type == DocumentType.COMMERCIAL_OFFER:
        return next(iter(package.commercial_offers), None)
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parse one procurement document with the same deterministic layer as extraction_cli."
    )
    parser.add_argument("--input", required=True, type=Path, help="Path to one .docx file.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("runtime/parser_lab"),
        help="Root directory for parser artifacts.",
    )
    parser.add_argument(
        "--type-hint",
        choices=[item.value for item in DocumentType],
        help="Optional document type hint, for example: plan, ooz, contract.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.input.exists():
        raise SystemExit(f"Input file not found: {args.input}")
    if args.input.suffix.casefold() != ".docx":
        raise SystemExit("single_document parser currently supports .docx only.")

    type_hint = DocumentType(args.type_hint) if args.type_hint else None
    document = InputDocument(
        path=args.input,
        type_hint=type_hint,
        display_name=args.input.name,
    )

    ir = read_docx(args.input)
    decision = DocumentClassifier().classify(ir, type_hint)
    tables = extract_tables(ir, decision.document_type)
    package = extract_package([document])
    schema = _schema_for_document_type(package, decision.document_type)
    llm_payload = build_document_llm_payload(
        ir=ir,
        document_type=decision.document_type,
        tables=tables,
        deterministic_schema=schema,
    )

    target = args.output_dir / _safe_name(args.input.stem)
    target.mkdir(parents=True, exist_ok=True)
    _write_json(target / "document_ir.json", ir)
    _write_json(target / "classification.json", decision)
    _write_json(target / "schema.json", schema or {})
    _write_json(target / "parsed_tables.json", [table.model_dump(mode="json") for table in tables])
    _write_json(target / "llm_payload.json", llm_payload)
    for table in tables:
        _write_json(target / "tables" / f"table_{table.table_index}.json", table)
    export_table_debug(target / "debug", ir, tables)
    _write_json(
        target / "run.json",
        {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "input_file": str(args.input),
            "document_type": decision.document_type.value,
            "classification_confidence": decision.confidence,
            "tables_count": len(tables),
            "table_types": [table.table_type for table in tables],
            "artifacts": [
                "document_ir.json",
                "classification.json",
                "schema.json",
                "parsed_tables.json",
                "llm_payload.json",
                "tables/table_N.json",
                "debug/tables/<file>/table_N_physical.md",
                "debug/tables/<file>/table_N_logical.json",
                "debug/tables/<file>/table_N_compact.md",
            ],
        },
    )
    print(f"Wrote parser artifacts to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
