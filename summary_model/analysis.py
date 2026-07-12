from __future__ import annotations

import asyncio
import json

from summary_model.domain.models import (
    AnalyzerResult,
    DocumentIR,
    Finding,
    FindingSeverity,
    FindingStatus,
    ProcurementPackage,
)
from summary_model.extraction.llm_client import StructuredLLMClient


ANALYZER_PROMPTS = {
    "items_consistency": """
Проверь структурированные данные о предмете закупки и товарных позициях.
Ищи смысловые расхождения наименований, пропущенные товарные позиции и
противоречия состава позиций между документами.
Не проверяй форматы ОКПД2/КТРУ и данные внешних реестров.
Не создавай finding только потому, что необязательное поле отсутствует в одном
из документов. Общий предмет закупки может быть широкой категорией: отдельные
комплектующие внутри неё не являются противоречием. Одинаковое наименование или
КТРУ в нескольких строках одной таблицы может обозначать разные варианты
товара; различающиеся характеристики таких строк нормальны. Сопоставляй
позиции между документами по КТРУ, ОКПД2, нормализованному наименованию,
количеству и совокупности характеристик. item_id является локальным техническим
идентификатором документа и никогда не сравнивается между документами.
unresolved_fields означает сбой или неполноту extraction, а не отсутствие
условия в исходном документе; не создавай на его основе finding об ошибке.
""",
    "delivery_and_finance": """
Проверь все упоминания сроков и мест поставки, этапов, сроков исполнения,
цен, НДС, оплаты и финансирования. Сравни каждое occurrence, включая
повторения в приложениях. Арифметические результаты переданы как факты:
не пересчитывай и не дублируй deterministic findings.
Не объявляй условие отсутствующим только из-за unresolved_fields.
""",
    "legal_and_completeness": """
Проверь гарантии, обеспечения, штрафы, права, ограничения, обязанности,
национальный режим, обязательные условия и обоснования. Ищи противоречия
между основным текстом и приложениями. Не делай выводов по внешним реестрам.
unresolved_fields означает, что поле не удалось извлечь, а не что условие
отсутствует в документе. В таком случае допустим только uncertain finding о
невозможности проверки, но не failed finding об отсутствии условия.
""",
}

ANALYZER_PROMPT_VERSIONS = {
    name: "2.4.0"
    for name in ANALYZER_PROMPTS
}

ANALYZER_FIELDS = {
    "items_consistency": {
        "subject",
        "items",
        "source_offers",
        "extraction_warnings",
        "unresolved_fields",
    },
    "delivery_and_finance": {
        "nmck",
        "price",
        "subtotal",
        "total",
        "vat",
        "vat_terms",
        "funding_source",
        "delivery_places",
        "delivery_periods",
        "contract_execution_periods",
        "execution_periods",
        "execution_stages",
        "items",
        "source_offers",
        "extraction_warnings",
        "unresolved_fields",
    },
    "legal_and_completeness": {
        "warranty_terms",
        "security",
        "penalties",
        "applications",
        "national_regime",
        "smp_terms",
        "single_supplier_basis",
        "procurement_method",
        "rights_transfer_required",
        "rights_transfer_documents",
        "rights_transfer_terms",
        "typical_contract_reference",
        "treasury_or_bank_support",
        "justification",
        "extraction_warnings",
        "unresolved_fields",
    },
}

ANALYSIS_RULES = """
Верни AnalyzerResult и только findings своей области.
Каждый finding должен иметь source="llm" и rule_id с префиксом имени анализатора.
Всегда возвращай объект, не null.
`status` допускает только passed, failed, skipped, uncertain.
`severity` допускает только info, warning, error, manual_review.
Для status=passed используй severity=info, а не severity=passed.
Сохраняй координаты evidence из входных данных дословно; не создавай новые
block_id. Поле quote можно оставить пустым: pipeline восстановит цитату из IR.
Один evidence на верхнем уровне item относится ко всем его вложенным полям,
включая коды, количество, цены и характеристики.
Ошибки возвращай как failed, неоднозначность как uncertain.
Не более пяти passed findings: только ключевые успешно проверенные условия.
Не повторяй registry, форматы кодов, арифметику и точные deterministic проверки.
""".strip()


