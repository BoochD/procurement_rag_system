from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel, Field

from summary_model.domain.models import DocumentIR, DocumentType, Evidence


class ClassificationDecision(BaseModel):
    document_type: DocumentType
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[Evidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


TYPE_MARKERS: dict[DocumentType, tuple[str, ...]] = {
    DocumentType.PLAN: ("заявка на внесение в план-график", "код позиции ктру"),
    DocumentType.REQUEST: ("обращение о проведении закупки", "приложение:"),
    DocumentType.COMMERCIAL_OFFER: ("коммерческое предложение", "ценовое предложение"),
    DocumentType.ONMCK: (
        "обоснование начальной",
        "определение цены контракта",
        "метод сопоставимых рыночных цен",
    ),
    DocumentType.OOZ: ("описание объекта закупки", "функциональные, технические"),
    DocumentType.CONTRACT: ("проект контракта", "предмет контракта", "контракт №"),
    DocumentType.EXPLANATORY_NOTE: ("пояснительная записка",),
}

FILE_MARKERS: dict[DocumentType, tuple[str, ...]] = {
    DocumentType.PLAN: ("план", "заявка"),
    DocumentType.REQUEST: ("обращение",),
    DocumentType.COMMERCIAL_OFFER: ("кп", "коммерч"),
    DocumentType.ONMCK: ("онмцк", "оцк", "обоснован"),
    DocumentType.OOZ: ("ооз", "описание"),
    DocumentType.CONTRACT: ("контракт",),
    DocumentType.EXPLANATORY_NOTE: ("поясн",),
}


def _paragraph_text(ir: DocumentIR) -> str:
    return "\n".join(
        block.text for block in ir.blocks
        if block.type == "paragraph" and block.text
    ).lower()


class DocumentClassifier:
    def __init__(self, llm_client=None) -> None:
        self.llm_client = llm_client

    def classify(
        self,
        ir: DocumentIR,
        type_hint: DocumentType | None = None,
    ) -> ClassificationDecision:
        text = _paragraph_text(ir)
        file_name = Path(ir.file_name).stem.lower()
        scores: dict[DocumentType, float] = defaultdict(float)
        evidence_by_type: dict[DocumentType, list[Evidence]] = defaultdict(list)

        for document_type, markers in TYPE_MARKERS.items():
            for marker in markers:
                if marker in text:
                    scores[document_type] += 2.0
                    block = next(
                        (
                            candidate for candidate in ir.blocks
                            if candidate.text and marker in candidate.text.lower()
                        ),
                        None,
                    )
                    if block:
                        evidence_by_type[document_type].append(
                            Evidence(
                                document_id=ir.document_id,
                                block_id=block.block_id,
                                quote=block.text or "",
                            )
                        )
        for document_type, markers in FILE_MARKERS.items():
            if any(marker in file_name for marker in markers):
                scores[document_type] += 1.0
        if type_hint and type_hint != DocumentType.UNKNOWN:
            scores[type_hint] += 1.25

        if not scores:
            decision = ClassificationDecision(
                document_type=DocumentType.UNKNOWN,
                confidence=0.0,
                warnings=["Document type could not be determined."],
            )
        else:
            ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
            best_type, best_score = ranked[0]
            second_score = ranked[1][1] if len(ranked) > 1 else 0.0
            confidence = min(0.99, 0.55 + 0.1 * best_score + 0.05 * (best_score - second_score))
            warnings: list[str] = []
            if type_hint and type_hint != DocumentType.UNKNOWN and type_hint != best_type:
                warnings.append(
                    f"Type hint {type_hint.value!r} conflicts with detected type {best_type.value!r}."
                )
            decision = ClassificationDecision(
                document_type=best_type,
                confidence=confidence,
                evidence=evidence_by_type[best_type][:3],
                warnings=warnings,
            )

        if self.llm_client is not None and (
            decision.document_type == DocumentType.UNKNOWN
            or decision.confidence < 0.75
            or decision.warnings
        ):
            llm_decision, error = self.llm_client.classify(ir, type_hint)
            if llm_decision is not None:
                if type_hint and llm_decision.document_type != type_hint:
                    llm_decision.warnings.append(
                        f"LLM classification conflicts with type hint {type_hint.value!r}."
                    )
                return llm_decision
            if error:
                decision.warnings.append(error)
        return decision

    async def aclassify(
        self,
        ir: DocumentIR,
        type_hint: DocumentType | None = None,
    ) -> ClassificationDecision:
        decision = DocumentClassifier().classify(ir, type_hint)
        if self.llm_client is not None and (
            decision.document_type == DocumentType.UNKNOWN
            or decision.confidence < 0.75
            or decision.warnings
        ):
            llm_decision, error = await self.llm_client.aclassify(ir, type_hint)
            if llm_decision is not None:
                if type_hint and llm_decision.document_type != type_hint:
                    llm_decision.warnings.append(
                        f"LLM classification conflicts with type hint {type_hint.value!r}."
                    )
                return llm_decision
            if error:
                decision.warnings.append(error)
        return decision
