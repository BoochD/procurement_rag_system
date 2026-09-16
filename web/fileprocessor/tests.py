from io import BytesIO
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from docx import Document

from summary_model.classification.upload_router import UploadRoute
from summary_model.classification.upload_router import route_upload_text
from summary_model.domain.models import DocumentType


def _docx_bytes(text: str) -> bytes:
    stream = BytesIO()
    document = Document()
    document.add_paragraph(text)
    document.save(stream)
    return stream.getvalue()


class UploadRoutingTests(TestCase):
    def test_algorithm_review_document_is_not_auto_assigned(self):
        route = route_upload_text(
            "Алгоритм от 15.09.2026 пояснения.docx",
            "Проект контракта Ответственность сторон",
        )

        assert route.target_field is None
        assert route.auto_assign is False

    def test_classify_uploads_returns_results_in_input_order(self):
        files = [
            SimpleUploadedFile(
                "3. заявка в ПГ.docx",
                _docx_bytes("Заявка на включение в план-график Код позиции КТРУ"),
            ),
            SimpleUploadedFile(
                "4. Пояснительная записка.docx",
                _docx_bytes("Пояснительная записка"),
            ),
        ]

        response = self.client.post(reverse("fileprocessor:classify_uploads"), {"files": files})

        assert response.status_code == 200
        payload = response.json()["files"]
        assert [item["name"] for item in payload] == [file.name for file in files]
        assert payload[0]["target_field"] == "plan"
        assert payload[1]["target_field"] == "zapiska"

    def test_classify_uploads_requires_files(self):
        response = self.client.post(reverse("fileprocessor:classify_uploads"))

        assert response.status_code == 400

    @patch("fileprocessor.views.route_upload")
    def test_classify_uploads_exposes_review_state(self, mocked_route):
        mocked_route.return_value = UploadRoute(
            document_type=DocumentType.OOZ,
            target_field="ooz",
            confidence=0.68,
            auto_assign=True,
            needs_review=True,
            reason="Проверьте распределение.",
        )
        file = SimpleUploadedFile("document.docx", _docx_bytes("Документ"))

        response = self.client.post(reverse("fileprocessor:classify_uploads"), {"files": [file]})

        assert response.status_code == 200
        result = response.json()["files"][0]
        assert result["target_field"] == "ooz"
        assert result["needs_review"] is True
