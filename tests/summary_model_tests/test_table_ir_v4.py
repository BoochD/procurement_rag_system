import json
from pathlib import Path

from docx import Document

from summary_model.domain.models import DocumentIR
from summary_model.extraction.table_projection import render_table_for_llm
from summary_model.ingestion import document_ir_json, read_docx


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "doci_primery"
PACK = FIXTURES / "PACK_06_05"


def _clean(value: str) -> str:
    return " ".join((value or "").replace("\xa0", " ").split()).strip()


def _legacy_sparse_projection(table, block_id: str) -> str:
    alias_index = {column.alias: column.index for column in table.columns}
    rows = []
    for row in table.rows:
        payload = {
            "row": row.row,
            "values": {
                str(alias_index[alias]): value
                for alias, value in row.values.items()
            },
        }
        if row.spans:
            payload["spans"] = {
                str(alias_index[alias]): list(span)
                for alias, span in row.spans.items()
            }
        rows.append(payload)
    return (
        f"[TABLE block_id={block_id} table_id={table.table_id}]\n"
        + json.dumps(
            {
                "title": table.title,
                "header_rows": table.header_rows,
                "kind": table.kind,
                "rows": rows,
            },
            ensure_ascii=False,
        )
    )


def test_all_fixture_matrices_match_physical_docx_tables():
    files = [
        path
        for path in FIXTURES.rglob("*.docx")
        if not path.name.startswith("~$")
        and path.name != "analysis_result.docx"
    ]
    table_count = 0
    for path in files:
        source = Document(path)
        expected = []
        for table in source.tables:
            rows = [
                [_clean(cell.text) for cell in row.cells]
                for row in table.rows
            ]
            width = max((len(row) for row in rows), default=0)
            expected.append([row + [""] * (width - len(row)) for row in rows])
        ir = read_docx(path)
        actual = [block.table.matrix() for block in ir.blocks if block.table]
        assert actual == expected, path
        table_count += len(actual)
    assert len(files) == 30
    assert table_count == 64


def test_representative_header_paths_and_kinds():
    onmck = read_docx(
        PACK / "2_ОЦК_метод_сопостовимых_рыночных_цен_без_учета_ПП_1875_.docx"
    )
    onmck_table = next(block.table for block in onmck.blocks if block.table)
    assert onmck_table.header_rows == [0, 1, 2]
    assert onmck_table.kind == "supplier_matrix"
    assert onmck_table.columns[4].header_path[-2:] == [
        "Поставщик 1 (письмо № 37 от 06.04.2026)",
        "Цена за ед. товара",
    ]

    ooz = read_docx(PACK / "3. ООЗ автошины и комплектующие.docx")
    ooz_table = next(block.table for block in ooz.blocks if block.table)
    assert ooz_table.header_rows == [0, 1]
    assert ooz_table.kind == "characteristics"

    plan = read_docx(PACK / "1_Заявка_на_включение_в_план_график.docx")
    plan_table = next(block.table for block in plan.blocks if block.table)
    assert plan_table.header_rows == []
    assert plan_table.kind == "key_value"


def test_contract_artifact_is_compact_valid_json():
    ir = read_docx(
        PACK / "4_Проект_контракта_шины_и_комплектующие.docx"
    )
    artifact = document_ir_json(ir)
    restored = DocumentIR.model_validate_json(artifact)

    assert len(artifact.splitlines()) <= 500
    assert '"cells"' not in artifact
    assert '"flattened_headers"' not in artifact
    assert [block.table.matrix() for block in restored.blocks if block.table] == [
        block.table.matrix() for block in ir.blocks if block.table
    ]


def test_text_projection_is_readable_and_smaller_than_sparse_json():
    ir = read_docx(PACK / "3. ООЗ автошины и комплектующие.docx")
    block = next(block for block in ir.blocks if block.table)
    projection = render_table_for_llm(block.table, block_id=block.block_id)
    legacy = _legacy_sparse_projection(block.table, block.block_id)

    assert "HEADER L1:" in projection
    assert "HEADER L2:" in projection
    assert "SCOPE g1 rows=r2..r10:" in projection
    assert "ROW r2 scope=g1:" in projection
    assert "c3=Категория использования шины" in projection
    assert '"row"' not in projection
    assert '"column"' not in projection
    assert '"text"' not in projection
    assert len(projection) <= len(legacy) * 0.85
