import json
from decimal import Decimal

from summary_model.checks import run_checks
from summary_model.checks_cli import main as checks_cli_main
from summary_model.extraction_models import (
    ContractDraftSchema,
    ContractSpecificationItem,
    DocumentEnvelope,
    ExplanatoryNoteSchema,
    MoneyValue,
    NmckItem,
    NmckJustificationSchema,
    PriceSource,
    ProcurementPackageExtraction,
    PurchaseItemCharacteristic,
    PurchaseDescriptionSchema,
    PurchaseItem,
    PurchaseRequestSchema,
    RawField,
    RequestAttachment,
    ScheduleApplicationSchema,
    SecurityValue,
    SupplierPrice,
)


def _base_package() -> ProcurementPackageExtraction:
    item = PurchaseItem(
        row_number="1",
        name="Картридж",
        okpd2_code="20.59.12.120",
        ktru_code="20.59.12.120-00000002",
        unit="шт",
        quantity=Decimal("2"),
    )
    nmck_item = NmckItem(
        row_number="1",
        name="Картридж",
        okpd2_code="20.59.12.120",
        ktru_code="20.59.12.120-00000002",
        unit="шт",
        quantity=Decimal("2"),
        supplier_prices=[
            SupplierPrice(source_id="supplier_1", unit_price=Decimal("100"), row_total=Decimal("200")),
            SupplierPrice(source_id="supplier_2", unit_price=Decimal("120"), row_total=Decimal("240")),
            SupplierPrice(source_id="supplier_3", unit_price=Decimal("110"), row_total=Decimal("220")),
        ],
        selected_min_unit_price=Decimal("100"),
        calculated_min_unit_price=Decimal("100"),
        row_total_declared=Decimal("200"),
        row_total_calculated=Decimal("200"),
        is_declared_min_price_correct=True,
        is_row_total_correct=True,
    )
    return ProcurementPackageExtraction(
        package_id="test-package",
        files=[
            DocumentEnvelope(file_name="request.docx", document_type="purchase_request"),
            DocumentEnvelope(file_name="plan.docx", document_type="schedule_application"),
            DocumentEnvelope(file_name="onmck.docx", document_type="nmck_justification"),
            DocumentEnvelope(file_name="ooz.docx", document_type="purchase_description"),
            DocumentEnvelope(file_name="contract.docx", document_type="contract_draft"),
            DocumentEnvelope(file_name="note.docx", document_type="explanatory_note"),
        ],
        purchase_request=PurchaseRequestSchema(
            purchase_subject="Картридж",
            nmck=MoneyValue(amount=Decimal("200")),
            attachments=[
                RequestAttachment(
                    title_raw="Заявка",
                    normalized_document_type="schedule_application",
                    attachment_kind="other",
                ),
                RequestAttachment(
                    title_raw="ОНМЦК",
                    normalized_document_type="nmck_justification",
                    attachment_kind="other",
                ),
                RequestAttachment(
                    title_raw="ООЗ",
                    normalized_document_type="purchase_description",
                    attachment_kind="purchase_description",
                ),
                RequestAttachment(
                    title_raw="Проект контракта",
                    normalized_document_type="contract_draft",
                    attachment_kind="other",
                ),
                RequestAttachment(
                    title_raw="Пояснительная записка",
                    normalized_document_type="explanatory_note",
                    attachment_kind="other",
                ),
            ],
        ),
        schedule_application=ScheduleApplicationSchema(
            raw_fields=[],
            raw_fields_dict={"НМЦК": "200"},
            purchase_subject="Картридж",
            okpd2_codes=["20.59.12.120"],
            ktru_codes=["20.59.12.120-00000002"],
            nmck=MoneyValue(amount=Decimal("200")),
            funding_source_text="средства областного бюджета",
        ),
        nmck_justification=NmckJustificationSchema(
            total_amount=MoneyValue(amount=Decimal("200")),
            price_sources=[
                PriceSource(source_id="supplier_1", raw_header="Поставщик 1"),
                PriceSource(source_id="supplier_2", raw_header="Поставщик 2"),
                PriceSource(source_id="supplier_3", raw_header="Поставщик 3"),
            ],
            items=[nmck_item],
        ),
        purchase_description=PurchaseDescriptionSchema(items=[item]),
        contract_draft=ContractDraftSchema(
            price=MoneyValue(amount=Decimal("200")),
            funding_source="Источник финансирования: средства областного бюджета",
            contract_security_raw="Обеспечение исполнения контракта не предусмотрено.",
            contract_security=SecurityValue(
                raw="Обеспечение исполнения контракта не предусмотрено.",
                is_not_required=True,
            ),
            referenced_attachments=[
                RequestAttachment(
                    number="1",
                    title_raw="Описание объекта закупки",
                    normalized_document_type="purchase_description",
                    attachment_kind="purchase_description",
                ),
                RequestAttachment(
                    number="2",
                    title_raw="Акт приема-передачи товара",
                    attachment_kind="acceptance_act_form",
                ),
                RequestAttachment(
                    number="3",
                    title_raw="Спецификация",
                    attachment_kind="contract_specification",
                ),
            ],
            items=[item],
            specification_items=[
                ContractSpecificationItem(
                    row_number="1",
                    name="Картридж",
                    unit="шт",
                    quantity=Decimal("2"),
                    total_price=Decimal("200"),
                )
            ],
        ),
        explanatory_note=ExplanatoryNoteSchema(
            nmck=MoneyValue(amount=Decimal("200")),
            subject="Картридж",
        ),
        commercial_offers_found_count=0,
        commercial_offers_required_count=3,
    )


