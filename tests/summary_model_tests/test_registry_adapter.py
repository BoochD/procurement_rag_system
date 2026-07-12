from types import SimpleNamespace

from services.procurement_reference_registry import ProcurementReferenceRegistry
from summary_model.domain.models import (
    DocumentType,
    ExtractedValue,
    ItemCharacteristic,
    OozSummary,
    ProcurementItem,
    ProcurementPackage,
    PlanRequestSummary,
)
from summary_model.external import ProcurementRegistryAdapter
from summary_model.external.procurement_registry import _value_allowed


def value(raw):
    return ExtractedValue(raw_value=raw, normalized_value=raw, confidence=1.0)


def test_ktru_http_client_ignores_environment_proxy_by_default(monkeypatch):
    monkeypatch.delenv("KTRU_TRUST_ENV_PROXY", raising=False)
    monkeypatch.delenv("KTRU_CA_BUNDLE", raising=False)
    monkeypatch.delenv("KTRU_VERIFY_TLS", raising=False)

    registry = ProcurementReferenceRegistry("data/parsed_tables")

    assert registry._http.trust_env is False
    assert registry._tls_verify is False


def test_ktru_tls_verification_can_use_ca_bundle_or_explicit_env(monkeypatch):
    monkeypatch.setenv("KTRU_CA_BUNDLE", "mincifry.pem")
    registry = ProcurementReferenceRegistry("data/parsed_tables")
    assert registry._tls_verify == "mincifry.pem"

    monkeypatch.delenv("KTRU_CA_BUNDLE")
    monkeypatch.setenv("KTRU_VERIFY_TLS", "1")
    registry = ProcurementReferenceRegistry("data/parsed_tables")
    assert registry._tls_verify is True

    monkeypatch.setenv("KTRU_VERIFY_TLS", "0")
    registry = ProcurementReferenceRegistry("data/parsed_tables")
    assert registry._tls_verify is False


def test_characteristic_values_accept_latin_cyrillic_lookalikes():
    assert _value_allowed("Т", ["T"])
    assert _value_allowed("Н", ["H"])
    assert _value_allowed("В", ["B"])
    assert _value_allowed("М", ["M"])


class FakeRegistry:
    def check_ktru(self, code, name):
        return SimpleNamespace(found=True, message=f"КТРУ {code} найден")

    def get_ktru_characteristics_detailed(self, _code):
        return {
            "Цвет": {"values": ["Черный"], "required": True},
            "Ресурс": {"values": ["8000"], "required": True},
        }


class UnavailableRegistry(FakeRegistry):
    def check_ktru(self, code, name):
        return SimpleNamespace(
            found=False,
            message=f"Не удалось получить карточку КТРУ {code}",
        )


def test_registry_adapter_validates_characteristics_without_network():
    item = ProcurementItem(
        item_id="item-1",
        name=value("Картридж"),
        ktru=[value("20.59.12.120-00000002")],
        characteristics=[
            ItemCharacteristic(name=value("Цвет"), value=value("Белый")),
        ],
    )
    package = ProcurementPackage(
        ooz=OozSummary(
            document_id="ooz",
            display_name="ooz.docx",
            detected_type=DocumentType.OOZ,
            items=[item],
        )
    )
    adapter = ProcurementRegistryAdapter(registry=FakeRegistry(), live_ktru=True)

    findings = adapter.validate(package)
    by_rule = {finding.rule_id: finding for finding in findings}

    assert by_rule["registry.ktru.20.59.12.120-00000002"].status == "passed"
    assert (
        by_rule["registry.ktru.20.59.12.120-00000002.characteristic.цвет"].status
        == "failed"
    )
    assert (
        by_rule["registry.ktru.20.59.12.120-00000002.required.ресурс"].status
        == "failed"
    )


def test_unavailable_ktru_site_is_uncertain_not_document_error():
    item = ProcurementItem(
        item_id="item-1",
        name=value("Картридж"),
        ktru=[value("20.59.12.120-00000002")],
    )
    package = ProcurementPackage(
        ooz=OozSummary(
            document_id="ooz",
            detected_type=DocumentType.OOZ,
            items=[item],
        )
    )

    findings = ProcurementRegistryAdapter(
        registry=UnavailableRegistry(),
        live_ktru=True,
    ).validate(package)

    assert findings[0].status == "uncertain"
    assert findings[0].severity == "manual_review"


def test_characteristics_are_checked_when_ktru_was_seen_in_plan_first():
    plan_item = ProcurementItem(
        item_id="plan-item",
        name=value("Картридж"),
        ktru=[value("20.59.12.120-00000002")],
    )
    ooz_item = ProcurementItem(
        item_id="ooz-item",
        name=value("Картридж"),
        ktru=[value("20.59.12.120-00000002")],
        characteristics=[
            ItemCharacteristic(name=value("Цвет"), value=value("Белый")),
        ],
    )
    package = ProcurementPackage(
        plan=PlanRequestSummary(
            document_id="plan",
            detected_type=DocumentType.PLAN,
            items=[plan_item],
        ),
        ooz=OozSummary(
            document_id="ooz",
            detected_type=DocumentType.OOZ,
            items=[ooz_item],
        ),
    )

    findings = ProcurementRegistryAdapter(
        registry=FakeRegistry(),
        live_ktru=True,
    ).validate(package)

    assert any(
        finding.rule_id
        == "registry.ktru.20.59.12.120-00000002.characteristic.цвет"
        for finding in findings
    )


class RangeRegistry(FakeRegistry):
    def get_ktru_characteristics_detailed(self, _code):
        return {
            "Емкость": {"values": ["<= 70"], "required": True},
        }


def test_registry_adapter_accepts_numeric_range_value():
    item = ProcurementItem(
        item_id="item-1",
        name=value("Аккумулятор"),
        ktru=[value("27.20.21.000-00000021")],
        characteristics=[
            ItemCharacteristic(name=value("Емкость"), value=value("65")),
        ],
    )
    package = ProcurementPackage(
        ooz=OozSummary(
            document_id="ooz",
            detected_type=DocumentType.OOZ,
            items=[item],
        )
    )

    findings = ProcurementRegistryAdapter(
        registry=RangeRegistry(),
        live_ktru=True,
    ).validate(package)
    characteristic = next(
        finding
        for finding in findings
        if ".characteristic." in finding.rule_id
    )

    assert characteristic.status == "passed"


def test_single_supplier_allows_extra_characteristic():
    plan = PlanRequestSummary(
        document_id="plan",
        detected_type=DocumentType.PLAN,
        procurement_method=value("Единственный поставщик"),
    )
    item = ProcurementItem(
        item_id="item-1",
        name=value("Картридж"),
        ktru=[value("20.59.12.120-00000002")],
        characteristics=[
            ItemCharacteristic(name=value("Материал"), value=value("Пластик")),
        ],
    )
    package = ProcurementPackage(
        plan=plan,
        ooz=OozSummary(
            document_id="ooz",
            detected_type=DocumentType.OOZ,
            items=[item],
        ),
    )

    findings = ProcurementRegistryAdapter(
        registry=FakeRegistry(),
        live_ktru=True,
    ).validate(package)

    assert not any(
        finding.rule_id.endswith(".characteristic.extra")
        for finding in findings
    )
