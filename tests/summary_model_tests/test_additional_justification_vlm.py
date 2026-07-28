import json
import shutil
from pathlib import Path
from uuid import uuid4

from summary_model.domain.models import (
    DocumentBlockIR,
    DocumentIR,
    DocumentType,
    TableColumnIR,
    TableIR,
    TableRowIR,
)
from summary_model.tables.models import HeaderPath, ParsedTable
from summary_model.ingestion import read_docx
from summary_model.tables import extract_tables
from summary_model.vlm_fallback import (
    VlmFallbackOptions,
    VlmFallbackRepairer,
    _parse_response,
    _unextracted_justification_table,
)
from summary_model.vlm_lab.candidates import justification_candidate_reasons


def _table(title: str = "Обоснование дополнительных характеристик") -> tuple[ParsedTable, TableIR]:
    headers = [["Дополнительная информация"], ["Обоснование"]]
    parsed = ParsedTable(
        table_id="table-1",
        block_id="block-1",
        table_index=1,
        table_type="generic_table",
        row_count=2,
        col_count=2,
        title=title,
        header_paths=[HeaderPath(col_index=index, parts=parts) for index, parts in enumerate(headers)],
    )
    source = TableIR(
        table_id="table-1",
        title=title,
        row_count=2,
        columns=[
            TableColumnIR(index=index, alias=f"c{index}", header_path=parts)
            for index, parts in enumerate(headers)
        ],
        rows=[TableRowIR(row_id="r0", row=0, values={"c0": "Функция", "c1": "Причина"})],
    )
    return parsed, source


def test_candidate_requires_strong_justification_signals():
    parsed, source = _table()
    assert justification_candidate_reasons(parsed, source)

    parsed.title = source.title = "Обоснование начальной максимальной цены контракта"
    parsed.header_paths = [HeaderPath(col_index=0, parts=["Цена"])]
    source.columns[0].header_path = ["Цена"]
    source.columns[1].header_path = ["Поставщик"]
    assert not justification_candidate_reasons(parsed, source)


def test_real_ooz_fixtures_find_justification_tables():
    paths = [
        Path("doci_primery/закупка_для_примера_расширение_ЦОД_с_лицензиями/2. Описание объекта закупки_2.docx"),
        Path("doci_primery/Данные для тестирования 01.06.26/3. ООЗ_Лицензии_на_ТД.docx"),
    ]
    for path in paths:
        ir = read_docx(path)
        tables = extract_tables(ir, DocumentType.OOZ)
        source_by_id = {
            block.table.table_id: block.table
            for block in ir.blocks
            if block.table is not None
        }
        candidates = [
            table
            for table in tables
            if justification_candidate_reasons(table, source_by_id.get(table.table_id))
        ]
        assert len(candidates) == 1, path


def test_recovery_keeps_valid_justification_rows():
    content = {
        "table_role": "additional_characteristics_justification",
        "justifications": [
            {
                "characteristic_names": "Централизованное управление",
                "justification_text": "Обусловлено подключением к кластеру",
            },
            "поврежденная строка",
            None,
        ],
    }
    response = {"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]}

    extraction, recovery = _parse_response(
        response,
        role="additional_characteristics_justification",
        table_title="Обоснование",
    )

    assert len(extraction.justifications) == 1
    assert extraction.justifications[0].characteristic_names == ["Централизованное управление"]
    assert recovery.status in {"recovered", "partial"}


def test_vlm_response_with_missing_final_brackets_is_recovered():
    response = {
        "choices": [{
            "message": {
                "content": '{"table_role":"purchase_description","items":[{"name":"Сервер"}'
            }
        }]
    }

    extraction, _recovery = _parse_response(
        response,
        role="purchase_description",
        table_title="Требования к серверу",
    )

    assert extraction.items[0].name == "Сервер"


def test_table_is_rendered_and_sent_to_vlm_once(monkeypatch):
    import summary_model.vlm_fallback as fallback_module

    parsed, source = _table()
    ir = DocumentIR(
        document_id="doc-1",
        file_name="ooz.docx",
        media_type="docx",
        blocks=[DocumentBlockIR(block_id="block-1", order=1, type="table", table=source)],
    )
    calls = {"render": 0, "vlm": 0}

    def fake_render(_table, output_path, **_kwargs):
        calls["render"] += 1
        Path(output_path).write_bytes(b"png")
        return {"path": str(output_path), "width": 100, "height": 400, "rows": 2, "columns": 2}

    def fake_call(**_kwargs):
        calls["vlm"] += 1
        content = {
            "table_role": "additional_characteristics_justification",
            "justifications": [{"characteristic_names": ["Функция"], "justification_text": "Причина"}],
        }
        return {"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]}

    monkeypatch.setattr(fallback_module, "render_table_image", fake_render)
    monkeypatch.setattr(fallback_module, "_call_vlm", fake_call)
    output_dir = Path("runtime") / f"summary_model_vlm_justification_{uuid4().hex}"
    output_dir.mkdir(parents=True)
    try:
        repairer = VlmFallbackRepairer(VlmFallbackOptions(enabled=True, output_dir=output_dir))
        first = repairer.repair_document_tables(ir, DocumentType.OOZ, [parsed])
        second = repairer.repair_document_tables(ir, DocumentType.OOZ, [parsed])
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)

    assert first[0].table_type == "additional_characteristics_justification_table"
    assert second[0].compact_json == first[0].compact_json
    assert calls == {"render": 1, "vlm": 1}


def test_failed_vlm_preserves_justification_candidate():
    parsed, _source = _table()
    recovered = _unextracted_justification_table(parsed)

    assert recovered.table_type == "additional_characteristics_justification_table"
    assert recovered.compact_json["justification_extraction"]["status"] == "contents_not_extracted"


def test_non_ooz_tables_never_use_justification_vlm_role(monkeypatch):
    import summary_model.vlm_fallback as fallback_module

    parsed, source = _table()
    ir = DocumentIR(
        document_id="request-1",
        file_name="request.docx",
        media_type="docx",
        blocks=[DocumentBlockIR(block_id="block-1", order=1, type="table", table=source)],
    )
    calls = []
    monkeypatch.setattr(fallback_module, "_call_vlm", lambda **kwargs: calls.append(kwargs))

    for document_type in (DocumentType.REQUEST, DocumentType.ONMCK, DocumentType.CONTRACT):
        repairer = VlmFallbackRepairer(VlmFallbackOptions(enabled=True))
        result = repairer.repair_document_tables(ir, document_type, [parsed])
        assert result[0].table_type == "generic_table"
    assert calls == []
