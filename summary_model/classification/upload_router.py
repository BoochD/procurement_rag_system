from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from summary_model.domain.models import DocumentIR, DocumentType
from summary_model.ingestion import read_docx


SUPPORTED_DESTINATIONS = {
    DocumentType.PLAN: "plan",
    DocumentType.CONTRACT: "contract",
    DocumentType.OOZ: "ooz",
    DocumentType.EXPLANATORY_NOTE: "zapiska",
    DocumentType.ONMCK: "onmck",
    DocumentType.REQUEST: "obrasheniye",
}

_REPORT_NAME_RE = re.compile(
    r"(?:analysis[ _-]*result|результат[ _-]*(?:анализа|проверки)|"
    r"отч[её]т)",
    re.IGNORECASE,
)

_FILE_PATTERNS: dict[DocumentType, tuple[str, ...]] = {
    DocumentType.PLAN: (r"заявк.*(?:план.?график|\bпг\b)", r"план.?график"),
    DocumentType.REQUEST: (r"обращени",),
    DocumentType.COMMERCIAL_OFFER: (r"(?:^|[^а-я])кп(?:[^а-я]|$)", r"коммерческ.*предлож"),
    DocumentType.ONMCK: (r"онмцк", r"(?:^|[^а-я])оцк(?:[^а-я]|$)", r"обоснован.*цены"),
    DocumentType.OOZ: (r"(?:^|[^а-я])ооз(?:[^а-я]|$)", r"описани.*объект.*закуп"),
    DocumentType.CONTRACT: (r"контракт", r"проект.*договора"),
    DocumentType.EXPLANATORY_NOTE: (r"пояснительн",),
}

_CONTENT_MARKERS: dict[DocumentType, tuple[str, ...]] = {
    DocumentType.PLAN: (
        "заявка на включение в план-график",
        "заявка на внесение в план-график",
        "код позиции ктру",
        "способ определения поставщика",
        "этапы исполнения контракта",
        "обеспечение исполнения контракта",
        "начальная (максимальная) цена контракта",
    ),
    DocumentType.REQUEST: (
        "обращение о проведении закупки",
        "просим провести закупку",
        "прошу провести закупку",
        "приложение:",
        "перечень прилагаемых документов",
    ),
    DocumentType.COMMERCIAL_OFFER: (
        "коммерческое предложение",
        "ценовое предложение",
        "направляем коммерческое предложение",
    ),
    DocumentType.ONMCK: (
        "обоснование начальной (максимальной) цены",
        "определение цены контракта",
        "метод сопоставимых рыночных цен",
        "источники информации о ценах",
        "минимальная цена за ед.",
    ),
    DocumentType.OOZ: (
        "описание объекта закупки",
        "функциональные, технические и качественные характеристики",
        "показатели товара",
        "требования к характеристикам",
    ),
    DocumentType.CONTRACT: (
        "проект контракта",
        "предмет контракта",
        "права и обязанности сторон",
        "ответственность сторон",
        "порядок приемки",
        "реквизиты сторон",
    ),
    DocumentType.EXPLANATORY_NOTE: (
        "пояснительная записка",
        "обоснование необходимости закупки",
    ),
}

_REASONS = {
    DocumentType.PLAN: "Найдена заявка в план-график и характерные поля формы.",
    DocumentType.REQUEST: "Найдены признаки обращения о проведении закупки.",
    DocumentType.COMMERCIAL_OFFER: "Файл похож на коммерческое предложение.",
    DocumentType.ONMCK: "Найдены расчёт цены и признаки ОНМЦК/ОЦК.",
    DocumentType.OOZ: "Найдены заголовок и признаки описания объекта закупки.",
    DocumentType.CONTRACT: "Найдены основные разделы проекта контракта.",
    DocumentType.EXPLANATORY_NOTE: "Найдены признаки пояснительной записки.",
}


@dataclass(frozen=True)
class UploadRoute:
    document_type: DocumentType = DocumentType.UNKNOWN
    target_field: str | None = None
    confidence: float = 0.0
    auto_assign: bool = False
    needs_review: bool = False
    reason: str = "Тип документа не определён. Загрузите файл в нужное поле вручную."

    def as_dict(self, *, name: str) -> dict[str, object]:
        return {
            "name": name,
            "document_type": self.document_type.value,
            "target_field": self.target_field,
            "confidence": round(self.confidence, 2),
            "auto_assign": self.auto_assign,
            "needs_review": self.needs_review,
            "reason": self.reason,
        }


