import os
import base64
import tempfile
import shutil
from io import BytesIO
import re
import html
from celery import shared_task
from docx import Document
from docx.shared import Pt, RGBColor
from summary_model.report_markup import mark_report_text
from summary_model.web_service import WebPipelineOptions, process_uploaded_documents


REQUIRED_DOCUMENTS = (
    ("plan", "Заявка в план-график"),
    ("contract", "Проект контракта"),
    ("ooz", "ООЗ"),
    ("zapiska", "Пояснительная записка"),
    ("onmck", "ОНМЦК"),
    ("obrasheniye", "Обращение о проведении закупки"),
)
MANDATORY_DOCUMENT_KEYS = {"plan"}


def build_result_docx_bytes(ai_response: str) -> bytes:
    """
    Собирает docx-файл из текстового ответа модели.

    Поддерживает базовое форматирование:
    `<b>...</b>` -> жирный, `<u>...</u>` и `<ins>...</ins>` -> подчёркивание,
    `<big>...</big>` -> увеличенный шрифт,
    `<ok>...</ok>` -> зелёный текст, `<warn>...</warn>` -> оранжевый текст,
    `<error>...</error>` -> красный текст.
    Абзацы создаются по пустым строкам.
    """
    document = Document()
    document.add_heading('Результат проверки документов', level=1)

    clean_response = (ai_response or '').replace('\r\n', '\n')
    blocks = [block.strip() for block in clean_response.split('\n\n') if block.strip()]
    tag_pattern = re.compile(r"</?(?:b|u|ins|ok|warn|error|big)>", re.IGNORECASE)

    for block in blocks:
        paragraph = document.add_paragraph()
        bold_active = False
        underline_active = False
        ok_active = False
        warn_active = False
        error_active = False
        big_active = False
        cursor = 0

        for match in tag_pattern.finditer(block):
            if match.start() > cursor:
                run = paragraph.add_run(html.unescape(block[cursor:match.start()]))
                run.bold = bold_active
                run.underline = underline_active
                if big_active:
                    run.font.size = Pt(14)
                if ok_active:
                    run.font.color.rgb = RGBColor(0x19, 0x87, 0x54)
                elif warn_active:
                    run.font.color.rgb = RGBColor(0xFD, 0x7E, 0x14)
                elif error_active:
                    run.font.color.rgb = RGBColor(0xDC, 0x35, 0x45)

            tag = match.group(0).lower()
            if tag == "<b>":
                bold_active = True
            elif tag == "</b>":
                bold_active = False
            elif tag in ("<u>", "<ins>"):
                underline_active = True
            elif tag in ("</u>", "</ins>"):
                underline_active = False
            elif tag == "<ok>":
                ok_active = True
            elif tag == "</ok>":
                ok_active = False
            elif tag == "<warn>":
                warn_active = True
            elif tag == "</warn>":
                warn_active = False
            elif tag == "<error>":
                error_active = True
            elif tag == "</error>":
                error_active = False
            elif tag == "<big>":
                big_active = True
            elif tag == "</big>":
                big_active = False

            cursor = match.end()

        if cursor < len(block):
            run = paragraph.add_run(html.unescape(block[cursor:]))
            run.bold = bold_active
            run.underline = underline_active
            if big_active:
                run.font.size = Pt(14)
            if ok_active:
                run.font.color.rgb = RGBColor(0x19, 0x87, 0x54)
            elif warn_active:
                run.font.color.rgb = RGBColor(0xFD, 0x7E, 0x14)
            elif error_active:
                run.font.color.rgb = RGBColor(0xDC, 0x35, 0x45)

    buffer = BytesIO()
    document.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()


@shared_task(bind=True, name='rag_worker.process_document_query')
def process_document_query(self, documents):
    try:
        if not isinstance(documents, list):
            raise ValueError("Expected a list of uploaded documents.")

        docs_by_key = {}
        for document in documents:
            doc_key = document.get('key')
            file_name = document.get('name')
            file_content_b64 = document.get('content_b64')

            if not doc_key or not file_name or not file_content_b64:
                raise ValueError("Each uploaded document must contain key, name and content_b64.")

            docs_by_key[doc_key] = {
                'name': file_name,
                'content': base64.b64decode(file_content_b64),
            }

        missing_docs = [
            label for key, label in REQUIRED_DOCUMENTS
            if key in MANDATORY_DOCUMENT_KEYS and key not in docs_by_key
        ]
        if missing_docs:
            raise ValueError(
                f"Missing required documents: {', '.join(missing_docs)}."
            )

        temp_dir = tempfile.mkdtemp()
        try:
            doc_paths = {}
            for key, _label in REQUIRED_DOCUMENTS:
                if key not in docs_by_key:
                    doc_paths[key] = None
                    continue

                file_name = os.path.basename(docs_by_key[key]['name'])
                temp_file_path = os.path.join(temp_dir, f"{key}_{file_name}")

                with open(temp_file_path, 'wb') as f:
                    f.write(docs_by_key[key]['content'])

                doc_paths[key] = temp_file_path

            pipeline_documents = [
                {
                    'key': key,
                    'label': label,
                    'name': docs_by_key[key]['name'],
                    'path': doc_paths[key],
                }
                for key, label in REQUIRED_DOCUMENTS
                if key in docs_by_key and doc_paths[key]
            ]
            pipeline_result = process_uploaded_documents(
                pipeline_documents,
                options=WebPipelineOptions(
                    with_llm_extraction=True,
                    with_semantic_llm=True,
                    with_ktru=True,
                    ktru_timeout_seconds=int(os.getenv("KTRU_TIMEOUT_SECONDS", "30")),
                    llm_concurrency=int(os.getenv("SUMMARY_LLM_CONCURRENCY", "6")),
                ),
            )
            ai_response = mark_report_text(pipeline_result.report_text)
            if pipeline_result.warnings:
                warnings_text = "\n".join(f"- {warning}" for warning in pipeline_result.warnings)
                ai_response = f"{ai_response}\n\n<b>Технические предупреждения</b>\n{warnings_text}"
            result_file_bytes = build_result_docx_bytes(ai_response)

            return {
                'ai_response': ai_response,
                'result_file_b64': base64.b64encode(result_file_bytes).decode('utf-8'),
                'result_file_name': 'analysis_result.docx',
                'documents': [
                    {
                        'key': key,
                        'label': label,
                        'name': docs_by_key[key]['name'],
                    }
                    for key, label in REQUIRED_DOCUMENTS
                    if key in docs_by_key
                ],
                'status': 'completed'
            }
        finally:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
    except Exception as e:
        error_msg = str(e)
        print(f"Error processing documents: {error_msg}")
        raise Exception(error_msg)

