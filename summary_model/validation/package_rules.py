from __future__ import annotations

from collections import defaultdict

from summary_model.domain.models import (
    AnyDocumentSummary,
    CommercialOfferSummary,
    ContractSummary,
    DocumentSummary,
    DocumentType,
    Finding,
    FindingSeverity,
    FindingStatus,
    OozSummary,
    OnmckSummary,
    PlanRequestSummary,
    ProcurementPackage,
    ProcurementRequestSummary,
    ExplanatoryNoteSummary,
)
from .normalization import normalize_decimal, normalize_text, normalize_unit, normalized_values
from .price_rules import validate_onmck_prices


def assemble_package(documents: list[AnyDocumentSummary]) -> ProcurementPackage:
    package = ProcurementPackage()
    for document in documents:
        if isinstance(document, PlanRequestSummary):
            package.plan = package.plan or document
        elif isinstance(document, ProcurementRequestSummary):
            package.request = package.request or document
        elif isinstance(document, CommercialOfferSummary):
            package.commercial_offers.append(document)
        elif isinstance(document, OnmckSummary):
            package.onmck = package.onmck or document
        elif isinstance(document, OozSummary):
            package.ooz = package.ooz or document
        elif isinstance(document, ContractSummary):
            package.contract = package.contract or document
        elif isinstance(document, ExplanatoryNoteSummary):
            package.explanatory_note = package.explanatory_note or document
        else:
            package.unknown_documents.append(document)
    return package


def _finding(
    rule_id: str,
    title: str,
    passed: bool,
    documents: list[str],
    expected=None,
    actual=None,
    message: str | None = None,
    uncertain: bool = False,
) -> Finding:
    if uncertain:
        return Finding(
            rule_id=rule_id,
            severity=FindingSeverity.MANUAL_REVIEW,
            status=FindingStatus.UNCERTAIN,
            title=title,
            message=message or "Недостаточно данных для автоматической проверки.",
            documents=documents,
            expected=expected,
            actual=actual,
            source="deterministic",
        )
    return Finding(
        rule_id=rule_id,
        severity=FindingSeverity.INFO if passed else FindingSeverity.ERROR,
        status=FindingStatus.PASSED if passed else FindingStatus.FAILED,
        title=title,
        message=message or ("Проверка пройдена." if passed else "Обнаружено расхождение."),
        documents=documents,
        expected=expected,
        actual=actual,
        source="deterministic",
    )


def _completeness(package: ProcurementPackage) -> list[Finding]:
    required = {
        "plan": package.plan,
        "request": package.request,
        "onmck": package.onmck,
        "ooz": package.ooz,
        "contract": package.contract,
        "explanatory_note": package.explanatory_note,
    }
    findings = [
        _finding(
            f"package.required.{name}",
            f"Наличие документа: {name}",
            document is not None,
            [document.document_id] if document else [],
            expected="present",
            actual="present" if document else "missing",
        )
        for name, document in required.items()
    ]
    findings.append(
        _finding(
            "package.commercial_offers",
            "Не менее трёх коммерческих предложений",
            len(package.commercial_offers) >= 3,
            [document.document_id for document in package.commercial_offers],
            expected=3,
            actual=len(package.commercial_offers),
        )
    )
    return findings


def _compare_subjects(package: ProcurementPackage) -> list[Finding]:
    values: list[tuple[str, str]] = []
    for document in (package.plan, package.ooz, package.contract, package.request):
        if document and getattr(document, "subject", None):
            normalized = normalize_text(document.subject)
            if normalized:
                values.append((document.document_id, normalized))
    if len(values) < 2:
        return [
            _finding(
                "package.subject",
                "Соответствие предмета закупки",
                False,
                [item[0] for item in values],
                uncertain=True,
            )
        ]
    baseline = values[0][1]
    passed = all(value == baseline for _, value in values[1:])
    return [
        _finding(
            "package.subject",
            "Соответствие предмета закупки",
            passed,
            [item[0] for item in values],
            expected=baseline,
            actual={document_id: value for document_id, value in values},
            message=(
                "Предмет закупки совпадает после нормализации."
                if passed
                else "Формулировки отличаются; требуется semantic review."
            ),
            uncertain=not passed,
        )
    ]


def _code_sets(document) -> tuple[set[str], set[str]]:
    okpd: set[str] = set()
    ktru: set[str] = set()
    for item in getattr(document, "items", []):
        okpd.update(normalized_values(item.okpd2))
        ktru.update(normalized_values(item.ktru))
    # KTRU embeds its parent OKPD2 code. This derived value is used only for
    # cross-document compatibility; it is not stored as extracted OKPD2.
    okpd.update(code.split("-", 1)[0] for code in ktru if "-" in code)
    return okpd, ktru


