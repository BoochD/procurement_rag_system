import asyncio
import json
from io import BytesIO

from docx import Document

from summary_model.domain.models import (
    AnalyzerResult,
    ContractSummary,
    DeliveryTermsExtraction,
    DocumentBlockIR,
    DocumentIR,
    DocumentType,
    Evidence,
    ExtractedValue,
    Finding,
    FindingSeverity,
    FindingStatus,
    ItemCharacteristic,
    OnmckItem,
    OnmckSummary,
    OozSummary,
    ProcurementPackage,
    ProcurementItem,
    TableColumnIR,
    TableIR,
    TableRowIR,
)
from summary_model.analysis import (
    _analysis_payload,
    _validate_findings,
    run_llm_analyzers,
)
from summary_model.extraction.extractors import (
    DocumentExtractionEngine,
    _merge_summaries,
    _sanitize_item_codes,
)
from summary_model.extraction.heuristics import _onmck_items, _plan_item_from_key_value
from summary_model.extraction.llm_client import StructuredLLMClient
from summary_model.reporting import build_report_docx_bytes, build_report_text
from summary_model.validation import merge_findings


def value(raw, block_id):
    return ExtractedValue(
        raw_value=raw,
        normalized_value=raw,
        confidence=1.0,
        evidence=[
            Evidence(
                document_id="contract",
                block_id=block_id,
                quote=str(raw),
            )
        ],
    )


def test_chunk_merge_keeps_occurrences_and_resolves_fields():
    first = ContractSummary(
        document_id="contract",
        detected_type=DocumentType.CONTRACT,
        delivery_periods=[value("15 рабочих дней", "block-1")],
        unresolved_fields=["delivery_periods", "price"],
    )
    second = ContractSummary(
        document_id="contract",
        detected_type=DocumentType.CONTRACT,
        delivery_periods=[value("15 рабочих дней", "block-2")],
        price=[value("350000", "block-3")],
    )

    merged = _merge_summaries([first, second])

    assert len(merged.delivery_periods) == 2
    assert {item.evidence[0].block_id for item in merged.delivery_periods} == {
        "block-1",
        "block-2",
    }
    assert merged.unresolved_fields == []


class DeliveryRepairClient:
    def __init__(self):
        self.calls = []

    async def aextract(self, schema, prompt, payload):
        self.calls.append((schema, prompt, payload))
        return (
            DeliveryTermsExtraction(
                delivery_periods=[
                    value("15 рабочих дней", "block-1"),
                    value("15 рабочих дней", "block-2"),
                ],
                delivery_places=[value("г. Новосибирск", "block-3")],
            ),
            None,
        )


def test_focused_delivery_repair_fills_missing_occurrences():
    client = DeliveryRepairClient()
    engine = DocumentExtractionEngine(client)
    summary = ContractSummary(
        document_id="contract",
        detected_type=DocumentType.CONTRACT,
        unresolved_fields=["delivery_periods", "delivery_places"],
    )
    ir = DocumentIR(
        document_id="contract",
        file_name="contract.docx",
        media_type="docx",
        blocks=[
            DocumentBlockIR(
                block_id="block-1",
                order=0,
                type="paragraph",
                text="Срок поставки: 15 рабочих дней.",
            ),
            DocumentBlockIR(
                block_id="block-2",
                order=1,
                type="paragraph",
                text="Срок поставки: 15 рабочих дней.",
            ),
            DocumentBlockIR(
                block_id="block-3",
                order=2,
                type="paragraph",
                text="Место поставки: г. Новосибирск.",
            ),
        ],
    )

    repaired = asyncio.run(
        engine._arepair_delivery_terms(summary, ir, DocumentType.CONTRACT)
    )

    assert [item.evidence[0].block_id for item in repaired.delivery_periods] == [
        "block-1",
        "block-2",
    ]
    assert repaired.delivery_places[0].normalized_value == "г. Новосибирск"
    assert repaired.unresolved_fields == []
    assert len(client.calls) == 1
    assert "Срок поставки: 15 рабочих дней." in client.calls[0][2]


