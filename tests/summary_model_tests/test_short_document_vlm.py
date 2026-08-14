from __future__ import annotations

import json
import asyncio

from summary_model.domain.models import DocumentType, InputDocument
from summary_model.extraction_models import ExplanatoryNoteSchema, PurchaseRequestSchema
from summary_model.extraction_models import ProcurementPackageExtraction
from summary_model.short_document_vlm import (
    ShortDocumentVlmResult,
    extract_short_document_with_vlm,
)
from summary_model.web_service import _split_input_documents
from summary_model import web_service


def test_short_pdf_vlm_extracts_request_into_existing_schema(tmp_path, monkeypatch):
    pdf = tmp_path / "request.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.setattr(
        "summary_model.short_document_vlm._document_images",
        lambda *_args, **_kwargs: [{"page": 1, "mime": "image/png", "data": b"page", "text": ""}],
    )
    payload = {
        "request_number": "42",
        "request_date": "04.08.2026",
        "purchase_subject": "Поставка оборудования",
        "nmck": {"raw": "1 200 000,50 руб.", "amount": "1 200 000,50"},
        "procurement_method_raw": "Электронный аукцион",
        "attachments": [],
    }
    monkeypatch.setattr(
        "summary_model.short_document_vlm._call_vlm",
        lambda *_args, **_kwargs: {"choices": [{"message": {"content": json.dumps(payload)}}]},
    )

    result = extract_short_document_with_vlm(pdf, DocumentType.REQUEST)

    assert isinstance(result.document, PurchaseRequestSchema)
    assert result.document.request_number == "42"
    assert result.document.request_date.isoformat() == "2026-08-04"
    assert str(result.document.nmck.amount) == "1200000.50"


def test_short_pdf_vlm_extracts_explanatory_note_and_web_split_keeps_only_allowed_types(tmp_path, monkeypatch):
    pdf = tmp_path / "note.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.setattr(
        "summary_model.short_document_vlm._document_images",
        lambda *_args, **_kwargs: [{"page": 1, "mime": "image/png", "data": b"page", "text": ""}],
    )
    payload = {
        "subject": "Оказание услуг",
        "nmck": {"raw": "500 000 руб.", "amount": "500 000"},
        "justification_text": "Необходимость подтверждена потребностью заказчика.",
    }
    monkeypatch.setattr(
        "summary_model.short_document_vlm._call_vlm",
        lambda *_args, **_kwargs: {"choices": [{"message": {"content": json.dumps(payload)}}]},
    )

    result = extract_short_document_with_vlm(pdf, DocumentType.EXPLANATORY_NOTE)

    assert isinstance(result.document, ExplanatoryNoteSchema)
    assert result.document.justification_text.startswith("Необходимость")

    documents = [
        InputDocument(path=tmp_path / "request.pdf", type_hint=DocumentType.REQUEST),
        InputDocument(path=tmp_path / "note.pdf", type_hint=DocumentType.EXPLANATORY_NOTE),
        InputDocument(path=tmp_path / "onmck.pdf", type_hint=DocumentType.ONMCK),
    ]
    docx, offers, short_documents, unsupported = _split_input_documents(documents)

    assert docx == []
    assert offers == []
    assert [item.type_hint for item in short_documents] == [
        DocumentType.REQUEST,
        DocumentType.EXPLANATORY_NOTE,
    ]
    assert [item.type_hint for item in unsupported] == [DocumentType.ONMCK]


def test_web_pipeline_assigns_pdf_explanatory_note_before_checks(tmp_path, monkeypatch):
    pdf = tmp_path / "Пояснительная записка.pdf"
    pdf.write_bytes(b"%PDF")
    package = ProcurementPackageExtraction(package_id="pdf-note")
    note = ExplanatoryNoteSchema(subject="Оказание услуг", justification_text="Обоснование найдено.")

    monkeypatch.setattr(web_service, "extract_package", lambda *_args, **_kwargs: package)
    monkeypatch.setattr(
        web_service,
        "extract_short_document_with_vlm",
        lambda *_args, **_kwargs: ShortDocumentVlmResult(note, {"enabled": True, "calls": 1}),
    )

    result = asyncio.run(
        web_service._aprocess_uploaded_documents(
            [{"key": "zapiska", "name": pdf.name, "path": str(pdf)}],
            options=web_service.WebPipelineOptions(
                with_llm_extraction=False,
                with_semantic_llm=False,
                with_ktru=False,
            ),
        )
    )

    assert result.package.explanatory_note is note
    assert result.metrics["short_document_vlm"][0]["file_name"] == pdf.name
