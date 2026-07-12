import json
import asyncio
from pathlib import Path
from types import SimpleNamespace

from summary_model.domain.models import DocumentType, InputDocument
from summary_model import web_service
from summary_model.extraction.llm_document_extractor import (
    SCHEMA_BY_DOCUMENT_TYPE,
    aextract_document_schema_with_llm,
    extract_document_schema_with_llm,
)
from summary_model.extraction.llm_client import StructuredLLMClient
from summary_model.extraction.llm_payloads import build_document_llm_payload
from summary_model.extraction.llm_prompts import prompt_for_document_type
from summary_model.extraction_pipeline import extract_package
from summary_model.extraction_models import (
    ContractDraftSchema,
    ExplanatoryNoteSchema,
    NmckJustificationSchema,
    ProcurementPackageExtraction,
    PurchaseDescriptionSchema,
    PurchaseItem,
    PurchaseRequestSchema,
    RawField,
    ScheduleApplicationSchema,
)
from summary_model.ingestion import read_docx
from summary_model.tables import extract_tables

from tests.summary_model_tests.test_extraction_layer import _save_plan


class FakeLLMClient:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    def extract(self, schema, system_prompt, payload):
        self.calls.append(
            {
                "schema": schema,
                "system_prompt": system_prompt,
                "payload": payload,
            }
        )
        if self.fail:
            return None, "fake LLM failure"
        return schema(
            document_title="LLM title",
            purchase_subject="LLM subject",
            delivery_term_text="15 working days",
        ), None

    def metrics(self):
        return {"calls": len(self.calls)}


class AsyncFakeLLMClient(FakeLLMClient):
    async def aextract(self, schema, system_prompt, payload):
        return self.extract(schema, system_prompt, payload)


class FakeStructuredModel:
    model_name = "fake-model"
    max_tokens = 123

    def with_structured_output(self, schema, method):
        class Runner:
            def invoke(self, prompt):
                return schema(purchase_subject="Поставка картриджей")

        return Runner()


class FakeOozLLMClient:
    def extract(self, schema, system_prompt, payload):
        return PurchaseDescriptionSchema(
            items=[
                PurchaseItem(
                    row_number="1",
                    name="Компрессор автомобильный",
                )
            ]
        ), None


def test_document_type_selects_prompt_and_schema():
    assert SCHEMA_BY_DOCUMENT_TYPE[DocumentType.PLAN].__name__ == "ScheduleApplicationSchema"
    assert SCHEMA_BY_DOCUMENT_TYPE[DocumentType.CONTRACT].__name__ == "ContractDraftSchema"
    assert "known_extracted" in prompt_for_document_type(DocumentType.PLAN)
    assert "specification_items" in prompt_for_document_type(DocumentType.CONTRACT)


def test_llm_payload_contains_text_tables_and_known_extracted(tmp_path):
    path = tmp_path / "plan.docx"
    _save_plan(path)
    package = extract_package(
        [InputDocument(path=path, type_hint=DocumentType.PLAN, display_name="plan")]
    )
    ir = read_docx(path)
    tables = extract_tables(ir, DocumentType.PLAN)

    payload = build_document_llm_payload(
        ir=ir,
        document_type=DocumentType.PLAN,
        tables=tables,
        deterministic_schema=package.schedule_application,
    )

    assert payload["plain_text_blocks"]
    assert payload["known_extracted"]["document_type"] == "schedule_application"
    assert [table["table_type"] for table in payload["tables"]] == [
        "schedule_application_table"
    ]
    assert "compact_json" not in payload["tables"][0]
    assert all(table["table_type"] not in {"signature_table", "ignored_table"} for table in payload["tables"])


def test_llm_result_restores_lost_deterministic_fields(tmp_path):
    path = tmp_path / "plan.docx"
    _save_plan(path)
    package = extract_package(
        [InputDocument(path=path, type_hint=DocumentType.PLAN, display_name="plan")]
    )
    ir = read_docx(path)
    tables = extract_tables(ir, DocumentType.PLAN)
    payload = build_document_llm_payload(
        ir=ir,
        document_type=DocumentType.PLAN,
        tables=tables,
        deterministic_schema=package.schedule_application,
    )

    result, error = extract_document_schema_with_llm(
        payload=payload,
        document_type=DocumentType.PLAN,
        deterministic_schema=package.schedule_application,
        llm_client=FakeLLMClient(),
    )

    assert error is None
    assert result.purchase_subject == "LLM subject"
    assert result.raw_fields == package.schedule_application.raw_fields
    assert result.raw_fields_dict == package.schedule_application.raw_fields_dict
    assert result.ktru_codes == package.schedule_application.ktru_codes
    assert any("deterministic" in warning for warning in result.parser_warnings)