def test_focused_delivery_repair_is_skipped_when_both_fields_are_present():
    client = DeliveryRepairClient()
    engine = DocumentExtractionEngine(client)
    summary = ContractSummary(
        document_id="contract",
        detected_type=DocumentType.CONTRACT,
        delivery_periods=[value("15 рабочих дней", "block-1")],
        delivery_places=[value("г. Новосибирск", "block-2")],
    )
    ir = DocumentIR(
        document_id="contract",
        file_name="contract.docx",
        media_type="docx",
    )

    repaired = asyncio.run(
        engine._arepair_delivery_terms(summary, ir, DocumentType.CONTRACT)
    )

    assert repaired is summary
    assert client.calls == []


class AsyncRunnable:
    def __init__(self):
        self.active = 0
        self.maximum = 0

    async def ainvoke(self, _prompt):
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        return {
            "analyzer": "items_consistency",
            "findings": [],
            "coverage": [],
        }


class AsyncModel:
    def __init__(self):
        self.runnable = AsyncRunnable()

    def with_structured_output(self, _schema, *, method):
        assert method == "function_calling"
        return self.runnable


def test_async_client_respects_global_concurrency_limit():
    async def run():
        model = AsyncModel()
        client = StructuredLLMClient(
            model=model,
            semaphore=asyncio.Semaphore(3),
        )
        await asyncio.gather(
            *(
                client.aextract(AnalyzerResult, "prompt", f"payload-{index}")
                for index in range(6)
            )
        )
        return model.runnable.maximum, client.metrics()

    maximum, metrics = asyncio.run(run())

    assert maximum == 3
    assert metrics["calls"] == 6


class InvalidRequestRunnable:
    def __init__(self):
        self.calls = 0

    async def ainvoke(self, _prompt):
        self.calls += 1
        raise RuntimeError(
            "Error code: 400 - {'error': {'type': 'invalid_request_error'}}"
        )


class InvalidRequestModel:
    def __init__(self):
        self.runnable = InvalidRequestRunnable()

    def with_structured_output(self, _schema, *, method):
        assert method == "function_calling"
        return self.runnable


def test_async_client_does_not_retry_invalid_request():
    async def run():
        model = InvalidRequestModel()
        client = StructuredLLMClient(model=model)
        result, error = await client.aextract(
            AnalyzerResult,
            "prompt",
            "payload",
        )
        return model, client, result, error

    model, client, result, error = asyncio.run(run())

    assert result is None
    assert "invalid_request_error" in error
    assert model.runnable.calls == 1
    assert client.metrics()["retries"] == 0


def finding(source, message):
    return Finding(
        rule_id="shared.rule",
        severity=FindingSeverity.ERROR,
        status=FindingStatus.FAILED,
        title="Rule",
        message=message,
        actual="same",
        source=source,
    )


def test_merge_findings_prefers_external_source():
    merged = merge_findings(
        [finding("llm", "llm")],
        [finding("deterministic", "deterministic")],
        [finding("external", "external")],
    )

    assert len(merged) == 1
    assert merged[0].source == "external"


class AnalyzerClient:
    def __init__(self, failing=None):
        self.failing = failing
        self.active = 0
        self.maximum = 0
        self.calls = []

    async def aextract(self, _schema, prompt, _payload):
        analyzer = prompt.rsplit("analyzer=", 1)[1]
        self.calls.append(analyzer)
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        if analyzer == self.failing:
            return None, "simulated failure"
        return AnalyzerResult(analyzer=analyzer), None


def test_three_analyzers_run_concurrently_and_isolate_failure():
    async def run():
        client = AnalyzerClient(failing="delivery_and_finance")
        findings, warnings = await run_llm_analyzers(
            ProcurementPackage(),
            {},
            client,
        )
        return client, findings, warnings

    client, findings, warnings = asyncio.run(run())

    assert set(client.calls) == {
        "items_consistency",
        "delivery_and_finance",
        "legal_and_completeness",
    }
    assert client.maximum == 3
    assert warnings == ["simulated failure"]
    assert findings[0].status == FindingStatus.UNCERTAIN