def _by_id(report):
    return {item.check_id: item for item in report.results}


def test_checks_pass_core_strict_rules_and_create_manual_reviews():
    package = _base_package()
    package.schedule_application.raw_fields = [
        RawField(
            key="НМЦК",
            value="200",
            normalized_key="нмцк",
            is_empty=False,
            is_negative_value=False,
        )
    ]

    report = run_checks(package)
    checks = _by_id(report)

    assert checks["strict.nmck.amounts"].status == "passed"
    assert checks["strict.onmck.arithmetic"].status == "passed"
    assert checks["strict.onmck.min_price"].status == "passed"
    assert checks["strict.codes.okpd2"].status == "passed"
    assert checks["strict.codes.ktru"].status == "passed"
    assert checks["strict.funding_source"].status == "passed"
    assert checks["strict.securities"].status == "passed"
    assert checks["strict.contract.attachments"].status == "passed"
    assert checks["manual.commercial_offers.count"].status == "manual_review"
    assert checks["manual.ktru.characteristics"].status == "manual_review"


def test_missing_required_document_fails():
    package = _base_package()
    package.contract_draft = None

    checks = _by_id(run_checks(package))

    assert checks["strict.package.contract_draft"].status == "failed"


def test_nmck_mismatch_fails():
    package = _base_package()
    package.contract_draft.price = MoneyValue(amount=Decimal("201"))

    checks = _by_id(run_checks(package))

    assert checks["strict.nmck.amounts"].status == "failed"


def test_schedule_negative_values_are_valid_filled_fields():
    package = _base_package()
    package.schedule_application.raw_fields = [
        RawField(
            key="Наличие преференций для СМП",
            value="нет",
            normalized_key="наличие_преференций_для_смп",
            is_empty=False,
            is_negative_value=True,
        )
    ]
    package.schedule_application.negative_value_fields = ["Наличие преференций для СМП"]

    checks = _by_id(run_checks(package))

    assert checks["strict.schedule.fields"].status == "passed"
    assert checks["strict.schedule.fields"].details["valid_negative_fields"] == [
        "Наличие преференций для СМП"
    ]
    assert checks["strict.schedule.fields"].details["summary_lines"] == ["строк извлечено: 1"]


def test_onmck_arithmetic_and_min_price_fail():
    package = _base_package()
    item = package.nmck_justification.items[0]
    item.row_total_declared = Decimal("201")
    item.selected_min_unit_price = Decimal("110")

    checks = _by_id(run_checks(package))

    assert checks["strict.onmck.arithmetic"].status == "failed"
    assert checks["strict.onmck.min_price"].status == "failed"