def test_async_llm_result_restores_lost_deterministic_fields():
    deterministic = ScheduleApplicationSchema(
        raw_fields=[RawField(key="НМЦК", value="350000")],
        raw_fields_dict={"НМЦК": "350000"},
        ktru_codes=["22.11.11.000-00000007"],
    )

    result, error = asyncio.run(
        aextract_document_schema_with_llm(
            payload={"known_extracted": deterministic.model_dump(mode="json")},
            document_type=DocumentType.PLAN,
            deterministic_schema=deterministic,
            llm_client=AsyncFakeLLMClient(),
        )
    )

    assert error is None
    assert result.purchase_subject == "LLM subject"
    assert result.raw_fields == deterministic.raw_fields
    assert result.raw_fields_dict == deterministic.raw_fields_dict
    assert result.ktru_codes == deterministic.ktru_codes


def test_llm_result_restores_missing_item_fields_when_item_count_matches():
    deterministic = PurchaseDescriptionSchema(
        items=[
            PurchaseItem(
                row_number="1",
                name="Компрессор автомобильный",
                okpd2_code="28.13.28.190",
                unit="шт",
            )
        ]
    )

    result, error = extract_document_schema_with_llm(
        payload={"known_extracted": deterministic.model_dump(mode="json")},
        document_type=DocumentType.OOZ,
        deterministic_schema=deterministic,
        llm_client=FakeOozLLMClient(),
    )

    assert error is None
    assert result.items[0].okpd2_code == "28.13.28.190"
    assert result.items[0].unit == "шт"
    assert any("restored" in warning for warning in result.parser_warnings)


def test_llm_error_returns_deterministic_schema_with_warning(tmp_path):
    path = tmp_path / "plan.docx"
    _save_plan(path)
    package = extract_package(
        [InputDocument(path=path, type_hint=DocumentType.PLAN, display_name="plan")]
    )
    ir = read_docx(path)
    tables = extract_tables(ir, DocumentType.PLAN)
    payload = build_document_llm_payload(
        ir=ir,
        document_type=DocumentType.PLAN,
        tables=tables,
        deterministic_schema=package.schedule_application,
    )

    result, error = extract_document_schema_with_llm(
        payload=payload,
        document_type=DocumentType.PLAN,
        deterministic_schema=package.schedule_application,
        llm_client=FakeLLMClient(fail=True),
    )

    assert error == "fake LLM failure"
    assert result.purchase_subject == package.schedule_application.purchase_subject
    assert "fake LLM failure" in result.parser_warnings


def test_extraction_cli_with_mocked_llm_writes_llm_artifacts(tmp_path, monkeypatch):
    from summary_model import extraction_cli

    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    _save_plan(input_dir / "plan.docx")

    monkeypatch.setattr(extraction_cli, "StructuredLLMClient", FakeLLMClient)

    exit_code = extraction_cli.main(
        [
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--with-llm",
        ]
    )

    assert exit_code == 0
    assert (output_dir / "extraction_result.json").exists()
    assert (output_dir / "extraction_result.llm.json").exists()
    assert list((output_dir / "llm_documents").glob("*.json"))
    run = json.loads((output_dir / "run.json").read_text(encoding="utf-8"))
    assert run["llm"]["enabled"] is True
    assert run["llm"]["metrics"]["calls"] == 1
    llm_result = json.loads((output_dir / "extraction_result.llm.json").read_text(encoding="utf-8"))
    assert llm_result["schedule_application"]["purchase_subject"] == "LLM subject"
    assert llm_result["schedule_application"]["raw_fields"]


def test_sync_llm_client_records_metrics():
    client = StructuredLLMClient(model=FakeStructuredModel())

    result, error = client.extract(
        SCHEMA_BY_DOCUMENT_TYPE[DocumentType.PLAN],
        "test prompt",
        "test payload",
    )

    assert error is None
    assert result.purchase_subject == "Поставка картриджей"
    metrics = client.metrics()
    assert metrics["calls"] == 1
    assert metrics["input_characters"] > 0
    assert metrics["duration_seconds"] >= 0