def test_postprocessing_separates_okpd2_and_ktru_by_format():
    summary = ContractSummary(
        document_id="contract",
        detected_type=DocumentType.CONTRACT,
        items=[
            ProcurementItem(
                item_id="1",
                name=value("Шина", "block-1"),
                okpd2=[
                    value("22.11.11.000-00000007", "block-2"),
                    value("29.32.30.220", "block-3"),
                ],
                ktru=[
                    value("22.11.11.000-00000004", "block-4"),
                    value("29.32.30.220", "block-5"),
                ],
            )
        ],
    )

    _sanitize_item_codes(summary)

    assert {item.normalized_value for item in summary.items[0].okpd2} == {
        "29.32.30.220"
    }
    assert {item.normalized_value for item in summary.items[0].ktru} == {
        "22.11.11.000-00000004",
        "22.11.11.000-00000007",
    }


def test_report_uses_document_labels_in_values_and_evidence():
    issue = Finding(
        rule_id="test",
        severity=FindingSeverity.ERROR,
        status=FindingStatus.FAILED,
        title="Коды",
        message="Расхождение",
        actual={"contract": ["29.32.30.220"]},
        evidence=[
            Evidence(
                document_id="contract",
                block_id="block-1",
                quote="29.32.30.220",
            )
        ],
        source="deterministic",
    )

    report = build_report_text(
        [issue],
        document_labels={"contract": "Проект контракта"},
    )

    assert "Проект контракта" in report
    assert "Источник: contract" not in report


def _nested_evidence_count(value):
    if isinstance(value, list):
        return sum(_nested_evidence_count(item) for item in value)
    if not isinstance(value, dict):
        return 0
    return int(bool(value.get("evidence"))) + sum(
        _nested_evidence_count(item)
        for key, item in value.items()
        if key != "evidence"
    )


def test_analysis_payload_collapses_nested_item_evidence():
    item = ProcurementItem(
        item_id="item-1",
        name=value("Шина", "name-block"),
        ktru=[value("22.11.11.000-00000007", "code-block")],
        unit=value("шт", "unit-block"),
        characteristics=[
            ItemCharacteristic(
                name=value("Сезонность", "characteristic-name"),
                value=value("Летняя", "characteristic-value"),
            )
        ],
    )
    package = ProcurementPackage(
        ooz=OozSummary(
            document_id="ooz",
            detected_type=DocumentType.OOZ,
            subject=value("Поставка шин", "subject-block"),
            items=[item],
        )
    )

    payload = json.loads(_analysis_payload(package, "items_consistency"))
    document = payload["documents"][0]
    compact_item = document["items"][0]

    assert _nested_evidence_count(compact_item) == 1
    assert compact_item["evidence"][0]["block_id"] == "name-block"
    assert "characteristics" not in compact_item
    assert compact_item["ktru"][0]["normalized_value"] == "22.11.11.000-00000007"
    assert document["subject"]["evidence"]


def test_onmck_payload_places_evidence_on_outer_item_wrapper():
    wrapped = OnmckItem(
        item=ProcurementItem(
            item_id="item-1",
            name=value("Шина", "name-block"),
        )
    )
    package = ProcurementPackage(
        onmck=OnmckSummary(
            document_id="onmck",
            detected_type=DocumentType.ONMCK,
            items=[wrapped],
        )
    )

    payload = json.loads(_analysis_payload(package, "items_consistency"))
    compact = payload["documents"][0]["items"][0]

    assert compact["evidence"][0]["block_id"] == "name-block"
    assert "evidence" not in compact["item"]["name"]


def test_analysis_payload_metrics_are_available_without_llm():
    async def run():
        metrics = {}
        findings, warnings = await run_llm_analyzers(
            ProcurementPackage(),
            {},
            None,
            payload_metrics=metrics,
        )
        return metrics, findings, warnings

    metrics, findings, warnings = asyncio.run(run())

    assert set(metrics) == {
        "items_consistency",
        "delivery_and_finance",
        "legal_and_completeness",
    }
    assert findings == []
    assert warnings == []


def test_items_analyzer_drops_low_value_missing_fields_finding():
    result = AnalyzerResult(
        analyzer="items_consistency",
        findings=[
            Finding(
                rule_id="items_consistency.missing",
                severity=FindingSeverity.MANUAL_REVIEW,
                status=FindingStatus.UNCERTAIN,
                title="Позиции без полного набора сопоставимых сведений",
                message="В некоторых документах отсутствуют отдельные поля.",
                source="llm",
            )
        ],
    )

    assert _validate_findings(result, "items_consistency", {}) == []