def _first_nested_evidence(value):
    if isinstance(value, list):
        for item in value:
            evidence = _first_nested_evidence(item)
            if evidence is not None:
                return evidence
        return None
    if not isinstance(value, dict):
        return None
    evidence = value.get("evidence")
    if isinstance(evidence, list) and evidence:
        return evidence[0]
    for key, item in value.items():
        if key == "evidence":
            continue
        nested = _first_nested_evidence(item)
        if nested is not None:
            return nested
    return None


def _strip_nested_evidence(value):
    if isinstance(value, list):
        return [_strip_nested_evidence(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: _strip_nested_evidence(item)
        for key, item in value.items()
        if key != "evidence"
    }


def _collapse_item_evidence(items: list[dict]) -> list[dict]:
    compact_items: list[dict] = []
    for wrapper in items:
        item = wrapper.get("item", wrapper)
        preferred = item.get("name", {}).get("evidence") or []
        evidence = preferred[0] if preferred else _first_nested_evidence(wrapper)
        compact = _strip_nested_evidence(wrapper)
        if evidence is not None:
            compact["evidence"] = [evidence]
        compact_items.append(compact)
    return compact_items


def _compact_for_llm(value):
    if isinstance(value, list):
        return [_compact_for_llm(item) for item in value]
    if not isinstance(value, dict):
        return value
    compact = {
        key: _compact_for_llm(item)
        for key, item in value.items()
        if key not in {"confidence", "warnings"}
        and not (key == "quote")
        and item not in (None, [], {})
    }
    if compact.get("raw_value") == compact.get("normalized_value"):
        compact.pop("raw_value", None)
    return compact


def _analysis_payload(package: ProcurementPackage, analyzer: str) -> str:
    fields = ANALYZER_FIELDS[analyzer]
    documents: list[dict] = []
    for document in (
        package.plan,
        package.request,
        package.onmck,
        package.ooz,
        package.contract,
        package.explanatory_note,
        *package.commercial_offers,
    ):
        if document is None:
            continue
        payload = document.model_dump(mode="json")
        compact = {
            key: value
            for key, value in payload.items()
            if key in fields
            or key in {"document_id", "display_name", "detected_type"}
        }
        if compact.get("detected_type") == "plan" and "items" in compact:
            compact["items"] = [
                wrapper
                for wrapper in compact["items"]
                if (
                    len(wrapper.get("item", wrapper).get("okpd2", []))
                    + len(wrapper.get("item", wrapper).get("ktru", []))
                )
                <= 1
            ]
        if analyzer == "delivery_and_finance" and "items" in compact:
            for wrapper in compact["items"]:
                item = wrapper.get("item", wrapper)
                item.pop("characteristics", None)
                item.pop("okpd2", None)
                item.pop("ktru", None)
        if analyzer == "items_consistency" and "items" in compact:
            for wrapper in compact["items"]:
                wrapper.get("item", wrapper).pop("characteristics", None)
        if "items" in compact:
            compact["items"] = _collapse_item_evidence(compact["items"])
        documents.append(_compact_for_llm(compact))
    return json.dumps({"documents": documents}, ensure_ascii=False)


def _failure_finding(analyzer: str, message: str) -> Finding:
    return Finding(
        rule_id=f"{analyzer}.execution",
        severity=FindingSeverity.MANUAL_REVIEW,
        status=FindingStatus.UNCERTAIN,
        title=f"LLM-анализ {analyzer}",
        message=message,
        source="llm",
    )


def _validate_findings(
    result: AnalyzerResult,
    analyzer: str,
    ir_by_document: dict[str, DocumentIR],
) -> list[Finding]:
    valid_documents = set(ir_by_document)
    blocks = {
        document_id: {block.block_id: block for block in ir.blocks}
        for document_id, ir in ir_by_document.items()
    }

    def valid_evidence(evidence) -> bool:
        if evidence.document_id not in valid_documents:
            return False
        block = blocks[evidence.document_id].get(evidence.block_id)
        if block is None:
            return False
        if evidence.table_id is None:
            return True
        table = block.table
        if table is None or table.table_id != evidence.table_id:
            return False
        if evidence.row is not None and not 0 <= evidence.row < table.row_count:
            return False
        if evidence.column is not None and not 0 <= evidence.column < table.column_count:
            return False
        return True

    def restore_quote(evidence) -> None:
        if evidence.quote:
            return
        block = blocks[evidence.document_id][evidence.block_id]
        if block.table is not None and evidence.row is not None and evidence.column is not None:
            matrix = block.table.matrix()
            evidence.quote = matrix[evidence.row][evidence.column][:500]
        else:
            evidence.quote = (block.text or "")[:500]
    findings: list[Finding] = []
    for finding in result.findings:
        finding.source = "llm"
        if not finding.rule_id.startswith(f"{analyzer}."):
            finding.rule_id = f"{analyzer}.{finding.rule_id}"
        finding.documents = [
            document_id
            for document_id in finding.documents
            if document_id in valid_documents
        ]
        original_evidence_count = len(finding.evidence)
        finding.evidence = [
            evidence
            for evidence in finding.evidence
            if valid_evidence(evidence)
        ]
        for evidence in finding.evidence:
            restore_quote(evidence)
        normalized_text = f"{finding.title} {finding.message}".lower()
        if (
            finding.status == FindingStatus.FAILED
            and "unresolved_fields" in normalized_text
        ):
            finding.status = FindingStatus.UNCERTAIN
            finding.severity = FindingSeverity.MANUAL_REVIEW
        formatting_only = any(
            marker in normalized_text
            for marker in (
                "только в регистре",
                "регистр/оформлен",
                "регистр и оформлен",
            )
        )
        no_semantic_conflict = any(
            marker in normalized_text
            for marker in (
                "противоречия не выявлено",
                "противоречий не выявлено",
                "смыслового расхождения не выявлено",
                "смысловых расхождений не выявлено",
                "нет смыслового противоречия",
            )
        )
        if formatting_only and no_semantic_conflict:
            continue
        if analyzer == "items_consistency" and (
            "item_id" in normalized_text
            or "item-идентификатор" in normalized_text
            or (
                "техническ" in normalized_text
                and "идентификатор" in normalized_text
            )
        ):
            continue
        if analyzer == "items_consistency" and any(
            marker in normalized_text
            for marker in (
                "без полного набора сопоставимых",
                "отсутствуют отдельные поля",
                "несоответствие наименований между основным предметом",
                "характерист",
                "неоднозначность сопоставления позиций",
                "разные варианты товара",
            )
        ):
            continue
        if len(finding.evidence) != original_evidence_count:
            if original_evidence_count > 0 and not finding.evidence:
                finding.status = FindingStatus.UNCERTAIN
                finding.severity = FindingSeverity.MANUAL_REVIEW
                finding.message += " Evidence не подтверждено Document IR."
        findings.append(finding)
    return findings


async def run_llm_analyzers(
    package: ProcurementPackage,
    ir_by_document: dict[str, DocumentIR],
    llm_client: StructuredLLMClient | None,
    payload_metrics: dict[str, int] | None = None,
) -> tuple[list[Finding], list[str]]:
    payloads = {
        name: _analysis_payload(package, name)
        for name in ANALYZER_PROMPTS
    }
    if payload_metrics is not None:
        payload_metrics.update(
            {name: len(payload) for name, payload in payloads.items()}
        )
    if llm_client is None:
        return [], []

    async def run(analyzer: str) -> tuple[list[Finding], str | None]:
        result, error = await llm_client.aextract(
            AnalyzerResult,
            f"{ANALYZER_PROMPTS[analyzer]}\n\n{ANALYSIS_RULES}\n"
            f"analyzer={analyzer}",
            payloads[analyzer],
        )
        if result is None:
            message = error or f"{analyzer} did not return a result."
            return [_failure_finding(analyzer, message)], message
        if result.analyzer != analyzer:
            message = (
                f"{analyzer} returned analyzer={result.analyzer}; "
                "result requires manual review."
            )
            findings = _validate_findings(result, analyzer, ir_by_document)
            findings.append(_failure_finding(analyzer, message))
            return findings, message
        return _validate_findings(result, analyzer, ir_by_document), None

    results = await asyncio.gather(*(run(name) for name in ANALYZER_PROMPTS))
    findings = [finding for batch, _ in results for finding in batch]
    warnings = [warning for _, warning in results if warning]
    return findings, warnings