def _compare_codes(package: ProcurementPackage) -> list[Finding]:
    documents = [document for document in (package.plan, package.ooz, package.contract) if document]
    findings: list[Finding] = []
    for code_type, index in (("okpd2", 0), ("ktru", 1)):
        values = [(document.document_id, _code_sets(document)[index]) for document in documents]
        nonempty = [(document_id, codes) for document_id, codes in values if codes]
        if len(nonempty) < 2:
            findings.append(
                _finding(
                    f"items.{code_type}",
                    f"Соответствие {code_type.upper()}",
                    False,
                    [item[0] for item in values],
                    uncertain=True,
                )
            )
            continue
        baseline = nonempty[0][1]
        passed = all(codes == baseline for _, codes in nonempty[1:])
        findings.append(
            _finding(
                f"items.{code_type}",
                f"Соответствие {code_type.upper()}",
                passed,
                [item[0] for item in nonempty],
                expected=sorted(baseline),
                actual={document_id: sorted(codes) for document_id, codes in nonempty},
            )
        )
    return findings


def _item_key(item) -> str:
    ktru = sorted(normalized_values(item.ktru))
    okpd = sorted(normalized_values(item.okpd2))
    if len(ktru) + len(okpd) > 1:
        return ""
    if ktru:
        return f"ktru:{ktru[0]}"
    name = normalize_text(item.name)
    if okpd:
        return f"okpd:{okpd[0]}:{name}"
    return f"name:{name}" if name else ""


def _compare_item_values(package: ProcurementPackage) -> list[Finding]:
    documents = [document for document in (package.plan, package.ooz, package.contract) if document]
    grouped: dict[str, dict[str, object]] = defaultdict(dict)
    ambiguous_keys: set[str] = set()
    for document in documents:
        for item in document.items:
            key = _item_key(item)
            if key:
                if document.document_id in grouped[key]:
                    ambiguous_keys.add(key)
                grouped[key][document.document_id] = item

    findings: list[Finding] = []
    for key, by_document in grouped.items():
        if key in ambiguous_keys:
            continue
        if len(by_document) < 2:
            continue
        for field, title, normalizer in (
            ("quantity", "Количество позиции", normalize_decimal),
            ("unit", "Единица измерения позиции", normalize_unit),
        ):
            values = {
                document_id: normalizer(getattr(item, field))
                for document_id, item in by_document.items()
                if getattr(item, field) is not None
            }
            nonempty = {document_id: value for document_id, value in values.items() if value not in (None, "")}
            if len(nonempty) < 2:
                continue
            expected = next(iter(nonempty.values()))
            findings.append(
                _finding(
                    f"items.{field}.{key}",
                    f"{title}: {key}",
                    all(value == expected for value in nonempty.values()),
                    list(nonempty),
                    expected=expected,
                    actual=nonempty,
                )
            )
    return findings


def _compare_price(package: ProcurementPackage) -> list[Finding]:
    values: list[tuple[str, object]] = []
    for document, occurrences in (
        (package.plan, package.plan.nmck if package.plan else []),
        (package.contract, package.contract.price if package.contract else []),
        (package.onmck, package.onmck.nmck if package.onmck else []),
    ):
        if document is None:
            continue
        values.extend(
            (document.document_id, normalize_decimal(occurrence))
            for occurrence in occurrences
        )
    valid = [(document_id, value) for document_id, value in values if value is not None]
    if len(valid) < 2:
        return [
            _finding(
                "package.price",
                "Соответствие НМЦК и цены контракта",
                False,
                [item[0] for item in values],
                uncertain=True,
            )
        ]
    expected = valid[0][1]
    return [
        _finding(
            "package.price",
            "Соответствие НМЦК и цены контракта",
            all(value == expected for _, value in valid[1:]),
            [item[0] for item in valid],
            expected=expected,
            actual={document_id: value for document_id, value in valid},
        )
    ]


def _compare_delivery(package: ProcurementPackage) -> list[Finding]:
    findings: list[Finding] = []
    for field, title in (
        ("delivery_places", "Соответствие места поставки"),
        ("delivery_periods", "Соответствие срока поставки"),
    ):
        values: list[tuple[str, set[str]]] = []
        for document in (package.plan, package.ooz, package.contract):
            if not document:
                continue
            normalized = {
                normalize_text(value)
                for value in getattr(document, field, [])
                if normalize_text(value)
            }
            values.append((document.document_id, normalized))
        nonempty = [(document_id, value) for document_id, value in values if value]
        if len(nonempty) < 2:
            findings.append(
                _finding(
                    f"delivery.{field}",
                    title,
                    False,
                    [item[0] for item in values],
                    uncertain=True,
                )
            )
            continue
        baseline = nonempty[0][1]
        findings.append(
            _finding(
                f"delivery.{field}",
                title,
                all(value == baseline for _, value in nonempty[1:]),
                [item[0] for item in nonempty],
                expected=sorted(baseline),
                actual={document_id: sorted(value) for document_id, value in nonempty},
            )
        )
    return findings


def validate_package(package: ProcurementPackage) -> list[Finding]:
    findings = _completeness(package)
    findings.extend(_compare_codes(package))
    findings.extend(_compare_item_values(package))
    findings.extend(_compare_price(package))
    if package.onmck:
        findings.extend(validate_onmck_prices(package.onmck))
    return findings