def test_partial_invalid_evidence_does_not_downgrade_finding():
    ir = DocumentIR(
        document_id="doc",
        file_name="doc.docx",
        media_type="docx",
        blocks=[
            DocumentBlockIR(
                block_id="valid-block",
                order=0,
                type="paragraph",
                text="Место поставки: Новосибирск",
            )
        ],
    )
    result = AnalyzerResult(
        analyzer="delivery_and_finance",
        findings=[
            Finding(
                rule_id="delivery_and_finance.place",
                severity=FindingSeverity.INFO,
                status=FindingStatus.PASSED,
                title="Место поставки",
                message="Место поставки совпадает.",
                evidence=[
                    Evidence(
                        document_id="doc",
                        block_id="valid-block",
                        quote="",
                    ),
                    Evidence(
                        document_id="doc",
                        block_id="invented-block",
                        quote="",
                    ),
                ],
                source="llm",
            )
        ],
    )

    findings = _validate_findings(
        result,
        "delivery_and_finance",
        {"doc": ir},
    )

    assert findings[0].status == FindingStatus.PASSED
    assert findings[0].message == "Место поставки совпадает."
    assert len(findings[0].evidence) == 1


def test_finding_normalizes_status_accidentally_used_as_severity():
    finding = Finding(
        rule_id="llm.passed",
        severity="passed",
        status="passed",
        title="Проверка",
        message="Пройдена",
        source="llm",
    )

    assert finding.severity == FindingSeverity.INFO


def test_report_keeps_passed_finding_on_one_line():
    passed = Finding(
        rule_id="items.quantity.item",
        severity=FindingSeverity.INFO,
        status=FindingStatus.PASSED,
        title="Количество позиции",
        message="Проверка пройдена.",
        expected="1",
        actual={"ooz": "1", "contract": "1"},
        evidence=[
            Evidence(
                document_id="ooz",
                block_id="block",
                quote="1",
            )
        ],
        source="deterministic",
    )

    report = build_report_text(
        [passed],
        document_labels={"ooz": "ООЗ", "contract": "Проект контракта"},
    )

    assert "- Количество позиции — ОК" in report
    assert "Проверка пройдена" not in report
    assert "Ожидалось:" not in report
    assert "Получено:" not in report
    assert "Источник:" not in report


def test_report_keeps_details_for_failed_finding():
    failed = Finding(
        rule_id="items.quantity.item",
        severity=FindingSeverity.ERROR,
        status=FindingStatus.FAILED,
        title="Количество позиции",
        message="Обнаружено расхождение.",
        expected="1",
        actual={"ooz": "2"},
        evidence=[
            Evidence(
                document_id="ooz",
                block_id="block",
                quote="2",
            )
        ],
        source="deterministic",
    )

    report = build_report_text([failed], document_labels={"ooz": "ООЗ"})

    assert "Количество позиции — ОШИБКА. Обнаружено расхождение." in report
    assert "Ожидалось: 1" in report
    assert "Получено: {'ООЗ': '2'}" in report
    assert "Источник: ООЗ: 2" in report


def test_report_uses_domain_section_order_in_text_and_docx():
    findings = [
        Finding(
            rule_id="price.variation",
            severity=FindingSeverity.INFO,
            status=FindingStatus.PASSED,
            title="Цена",
            message="ОК",
            source="deterministic",
        ),
        Finding(
            rule_id="registry.okpd2.22.11.11.000",
            severity=FindingSeverity.INFO,
            status=FindingStatus.PASSED,
            title="ОКПД2",
            message="ОК",
            source="external",
        ),
        Finding(
            rule_id="package.required.plan",
            severity=FindingSeverity.INFO,
            status=FindingStatus.PASSED,
            title="Комплектность",
            message="ОК",
            source="deterministic",
        ),
    ]

    report = build_report_text(findings)
    docx = Document(BytesIO(build_report_docx_bytes(findings)))
    docx_text = "\n".join(paragraph.text for paragraph in docx.paragraphs)

    assert report.index("Комплектность пакета") < report.index("Проверка ОКПД2")
    assert report.index("Проверка ОКПД2") < report.index("Расчёт НМЦК")
    assert docx_text.index("Комплектность пакета") < docx_text.index("Проверка ОКПД2")
    assert docx_text.index("Проверка ОКПД2") < docx_text.index("Расчёт НМЦК")