def route_upload(path: str | Path) -> UploadRoute:
    source = Path(path)
    name = source.name
    suffix = source.suffix.casefold()

    if name.startswith("~$") or _REPORT_NAME_RE.search(name):
        return UploadRoute(reason="Это служебный файл или готовый отчёт, а не исходный документ.")
    if suffix not in {".docx", ".pdf"}:
        return UploadRoute(reason="Формат файла не поддерживается для общей загрузки.")

    if suffix == ".docx":
        try:
            ir = read_docx(source)
        except Exception:
            return UploadRoute(reason="DOCX не удалось прочитать. Загрузите файл вручную или проверьте его.")
        text = _document_text(ir)
    else:
        text = _pdf_text(source)

    return route_upload_text(name, text)


def route_upload_text(file_name: str, text: str) -> UploadRoute:
    if file_name.startswith("~$") or _REPORT_NAME_RE.search(file_name):
        return UploadRoute(reason="Это служебный файл или готовый отчёт, а не исходный документ.")

    normalized_name = _normalize(file_name)
    normalized_text = _normalize(text)
    scores: dict[DocumentType, float] = {}

    for document_type in _CONTENT_MARKERS:
        file_hits = sum(
            1 for pattern in _FILE_PATTERNS.get(document_type, ())
            if re.search(pattern, normalized_name, re.IGNORECASE)
        )
        content_hits = sum(
            1 for marker in _CONTENT_MARKERS[document_type]
            if marker in normalized_text
        )
        heading_bonus = _heading_bonus(document_type, normalized_text)
        scores[document_type] = min(file_hits, 1) * 4.0 + content_hits * 1.5 + heading_bonus

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_type, best_score = ranked[0]
    second_score = ranked[1][1]
    margin = best_score - second_score

    if best_score < 3.0 or margin < 0.75:
        return UploadRoute(reason="Недостаточно признаков для надёжного определения типа документа.")

    confidence = min(0.99, 0.48 + best_score * 0.055 + margin * 0.035)
    if best_type == DocumentType.COMMERCIAL_OFFER:
        return UploadRoute(
            document_type=best_type,
            confidence=confidence,
            reason="Файл похож на КП. Загрузите его в отдельный блок коммерческих предложений.",
        )

    target = SUPPORTED_DESTINATIONS[best_type]
    suffix = Path(file_name).suffix.casefold()
    if suffix == ".pdf" and best_type not in {
        DocumentType.REQUEST,
        DocumentType.EXPLANATORY_NOTE,
    }:
        return UploadRoute(
            document_type=best_type,
            confidence=confidence,
            reason="Тип определён, но для этой позиции поддерживается только DOCX.",
        )

    confident = best_score >= 7.0 and margin >= 2.0
    return UploadRoute(
        document_type=best_type,
        target_field=target,
        confidence=confidence,
        auto_assign=True,
        needs_review=not confident,
        reason=_REASONS[best_type] if confident else _REASONS[best_type] + " Проверьте распределение.",
    )


def _document_text(ir: DocumentIR) -> str:
    parts: list[str] = []
    for block in ir.blocks:
        if block.text:
            parts.append(block.text)
        if block.table:
            parts.extend(value for row in block.table.matrix() for value in row if value)
    return "\n".join(parts)


def _pdf_text(path: Path, max_pages: int = 4) -> str:
    try:
        import fitz  # type: ignore

        with fitz.open(path) as document:
            return "\n".join(page.get_text("text") for page in list(document)[:max_pages])
    except Exception:
        return ""


def _normalize(value: str) -> str:
    return " ".join((value or "").casefold().replace("ё", "е").split())


def _heading_bonus(document_type: DocumentType, text: str) -> float:
    headings = {
        DocumentType.PLAN: ("заявка на включение в план-график", "заявка на внесение в план-график"),
        DocumentType.REQUEST: ("обращение о проведении закупки",),
        DocumentType.COMMERCIAL_OFFER: ("коммерческое предложение", "ценовое предложение"),
        DocumentType.ONMCK: ("обоснование начальной (максимальной) цены", "определение цены контракта"),
        DocumentType.OOZ: ("описание объекта закупки",),
        DocumentType.CONTRACT: ("проект контракта",),
        DocumentType.EXPLANATORY_NOTE: ("пояснительная записка",),
    }
    first_part = text[:2500]
    return 3.0 if any(heading in first_part for heading in headings[document_type]) else 0.0
