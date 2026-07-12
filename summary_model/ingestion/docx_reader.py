from __future__ import annotations

import hashlib
from pathlib import Path

from docx import Document
from docx.document import Document as DocumentObject
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P

from summary_model.domain.models import DocumentBlockIR, DocumentIR, TableIR
from .table_normalizer import build_rows, normalize_table


def _clean(value: str) -> str:
    return " ".join((value or "").replace("\xa0", " ").split()).strip()


def _document_id(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return f"doc-{digest}"


def _iter_blocks(document: DocumentObject):
    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            yield "paragraph", Paragraph(child, document)
        elif isinstance(child, CT_Tbl):
            yield "table", Table(child, document)


def _table_ir(table: Table, table_id: str) -> TableIR:
    rows: list[list[tuple[str, object]]] = []
    width = 0
    for row in table.rows:
        current = [(_clean(cell.text), cell._tc) for cell in row.cells]
        rows.append(current)
        width = max(width, len(current))
    for row in rows:
        while len(row) < width:
            row.append(("", object()))

    columns, table_rows = build_rows(rows, table_id)
    ir = TableIR(
        table_id=table_id,
        row_count=len(rows),
        columns=columns,
        rows=table_rows,
    )
    return normalize_table(ir)


def _attach_table_context(blocks: list[DocumentBlockIR], radius: int = 2) -> None:
    for index, block in enumerate(blocks):
        if block.type != "table" or block.table is None:
            continue
        before = [
            candidate.text
            for candidate in blocks[max(0, index - radius):index]
            if candidate.type == "paragraph" and candidate.text
        ]
        after = [
            candidate.text
            for candidate in blocks[index + 1:index + radius + 1]
            if candidate.type == "paragraph" and candidate.text
        ]
        block.table.context_before = before
        block.table.context_after = after
        title_candidates = [
            text for text in reversed(before)
            if "таблиц" in text.lower() or "приложен" in text.lower()
        ]
        block.table.title = title_candidates[0] if title_candidates else (before[-1] if before else None)


def read_docx(path: str | Path) -> DocumentIR:
    source = Path(path)
    if source.suffix.lower() != ".docx":
        raise ValueError(f"Only DOCX is supported: {source}")
    document = Document(source)
    document_id = _document_id(source)
    blocks: list[DocumentBlockIR] = []
    table_index = 0

    for order, (kind, item) in enumerate(_iter_blocks(document)):
        block_id = f"{document_id}-block-{order}"
        if kind == "paragraph":
            text = _clean(item.text)
            if not text:
                continue
            blocks.append(
                DocumentBlockIR(
                    block_id=block_id,
                    order=order,
                    type="paragraph",
                    text=text,
                )
            )
        else:
            table_index += 1
            blocks.append(
                DocumentBlockIR(
                    block_id=block_id,
                    order=order,
                    type="table",
                    table=_table_ir(item, f"{document_id}-table-{table_index}"),
                )
            )

    _attach_table_context(blocks)
    return DocumentIR(
        document_id=document_id,
        file_name=source.name,
        media_type="docx",
        blocks=blocks,
    )
