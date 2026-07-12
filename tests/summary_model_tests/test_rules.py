from decimal import Decimal

from summary_model.domain.models import (
    ContractSummary,
    DocumentType,
    ExtractedValue,
    OozSummary,
    PlanRequestSummary,
    ProcurementItem,
)
from summary_model.validation import assemble_package, validate_package


def value(raw):
    return ExtractedValue(raw_value=raw, normalized_value=raw, confidence=1.0)


def item(document_id):
    return ProcurementItem(
        item_id=f"{document_id}-item",
        name=value("Шина"),
        okpd2=[value("22.11.11.000")],
        ktru=[value("22.11.11.000-00000007")],
        quantity=value("4"),
        unit=value("шт"),
    )


def test_package_rules_compare_core_fields():
    plan = PlanRequestSummary(
        document_id="plan",
        display_name="plan.docx",
        detected_type=DocumentType.PLAN,
        subject=value("Поставка шин"),
        nmck=[value(Decimal("350000"))],
        delivery_places=[value("г. Новосибирск")],
        delivery_periods=[value("15 рабочих дней")],
        items=[item("plan")],
    )
    ooz = OozSummary(
        document_id="ooz",
        display_name="ooz.docx",
        detected_type=DocumentType.OOZ,
        subject=value("Поставка шин"),
        delivery_places=[value("г Новосибирск")],
        delivery_periods=[value("15 рабочих дней")],
        items=[item("ooz")],
    )
    contract = ContractSummary(
        document_id="contract",
        display_name="contract.docx",
        detected_type=DocumentType.CONTRACT,
        subject=value("Поставка шин"),
        price=[value(Decimal("350000"))],
        delivery_places=[value("г. Новосибирск")],
        delivery_periods=[value("15 рабочих дней")],
        items=[item("contract")],
    )

    findings = validate_package(assemble_package([plan, ooz, contract]))
    by_rule = {finding.rule_id: finding for finding in findings}

    assert by_rule["items.okpd2"].status == "passed"
    assert by_rule["items.ktru"].status == "passed"
    assert by_rule["package.price"].status == "passed"


def test_okpd_comparison_derives_parent_only_for_ktru_compatibility():
    plan_item = item("plan")
    contract_item = item("contract")
    contract_item.okpd2 = []
    plan = PlanRequestSummary(
        document_id="plan",
        detected_type=DocumentType.PLAN,
        items=[plan_item],
    )
    contract = ContractSummary(
        document_id="contract",
        detected_type=DocumentType.CONTRACT,
        items=[contract_item],
    )

    findings = validate_package(assemble_package([plan, contract]))
    by_rule = {finding.rule_id: finding for finding in findings}

    assert by_rule["items.okpd2"].status == "passed"
    assert contract_item.okpd2 == []


def test_aggregate_plan_item_is_not_compared_as_single_position():
    aggregate = item("plan")
    aggregate.ktru.append(value("27.20.21.000-00000021"))
    aggregate.quantity = value("39")
    plan = PlanRequestSummary(
        document_id="plan",
        detected_type=DocumentType.PLAN,
        items=[aggregate],
    )
    contract_position = item("contract")
    contract_position.quantity = value("4")
    contract = ContractSummary(
        document_id="contract",
        detected_type=DocumentType.CONTRACT,
        items=[contract_position],
    )

    findings = validate_package(assemble_package([plan, contract]))

    assert not any(
        finding.rule_id.startswith("items.quantity.ktru:")
        for finding in findings
    )
