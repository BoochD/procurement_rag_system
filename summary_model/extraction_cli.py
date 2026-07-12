from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from summary_model.classification import DocumentClassifier
from summary_model.domain.models import DocumentType, InputDocument
from summary_model.extraction.llm_client import StructuredLLMClient
from summary_model.extraction.llm_document_extractor import (
    apply_llm_document_result,
    extract_document_schema_with_llm,
)
from summary_model.extraction.llm_payloads import build_document_llm_payload
from summary_model.extraction.llm_prompts import prompt_versions
from summary_model.extraction_pipeline import extract_package
from summary_model.ingestion import read_docx
from summary_model.tables import export_table_debug, extract_tables


def _load_manifest(path: Path | None) -> dict[str, dict]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "documents" in payload:
        return {entry["file"]: entry for entry in payload["documents"]}
    if isinstance(payload, dict):
        return {
            name: value if isinstance(value, dict) else {"type_hint": value}
            for name, value in payload.items()
        }
    raise ValueError("Manifest must be an object or contain a documents list.")


def _inputs(input_dir: Path, manifest: dict[str, dict]) -> list[InputDocument]:
    result = []
    for path in sorted(input_dir.glob("*.docx")):
        if path.name.startswith("~$"):
            continue
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


def _write_json(path: Path, value) -> None:
    if isinstance(value, BaseModel):
        path.write_text(
            value.model_dump_json(indent=2, exclude_none=True),
            encoding="utf-8",
        )
        return
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run typed procurement extraction without registry checks."
    )
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--with-llm",
        action="store_true",
        help="Run paid/live structured LLM extraction after deterministic parsing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = _load_manifest(args.manifest)
    documents = _inputs(args.input_dir, manifest)
    if not documents:
        raise SystemExit(f"No DOCX files found in {args.input_dir}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    documents_dir = args.output_dir / "documents"
    tables_dir = args.output_dir / "tables"
    llm_payloads_dir = args.output_dir / "llm_payloads"
    llm_documents_dir = args.output_dir / "llm_documents"
    debug_dir = args.output_dir / "debug"
    documents_dir.mkdir(exist_ok=True)
    tables_dir.mkdir(exist_ok=True)
    llm_payloads_dir.mkdir(exist_ok=True)
    if args.with_llm:
        llm_documents_dir.mkdir(exist_ok=True)
    debug_dir.mkdir(exist_ok=True)

    result = extract_package(documents)
    _write_json(args.output_dir / "extraction_result.json", result)
    classifier = DocumentClassifier()
    llm_client = StructuredLLMClient() if args.with_llm else None
    llm_errors: list[str] = []

    for document in documents:
        ir = read_docx(document.path)
        decision = classifier.classify(ir, document.type_hint)
        envelope = next(
            item for item in result.files
            if item.file_name == ir.file_name
        )
        document_tables = extract_tables(ir, decision.document_type)
        deterministic_schema = _schema_for_document_type(result, decision.document_type)
        llm_payload = build_document_llm_payload(
            ir=ir,
            document_type=decision.document_type,
            tables=document_tables,
            deterministic_schema=deterministic_schema,
        )
        _write_json(
            documents_dir / f"{ir.document_id}.json",
            {
                "document": envelope.model_dump(mode="json"),
                "tables": [table.model_dump(mode="json") for table in document_tables],
            },
        )
        for table in document_tables:
            _write_json(
                tables_dir / f"{ir.document_id}_table_{table.table_index}.json",
                table,
            )
        _write_json(
            llm_payloads_dir / f"{ir.document_id}.json",
            llm_payload,
        )
        if llm_client is not None:
            llm_schema, error = extract_document_schema_with_llm(
                payload=llm_payload,
                document_type=decision.document_type,
                deterministic_schema=deterministic_schema,
                llm_client=llm_client,
            )
            if error:
                llm_errors.append(f"{ir.file_name}: {error}")
            apply_llm_document_result(result, decision.document_type, llm_schema)
            _write_json(
                llm_documents_dir / f"{ir.document_id}.json",
                llm_schema.model_dump(mode="json", exclude_none=True) if llm_schema else {},
            )
        export_table_debug(debug_dir, ir, document_tables)

    if args.with_llm:
        _write_json(args.output_dir / "extraction_result.llm.json", result)

    run_payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": result.schema_version,
            "package_id": result.package_id,
            "input_files": [document.path.name for document in documents],
            "commercial_offers_found_count": result.commercial_offers_found_count,
            "commercial_offers_missing": result.commercial_offers_missing,
            "artifacts": [
                "extraction_result.json",
                "documents/*.json",
                "tables/*.json",
                "llm_payloads/*.json",
                "debug/tables/<file>/table_N_physical.md",
                "debug/tables/<file>/table_N_logical.json",
                "debug/tables/<file>/table_N_compact.md",
            ],
        }
    if args.with_llm:
        run_payload["artifacts"].extend(
            [
                "llm_documents/*.json",
                "extraction_result.llm.json",
            ]
        )
        run_payload["llm"] = {
            "enabled": True,
            "prompt_versions": prompt_versions(),
            "errors": llm_errors,
            "metrics": llm_client.metrics() if llm_client else {},
        }
    else:
        run_payload["llm"] = {"enabled": False}
    _write_json(args.output_dir / "run.json", run_payload)
    return 0


def _schema_for_document_type(result, document_type: DocumentType):
    if document_type == DocumentType.PLAN:
        return result.schedule_application
    if document_type == DocumentType.REQUEST:
        return result.purchase_request
    if document_type == DocumentType.ONMCK:
        return result.nmck_justification
    if document_type == DocumentType.OOZ:
        return result.purchase_description
    if document_type == DocumentType.CONTRACT:
        return result.contract_draft
    if document_type == DocumentType.EXPLANATORY_NOTE:
        return result.explanatory_note
    if document_type == DocumentType.COMMERCIAL_OFFER:
        return next(iter(result.commercial_offers), None)
    return None


if __name__ == "__main__":
    raise SystemExit(main())