def test_code_mismatch_fails_and_missing_codes_manual_review():
    package = _base_package()
    package.contract_draft.items[0].okpd2_code = "99.99.99.999"
    checks = _by_id(run_checks(package))
    assert checks["strict.codes.okpd2"].status == "failed"
    assert checks["strict.codes.okpd2"].details["missing_by_document"]["schedule_application"] == [
        "99.99.99.999"
    ]

    package.schedule_application.okpd2_codes = []
    package.schedule_application.ktru_codes = []
    package.purchase_description.items[0].okpd2_code = None
    package.purchase_description.items[0].ktru_code = None
    package.contract_draft.items[0].okpd2_code = None
    package.contract_draft.items[0].ktru_code = None
    package.nmck_justification.items[0].okpd2_code = None
    package.nmck_justification.items[0].ktru_code = None
    checks = _by_id(run_checks(package))
    assert checks["strict.codes.okpd2"].status == "manual_review"


def test_okpd2_check_uses_ktru_prefix_when_explicit_okpd2_is_missing():
    package = _base_package()
    package.purchase_description.items[0].okpd2_code = None
    package.contract_draft.items[0].okpd2_code = None
    package.nmck_justification.items[0].okpd2_code = None

    checks = _by_id(run_checks(package))

    assert checks["strict.codes.okpd2"].status == "passed"


def test_contract_attachment_missing_tables_fails():
    package = _base_package()
    package.contract_draft.items = []
    package.contract_draft.specification_items = []

    checks = _by_id(run_checks(package))

    assert checks["strict.contract.attachments"].status == "failed"