def test_package_report_groups_codes_and_quantities_by_document():
    item = ProcurementItem(
        item_id="item-1",
        name=ExtractedValue(normalized_value="Картридж"),
        okpd2=[ExtractedValue(normalized_value="20.59.12.120")],
        ktru=[ExtractedValue(normalized_value="20.59.12.120-00000002")],
        quantity=ExtractedValue(normalized_value=2),
        unit=ExtractedValue(normalized_value="шт"),
    )
    package = ProcurementPackage(
        ooz=OozSummary(
            document_id="ooz",
            detected_type=DocumentType.OOZ,
            items=[item],
        ),
        contract=ContractSummary(
            document_id="contract",
            detected_type=DocumentType.CONTRACT,
            items=[item, item.model_copy(update={"item_id": "item-2"})],
        ),
    )

    report = build_report_text([], package=package)

    assert 'Вхождения в документе "Описание объекта закупки (ООЗ)"' in report
    assert "20.59.12.120 - Картридж" in report
    assert "20.59.12.120-00000002 - Картридж" in report
    assert "Картридж - 2 шт; 2 шт" in report


def test_onmck_supplier_matrix_is_parsed_deterministically():
    table = TableIR(
        table_id="prices",
        row_count=4,
        columns=[
            TableColumnIR(index=0, alias="c0", header_path=["№"]),
            TableColumnIR(index=1, alias="c1", header_path=["Наименование"]),
            TableColumnIR(index=2, alias="c2", header_path=["Ед. изм."]),
            TableColumnIR(index=3, alias="c3", header_path=["Кол-во"]),
            TableColumnIR(
                index=4,
                alias="c4",
                header_path=["Поставщик 1", "Цена за ед. товара"],
            ),
            TableColumnIR(
                index=5,
                alias="c5",
                header_path=["Поставщик 1", "Стоимость товаров"],
            ),
            TableColumnIR(
                index=6,
                alias="c6",
                header_path=["Минимальная цена за ед. товара"],
            ),
            TableColumnIR(
                index=7,
                alias="c7",
                header_path=["Цена контракта"],
            ),
        ],
        header_rows=[0, 1, 2],
        kind="supplier_matrix",
        rows=[
            TableRowIR(
                row_id="prices.r1",
                row=1,
                values={"c4": "Поставщик 1"},
                spans={"c4": (1, 2)},
            ),
            TableRowIR(
                row_id="prices.r2",
                row=2,
                values={
                    "c4": "Цена за ед. товара",
                    "c5": "Стоимость товаров",
                },
            ),
            TableRowIR(
                row_id="prices.r3",
                row=3,
                values={
                    "c1": "Картридж",
                    "c4": "100,00",
                    "c5": "200,00",
                    "c6": "100,00",
                    "c7": "200,00",
                },
            ),
        ],
    )
    ir = DocumentIR(
        document_id="onmck",
        file_name="onmck.docx",
        media_type="docx",
        blocks=[
            DocumentBlockIR(
                block_id="block",
                order=0,
                type="table",
                table=table,
            )
        ],
    )
    item = ProcurementItem(
        item_id="item",
        name=ExtractedValue(
            normalized_value="Картридж",
            evidence=[
                Evidence(
                    document_id="onmck",
                    block_id="block",
                    table_id="prices",
                    row=3,
                    column=1,
                    quote="Картридж",
                )
            ],
        ),
    )

    parsed = _onmck_items(ir, [item])[0]

    assert [price.unit_price.raw_value for price in parsed.supplier_prices] == [
        "100,00"
    ]
    assert parsed.selected_unit_price.raw_value == "100,00"
    assert parsed.calculated_total.raw_value == "200,00"