def test_purchase_request_subject_is_cleaned_and_technical_restore_is_quiet(tmp_path):
    path = tmp_path / "request.docx"
    _save_plan(path)
    ir = read_docx(path)
    tables = extract_tables(ir, DocumentType.REQUEST)
    deterministic_schema = PurchaseRequestSchema()
    payload = build_document_llm_payload(
        ir=ir,
        document_type=DocumentType.REQUEST,
        tables=tables,
        deterministic_schema=deterministic_schema,
    )

    class RequestLLM(FakeLLMClient):
        def extract(self, schema, system_prompt, payload):
            return schema(
                purchase_subject=(
                    "Обращение о проведении закупки "
                    "(поставка шин пневматических и комплектующих для автомобилей)"
                )
            ), None

    result, error = extract_document_schema_with_llm(
        payload=payload,
        document_type=DocumentType.REQUEST,
        deterministic_schema=deterministic_schema,
        llm_client=RequestLLM(),
    )

    assert error is None
    assert result.purchase_subject == "Поставка шин пневматических и комплектующих для автомобилей"
    assert not any("deterministic values were restored" in warning for warning in result.parser_warnings)


def test_web_llm_extraction_uses_async_limit(monkeypatch):
    package = ProcurementPackageExtraction(
        schedule_application=ScheduleApplicationSchema(document_title="plan"),
        purchase_request=PurchaseRequestSchema(document_title="request"),
        nmck_justification=NmckJustificationSchema(document_title="onmck"),
        purchase_description=PurchaseDescriptionSchema(document_title="ooz"),
        contract_draft=ContractDraftSchema(document_title="contract"),
        explanatory_note=ExplanatoryNoteSchema(document_title="note"),
    )
    documents = [
        InputDocument(path=Path(f"doc-{index}.docx"), type_hint=document_type, display_name=str(document_type))
        for index, document_type in enumerate(
            [
                DocumentType.PLAN,
                DocumentType.REQUEST,
                DocumentType.ONMCK,
                DocumentType.OOZ,
                DocumentType.CONTRACT,
                DocumentType.EXPLANATORY_NOTE,
            ]
        )
    ]

    class FakeClassifier:
        def classify(self, ir, type_hint):
            return SimpleNamespace(document_type=type_hint)

    active_calls = 0
    max_active_calls = 0

    async def fake_aextract_document_schema_with_llm(**kwargs):
        nonlocal active_calls, max_active_calls
        active_calls += 1
        max_active_calls = max(max_active_calls, active_calls)
        await asyncio.sleep(0.01)
        active_calls -= 1
        return kwargs["deterministic_schema"], None

    monkeypatch.setattr(web_service, "DocumentClassifier", FakeClassifier)
    monkeypatch.setattr(web_service, "read_docx", lambda path: SimpleNamespace(file_name=Path(path).name))
    monkeypatch.setattr(web_service, "extract_tables", lambda ir, document_type: [])
    monkeypatch.setattr(web_service, "build_document_llm_payload", lambda **kwargs: {})
    monkeypatch.setattr(
        web_service,
        "aextract_document_schema_with_llm",
        fake_aextract_document_schema_with_llm,
    )

    warnings, metrics = asyncio.run(
        web_service._apply_llm_extraction(package, documents, concurrency=6)
    )

    assert warnings == []
    assert metrics["calls"] == 0
    assert max_active_calls == 6


def test_web_llm_extraction_keeps_package_on_failed_document(monkeypatch):
    package = ProcurementPackageExtraction(
        schedule_application=ScheduleApplicationSchema(document_title="old plan"),
        purchase_request=PurchaseRequestSchema(document_title="old request"),
    )
    documents = [
        InputDocument(path=Path("plan.docx"), type_hint=DocumentType.PLAN, display_name="plan"),
        InputDocument(path=Path("request.docx"), type_hint=DocumentType.REQUEST, display_name="request"),
    ]

    class FakeClassifier:
        def classify(self, ir, type_hint):
            return SimpleNamespace(document_type=type_hint)

    async def fake_aextract_document_schema_with_llm(**kwargs):
        if kwargs["document_type"] == DocumentType.PLAN:
            raise RuntimeError("planned failure")
        schema = kwargs["deterministic_schema"].model_copy(deep=True)
        schema.document_title = "updated request"
        return schema, None

    monkeypatch.setattr(web_service, "DocumentClassifier", FakeClassifier)
    monkeypatch.setattr(web_service, "read_docx", lambda path: SimpleNamespace(file_name=Path(path).name))
    monkeypatch.setattr(web_service, "extract_tables", lambda ir, document_type: [])
    monkeypatch.setattr(web_service, "build_document_llm_payload", lambda **kwargs: {})
    monkeypatch.setattr(
        web_service,
        "aextract_document_schema_with_llm",
        fake_aextract_document_schema_with_llm,
    )

    warnings, _metrics = asyncio.run(
        web_service._apply_llm_extraction(package, documents, concurrency=6)
    )

    assert any("planned failure" in warning for warning in warnings)
    assert package.schedule_application.document_title == "old plan"
    assert package.purchase_request.document_title == "updated request"
