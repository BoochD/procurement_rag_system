from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from shared_modules.llm_models import get_chatGPT_client, OPENAI_MODEL
from summary_model.classification import DocumentClassifier
from summary_model.domain.models import DocumentIR, DocumentType, TableIR
from summary_model.extraction.structured_recovery import recover_model
from summary_model.ingestion import read_docx
from summary_model.tables import ParsedTable, extract_tables
from summary_model.vlm_lab.candidates import rank_table_candidates, table_role
from summary_model.vlm_lab.models import VlmTableExtraction, VlmTableRole
from summary_model.vlm_lab.prompts import (
    VLM_TABLE_PROMPT_VERSION,
    vlm_table_prompt,
    vlm_user_context,
)
from summary_model.vlm_lab.table_image import render_table_image


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    ir = read_docx(input_path)
    document_type = _document_type(args.type_hint, ir)
    tables = extract_tables(ir, document_type)
    candidates = rank_table_candidates(
        tables,
        target_role=args.target_role,
        query=args.query,
    )
    _write_json(output_dir / "candidates.json", [candidate.model_dump(mode="json") for candidate in candidates])

    selected = _select_table(tables, candidates, args.table_index)
    source_table = _source_table(ir, selected.table_id)
    if source_table is None:
        raise RuntimeError(f"Source TableIR not found for {selected.table_id}")

    image_info = render_table_image(
        source_table,
        output_dir / f"table_{selected.table_index}.png",
        max_width=args.max_width,
        font_size=args.font_size,
    )
    role = table_role(selected)
    payload = _payload(
        ir=ir,
        document_type=document_type,
        table=selected,
        source_table=source_table,
        image_info=image_info,
    )
    prompt = vlm_table_prompt(role)
    _write_json(output_dir / "payload.json", payload)
    _write_text(output_dir / "prompt.txt", prompt)
    _write_json(output_dir / "schema.json", VlmTableExtraction.model_json_schema())

    if args.with_vlm:
        result = _call_vlm(
            image_path=Path(image_info["path"]),
            role=role,
            prompt=prompt,
            payload=payload,
            model=args.model or OPENAI_MODEL,
        )
        _write_json(output_dir / "vlm_raw.json", result)
        parsed, validation_error = _parse_vlm_result(
            result,
            role=role,
            table_title=selected.title,
        )
        if parsed is not None:
            _write_json(output_dir / "vlm_result.json", parsed.model_dump(mode="json", exclude_none=True))
        if validation_error:
            _write_text(output_dir / "vlm_validation_error.txt", validation_error)

    print(f"document_type={document_type.value}")
    print(f"selected table={selected.table_index} role={role} type={selected.table_type}")
    print(f"image={image_info['path']} {image_info['width']}x{image_info['height']}")
    print(f"artifacts={output_dir}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render one DOCX table and build a VLM parsing payload.")
    parser.add_argument("--input", required=True, help="Path to a .docx file.")
    parser.add_argument("--output-dir", default="runtime/vlm_lab/single_table")
    parser.add_argument(
        "--type-hint",
        choices=[item.value for item in DocumentType],
        default=None,
        help="Optional document type hint.",
    )
    parser.add_argument(
        "--target-role",
        choices=list(VlmTableRole.__args__),
        default=None,
        help="Expected role of the table to find.",
    )
    parser.add_argument("--query", default=None, help="Optional free-text table search query.")
    parser.add_argument("--table-index", type=int, default=None, help="Render this physical table index.")
    parser.add_argument("--max-width", type=int, default=2600)
    parser.add_argument("--font-size", type=int, default=18)
    parser.add_argument("--with-vlm", action="store_true", help="Call configured vision-capable OpenAI-compatible model.")
    parser.add_argument("--model", default=None, help="Override model only for this lab run.")
    return parser


def _document_type(type_hint: str | None, ir: DocumentIR) -> DocumentType:
    hint = DocumentType(type_hint) if type_hint else None
    return DocumentClassifier().classify(ir, hint).document_type


def _select_table(
    tables: list[ParsedTable],
    candidates,
    table_index: int | None,
) -> ParsedTable:
    if table_index is not None:
        for table in tables:
            if table.table_index == table_index:
                return table
        raise RuntimeError(f"Table index {table_index} not found.")
    if not candidates:
        raise RuntimeError("No non-ignored table candidates found.")
    selected_id = candidates[0].table_id
    return next(table for table in tables if table.table_id == selected_id)


def _source_table(ir: DocumentIR, table_id: str) -> TableIR | None:
    for block in ir.blocks:
        if block.table and block.table.table_id == table_id:
            return block.table
    return None


def _payload(
    *,
    ir: DocumentIR,
    document_type: DocumentType,
    table: ParsedTable,
    source_table: TableIR,
    image_info: dict[str, int | str],
) -> dict[str, Any]:
    return {
        "schema_version": "vlm-lab-payload-0.1.0",
        "prompt_version": VLM_TABLE_PROMPT_VERSION,
        "document": {
            "document_id": ir.document_id,
            "file_name": ir.file_name,
            "document_type": document_type.value,
        },
        "table": {
            "table_id": table.table_id,
            "block_id": table.block_id,
            "table_index": table.table_index,
            "table_type": table.table_type,
            "target_role": table_role(table),
            "title": table.title,
            "row_count": table.row_count,
            "col_count": table.col_count,
            "header_rows": table.header_rows,
            "headers": source_table.header_labels(),
            "parser_warnings": table.parser_warnings,
            "compact_json": table.compact_json,
        },
        "image": image_info,
        "expected_contract_tables": [
            "Описание объекта закупки / товарные позиции и характеристики",
            "Этапность оказания услуг / выполнения работ",
            "Спецификация с ценами и количеством, если заполнена",
            "Список приложений к контракту",
        ],
    }


def _call_vlm(
    *,
    image_path: Path,
    role: VlmTableRole,
    prompt: str,
    payload: dict[str, Any],
    model: str,
) -> dict[str, Any]:
    client = get_chatGPT_client()
    image_data = base64.b64encode(image_path.read_bytes()).decode("ascii")
    data_url = f"data:image/png;base64,{image_data}"
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": vlm_user_context()},
                    {
                        "type": "text",
                        "text": json.dumps(payload, ensure_ascii=False, default=str),
                    },
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "vlm_table_extraction",
                "schema": VlmTableExtraction.model_json_schema(),
                "strict": True,
            },
        },
    )
    return response.model_dump(mode="json")


def _message_content(response: dict[str, Any]) -> str:
    return response["choices"][0]["message"]["content"]


def _parse_vlm_result(
    response: dict[str, Any],
    *,
    role: VlmTableRole,
    table_title: str | None,
) -> tuple[VlmTableExtraction | None, str | None]:
    content = _message_content(response)
    try:
        data = json.loads(content)
    except json.JSONDecodeError as error:
        return None, f"VLM response is not valid JSON: {error}\n\n{content[:4000]}"
    if isinstance(data, dict):
        data.setdefault("table_role", role)
        if table_title:
            data.setdefault("table_title", table_title)
    recovery = recover_model(VlmTableExtraction, data)
    if isinstance(recovery.value, VlmTableExtraction):
        return recovery.value, None
    return None, recovery.error or "VLM response does not match VlmTableExtraction."


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
