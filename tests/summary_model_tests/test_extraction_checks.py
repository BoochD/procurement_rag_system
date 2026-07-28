import json
import shutil
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from summary_model.checks import run_checks
from summary_model.checks import runner as checks_runner
from summary_model.checks.models import ProcurementChecksReport
from summary_model.checks.report import build_checks_report_text, build_commercial_offer_report_text
from summary_model.checks_cli import main as checks_cli_main
from summary_model.extraction_models import (
    AdditionalCharacteristicsJustification,
    CommercialOfferItem,
    CommercialOfferSchema,
    ContractDraftSchema,
    ContractSpecificationItem,
    DocumentEnvelope,
    ExplanatoryNoteSchema,
    MoneyValue,
    NmckItem,
    PenaltyClause,
    NmckJustificationSchema,
    PriceSource,
    ProcurementPackageExtraction,
    ProcurementStage,
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
from summary_model.vlm_lab.models import VlmNmckItem, VlmPurchaseItem, VlmStage


@contextmanager
def _runtime_temp_dir(prefix: str):
    runtime_dir = Path("runtime")
    runtime_dir.mkdir(exist_ok=True)
    path = runtime_dir / f"summary_model_{prefix}{uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


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
            contract_security_raw="Обеспечение исполнения контракта не предусмотрено.",
            contract_security=SecurityValue(
                raw="Обеспечение исполнения контракта не предусмотрено.",
                is_not_required=True,
            ),
            subcontract_smp_sonko_required_raw="Отсутствует",
            subcontract_smp_sonko_required=False,
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
            penalty_clauses=[
                PenaltyClause(
                    party="customer",
                    obligation_kind="value_obligation",
                    raw_text="Размер штрафа устанавливается в размере 1000 рублей.",
                    amount=Decimal("1000"),
                ),
                PenaltyClause(
                    party="supplier",
                    obligation_kind="value_obligation",
                    raw_text="Штраф составляет 10 процентов цены Контракта.",
                    percent=Decimal("10"),
                ),
                PenaltyClause(
                    party="supplier",
                    obligation_kind="non_value_obligation",
                    raw_text="Штраф за ненадлежащее исполнение обязательства без стоимостного выражения составляет 1000 рублей.",
                    amount=Decimal("1000"),
                ),
            ],
            peni_clauses=[
                PenaltyClause(
                    party="supplier",
                    obligation_kind="delay_peni",
                    raw_text="Пени начисляются за каждый день просрочки исполнения обязательства.",
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
    assert checks["strict.contract.penalties"].status == "passed"
    assert checks["strict.smp_sonko_subcontract"].status == "passed"
    assert checks["strict.contract.attachments"].status == "passed"
    assert checks["manual.commercial_offers.count"].status == "manual_review"
    assert checks["manual.ktru.characteristics"].status == "manual_review"


def test_stage_check_keeps_report_ready_tables_without_json_dump():
    package = _base_package()
    package.schedule_application.stages = [
        ProcurementStage(stage_number="1", stage_name="Поставка", service_term_text="по 13.07.2026", quantity_text="1 усл. ед.")
    ]
    package.purchase_description.stages = [
        ProcurementStage(stage_number="1", stage_name="Поставка", service_term_text="по 13.07.2026", quantity_text="1 усл. ед.")
    ]
    package.contract_draft.stages = [
        ProcurementStage(stage_number="1", stage_name="Поставка", service_term_text="по 13.07.2026", quantity_text="1 усл. ед.")
    ]
    package.nmck_justification.stages = [
        ProcurementStage(stage_number="1", stage_name="Поставка", price=MoneyValue(amount=Decimal("200")))
    ]

    check = _by_id(run_checks(package))["strict.plan.stages"]

    assert len(check.details["stage_tables"]) == 4
    assert check.details["stage_tables"][0]["rows"][0]["name"] == "Поставка"
    assert "stage_number" not in str(check.details["stage_tables"])


def test_schedule_stage_merge_fills_sparse_table_rows_from_plan_fields():
    from summary_model.extraction_pipeline import _merge_schedule_stages

    merged = _merge_schedule_stages(
        [ProcurementStage(stage_number="1", evidence="table")],
        [ProcurementStage(
            stage_number="1",
            stage_name="1 этап",
            service_term_text="с даты заключения по 13.07.2026",
            quantity_text="1 усл. ед.",
            evidence="raw_fields",
        )],
    )

    assert len(merged) == 1
    assert merged[0].service_term_text == "с даты заключения по 13.07.2026"
    assert merged[0].quantity_text == "1 усл. ед."


def test_vlm_notes_accept_string_dict_and_list_values():
    assert VlmPurchaseItem(notes="Пояснение").notes == ["Пояснение"]
    assert VlmStage(notes={"reason": "пустая строка"}).notes == ["пустая строка"]
    assert VlmNmckItem(notes=["первая", "вторая"]).notes == ["первая", "вторая"]


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


def test_nmck_money_compares_kopeks_and_report_lines():
    package = _base_package()
    package.schedule_application.nmck = MoneyValue(raw="200 рублей 00 копеек", amount=Decimal("200.00"))
    package.purchase_request.nmck = MoneyValue(raw="200,00", amount=Decimal("200.00"))
    package.nmck_justification.total_amount = MoneyValue(raw="200.00", amount=Decimal("200.00"))
    package.contract_draft.price = MoneyValue(raw="Итого к оплате: 200 рублей 00 копеек", amount=Decimal("200.00"))
    package.explanatory_note.nmck = MoneyValue(raw="200 рублей 00 копеек", amount=Decimal("200.00"))

    checks = _by_id(run_checks(package))

    assert checks["strict.nmck.amounts"].status == "passed"
    assert "Заявка в план-график: 200.00" in checks["strict.nmck.amounts"].details["summary_lines"]


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


def test_smp_sonko_subcontract_fails_when_plan_requires_percent_missing_in_contract():
    package = _base_package()
    package.schedule_application.subcontract_smp_sonko_required_raw = "Требуется привлечь СМП/СОНКО"
    package.schedule_application.subcontract_smp_sonko_required = True
    package.schedule_application.subcontract_smp_sonko_percent_raw = "25%"
    package.schedule_application.subcontract_smp_sonko_percent = Decimal("25")
    package.contract_draft.subcontract_smp_sonko_required_raw = "Исполнитель обязан привлечь СМП/СОНКО"
    package.contract_draft.subcontract_smp_sonko_required = True
    package.contract_draft.subcontract_smp_sonko_percent = None

    checks = _by_id(run_checks(package))

    assert checks["strict.smp_sonko_subcontract"].status == "failed"
    assert "процент" in checks["strict.smp_sonko_subcontract"].message.casefold()


def test_smp_sonko_plain_contract_clause_matches_plan_without_semantic_llm():
    from summary_model.extraction_pipeline import (
        _contract_smp_sonko_clause,
        _contract_smp_sonko_required,
        _percent_from_text,
    )

    contract_text = """
    5. Права и обязанности Сторон
    5.4.8. Представлять сведения о привлекаемых им соисполнителях в установленный срок.
    5.4.11. Привлечь к исполнению Контракта соисполнителей из числа субъектов
    малого предпринимательства, социально ориентированных некоммерческих
    организаций в объеме 90 (девяноста) процентов от цены Контракта.
    6. Приемка услуг
    """
    clause = _contract_smp_sonko_clause(contract_text)

    assert clause is not None
    assert clause.startswith("5.4.11.")
    assert "5.4.8" not in clause
    assert _contract_smp_sonko_required(clause) is True
    assert _percent_from_text(clause) == Decimal("90")

    package = _base_package()
    package.schedule_application.subcontract_smp_sonko_required_raw = (
        "Предусмотрена в объеме 90% от цены Контракта"
    )
    package.schedule_application.subcontract_smp_sonko_required = True
    package.schedule_application.subcontract_smp_sonko_percent = Decimal("90")
    package.contract_draft.subcontract_smp_sonko_required_raw = clause
    package.contract_draft.subcontract_smp_sonko_required = True
    package.contract_draft.subcontract_smp_sonko_percent = Decimal("90")

    checks = _by_id(run_checks(package))

    assert checks["strict.smp_sonko_subcontract"].status == "passed"
    assert "согласованы" in checks["strict.smp_sonko_subcontract"].message


def test_plan_ground_truth_text_fields_warn_on_mismatch_and_pass_on_match():
    package = _base_package()
    package.schedule_application.purchase_subject = "Поставка картриджей"
    package.purchase_description.purchase_subject = "Поставка картриджей"
    package.contract_draft.subject = "Поставка картриджей"
    package.schedule_application.delivery_term_text = "15 рабочих дней"
    package.purchase_description.delivery_term_text = "15 рабочих дней"
    package.contract_draft.delivery_term_text = "30 календарных дней"
    package.schedule_application.delivery_place = "г. Новосибирск, ул. Крамского 40"
    package.purchase_description.delivery_place = "г. Новосибирск, ул. Крамского 40"
    package.contract_draft.delivery_place = "г. Новосибирск, ул. Крамского 40"
    package.schedule_application.contract_execution_term_text = "до 31.12.2026"
    package.contract_draft.contract_execution_term_text = "до 31.12.2026"

    checks = _by_id(run_checks(package))

    assert checks["strict.plan.subject"].status == "passed"
    assert checks["strict.plan.delivery_term"].status == "warning"
    assert checks["strict.plan.delivery_place"].status == "passed"
    assert checks["strict.plan.contract_execution_term"].status == "passed"


def test_plan_ground_truth_missing_plan_value_requires_manual_review():
    package = _base_package()
    package.schedule_application.delivery_place = None
    package.purchase_description.delivery_place = "г. Новосибирск"
    package.contract_draft.delivery_place = "г. Новосибирск"

    checks = _by_id(run_checks(package))

    assert checks["strict.plan.delivery_place"].status == "manual_review"
    assert "не найдено поле" in checks["strict.plan.delivery_place"].message


def test_delivery_term_uses_matching_stages_only_when_direct_fields_are_missing():
    package = _base_package()
    package.schedule_application.delivery_term_text = "оказание услуг по этапам"
    package.schedule_application.stages = [
        ProcurementStage(stage_number="1", service_term_text="по 13.07.2026")
    ]
    package.purchase_description.delivery_term_text = None
    package.purchase_description.stages = [
        ProcurementStage(stage_number="1", service_term_text="по 13.07.2026")
    ]
    package.contract_draft.delivery_term_text = None
    package.contract_draft.stages = [
        ProcurementStage(stage_number="1", service_term_text="по 13.07.2026")
    ]

    checks = _by_id(run_checks(package))

    result = checks["strict.plan.delivery_term"]
    assert result.status == "passed"
    assert result.details["comparison_source"] == "stages"


def test_funding_source_eis_reference_requires_manual_review():
    package = _base_package()
    package.contract_draft.funding_source = (
        "Источник финансирования указывается в структурированном виде электронной формы "
        "контракта в единой информационной системе в сфере закупок."
    )

    checks = _by_id(run_checks(package))

    result = checks["strict.funding_source"]
    assert result.status == "manual_review"
    assert "напрямую не указан" in result.message


def test_stages_against_plan_handle_absence_missing_structure_and_mismatch():
    package = _base_package()
    checks = _by_id(run_checks(package))
    assert checks["strict.plan.stages"].status == "passed"

    package.schedule_application.has_stages = True
    package.schedule_application.stages = []
    checks = _by_id(run_checks(package))
    assert checks["strict.plan.stages"].status == "manual_review"

    package.schedule_application.stages = [
        ProcurementStage(stage_number="1", stage_name="1 этап", service_term_text="10 дней"),
        ProcurementStage(stage_number="2", stage_name="2 этап", service_term_text="20 дней"),
    ]
    package.purchase_description.stages = [
        ProcurementStage(stage_number="1", stage_name="1 этап", service_term_text="10 дней")
    ]
    checks = _by_id(run_checks(package))
    assert checks["strict.plan.stages"].status == "failed"
    assert any("ООЗ" in line for line in checks["strict.plan.stages"].details["summary_lines"])


def test_stage_results_argument_replaces_deterministic_stage_check():
    from summary_model.checks.models import CheckResult

    package = _base_package()
    injected = CheckResult(
        check_id="strict.plan.stages",
        title="Этапы исполнения",
        severity="info",
        status="manual_review",
        mode="semantic",
        message="LLM fallback требует проверки.",
        report_text="LLM fallback требует проверки.",
        details={"summary_lines": ["Заявка в план-график: этапы требуют проверки"]},
    )

    checks = _by_id(run_checks(package, stage_results=[injected]))

    assert checks["strict.plan.stages"].status == "manual_review"
    assert checks["strict.plan.stages"].message == "LLM fallback требует проверки."


def test_smp_sonko_subcontract_passes_absent_and_fails_percent_mismatch():
    package = _base_package()
    package.schedule_application.subcontract_smp_sonko_required = False
    package.schedule_application.subcontract_smp_sonko_required_raw = "Отсутствует"
    package.contract_draft.subcontract_smp_sonko_required = None
    package.contract_draft.subcontract_smp_sonko_required_raw = None
    checks = _by_id(run_checks(package))
    assert checks["strict.smp_sonko_subcontract"].status == "passed"

    package.schedule_application.subcontract_smp_sonko_required = True
    package.schedule_application.subcontract_smp_sonko_required_raw = "Требуется привлечь СМП/СОНКО"
    package.schedule_application.subcontract_smp_sonko_percent = Decimal("90")
    package.contract_draft.subcontract_smp_sonko_required = True
    package.contract_draft.subcontract_smp_sonko_required_raw = "Привлечь СМП/СОНКО"
    package.contract_draft.subcontract_smp_sonko_percent = Decimal("25")
    checks = _by_id(run_checks(package))
    assert checks["strict.smp_sonko_subcontract"].status == "failed"
    assert "Процент" in checks["strict.smp_sonko_subcontract"].message


def test_contract_penalties_check_pp1042_thresholds():
    package = _base_package()

    checks = _by_id(run_checks(package))

    result = checks["strict.contract.penalties"]
    assert result.status == "passed"
    assert result.details["expected_supplier_value_percent"] == "10"
    assert result.details["expected_fixed_fine"] == "1000.00"
    assert any("Штраф заказчика" in line for line in result.details["summary_lines"])


def test_contract_penalties_fail_on_wrong_supplier_percent():
    package = _base_package()
    package.contract_draft.penalty_clauses[1].percent = Decimal("5")

    checks = _by_id(run_checks(package))

    result = checks["strict.contract.penalties"]
    assert result.status == "failed"
    assert any("ожидалось 10%" in line for line in result.details["summary_lines"])


def test_contract_penalties_use_structured_clauses_when_section_is_placeholder():
    package = _base_package()
    package.contract_draft.responsibility_section_text = (
        "7.1. ... (full responsibility text preserved in known_extracted)."
    )

    checks = _by_id(run_checks(package))

    result = checks["strict.contract.penalties"]
    assert result.status == "passed"
    assert any("структурированных пунктах" in line for line in result.details["summary_lines"])


def test_responsibility_section_parser_accepts_heading_variants_and_stops_at_next_section():
    from summary_model.extraction_pipeline import _contract_responsibility_section

    for heading in (
        "7. ОТВЕТСТВЕННОСТЬ СТОРОН",
        "7. Ответственности сторон",
        "Ответственность Сторон",
    ):
        section = _contract_responsibility_section(
            f"{heading}\n7.1. За нарушение начисляются штраф и пеня.\n"
            "8. Обеспечение исполнения Контракта\n8.1. Размер обеспечения составляет 5%."
        )

        assert section is not None
        assert "штраф и пеня" in section
        assert "Размер обеспечения" not in section


def test_dedicated_penalty_llm_receives_full_section_and_applicable_threshold():
    from summary_model.checks.penalty_llm import run_penalty_llm_checks

    package = _base_package()
    package.schedule_application.nmck = MoneyValue(amount=Decimal("106312006"))
    package.contract_draft.price = None
    package.contract_draft.penalty_clauses = []
    package.contract_draft.peni_clauses = []
    package.contract_draft.responsibility_section_text = (
        "7. ОТВЕТСТВЕННОСТЬ СТОРОН\n"
        "7.4. За каждый факт неисполнения Заказчиком обязательств размер штрафа: "
        "1000 рублей, если цена не превышает 3 млн рублей; "
        "5000 рублей, если цена от 3 до 50 млн рублей; "
        "10000 рублей, если цена от 50 до 100 млн рублей; "
        "100000 рублей, если цена Контракта превышает 100 млн рублей.\n"
        "7.6. Штраф Исполнителя за обязательство без стоимостного выражения составляет "
        "100000 рублей при цене Контракта свыше 100 млн рублей.\n"
        "7.7. Штраф Исполнителя за стоимостное обязательство составляет 0,5 процента.\n"
        "7.8. Пеня начисляется как 1/300 действующей ключевой ставки."
    )

    class FakePenaltyClient:
        def __init__(self):
            self.payload = None

        def extract(self, schema, system_prompt, payload):
            self.payload = json.loads(payload)
            return schema(
                penalty_clauses=[
                    PenaltyClause(
                        party="customer",
                        obligation_kind="value_obligation",
                        raw_text="100000 рублей, если цена Контракта превышает 100 млн рублей.",
                        amount=Decimal("100000"),
                        evidence="п. 7.4",
                    ),
                    PenaltyClause(
                        party="supplier",
                        obligation_kind="non_value_obligation",
                        raw_text="Штраф составляет 100000 рублей.",
                        amount=Decimal("100000"),
                        evidence="п. 7.6",
                    ),
                    PenaltyClause(
                        party="supplier",
                        obligation_kind="value_obligation",
                        raw_text="Штраф составляет 0,5 процента.",
                        percent=Decimal("0.5"),
                        evidence="п. 7.7",
                    ),
                ],
                peni_clauses=[
                    PenaltyClause(
                        party="supplier",
                        obligation_kind="delay_peni",
                        raw_text="Пеня начисляется как 1/300 действующей ключевой ставки.",
                        basis="1/300 действующей ключевой ставки",
                        evidence="п. 7.8",
                    )
                ],
            ), None

        def metrics(self):
            return {"calls": 1}

    client = FakePenaltyClient()
    results, metrics = run_penalty_llm_checks(package, llm_client=client)

    assert metrics["calls"] == 1
    assert client.payload["nmck"] == "106312006.00"
    assert "7.4." in client.payload["responsibility_section_text"]
    assert results[0].status == "passed"
    extraction = results[0].details["penalty_llm_extraction"]
    assert extraction["penalty_clauses"][0]["amount"] == "100000"
    assert extraction["penalty_clauses"][0]["evidence"] == "п. 7.4"


def test_penalty_llm_is_not_called_without_usable_responsibility_section():
    from summary_model.checks.penalty_llm import run_penalty_llm_checks

    package = _base_package()
    package.contract_draft.penalty_clauses = []
    package.contract_draft.peni_clauses = []
    package.contract_draft.responsibility_section_text = "7. Ответственность сторон."

    class UnexpectedClient:
        def extract(self, *args, **kwargs):
            raise AssertionError("Penalty LLM must not be called")

    results, metrics = run_penalty_llm_checks(package, llm_client=UnexpectedClient())

    assert metrics["calls"] == 0
    assert metrics["skipped_reason"] == "penalty_terms_not_found_in_section"
    assert results[0].status == "manual_review"


def test_penalty_llm_failure_returns_manual_review_instead_of_general_llm_data():
    from summary_model.checks.penalty_llm import run_penalty_llm_checks

    package = _base_package()
    package.contract_draft.penalty_clauses = []
    package.contract_draft.peni_clauses = []
    package.contract_draft.responsibility_section_text = (
        "7. Ответственность сторон\n7.1. За нарушение обязательств начисляются штраф и пеня."
    )

    class FailedClient:
        def extract(self, *args, **kwargs):
            return None, "provider unavailable"

        def metrics(self):
            return {"calls": 1, "error": "provider unavailable"}

    results, metrics = run_penalty_llm_checks(package, llm_client=FailedClient())

    assert metrics["calls"] == 1
    assert results[0].status == "manual_review"
    assert results[0].details["penalty_llm_error"] == "provider unavailable"


def test_stage_llm_runs_only_for_manual_review_and_excludes_prices():
    from summary_model.checks.stage_llm import run_stage_llm_checks

    package = _base_package()
    package.schedule_application.has_stages = True
    package.schedule_application.stages = [
        ProcurementStage(
            stage_number="1",
            stage_name="Первый этап",
            service_term_text="по 13.07.2026",
            price=MoneyValue(amount=Decimal("40000")),
        )
    ]
    package.purchase_description.stages = []
    package.contract_draft.stages = []
    package.nmck_justification.stages = []

    class FakeStageClient:
        def __init__(self):
            self.payload = None

        def extract(self, schema, system_prompt, payload):
            self.payload = json.loads(payload)
            return schema(
                status="manual_review",
                message="Не хватает этапов ООЗ и контракта.",
                summary_lines=["ПГ: этап 1", "ООЗ: этапы не найдены"],
            ), None

        def metrics(self):
            return {"calls": 1}

    client = FakeStageClient()
    results, metrics = run_stage_llm_checks(package, llm_client=client)

    assert metrics["calls"] == 1
    assert results[0].status == "manual_review"
    assert "price" not in client.payload["schedule_application"]["stages"][0]

    package.purchase_description.stages = [
        ProcurementStage(stage_number="2", service_term_text="по 20.07.2026")
    ]
    assert run_stage_llm_checks(package, llm_client=client) == (None, None)


def test_general_semantic_checks_do_not_include_stages():
    from summary_model.checks.semantic_llm import SEMANTIC_CHECK_IDS, _semantic_payload

    package = _base_package()
    payload = _semantic_payload(package)

    assert "semantic.stages" not in SEMANTIC_CHECK_IDS
    assert "stages" not in payload["schedule_application"]
    assert "stages" not in payload["purchase_description"]
    assert "stages" not in payload["contract_draft"]
    assert "semantic.stages" not in _by_id(run_checks(package))


def test_onmck_arithmetic_and_min_price_fail():
    package = _base_package()
    item = package.nmck_justification.items[0]
    item.row_total_declared = Decimal("201")
    item.selected_min_unit_price = Decimal("110")

    checks = _by_id(run_checks(package))

    assert checks["strict.onmck.arithmetic"].status == "failed"
    assert checks["strict.onmck.min_price"].status == "failed"


def test_onmck_supplier_price_report_contains_variation_and_supplier_labels():
    package = _base_package()

    checks = _by_id(run_checks(package))

    supplier_check = checks["strict.onmck.supplier_prices"]
    min_check = checks["strict.onmck.min_price"]

    assert supplier_check.status == "passed"
    assert any("коэффициент вариации" in line for line in supplier_check.details["summary_lines"])
    assert any("Поставщик1 = 100" in line for line in supplier_check.details["summary_lines"])
    assert any("выбранная минимальная цена 100" in line for line in min_check.details["summary_lines"])


def _commercial_offer(
    *,
    supplier_name: str,
    unit_price: Decimal,
    quantity: Decimal = Decimal("2"),
    unit: str = "шт",
) -> CommercialOfferSchema:
    return CommercialOfferSchema(
        supplier_name=supplier_name,
        inn="5400000000",
        outgoing_number=f"{supplier_name}-1",
        offer_date="2026-01-01",
        delivery_term_text="15 рабочих дней",
        delivery_place="г. Новосибирск",
        vat_text="НДС не облагается",
        total_amount=MoneyValue(amount=quantity * unit_price),
        items=[
            CommercialOfferItem(
                row_number="1",
                name="Картридж",
                okpd2_code="20.59.12.120",
                ktru_code="20.59.12.120-00000002",
                unit=unit,
                quantity=quantity,
                unit_price=unit_price,
                total_price=quantity * unit_price,
            )
        ],
    )


def test_commercial_offers_count_and_onmck_match_pass_with_three_offers():
    package = _base_package()
    package.commercial_offers_found_count = 3
    package.commercial_offers = [
        _commercial_offer(supplier_name="Поставщик 1", unit_price=Decimal("100")),
        _commercial_offer(supplier_name="Поставщик 2", unit_price=Decimal("120")),
        _commercial_offer(supplier_name="Поставщик 3", unit_price=Decimal("110")),
    ]

    checks = _by_id(run_checks(package))

    assert checks["manual.commercial_offers.count"].status == "passed"
    assert checks["manual.commercial_offers.content"].status == "passed"
    assert checks["manual.commercial_offers.onmck"].status == "passed"
    assert any("коэффициент вариации" in line for line in checks["manual.commercial_offers.onmck"].details["summary_lines"])


def test_commercial_offers_onmck_match_fails_on_price_quantity_or_unit_mismatch():
    package = _base_package()
    package.commercial_offers_found_count = 3
    package.commercial_offers = [
        _commercial_offer(supplier_name="Поставщик 1", unit_price=Decimal("101")),
        _commercial_offer(supplier_name="Поставщик 2", unit_price=Decimal("120"), quantity=Decimal("3")),
        _commercial_offer(supplier_name="Поставщик 3", unit_price=Decimal("110"), unit="компл"),
    ]

    checks = _by_id(run_checks(package))

    result = checks["manual.commercial_offers.onmck"]
    assert result.status == "failed"
    assert any("цена за единицу" in line for line in result.details["failures"])
    assert any("количество" in line for line in result.details["failures"])
    assert any("единица" in line for line in result.details["failures"])


def test_commercial_offer_minimum_is_manual_when_one_offer_item_is_unmatched():
    package = _base_package()
    package.nmck_justification.items[0].supplier_prices[2].unit_price = Decimal("90")
    package.nmck_justification.items[0].selected_min_unit_price = Decimal("90")
    package.commercial_offers_found_count = 3
    third_offer = _commercial_offer(supplier_name="Поставщик 3", unit_price=Decimal("90"))
    third_offer.items[0].name = "Неоднозначное программное обеспечение"
    third_offer.items[0].okpd2_code = None
    third_offer.items[0].ktru_code = None
    package.commercial_offers = [
        _commercial_offer(supplier_name="Поставщик 1", unit_price=Decimal("100")),
        _commercial_offer(supplier_name="Поставщик 2", unit_price=Decimal("120")),
        third_offer,
    ]

    checks = _by_id(run_checks(package))

    result = checks["manual.commercial_offers.onmck"]
    assert result.status == "manual_review"
    assert result.details["failures"] == []
    assert any("минимальную цену" in line for line in result.details["manual_review"])


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


def test_securities_pass_when_not_required_and_manual_when_contract_missing_value():
    package = _base_package()
    checks = _by_id(run_checks(package))
    assert checks["strict.securities"].status == "passed"

    package.schedule_application.contract_security = SecurityValue(
        raw="Обеспечение исполнения контракта 5%",
        value_percent=Decimal("5"),
    )
    package.contract_draft.contract_security = None
    package.contract_draft.contract_security_raw = None

    checks = _by_id(run_checks(package))

    assert checks["strict.securities"].status == "manual_review"
    assert "в проекте контракта" in checks["strict.securities"].message


def test_security_sizes_are_reported_separately_and_structured_eis_value_is_manual():
    package = _base_package()
    schedule = package.schedule_application
    contract = package.contract_draft
    schedule.nmck = MoneyValue(amount=Decimal("106312006"))
    schedule.procurement_method = "auction"
    schedule.application_security = SecurityValue(raw="5%", value_percent=Decimal("5"))
    schedule.contract_security = SecurityValue(raw="30%", value_percent=Decimal("30"))
    schedule.warranty_security = SecurityValue(raw="1%", value_percent=Decimal("1"))
    contract.contract_security = SecurityValue(
        raw="8.2. Размер обеспечения исполнения контракта указывается в структурированном виде ЕИС."
    )
    contract.warranty_security = SecurityValue(
        raw="8.11. Размер обеспечения гарантийных обязательств указывается в структурированном виде ЕИС."
    )

    checks = _by_id(run_checks(package))

    assert checks["strict.application_security"].status == "passed"
    assert checks["strict.plan.contract_security_limits"].status == "passed"
    assert checks["strict.plan.warranty_security_limits"].status == "passed"
    assert checks["strict.securities"].status == "manual_review"
    assert checks["strict.warranty_security"].status == "manual_review"
    assert "30%" in checks["strict.securities"].details["summary_lines"][0]
    assert "1%" in checks["strict.warranty_security"].details["summary_lines"][0]
    assert "см. п. 8.2" in checks["strict.securities"].details["summary_lines"][1]
    assert "см. п. 8.11" in checks["strict.warranty_security"].details["summary_lines"][1]

    report_text = build_checks_report_text(run_checks(package))
    assert "числовой размер указан в структурированной форме ЕИС (см. п. 8.2)" in report_text
    assert "Размер обеспечения исполнения контракта указывается" not in report_text


def test_contract_security_extraction_prefers_structured_eis_clause_and_reference():
    from summary_model.extraction_pipeline import (
        _contract_security_text,
        _contract_warranty_security_text,
        _security_value,
    )

    text = """8.1. Обеспечение исполнения Контракта предусмотрено.
8.2. Размер обеспечения исполнения Контракта указывается в структурированном виде ЕИС.
8.11. Обеспечение гарантийных обязательств устанавливается в размере, указанном в структурированном виде ЕИС."""

    contract_security = _security_value(_contract_security_text(text))
    warranty_security = _security_value(_contract_warranty_security_text(text))

    assert contract_security.source_reference == "п. 8.2"
    assert warranty_security.source_reference == "п. 8.11"


def test_report_renders_plan_regulatory_section_before_registry_sections():
    package = _base_package()
    report_text = build_checks_report_text(run_checks(package))

    assert report_text.index("1) Нормативные проверки заявки в план-график:") < report_text.index(
        "2) Проверка КТРУ через сервис zakupki.gov.ru:"
    )


def test_application_security_uses_twenty_million_boundary():
    package = _base_package()
    schedule = package.schedule_application
    schedule.procurement_method = "auction"
    schedule.application_security = SecurityValue(raw="1%", value_percent=Decimal("1"))
    schedule.nmck = MoneyValue(amount=Decimal("20000000"))
    assert _by_id(run_checks(package))["strict.application_security"].status == "passed"

    schedule.application_security = SecurityValue(raw="1.01%", value_percent=Decimal("1.01"))
    assert _by_id(run_checks(package))["strict.application_security"].status == "failed"

    schedule.nmck = MoneyValue(amount=Decimal("20000000.01"))
    schedule.application_security = SecurityValue(raw="5%", value_percent=Decimal("5"))
    assert _by_id(run_checks(package))["strict.application_security"].status == "passed"


def test_contract_security_uses_fifty_million_boundary():
    package = _base_package()
    schedule = package.schedule_application
    schedule.nmck = MoneyValue(amount=Decimal("50000000"))
    schedule.contract_security = SecurityValue(raw="0.5%", value_percent=Decimal("0.5"))
    assert _by_id(run_checks(package))["strict.plan.contract_security_limits"].status == "passed"

    schedule.contract_security = SecurityValue(raw="0.49%", value_percent=Decimal("0.49"))
    assert _by_id(run_checks(package))["strict.plan.contract_security_limits"].status == "failed"

    schedule.nmck = MoneyValue(amount=Decimal("50000000.01"))
    schedule.contract_security = SecurityValue(raw="9.99%", value_percent=Decimal("9.99"))
    assert _by_id(run_checks(package))["strict.plan.contract_security_limits"].status == "failed"

    schedule.contract_security = SecurityValue(raw="10%", value_percent=Decimal("10"))
    assert _by_id(run_checks(package))["strict.plan.contract_security_limits"].status == "passed"


def test_security_special_case_is_manual_review_not_false_failure():
    package = _base_package()
    schedule = package.schedule_application
    schedule.procurement_method = "auction"
    schedule.nmck = MoneyValue(amount=Decimal("2000000"))
    schedule.contract_security = SecurityValue(raw="40%", value_percent=Decimal("40"))
    schedule.raw_fields.append(RawField(key="Авансовый платеж", value="40%", is_empty=False))

    checks = _by_id(run_checks(package))

    assert checks["strict.plan.contract_security_limits"].status == "manual_review"


def test_warranty_security_limit_is_ten_percent():
    package = _base_package()
    schedule = package.schedule_application
    schedule.warranty_security = SecurityValue(raw="10%", value_percent=Decimal("10"))
    assert _by_id(run_checks(package))["strict.plan.warranty_security_limits"].status == "passed"

    schedule.warranty_security = SecurityValue(raw="10.01%", value_percent=Decimal("10.01"))
    assert _by_id(run_checks(package))["strict.plan.warranty_security_limits"].status == "failed"


def test_plan_national_regime_rows_are_checked_for_presence():
    package = _base_package()
    package.schedule_application.okpd2_codes = []
    package.schedule_application.national_regime_fields = [
        RawField(key="17.1.", value="Не применяются", is_empty=False),
        RawField(key="17.2.", value="Не применяются", is_empty=False),
        RawField(key="17.3.", value="Преимущества: Нет", is_empty=False),
    ]

    checks = _by_id(run_checks(package))

    assert checks["strict.plan.national_regime_fields"].status == "passed"
    assert len(checks["strict.plan.national_regime_fields"].details["summary_lines"]) == 3


def test_plan_national_regime_requires_matching_rows_for_plan_codes():
    class FakeRegistry:
        def check_okpd2(self, code):
            if code == "58.29.31.000":
                return SimpleNamespace(found=True, table_id="table_01", matched_okpd2="58.29.31")
            if code == "26.20.14.120":
                return SimpleNamespace(found=True, table_id="table_02", matched_okpd2="26.20.14")
            return SimpleNamespace(found=False)

    package = _base_package()
    package.schedule_application.okpd2_codes = ["58.29.31.000", "26.20.14.120"]
    package.schedule_application.national_regime_fields = [
        RawField(key="17.1.", value="Запрет: 58.29.31", is_empty=False),
        RawField(key="17.2.", value="Ограничение: 26.20.14", is_empty=False),
        RawField(key="17.3.", value="Нет", is_empty=False),
    ]
    checks = _by_id(run_checks(package, pp1875_registry=FakeRegistry()))
    assert checks["strict.plan.national_regime_fields"].status == "passed"
    assert "ОКПД2 58.29.31.000" in checks["strict.plan.national_regime_fields"].details["summary_lines"][3]

    package.schedule_application.national_regime_fields[1] = RawField(
        key="17.2.", value="Не применяется", is_empty=False
    )
    checks = _by_id(run_checks(package, pp1875_registry=FakeRegistry()))
    assert checks["strict.plan.national_regime_fields"].status == "failed"


def test_plan_national_regime_missing_advantages_row_is_warning():
    package = _base_package()
    package.schedule_application.okpd2_codes = []
    package.schedule_application.national_regime_fields = [
        RawField(key="17.1.", value="Нет", is_empty=False),
        RawField(key="17.2.", value="Нет", is_empty=False),
    ]
    checks = _by_id(run_checks(package))
    assert checks["strict.plan.national_regime_fields"].status == "warning"


def test_plan_national_regime_registry_initialization_failure_is_manual_review(monkeypatch):
    package = _base_package()
    package.schedule_application.okpd2_codes = ["58.29.31.000"]
    package.schedule_application.national_regime_fields = [
        RawField(key="17.1.", value="Запрет: 58.29.31", is_empty=False),
        RawField(key="17.2.", value="Нет", is_empty=False),
        RawField(key="17.3.", value="Нет", is_empty=False),
    ]
    monkeypatch.setattr(
        checks_runner,
        "ProcurementReferenceRegistry",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("registry unavailable")),
    )

    checks = _by_id(run_checks(package))
    result = checks["strict.plan.national_regime_fields"]

    assert result.status == "manual_review"
    assert "Локальный реестр ПП №1875 недоступен" in result.message
    assert "registry unavailable" in result.details["registry_errors"][0]


def test_checks_cli_writes_artifacts():
    package = _base_package()
    with _runtime_temp_dir("checks_cli_") as tmp_path:
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


def test_checks_cli_with_mocked_semantic_llm_replaces_manual_stubs(monkeypatch):
    from summary_model import checks_cli

    package = _base_package()

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

    with _runtime_temp_dir("checks_semantic_") as tmp_path:
        input_path = tmp_path / "package.json"
        output_dir = tmp_path / "checks"
        input_path.write_text(package.model_dump_json(indent=2), encoding="utf-8")

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
    assert results["manual.ktru.additional"].status == "failed"
    assert results["manual.ktru.additional"].details["extra_characteristics"]
    assert results["manual.ktru.characteristics"].details["characteristic_rows"]
    assert results["manual.ktru.additional"].details["additional_rows"]


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


def test_ktru_adapter_uses_unambiguous_plan_code_for_extra_characteristics():
    from summary_model.checks.ktru_adapter import run_ktru_characteristic_checks

    class PlanFallbackRegistry(FakeKtruRegistry):
        def get_ktru_common_info(self, ktru_code):
            return {}

    package = _base_package()
    item = package.purchase_description.items[0]
    item.name = "Сервер"
    item.okpd2_code = None
    item.ktru_code = "26.20.14.000-00000189"
    item.characteristics = [PurchaseItemCharacteristic(name="Доп. параметр", value="Да")]
    package.schedule_application.included_goods = [
        PurchaseItem(
            name="Серверы (однопроцессорные, двухпроцессорные)",
            okpd2_code="26.20.14.120",
        )
    ]

    results = {
        result.check_id: result
        for result in run_ktru_characteristic_checks(package, registry=PlanFallbackRegistry())
    }

    row = results["manual.ktru.additional"].details["additional_rows"][0]
    assert row["okpd2_code"] is None
    assert row["plan_okpd2_code"] == "26.20.14.120"
    assert row["rule_okpd2_code"] == "26.20.14.120"
    assert row["rule_okpd2_source"] == "позиция ПГ по наименованию"


def test_ktru_adapter_prefers_official_okpd2_from_card_for_rule():
    from summary_model.checks.ktru_adapter import run_ktru_characteristic_checks

    class OfficialCodeRegistry(FakeKtruRegistry):
        def get_ktru_common_info(self, ktru_code):
            return {"okpd2_code": "26.20.14.000"}

    package = _base_package()
    item = package.purchase_description.items[0]
    item.name = "Сервер"
    item.okpd2_code = None
    item.ktru_code = "26.20.14.000-00000189"
    item.characteristics = [PurchaseItemCharacteristic(name="Доп. параметр", value="Да")]
    package.schedule_application.included_goods = [
        PurchaseItem(name="Серверы", okpd2_code="26.20.14.120")
    ]

    results = {
        result.check_id: result
        for result in run_ktru_characteristic_checks(package, registry=OfficialCodeRegistry())
    }
    row = results["manual.ktru.additional"].details["additional_rows"][0]

    assert row["plan_okpd2_code"] == "26.20.14.120"
    assert row["rule_okpd2_code"] == "26.20.14.000"
    assert row["rule_okpd2_source"] == "карточка КТРУ"


class SpecialPositionKtruRegistry(FakeKtruRegistry):
    def get_ktru_common_info(self, ktru_code):
        return {"okpd2_code": "26.20.14.000"}

    def check_okpd2(self, okpd2):
        return SimpleNamespace(
            found=True,
            table_id="table_02",
            position="198",
            matched_okpd2="26.20.14.000",
            reference_name="Серверы",
            row=None,
        )


def _additional_justification(source_type: str, text: str = "Требование обусловлено совместимостью"):
    return AdditionalCharacteristicsJustification(
        scope_text="Сервер",
        characteristic_names=["Доп. параметр"],
        justification_text=text,
        evidence_text=text,
        source_document_type=source_type,
        source_table_id=f"{source_type}-table-2",
        source_table_index=2,
        extraction_method="vlm_table",
    )


def test_ktru_special_position_is_forbidden_only_when_plan_regime_is_confirmed():
    from summary_model.checks.ktru_adapter import run_ktru_characteristic_checks

    package = _base_package()
    item = package.purchase_description.items[0]
    item.okpd2_code = "26.20.14.120"
    item.ktru_code = "26.20.14.000-00000189"
    item.characteristics = [PurchaseItemCharacteristic(name="Доп. параметр", value="Да")]
    package.purchase_description.additional_characteristics_justifications = [
        _additional_justification("purchase_description")
    ]
    package.schedule_application.national_regime_fields = [
        RawField(key="17.2", value="Ограничение применяется: 26.20.14.120")
    ]

    confirmed = {
        result.check_id: result
        for result in run_ktru_characteristic_checks(package, registry=SpecialPositionKtruRegistry())
    }
    assert confirmed["manual.ktru.additional"].status == "warning"
    assert confirmed["manual.ktru.additional"].details["assessments"][0]["decision"] == "restricted"
    assert confirmed["manual.ktru.additional"].details["assessments"][0]["plan_regime"]["status"] == "confirmed"
    assert confirmed["manual.ktru.additional"].details["additional_rows"][0]["plan_regime"]["field_match_aliases"] == ["26.20.14.120"]

    package.schedule_application.national_regime_fields = []
    missing = {
        result.check_id: result
        for result in run_ktru_characteristic_checks(package, registry=SpecialPositionKtruRegistry())
    }
    assert missing["manual.ktru.additional"].status == "manual_review"


def test_ktru_uses_ooz_justification_without_contract_table_comparison():
    from summary_model.checks.ktru_adapter import run_ktru_characteristic_checks

    package = _base_package()
    package.purchase_description.items[0].characteristics = [
        PurchaseItemCharacteristic(name="Доп. параметр", value="Да")
    ]
    package.purchase_description.additional_characteristics_justifications = [
        _additional_justification("purchase_description")
    ]
    results = {
        result.check_id: result
        for result in run_ktru_characteristic_checks(package, registry=FakeKtruRegistry())
    }

    assert results["manual.ktru.additional"].status == "passed"
    assert "strict.ktru.additional_justification_tables" not in results
    assert results["manual.ktru.additional"].details["assessments"][0]["decision"] == "allowed"


def test_ktru_partial_justification_table_degrades_to_manual_review():
    from summary_model.checks.ktru_adapter import run_ktru_characteristic_checks

    package = _base_package()
    package.purchase_description.items[0].characteristics = [
        PurchaseItemCharacteristic(name="Доп. параметр", value="Да")
    ]
    package.purchase_description.additional_characteristics_justifications = [
        AdditionalCharacteristicsJustification(
            source_table_id="ooz-table-2",
            extraction_method="table_candidate",
            parser_warnings=["contents_not_extracted"],
        )
    ]

    results = {
        result.check_id: result
        for result in run_ktru_characteristic_checks(package, registry=FakeKtruRegistry())
    }

    assert results["manual.ktru.additional"].status == "manual_review"
    assert "strict.ktru.additional_justification_tables" not in results


def test_checks_cli_with_mocked_ktru_replaces_only_ktru_manual_items(monkeypatch):
    from summary_model import checks_cli
    from summary_model.checks.models import CheckResult

    package = _base_package()

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

    with _runtime_temp_dir("checks_ktru_") as tmp_path:
        input_path = tmp_path / "package.json"
        output_dir = tmp_path / "checks"
        input_path.write_text(package.model_dump_json(indent=2), encoding="utf-8")

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
        assert by_id["manual.national_regime_1875"]["status"] == "warning"
        assert "manual.penalties" not in by_id
        assert run_payload["with_ktru"] is True


def test_commercial_offer_money_value_coercion():
    from summary_model.extraction_models import CommercialOfferSchema, MoneyValue
    from decimal import Decimal

    # Test float input from VLM
    offer_float = CommercialOfferSchema.model_validate({"supplier_name": "ООО ТЕСТ", "total_amount": 107648484.0})
    assert isinstance(offer_float.total_amount, MoneyValue)
    assert offer_float.total_amount.amount == Decimal("107648484.0")
    assert offer_float.supplier_name == "ООО ТЕСТ"

    # Test int input
    offer_int = CommercialOfferSchema.model_validate({"total_amount": 50000})
    assert offer_int.total_amount.amount == Decimal("50000")

    # Test dict input
    offer_dict = CommercialOfferSchema.model_validate({"total_amount": {"raw": "50 000 руб.", "amount": "50000"}})
    assert offer_dict.total_amount.amount == Decimal("50000")
    assert offer_dict.total_amount.raw == "50 000 руб."


def test_procurement_method_translation_and_auction_guard():
    from summary_model.checks.semantic_llm import _apply_procurement_method_guard, SemanticCheckFinding
    from summary_model.extraction_models import ProcurementPackageExtraction, ScheduleApplicationSchema

    pkg = ProcurementPackageExtraction(
        schedule_application=ScheduleApplicationSchema(procurement_method_raw="Электронный аукцион")
    )
    finding = SemanticCheckFinding(
        check_id="semantic.procurement_method",
        status="warning",
        message="Способ закупки: auction",
        compared_values=["Заявка в план-график: auction"],
    )

    guarded = _apply_procurement_method_guard(pkg, finding)
    assert guarded.status == "passed"
    assert "Электронный аукцион" in guarded.message
    assert "обоснование ЕП не требуется" in guarded.message
    assert "auction" not in guarded.compared_values[0]
    assert "Электронный аукцион" in guarded.compared_values[0]


def test_smp_preference_guard_does_not_mix_subcontracting_requirement():
    from summary_model.checks.semantic_llm import _apply_smp_preference_guard, SemanticCheckFinding

    package = ProcurementPackageExtraction(
        schedule_application=ScheduleApplicationSchema(
            smp_preference_raw="Отсутствуют",
            smp_preference=False,
            subcontract_smp_sonko_required_raw="Предусмотрена в объеме 90%",
            subcontract_smp_sonko_required=True,
            subcontract_smp_sonko_percent=Decimal("90"),
        )
    )
    finding = SemanticCheckFinding(
        check_id="semantic.smp_preferences",
        status="failed",
        message="Найдены противоречия.",
        compared_values=["Заявка: Отсутствуют", "Проект контракта: 90%"],
    )

    guarded = _apply_smp_preference_guard(package, finding)

    assert guarded.status == "warning"
    assert guarded.compared_values == ["Заявка в план-график: Отсутствуют"]
    assert "разные условия" in guarded.message


def test_bool_parser_accepts_absent_plural_as_false():
    from summary_model.extraction_pipeline import _bool_from_text

    assert _bool_from_text("Отсутствуют") is False


def test_stage_table_markdown_rendering():
    from summary_model.checks.models import CheckResult
    from summary_model.checks.report import _render_titled_result

    result = CheckResult(
            check_id="strict.plan.stages",
        title="Этапы исполнения",
        severity="info",
        status="passed",
        mode="semantic",
        message="Этапы согласованы.",
        report_text="Порядок и сроки этапов согласованы.",
        details={
                "stage_tables": [{
                    "title": "Заявка в план-график (ПГ)",
                    "kind": "standard",
                    "rows": [{
                        "number": "1",
                        "name": "Поставка",
                        "term": "по 13.07.2026",
                        "quantity": "1 усл. ед.",
                        "price": "Не выделена",
                    }],
                }]
        },
    )

    lines = _render_titled_result(result)
    text = "\n".join(lines)
    assert "#### 📌 Таблица 1: Заявка в план-график (ПГ)" in text
    assert "| 1 | Поставка | по 13.07.2026 | 1 усл. ед. | Не выделена |" in text
    assert "[{" not in text


def test_mocked_vlm_commercial_offer_extraction(monkeypatch):
    from summary_model.commercial_offer_vlm import extract_commercial_offer_with_vlm, CommercialOfferVlmOptions

    def mock_completion(*_args, **_kwargs):
        class Choice:
            message = type("Msg", (), {"content": '{"supplier_name": "ООО Ромашка", "total_amount": 123456.0, "items": []}'})()
        class Response:
            choices = [Choice()]
            def model_dump(self, mode="json"):
                return {"choices": [{"message": {"content": self.choices[0].message.content}}]}
        return Response()

    import shared_modules.llm_models as llm_mod
    class FakeOpenAI:
        def __init__(self, *args, **kwargs):
            self.chat = type("Chat", (), {"completions": type("Comp", (), {"create": mock_completion})()})()

    monkeypatch.setattr(llm_mod, "get_chatGPT_client", lambda: FakeOpenAI())
    monkeypatch.setattr("summary_model.commercial_offer_vlm.get_chatGPT_client", lambda: FakeOpenAI())
    monkeypatch.setattr(
        "summary_model.commercial_offer_vlm._pdf_images",
        lambda p, **_kwargs: [{"page": 1, "mime": "image/png", "data": b"fake-image"}],
    )

    with _runtime_temp_dir("vlm_offer_") as tmp_path:
        fake_pdf = tmp_path / "offer.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4 fake pdf content")

        result = extract_commercial_offer_with_vlm(fake_pdf, options=CommercialOfferVlmOptions(enabled=True))
        assert result.offer.supplier_name == "ООО Ромашка"
        assert result.offer.total_amount.amount == Decimal("123456.0")


def test_vlm_defaults_to_dedicated_model_without_changing_text_model():
    from shared_modules.llm_models import OPENAI_MODEL, OPENAI_VLM_MODEL
    from summary_model.commercial_offer_vlm import CommercialOfferVlmOptions
    from summary_model.vlm_fallback import VlmFallbackOptions

    assert OPENAI_MODEL
    assert OPENAI_VLM_MODEL
    assert CommercialOfferVlmOptions().model == OPENAI_VLM_MODEL
    assert VlmFallbackOptions().model == OPENAI_VLM_MODEL


def test_commercial_offer_lab_report_uses_production_section_renderer():
    package = ProcurementPackageExtraction(
        commercial_offers=[
            CommercialOfferSchema(
                supplier_name="ООО Ромашка",
                outgoing_number="42",
                items=[CommercialOfferItem(name="Товар", quantity=Decimal("1"))],
                parser_warnings=["Для VLM-парсинга PDF КП нужен пакет PyMuPDF."],
            )
        ],
        commercial_offers_found_count=1,
        commercial_offers_missing=True,
    )
    checks = run_checks(package)
    commercial_report = ProcurementChecksReport.from_results(
        package_id=package.package_id,
        results=[
            result
            for result in checks.results
            if result.check_id.startswith("manual.commercial_offers.")
        ],
    )

    text = build_commercial_offer_report_text(commercial_report)

    assert "6) Коммерческие предложения:" in text
    assert "ООО Ромашка" in text
    assert "Особенности распознавания" in text
    assert "PyMuPDF" in text


def test_scanned_commercial_offer_uses_all_pages_in_one_vlm_request(monkeypatch):
    from summary_model.commercial_offer_vlm import extract_commercial_offer_with_vlm, CommercialOfferVlmOptions

    response_content = (
        '{"supplier_name":"ООО Тест","outgoing_number":"42","total_amount":300,'
        '"delivery_term_text":"до 01.09.2026","items":['
        '{"row_number":"1.1","name":"Услуга","unit":"усл. ед.","quantity":1,"unit_price":100,"total_price":100},'
        '{"row_number":"1.2","name":"Сервер","unit":"шт.","quantity":2,"unit_price":100,"total_price":200}]}'
    )

    class FakeResponse:
        def __init__(self, content):
            self.content = content

        def model_dump(self, mode="json"):
            return {"choices": [{"message": {"content": self.content}}]}

    class FakeOpenAI:
        def __init__(self):
            self.chat = type(
                "Chat",
                (),
                {"completions": type("Completions", (), {"create": lambda _self, **_kwargs: FakeResponse(response_content)})()},
            )()

    monkeypatch.setattr("summary_model.commercial_offer_vlm.get_chatGPT_client", lambda: FakeOpenAI())
    monkeypatch.setattr(
        "summary_model.commercial_offer_vlm._pdf_images",
        lambda _path, **_kwargs: [
            {"page": 1, "mime": "image/png", "data": b"page-1", "text": ""},
            {"page": 2, "mime": "image/png", "data": b"page-2", "text": ""},
            {"page": 3, "mime": "image/png", "data": b"appendix", "text": ""},
        ],
    )

    with _runtime_temp_dir("vlm_offer_pages_") as tmp_path:
        path = tmp_path / "offer.pdf"
        path.write_bytes(b"%PDF")
        result = extract_commercial_offer_with_vlm(path, options=CommercialOfferVlmOptions())

    assert result.metrics["calls"] == 1
    assert result.offer.supplier_name == "ООО Тест"
    assert result.offer.total_amount.amount == Decimal("300")
    assert [item.row_number for item in result.offer.items] == ["1.1", "1.2"]


def test_commercial_offer_vlm_prices_are_not_overwritten_by_text_layer():
    from summary_model.commercial_offer_vlm import _merge_offer_with_deterministic

    vlm_offer = CommercialOfferSchema(
        items=[
            CommercialOfferItem(
                row_number="1.1",
                name="Сервер",
                unit="шт.",
                quantity=Decimal("4"),
                unit_price=Decimal("10245000"),
                total_price=Decimal("40980000"),
            )
        ]
    )
    text_offer = CommercialOfferSchema(
        items=[
            CommercialOfferItem(
                row_number="1.1",
                name="Сервер",
                unit="шт.",
                quantity=Decimal("4"),
                unit_price=Decimal("10300000"),
                total_price=Decimal("41200000"),
                trademark="DEPO",
            )
        ]
    )

    result = _merge_offer_with_deterministic(vlm_offer, text_offer)

    assert result.items[0].unit_price == Decimal("10245000")
    assert result.items[0].trademark == "DEPO"


def test_commercial_offer_removes_only_arithmetically_proven_aggregate_row():
    from summary_model.commercial_offer_vlm import _remove_proven_aggregate_items

    offer = CommercialOfferSchema(
        total_amount=MoneyValue(amount=Decimal("100")),
        items=[
            CommercialOfferItem(
                row_number="1",
                name="Итого услуги",
                quantity=Decimal("1"),
                unit_price=Decimal("100"),
                total_price=Decimal("100"),
            ),
            CommercialOfferItem(
                row_number="1.1",
                name="Этап 1",
                quantity=Decimal("1"),
                unit_price=Decimal("40"),
                total_price=Decimal("40"),
            ),
            CommercialOfferItem(
                row_number="1.2",
                name="Этап 2",
                quantity=Decimal("1"),
                unit_price=Decimal("60"),
                total_price=Decimal("60"),
            ),
        ],
    )

    cleaned, removed = _remove_proven_aggregate_items(offer)

    assert removed == 1
    assert [item.row_number for item in cleaned.items] == ["1.1", "1.2"]
    assert any("Агрегатная итоговая строка" in warning for warning in cleaned.parser_warnings)


def test_commercial_offer_keeps_total_like_row_when_detail_sum_is_not_proven():
    from summary_model.commercial_offer_vlm import _remove_proven_aggregate_items

    offer = CommercialOfferSchema(
        total_amount=MoneyValue(amount=Decimal("100")),
        items=[
            CommercialOfferItem(total_price=Decimal("100")),
            CommercialOfferItem(total_price=Decimal("40")),
            CommercialOfferItem(total_price=Decimal("50")),
        ],
    )

    cleaned, removed = _remove_proven_aggregate_items(offer)

    assert removed == 0
    assert len(cleaned.items) == 3


def test_commercial_offer_removes_aggregate_using_quantity_times_unit_price():
    from summary_model.commercial_offer_vlm import _remove_proven_aggregate_items

    offer = CommercialOfferSchema(
        total_amount=MoneyValue(amount=Decimal("100")),
        items=[
            CommercialOfferItem(name="Оказание услуг, в том числе", quantity=1, unit_price=100),
            CommercialOfferItem(name="Этап 1", quantity=2, unit_price=20),
            CommercialOfferItem(name="Этап 2", quantity=3, unit_price=20),
        ],
    )

    cleaned, removed = _remove_proven_aggregate_items(offer)

    assert removed == 1
    assert [item.name for item in cleaned.items] == ["Этап 1", "Этап 2"]


def test_commercial_offer_separates_aggregate_and_non_price_appendix_rows():
    from summary_model.commercial_offer_vlm import (
        _remove_noncommercial_reference_items,
        _remove_proven_aggregate_items,
    )

    offer = CommercialOfferSchema(
        total_amount=MoneyValue(amount=Decimal("100")),
        items=[
            CommercialOfferItem(name="Итого", total_price=Decimal("100")),
            CommercialOfferItem(
                name="Этап 1",
                quantity=Decimal("1"),
                unit_price=Decimal("40"),
                total_price=Decimal("40"),
            ),
            CommercialOfferItem(
                name="Этап 2",
                quantity=Decimal("1"),
                unit_price=Decimal("60"),
                total_price=Decimal("60"),
            ),
            CommercialOfferItem(
                name="Техническая характеристика приложения",
                quantity=Decimal("4"),
            ),
        ],
    )

    without_aggregate, aggregate_removed = _remove_proven_aggregate_items(offer)
    cleaned, references_removed = _remove_noncommercial_reference_items(without_aggregate)

    assert aggregate_removed == 1
    assert references_removed == 1
    assert [item.name for item in cleaned.items] == ["Этап 1", "Этап 2"]
    assert any("справочных строк" in warning for warning in cleaned.parser_warnings)


def test_commercial_offer_arithmetic_checks_rows_and_declared_total():
    package = ProcurementPackageExtraction(
        commercial_offers=[
            CommercialOfferSchema(
                supplier_name="ООО Тест",
                total_amount=MoneyValue(amount=Decimal("100")),
                items=[
                    CommercialOfferItem(
                        name="Этап 1",
                        quantity=Decimal("2"),
                        unit_price=Decimal("20"),
                        total_price=Decimal("40"),
                    ),
                    CommercialOfferItem(
                        name="Этап 2",
                        quantity=Decimal("1"),
                        unit_price=Decimal("60"),
                        total_price=Decimal("60"),
                    ),
                ],
            )
        ],
        commercial_offers_found_count=1,
    )

    result = _by_id(run_checks(package))["manual.commercial_offers.content"]

    assert result.details["arithmetic_rows"][0]["status"] == "passed"
    assert result.details["arithmetic_rows"][0]["checked_rows"] == 2
    assert result.details["arithmetic_rows"][0]["calculated_total"] == "100.00"

    package.commercial_offers[0].items[1].total_price = Decimal("61")
    result = _by_id(run_checks(package))["manual.commercial_offers.content"]

    assert result.status == "failed"
    assert result.details["arithmetic_rows"][0]["status"] == "failed"
    assert len(result.details["arithmetic_failures"]) == 2


def test_commercial_offer_arithmetic_uses_calculated_totals_without_repeating_warnings():
    package = ProcurementPackageExtraction(
        commercial_offers=[
            CommercialOfferSchema(
                supplier_name="ООО Тест",
                total_amount=MoneyValue(amount=Decimal("100")),
                items=[
                    CommercialOfferItem(name="Строка 1", quantity=2, unit_price=20),
                    CommercialOfferItem(name="Строка 2", quantity=3, unit_price=20),
                ],
            )
        ],
        commercial_offers_found_count=1,
    )

    result = _by_id(run_checks(package))["manual.commercial_offers.content"]
    arithmetic = result.details["arithmetic_rows"][0]

    assert arithmetic["checked_rows"] == 2
    assert arithmetic["derived_rows"] == 2
    assert arithmetic["calculated_total"] == "100.00"
    assert arithmetic["total_matches"] is True
    assert len(arithmetic["manual_review"]) == 1


def test_commercial_offer_vlm_normalizes_russian_dates_and_money_strings():
    from summary_model.commercial_offer_vlm import _normalize_vlm_offer_payload

    payload = _normalize_vlm_offer_payload(
        {
            "outgoing_date": "28.04.2026",
            "offer_date": "30.04.2026",
            "items": [
                {
                    "name": "Сервер",
                    "quantity": "4",
                    "unit_price": "10 470 000,00",
                    "total_price": "41 880 000,00",
                }
            ],
        }
    )
    offer = CommercialOfferSchema.model_validate(payload)

    assert str(offer.outgoing_date) == "2026-04-28"
    assert str(offer.offer_date) == "2026-04-30"
    assert offer.items[0].unit_price == Decimal("10470000.00")
    assert offer.items[0].total_price == Decimal("41880000.00")


def test_commercial_offer_text_layer_restores_requisites_and_leaf_items():
    from summary_model.commercial_offer_vlm import _offer_from_embedded_text

    text = """
27.04.2026 № К-033
ООО «Дибиэй» предлагает рассмотреть возможность поставки.
1
Общая стоимость услуг
усл. ед.
1
107 648 484,00
22
107 648 484,00
1.1
Подготовка технической документации
усл. ед.
1
43 000,00
22
43 000,00
1.2
Сервер DEPO Storm
шт.
4
10 300 000,00
22
41 200 000,00
Сумма коммерческого предложения составляет: 107 648 484 рублей 00 копеек.
Срок поставки оборудования и оказания услуг: с даты заключения контракта по 21.08.2026 г.
"""

    offer = _offer_from_embedded_text(
        "КП_1.pdf",
        [{"page": 1, "text": text}],
    )

    assert offer.supplier_name == "ООО «Дибиэй»"
    assert offer.outgoing_number == "К-033"
    assert str(offer.outgoing_date) == "2026-04-27"
    assert offer.total_amount.amount == Decimal("107648484")
    assert offer.delivery_term_text == "с даты заключения контракта по 21.08.2026 г."
    assert [item.row_number for item in offer.items] == ["1.1", "1.2"]
    assert offer.items[1].quantity == Decimal("4")
    assert offer.items[1].unit_price == Decimal("10300000.00")


def test_justification_state_prefers_explicit_justification_heading():
    from summary_model.checks.additional_characteristics import justification_state

    state = justification_state([
        AdditionalCharacteristicsJustification(
            justification_text="Лицензии необходимы для оказания услуг.",
        ),
        AdditionalCharacteristicsJustification(
            justification_text=(
                "Обоснование применения дополнительных характеристик: "
                "централизованное управление обусловлено существующей инфраструктурой."
            ),
        ),
    ])

    assert state["quote"].startswith("Обоснование применения дополнительных характеристик")