def test_checks_cli_writes_artifacts(tmp_path):
    package = _base_package()
    input_path = tmp_path / "package.json"
    output_dir = tmp_path / "checks"
    input_path.write_text(package.model_dump_json(indent=2), encoding="utf-8")

    exit_code = checks_cli_main(
        [
            "--input",
            str(input_path),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    assert (output_dir / "checks.json").exists()
    assert (output_dir / "report.txt").exists()
    assert (output_dir / "run.json").exists()


def test_checks_cli_with_mocked_semantic_llm_replaces_manual_stubs(tmp_path, monkeypatch):
    from summary_model import checks_cli

    package = _base_package()
    input_path = tmp_path / "package.json"
    output_dir = tmp_path / "checks"
    input_path.write_text(package.model_dump_json(indent=2), encoding="utf-8")

    def fake_semantic(package):
        from summary_model.checks.models import CheckResult

        return [
            CheckResult(
                check_id="semantic.subject",
                title="Предмет закупки",
                severity="info",
                status="passed",
                mode="semantic",
                message="Предмет согласован.",
                report_text="Предмет согласован.",
                details={"summary_lines": ["Заявка: Картридж", "Контракт: Картридж"]},
            )
        ], {"calls": 1, "model": "fake"}

    monkeypatch.setattr(checks_cli, "run_semantic_llm_checks", fake_semantic)

    exit_code = checks_cli.main(
        [
            "--input",
            str(input_path),
            "--output-dir",
            str(output_dir),
            "--with-llm",
        ]
    )

    payload = json.loads((output_dir / "checks.json").read_text(encoding="utf-8"))
    semantic_subject = [
        item for item in payload["results"] if item["check_id"] == "semantic.subject"
    ][0]
    run_payload = json.loads((output_dir / "run.json").read_text(encoding="utf-8"))

    assert exit_code == 0
    assert semantic_subject["status"] == "passed"
    assert run_payload["with_llm"] is True
    assert run_payload["llm_metrics"]["calls"] == 1


def test_semantic_llm_result_uses_deterministic_document_labels():
    from summary_model.checks.semantic_llm import _to_check_result
    from summary_model.checks.semantic_llm import _semantic_summary_lines, SemanticCheckFinding

    package = _base_package()
    finding = SemanticCheckFinding(
        check_id="semantic.subject",
        status="passed",
        message="Предмет совпадает.",
        compared_values=["Документ: Картридж"],
    )

    result = _to_check_result(
        finding,
        "Предмет закупки",
        _semantic_summary_lines(package, "semantic.subject"),
    )

    assert "Документ: Картридж" not in result.details["summary_lines"]
    assert "Заявка в план-график: Картридж" in result.details["summary_lines"]


class FakeKtruRegistry:
    def get_ktru_characteristics_detailed(self, ktru_code):
        return {
            "Цвет": {"values": ["Черный"], "required": True},
            "Ресурс": {"values": [">= 100"], "required": False},
        }

    def get_ktru_common_info(self, ktru_code):
        return {"okpd2_code": "20.59.12.120"}

    def check_okpd2(self, okpd2):
        class Result:
            found = False
            table_id = None
            position = None
            row = None

        return Result()


class FallbackKtruRegistry(FakeKtruRegistry):
    def __init__(self):
        self.common_info_called = False

    def get_ktru_common_info(self, ktru_code):
        self.common_info_called = True
        return {"okpd2_code": "22.11.11.000"}

    def get_ktru_characteristics_detailed(self, ktru_code):
        if not self.common_info_called:
            raise RuntimeError("common info fallback was not called first")
        return {
            "Индекс категории скорости": {"values": ["T"], "required": True},
        }


def test_ktru_adapter_checks_characteristics_without_docx_parsing():
    from summary_model.checks.ktru_adapter import run_ktru_characteristic_checks

    package = _base_package()
    package.purchase_description.items[0].characteristics = [
        PurchaseItemCharacteristic(name="Цвет", value="Черный"),
        PurchaseItemCharacteristic(name="Ресурс", value="150"),
        PurchaseItemCharacteristic(name="Доп. параметр", value="Да"),
    ]

    results = {
        item.check_id: item
        for item in run_ktru_characteristic_checks(package, registry=FakeKtruRegistry())
    }

    assert results["manual.ktru.characteristics"].status == "passed"
    assert results["manual.ktru.characteristics"].details["checked_characteristics"] == 2
    assert results["manual.ktru.additional"].status == "passed"
    assert results["manual.ktru.additional"].details["extra_characteristics"]


def test_ktru_adapter_uses_common_info_fallback_and_visual_aliases():
    from summary_model.checks.ktru_adapter import run_ktru_characteristic_checks

    package = _base_package()
    package.purchase_description.items[0].characteristics = [
        PurchaseItemCharacteristic(name="Индекс категории скорости", value="Т"),
    ]
    registry = FallbackKtruRegistry()

    results = {
        item.check_id: item
        for item in run_ktru_characteristic_checks(package, registry=registry)
    }

    assert registry.common_info_called is True
    assert results["manual.ktru.characteristics"].status == "passed"
    assert results["manual.ktru.characteristics"].details["checked_characteristics"] == 1
    assert not results["manual.ktru.characteristics"].details["invalid_values"]


def test_checks_cli_with_mocked_ktru_replaces_only_ktru_manual_items(tmp_path, monkeypatch):
    from summary_model import checks_cli
    from summary_model.checks.models import CheckResult

    package = _base_package()
    input_path = tmp_path / "package.json"
    output_dir = tmp_path / "checks"
    input_path.write_text(package.model_dump_json(indent=2), encoding="utf-8")

    def fake_ktru(package, **_kwargs):
        return [
            CheckResult(
                check_id="manual.ktru.characteristics",
                title="КТРУ-характеристики",
                severity="info",
                status="passed",
                mode="manual_review",
                message="КТРУ проверены.",
                report_text="КТРУ проверены.",
            ),
            CheckResult(
                check_id="manual.ktru.additional",
                title="Дополнительные характеристики КТРУ",
                severity="info",
                status="passed",
                mode="manual_review",
                message="Дополнительные характеристики допустимы.",
                report_text="Дополнительные характеристики допустимы.",
            ),
        ]

    monkeypatch.setattr(checks_cli, "run_ktru_characteristic_checks", fake_ktru)

    exit_code = checks_cli.main(
        [
            "--input",
            str(input_path),
            "--output-dir",
            str(output_dir),
            "--with-ktru",
        ]
    )

    payload = json.loads((output_dir / "checks.json").read_text(encoding="utf-8"))
    by_id = {item["check_id"]: item for item in payload["results"]}
    run_payload = json.loads((output_dir / "run.json").read_text(encoding="utf-8"))

    assert exit_code == 0
    assert by_id["manual.ktru.characteristics"]["status"] == "passed"
    assert by_id["manual.ktru.additional"]["status"] == "passed"
    assert by_id["manual.national_regime_1875"]["status"] == "manual_review"
    assert "manual.penalties" not in by_id
    assert run_payload["with_ktru"] is True
