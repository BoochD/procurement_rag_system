import os
import base64
from io import BytesIO

from celery import Celery
from celery.result import AsyncResult
from django.http import FileResponse, HttpResponse
from django.shortcuts import redirect, render

DOCUMENT_FIELDS = (
    ("plan", "Заявка в план-график"),
    ("contract", "Проект контракта"),
    ("ooz", "ООЗ"),
    ("zapiska", "Пояснительная записка"),
    ("onmck", "ОНМЦК"),
    ("obrasheniye", "Обращение о проведении закупки"),
)
REQUIRED_DOCUMENT_KEYS = {"plan"}

celery_app = Celery("django_client")
celery_app.conf.broker_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
celery_app.conf.result_backend = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")
celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.accept_content = ["json"]


def index(request):
    context = {
        "document_fields": DOCUMENT_FIELDS,
        "required_document_keys": REQUIRED_DOCUMENT_KEYS,
    }
    if "error" in request.GET:
        context["error"] = request.GET["error"]
    return render(request, "fileprocessor/index.html", context)


def upload_and_process(request):
    if request.method == "POST":
        documents = []

        for field_name, field_label in DOCUMENT_FIELDS:
            uploaded_file = request.FILES.get(field_name)
            if not uploaded_file:
                if field_name in REQUIRED_DOCUMENT_KEYS:
                    return redirect(
                        f"fileprocessor:index?error=Please upload document: {field_label}."
                    )
                continue

            documents.append({
                "key": field_name,
                "label": field_label,
                "name": uploaded_file.name,
                "content_b64": base64.b64encode(uploaded_file.read()).decode("utf-8"),
            })

        result = celery_app.send_task(
            "rag_worker.process_document_query",
            args=[documents],
        )

        request.session["task_id"] = result.id
        request.session["documents"] = [
            {
                "key": document["key"],
                "label": document["label"],
                "name": document["name"],
            }
            for document in documents
        ]

        return redirect("fileprocessor:result")

    return redirect("fileprocessor:index")


def result(request):
    task_id = request.session.get("task_id")

    if not task_id:
        return redirect("fileprocessor:index")

    result = AsyncResult(task_id, app=celery_app)

    context = {
        "task_id": task_id,
        "documents": request.session.get("documents", []),
        "status": "processing",
    }

    if result.ready():
        if result.successful():
            data = result.get()
            context["ai_response"] = data.get("ai_response", "")
            context["documents"] = data.get("documents", context["documents"])
            context["status"] = "completed"
            context["download_available"] = bool(data.get("result_file_b64"))
        else:
            context["error"] = str(result.info) if result.info else "Unknown error occurred"
            context["status"] = "failed"
    else:
        context["status"] = "processing"

    return render(request, "fileprocessor/result.html", context)


def download_result(request):
    task_id = request.session.get("task_id")
    if not task_id:
        return redirect("fileprocessor:index")

    result = AsyncResult(task_id, app=celery_app)
    if not result.ready() or not result.successful():
        return HttpResponse("Result file is not ready yet.", status=409)

    data = result.get()
    result_file_b64 = data.get("result_file_b64")
    if not result_file_b64:
        return HttpResponse("Result file is unavailable.", status=404)

    file_name = data.get("result_file_name", "analysis_result.docx")
    file_bytes = base64.b64decode(result_file_b64)
    return FileResponse(
        BytesIO(file_bytes),
        as_attachment=True,
        filename=file_name,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
