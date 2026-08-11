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
    _merge_role_result,
    _parse_response,
    _supplier_source_id,
    _unextracted_justification_table,
)
from summary_model.vlm_lab.candidates import justification_candidate_reasons
from summary_model.checks.additional_characteristics import build_assessments, justification_state
from summary_model.extraction_models import AdditionalCharacteristicsJustification


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


def test_nmck_legacy_total_object_is_recovered_as_structured_summary():
    content = {
        "table_role": "nmck_calculation",
        "nmck_items": [{"row_number": "1", "name": "Моноблок", "quantity_raw": "12"}],
        "totals": [{
            "label": "Итого",
            "unit": "шт.",
            "quantity_raw": "11",
            "supplier_totals_raw": ["1 661 000,00", "1 647 800,00", "1 652 200,00"],
            "nmck_total_raw": "1 647 800,00",
        }],
    }
    response = {"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]}

    extraction, recovery = _parse_response(response, role="nmck_calculation", table_title="ОНМЦК")

    assert recovery.lossy_warnings == []
    assert extraction.nmck_totals[0].quantity_raw == "11"
    assert extraction.nmck_totals[0].nmck_total_raw == "1 647 800,00"


def test_nmck_vlm_merge_fills_only_missing_deterministic_fields():
    base = ParsedTable(
        table_id="table-1",
        block_id="block-1",
        table_index=1,
        table_type="nmck_calculation_table",
        row_count=2,
        col_count=4,
        compact_json={
            "price_sources": [{"source_id": "supplier_1", "raw_header": "Поставщик 1"}],
            "items": [{
                "row_index": 3,
                "row_number": "1",
                "name": "Моноблок",
                "unit": "шт.",
                "quantity_raw": None,
                "supplier_prices": [{"source_id": "supplier_1", "raw_unit_price": "149 800,00"}],
                "selected_min_unit_price_raw": "149 800,00",
                "row_total_declared_raw": "1 647 800,00",
            }],
        },
    )
    repaired = base.model_copy(deep=True)
    repaired.compact_json = {
        "items": [{
            "row_index": 3,
            "row_number": "1",
            "name": "Моноблок",
            "unit": "шт.",
            "quantity_raw": "12",
            "supplier_prices": [{"source_id": "supplier_1", "raw_unit_price": "149 800,00"}],
            "selected_min_unit_price_raw": None,
            "row_total_declared_raw": None,
        }],
        "nmck_totals": [{"label": "Итого", "quantity_raw": "11"}],
    }

    merged = _merge_role_result(base, repaired, "nmck_calculation")
    item = merged.compact_json["items"][0]

    assert item["quantity_raw"] == "12"
    assert item["selected_min_unit_price_raw"] == "149 800,00"
    assert item["row_total_declared_raw"] == "1 647 800,00"
    assert merged.compact_json["nmck_totals"][0]["quantity_raw"] == "11"


def test_nmck_vlm_result_keeps_explicit_stages_from_direct_rows():
    base = ParsedTable(
        table_id="table-1",
        block_id="block-1",
        table_index=1,
        table_type="nmck_calculation_table",
        row_count=2,
        col_count=4,
        compact_json={"items": [], "price_sources": []},
    )
    repaired = base.model_copy(deep=True)
    repaired.compact_json = {
        "items": [{"row_number": "1", "name": "Услуга (1 этап)", "quantity_raw": "1"}],
        "stages": [{
            "stage_number": "1",
            "stage_name": "Услуга (1 этап)",
            "service_term_text": "с 01.01.2026 по 10.01.2026",
            "price_raw": "45 000,00",
        }],
    }

    merged = _merge_role_result(base, repaired, "nmck_calculation")

    assert merged.compact_json["stages"][0]["stage_number"] == "1"


def test_staged_nmck_vlm_result_replaces_incomplete_deterministic_rows():
    base = ParsedTable(
        table_id="table-1",
        block_id="block-1",
        table_index=1,
        table_type="nmck_staged_calculation_table",
        row_count=2,
        col_count=4,
        compact_json={"price_sources": [{
            "source_id": "supplier_1",
            "raw_header": "Исполнитель 1 (письмо № К-056 от 03.08.2026 г.)",
        }], "items": [{
            "row_index": 2,
            "row_number": "1",
            "supplier_prices": [{"source_id": "supplier_1", "raw_unit_price": "4 790", "raw_row_total": "4 700"}],
        }]},
    )
    repaired = base.model_copy(deep=True)
    repaired.compact_json = {"items": [{
        "row_index": 2,
        "row_number": "1",
        "supplier_prices": [{"source_id": "supplier_1", "raw_unit_price": "4 700"}],
        "selected_min_unit_price_raw": "4 700",
        "row_total_declared_raw": "4 700",
    }], "stages": [{"stage_number": "1", "price_raw": "4 700"}]}

    merged = _merge_role_result(base, repaired, "nmck_calculation")

    assert merged.compact_json["items"] == repaired.compact_json["items"]
    assert merged.compact_json["stages"] == repaired.compact_json["stages"]
    assert merged.compact_json["price_sources"] == base.compact_json["price_sources"]


def test_nmck_supplier_id_ignores_letter_number_and_date():
    assert _supplier_source_id("Исполнитель 2 (письмо № 280426/10 от 28.04.2026)", 2) == "supplier_2"


def test_nmck_staged_merge_uses_full_vlm_result():
    base = ParsedTable(
        table_id="table-1",
        block_id="block-1",
        table_index=1,
        table_type="nmck_staged_calculation_table",
        row_count=2,
        col_count=4,
        compact_json={"items": [{
            "row_index": 3,
            "row_number": "2.1",
            "parent_stage_number": "2",
            "name": "Сервер",
            "quantity_raw": None,
        }]},
    )
    repaired = base.model_copy(deep=True)
    repaired.compact_json = {"items": [
        {
            "row_index": 3,
            "row_number": "2.1",
            "parent_stage_number": "2",
            "name": "Сервер",
            "quantity_raw": "4",
        },
        {
            "row_index": 4,
            "row_number": "2",
            "name": "Поставка оборудования (2 этап)",
            "row_total_declared_raw": "106 212 006,00",
        },
    ]}

    merged = _merge_role_result(base, repaired, "nmck_calculation")

    assert merged.compact_json == repaired.compact_json


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


def test_mixed_item_and_justification_table_runs_both_roles_without_data_loss(
    monkeypatch,
):
    import summary_model.vlm_fallback as fallback_module

    parsed, source = _table("Таблица характеристик и обоснований")
    parsed.table_type = "ooz_items_table"
    parsed.compact_json = {
        "items": [
            {"row_number": "1", "name": "Программное обеспечение"},
            {
                "row_number": "2",
                "name": "Программное обеспечение (тип №2)",
                "okpd2_code": "58.29.32.000",
                "ktru_code": "58.29.11.000-00000003",
            },
        ]
    }
    source.context_before = ["Обоснование применения дополнительных характеристик"]
    ir = DocumentIR(
        document_id="doc-mixed",
        file_name="ooz-mixed.docx",
        media_type="docx",
        blocks=[DocumentBlockIR(block_id="block-1", order=1, type="table", table=source)],
    )
    roles = []

    def fake_render(_table, output_path, **_kwargs):
        Path(output_path).write_bytes(b"png")
        return {"path": str(output_path), "width": 100, "height": 400, "rows": 2, "columns": 2}

    def fake_call(**kwargs):
        role = kwargs["role"]
        roles.append(role)
        if role == "purchase_description":
            content = {
                "table_role": role,
                "items": [{
                    "row_number": "1",
                    "name": "Программное обеспечение",
                    "ktru_code": "58.29.11.000-00000003",
                    "trademark": "Example",
                }],
            }
        else:
            assert kwargs["payload"]["known_items"][0]["name"] == "Программное обеспечение"
            content = {
                "table_role": role,
                "justifications": [{
                    "item_name": "Программное обеспечение",
                    "item_row_number": "1",
                    "characteristic_names": ["Централизованное управление"],
                    "justification_text": "Необходимо для управления кластером.",
                }],
            }
        return {"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]}

    monkeypatch.setattr(fallback_module, "render_table_image", fake_render)
    monkeypatch.setattr(fallback_module, "_call_vlm", fake_call)
    output_dir = Path("runtime") / f"summary_model_vlm_mixed_{uuid4().hex}"
    output_dir.mkdir(parents=True)
    try:
        repairer = VlmFallbackRepairer(VlmFallbackOptions(enabled=True, output_dir=output_dir))
        result = repairer.repair_document_tables(ir, DocumentType.OOZ, [parsed])[0]
        artifact_dirs = {path.name for path in (output_dir / "ooz-mixed.docx").iterdir()}
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)

    assert roles == ["purchase_description", "additional_characteristics_justification"]
    assert result.table_type == "ooz_items_table"
    assert result.compact_json["items"][0]["ktru_code"] == "58.29.11.000-00000003"
    assert result.compact_json["items"][0]["trademark"] == "Example"
    assert result.compact_json["items"][1]["name"] == "Программное обеспечение (тип №2)"
    assert result.compact_json["items"][1]["okpd2_code"] == "58.29.32.000"
    assert result.compact_json["additional_characteristics_justifications"][0]["item_row_number"] == "1"
    assert artifact_dirs == {
        "table_1_purchase_description",
        "table_1_additional_characteristics_justification",
    }


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


def test_justification_is_linked_to_matching_item_only():
    records = [
        AdditionalCharacteristicsJustification(
            item_ktru_code="58.29.11.000-00000003",
            characteristic_names=["Централизованное управление"],
            justification_text="Необходимо для управления кластером.",
        )
    ]
    rows = [
        {
            "item_name": "ПО",
            "ktru_code": "58.29.11.000-00000003",
            "characteristic_name": "Централизованное управление",
            "status": "passed",
        },
        {
            "item_name": "Сервер",
            "ktru_code": "26.20.14.000-00000189",
            "characteristic_name": "Количество портов",
            "status": "passed",
        },
    ]

    assessments = build_assessments(
        rows,
        ooz_state=justification_state(records),
        records=records,
    )

    assert assessments[0]["justification"]["status"] == "found"
    assert assessments[1]["justification"]["status"] == "missing"