def test_plan_key_value_table_extracts_codes_and_aggregate_quantity():
    table = TableIR(
        table_id="plan",
        row_count=4,
        columns=[
            TableColumnIR(index=0, alias="c0", header_path=["field"]),
            TableColumnIR(index=1, alias="c1", header_path=["value_1"]),
            TableColumnIR(index=2, alias="c2", header_path=["value_2"]),
        ],
        kind="key_value",
        rows=[
            TableRowIR(
                row_id="plan.r0",
                row=0,
                values={
                    "c1": "Наименование объекта закупки",
                    "c2": "Поставка картриджей",
                },
            ),
            TableRowIR(
                row_id="plan.r1",
                row=1,
                values={
                    "c1": "Код ОКПД 2 и его наименования",
                    "c2": "20.59.12.120 - Картридж 26.20.40.120 - Фотобарабан",
                },
            ),
            TableRowIR(
                row_id="plan.r2",
                row=2,
                values={
                    "c1": "Код позиции КТРУ",
                    "c2": "20.59.12.120-00000002 - Картридж",
                },
            ),
            TableRowIR(
                row_id="plan.r3",
                row=3,
                values={"c1": "Количество", "c2": "39 шт"},
            ),
        ],
    )
    ir = DocumentIR(
        document_id="plan",
        file_name="plan.docx",
        media_type="docx",
        blocks=[
            DocumentBlockIR(
                block_id="block",
                order=0,
                type="table",
                table=table,
            )
        ],
    )

    item = _plan_item_from_key_value(ir)

    assert item is not None
    assert [value.normalized_value for value in item.okpd2] == [
        "20.59.12.120",
        "26.20.40.120",
    ]
    assert [value.normalized_value for value in item.ktru] == [
        "20.59.12.120-00000002"
    ]
    assert item.quantity.normalized_value == {"value": 39, "unit": "шт"}
    assert item.unit.normalized_value == "шт"


def test_nmck_without_currency_is_rendered_as_rubles():
    package = ProcurementPackage(
        contract=ContractSummary(
            document_id="contract",
            detected_type=DocumentType.CONTRACT,
            price=[ExtractedValue(normalized_value="350000")],
        )
    )

    report = build_report_text([], package=package)

    assert "- 350 000 рублей" in report


def test_case_only_subject_difference_is_not_reported():
    result = AnalyzerResult(
        analyzer="items_consistency",
        findings=[
            Finding(
                rule_id="subject-case",
                severity=FindingSeverity.MANUAL_REVIEW,
                status=FindingStatus.UNCERTAIN,
                title="Различие в наименовании предмета",
                message=(
                    "Расхождение только в регистре/оформлении, "
                    "признаков смыслового противоречия не выявлено."
                ),
                source="llm",
            )
        ],
    )

    assert _validate_findings(result, "items_consistency", {}) == []


def test_item_id_comparison_is_not_reported():
    finding = Finding(
        rule_id="items_consistency.missing_item",
        severity=FindingSeverity.ERROR,
        status=FindingStatus.FAILED,
        title="Позиции отсутствуют по item-идентификаторам",
        message="Сопоставление выполнялось по item_id между документами.",
        source="llm",
    )
    result = AnalyzerResult(
        analyzer="items_consistency",
        findings=[finding],
    )

    assert _validate_findings(result, "items_consistency", {}) == []
    assert "item_id" not in build_report_text([finding])


def test_additional_findings_are_grouped_by_topic():
    findings = [
        Finding(
            rule_id="delivery_and_finance.vat",
            severity=FindingSeverity.MANUAL_REVIEW,
            status=FindingStatus.UNCERTAIN,
            title="НДС не проверен",
            message="Недостаточно данных.",
            source="llm",
        ),
        Finding(
            rule_id="legal_and_completeness.penalties",
            severity=FindingSeverity.MANUAL_REVIEW,
            status=FindingStatus.UNCERTAIN,
            title="Штрафы не проверены",
            message="Недостаточно данных.",
            source="llm",
        ),
    ]
    package = ProcurementPackage()

    report = build_report_text(findings, package=package)

    assert "6) Дополнительные проверки" in report
    assert "Финансовые условия, НДС и порядок оплаты:" in report
    assert "Штрафы, пени и неустойки:" in report
