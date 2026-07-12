from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal
from math import sqrt
from typing import Any

from summary_model.checks.models import CheckMode, CheckResult, ProcurementChecksReport
from summary_model.checks.normalization import (
    normalize_code,
    normalize_decimal,
    normalize_text,
    normalize_unit,
)
from summary_model.extraction_models import (
    ContractDraftSchema,
    NmckItem,
    ProcurementPackageExtraction,
    PurchaseItem,
)


REQUIRED_DOCUMENTS = {
    "purchase_request": "Обращение о проведении закупки",
    "schedule_application": "Заявка в план-график",
    "nmck_justification": "ОНМЦК",
    "purchase_description": "Описание объекта закупки",
    "contract_draft": "Проект контракта",
    "explanatory_note": "Пояснительная записка",
}

DOCUMENT_LABELS = {
    "schedule_application": "Заявка в план-график",
    "purchase_request": "Обращение",
    "nmck_justification": "ОНМЦК",
    "purchase_description": "ООЗ",
    "contract_draft": "Проект контракта",
    "explanatory_note": "Пояснительная записка",
}


def run_checks(
    package: ProcurementPackageExtraction,
    *,
    semantic_results: list[CheckResult] | None = None,
    external_results: list[CheckResult] | None = None,
) -> ProcurementChecksReport:
    results: list[CheckResult] = []
    results.extend(_check_package_completeness(package))
    results.extend(_check_request_attachments(package))
    results.extend(_check_schedule_completeness(package))
    results.extend(_check_nmck_amounts(package))
    results.extend(_check_onmck_arithmetic(package))
    results.extend(_check_onmck_min_prices(package))
    results.extend(_check_onmck_supplier_prices(package))
    results.extend(_check_codes(package, "okpd2"))
    results.extend(_check_codes(package, "ktru"))
    results.extend(_check_funding_source(package))
    results.extend(_check_securities(package))
    results.extend(_check_contract_attachments(package))
    results.extend(semantic_results if semantic_results is not None else _semantic_manual_checks(package))
    results.extend(external_results if external_results is not None else _external_manual_checks(package))
    return ProcurementChecksReport.from_results(
        package_id=package.package_id,
        results=results,
    )


def _result(
    check_id: str,
    title: str,
    status: str,
    mode: CheckMode,
    message: str,
    *,
    documents: list[str] | None = None,
    fields: list[str] | None = None,
    evidence: list[str] | None = None,
    details: dict[str, Any] | None = None,
    report_text: str | None = None,
) -> CheckResult:
    severity = {
        "passed": "info",
        "failed": "error",
        "warning": "warning",
        "manual_review": "manual_review",
        "not_applicable": "info",
        "skipped": "info",
    }[status]
    return CheckResult(
        check_id=check_id,
        title=title,
        severity=severity,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        mode=mode,
        documents=documents or [],
        fields_compared=fields or [],
        message=message,
        report_text=report_text or message,
        evidence=evidence or [],
        details=details or {},
    )


def _check_package_completeness(package: ProcurementPackageExtraction) -> list[CheckResult]:
    results = []
    for field_name, title in REQUIRED_DOCUMENTS.items():
        present = getattr(package, field_name) is not None
        results.append(
            _result(
                f"strict.package.{field_name}",
                f"Наличие документа: {title}",
                "passed" if present else "failed",
                "strict",
                f"Документ найден: {title}." if present else f"Документ отсутствует: {title}.",
                documents=[field_name],
                fields=[field_name],
                details={"present": present},
            )
        )
    if package.commercial_offers_found_count >= package.commercial_offers_required_count:
        status = "passed"
        message = "Коммерческие предложения найдены в требуемом количестве."
    else:
        status = "manual_review"
        message = (
            "Коммерческие предложения отсутствуют или их меньше трёх; "
            "сверка КП на текущем этапе требует ручной проверки."
        )
    results.append(
        _result(
            "manual.commercial_offers.count",
            "Количество коммерческих предложений",
            status,
            "manual_review" if status == "manual_review" else "strict",
            message,
            fields=["commercial_offers_found_count", "commercial_offers_required_count"],
            details={
                "found": package.commercial_offers_found_count,
                "required": package.commercial_offers_required_count,
            },
        )
    )
    return results


def _check_request_attachments(package: ProcurementPackageExtraction) -> list[CheckResult]:
    request = package.purchase_request
    if request is None:
        return [
            _result(
                "strict.request.attachments",
                "Приложения в обращении",
                "manual_review",
                "strict",
                "Обращение отсутствует; список приложений проверить невозможно.",
                fields=["purchase_request.attachments", "files.document_type"],
            )
        ]
    uploaded = {item.document_type for item in package.files if item.document_type != "unknown"}
    listed = {
        item.normalized_document_type
        for item in request.attachments
        if item.normalized_document_type != "unknown"
    }
    if not request.attachments:
        return [
            _result(
                "strict.request.attachments",
                "Приложения в обращении",
                "manual_review",
                "strict",
                "В обращении не извлечён список приложений.",
                documents=["purchase_request"],
                fields=["purchase_request.attachments"],
            )
        ]
    missing = sorted(listed - uploaded)
    extra = sorted(uploaded - listed - {"commercial_offer", "purchase_request"})
    if missing:
        status = "failed"
        message = "В обращении указаны приложения, но соответствующие файлы не найдены."
    elif extra:
        status = "warning"
        message = "В пакете есть документы, которые не найдены в списке приложений обращения."
    else:
        status = "passed"
        message = "Список приложений обращения соответствует загруженным документам."
    return [
        _result(
            "strict.request.attachments",
            "Приложения в обращении",
            status,
            "strict",
            message,
            documents=["purchase_request"],
            fields=["purchase_request.attachments", "files.document_type"],
            details={"listed": sorted(listed), "uploaded": sorted(uploaded), "missing": missing, "extra": extra},
        )
    ]


def _check_schedule_completeness(package: ProcurementPackageExtraction) -> list[CheckResult]:
    schedule = package.schedule_application
    if schedule is None:
        return [
            _result(
                "strict.schedule.fields",
                "Заполненность заявки",
                "failed",
                "strict",
                "Заявка в план-график отсутствует.",
                fields=["schedule_application.raw_fields"],
            )
        ]
    if not schedule.raw_fields:
        return [
            _result(
                "strict.schedule.fields",
                "Заполненность заявки",
                "manual_review",
                "strict",
                "Строки заявки не извлечены.",
                documents=["schedule_application"],
                fields=["schedule_application.raw_fields"],
            )
        ]
    if schedule.empty_fields:
        status = "warning"
        message = "В заявке есть пустые строки."
    else:
        status = "passed"
        message = "Заполненность строк заявки проверена."
    return [
        _result(
            "strict.schedule.fields",
            "Заполненность заявки",
            status,
            "strict",
            message,
            documents=["schedule_application"],
            fields=[
                "schedule_application.raw_fields",
                "schedule_application.empty_fields",
                "schedule_application.negative_value_fields",
            ],
            details={
                "raw_fields_count": len(schedule.raw_fields),
                "empty_fields": schedule.empty_fields,
                "valid_negative_fields": schedule.negative_value_fields,
                "summary_lines": [f"строк извлечено: {len(schedule.raw_fields)}"],
            },
        )
    ]


def _money_amounts(package: ProcurementPackageExtraction) -> dict[str, Decimal | None]:
    return {
        "schedule_application": _money_amount(package.schedule_application.nmck if package.schedule_application else None),
        "purchase_request": _money_amount(package.purchase_request.nmck if package.purchase_request else None),
        "nmck_justification": _money_amount(package.nmck_justification.total_amount if package.nmck_justification else None),
        "contract_draft": _money_amount(package.contract_draft.price if package.contract_draft else None),
        "explanatory_note": _money_amount(package.explanatory_note.nmck if package.explanatory_note else None),
    }


def _money_amount(value) -> Decimal | None:
    return normalize_decimal(getattr(value, "amount", None))


def _check_nmck_amounts(package: ProcurementPackageExtraction) -> list[CheckResult]:
    amounts = _money_amounts(package)
    present = {name: value for name, value in amounts.items() if value is not None}
    if len(present) < 2:
        return [
            _result(
                "strict.nmck.amounts",
                "НМЦК / цена между документами",
                "manual_review",
                "strict",
                "Недостаточно извлечённых сумм для сверки НМЦК.",
                fields=list(amounts),
                details={"amounts": amounts},
            )
        ]
    expected = next(iter(present.values()))
    passed = all(value == expected for value in present.values())
    summary_lines = [
        f"{DOCUMENT_LABELS.get(key, key)}: {value}"
        for key, value in present.items()
    ]
    return [
        _result(
            "strict.nmck.amounts",
            "НМЦК / цена между документами",
            "passed" if passed else "failed",
            "strict",
            "НМЦК/цена совпадает между документами." if passed else "Найдены расхождения НМЦК/цены между документами.",
            documents=list(present),
            fields=[
                "schedule_application.nmck.amount",
                "purchase_request.nmck.amount",
                "nmck_justification.total_amount.amount",
                "contract_draft.price.amount",
                "explanatory_note.nmck.amount",
            ],
            details={
                "amounts": {key: str(value) for key, value in present.items()},
                "summary_lines": summary_lines,
            },
        )
    ]


def _check_onmck_arithmetic(package: ProcurementPackageExtraction) -> list[CheckResult]:
    onmck = package.nmck_justification
    if onmck is None or not onmck.items:
        return [
            _result(
                "strict.onmck.arithmetic",
                "Арифметика ОНМЦК",
                "manual_review",
                "strict",
                "ОНМЦК или строки расчёта не извлечены.",
                fields=["nmck_justification.items"],
            )
        ]
    failed = []
    incomplete = []
    for item in onmck.items:
        quantity = normalize_decimal(item.quantity)
        unit_price = normalize_decimal(item.selected_min_unit_price)
        declared = normalize_decimal(item.row_total_declared)
        if quantity is None or unit_price is None or declared is None:
            incomplete.append(_item_label(item))
            continue
        calculated = quantity * unit_price
        if calculated != declared:
            failed.append({"item": _item_label(item), "expected": str(calculated), "actual": str(declared)})
    row_sum = sum((normalize_decimal(item.row_total_declared) or Decimal("0")) for item in onmck.items)
    total = _money_amount(onmck.total_amount)
    plan_total = _money_amount(package.schedule_application.nmck if package.schedule_application else None)
    total_mismatch = total is not None and row_sum != total
    plan_mismatch = plan_total is not None and row_sum != plan_total
    if failed or total_mismatch or plan_mismatch:
        status = "failed"
        message = "В арифметике ОНМЦК найдены расхождения."
    elif incomplete:
        status = "manual_review"
        message = "В части строк ОНМЦК не хватает данных для арифметической проверки."
    else:
        status = "passed"
        message = "Арифметика ОНМЦК проверена."
    return [
        _result(
            "strict.onmck.arithmetic",
            "Арифметика ОНМЦК",
            status,
            "strict",
            message,
            documents=["nmck_justification", "schedule_application"],
            fields=[
                "nmck_justification.items[].quantity",
                "nmck_justification.items[].selected_min_unit_price",
                "nmck_justification.items[].row_total_declared",
                "nmck_justification.total_amount.amount",
                "schedule_application.nmck.amount",
            ],
            details={
                "failed_items": failed,
                "incomplete_items": incomplete,
                "row_sum": str(row_sum),
                "onmck_total": str(total) if total is not None else None,
                "plan_nmck": str(plan_total) if plan_total is not None else None,
                "summary_lines": [
                    f"строк ОНМЦК: {len(onmck.items)}",
                    f"сумма строк: {row_sum}",
                    f"итог ОНМЦК: {total}" if total is not None else "итог ОНМЦК: не найден",
                    f"НМЦК в заявке: {plan_total}" if plan_total is not None else "НМЦК в заявке: не найден",
                ],
            },
        )
    ]


def _check_onmck_min_prices(package: ProcurementPackageExtraction) -> list[CheckResult]:
    onmck = package.nmck_justification
    if onmck is None or not onmck.items:
        return [
            _result(
                "strict.onmck.min_price",
                "Минимальная цена ОНМЦК",
                "manual_review",
                "strict",
                "ОНМЦК или строки расчёта не извлечены.",
                fields=["nmck_justification.items[].supplier_prices"],
            )
        ]
    failed = []
    incomplete = []
    item_summary_lines: list[str] = []
    source_labels = {
        source.source_id: _supplier_label(source.supplier_name_raw or source.raw_header or source.source_id)
        for source in onmck.price_sources
    }
    for item in onmck.items:
        source_prices = [
            (price.source_id, normalize_decimal(price.unit_price))
            for price in item.supplier_prices
            if normalize_decimal(price.unit_price) is not None
        ]
        prices = [price for _source_id, price in source_prices if price is not None]
        selected = normalize_decimal(item.selected_min_unit_price)
        if not prices or selected is None:
            incomplete.append(_item_label(item))
            continue
        minimum = min(prices)
        price_text = ", ".join(
            f"{source_labels.get(source_id, _supplier_label(source_id))} = {_format_decimal(price)}"
            for source_id, price in source_prices
            if price is not None
        )
        item_summary_lines.append(
            f"{_item_label(item)}: минимальная цена {_format_decimal(selected)}; цены поставщиков: {price_text}"
        )
        if selected != minimum:
            failed.append({"item": _item_label(item), "expected": str(minimum), "actual": str(selected)})
    checked_count = len(onmck.items) - len(incomplete)
    if failed:
        status = "failed"
        message = "Выбранная минимальная цена отличается от минимума среди поставщиков."
    elif incomplete:
        status = "manual_review"
        message = "В части строк не хватает цен поставщиков или выбранной минимальной цены."
    else:
        status = "passed"
        message = "Минимальные цены ОНМЦК проверены."
    return [
        _result(
            "strict.onmck.min_price",
            "Минимальная цена ОНМЦК",
            status,
            "strict",
            message,
            documents=["nmck_justification"],
            fields=[
                "nmck_justification.items[].supplier_prices[].unit_price",
                "nmck_justification.items[].selected_min_unit_price",
            ],
            details={
                "failed_items": failed,
                "incomplete_items": incomplete,
                "summary_lines": [
                    f"проверено позиций: {checked_count}",
                    f"позиций с ошибкой: {len(failed)}",
                    *item_summary_lines,
                ],
            },
        )
    ]


def _check_onmck_supplier_prices(package: ProcurementPackageExtraction) -> list[CheckResult]:
    onmck = package.nmck_justification
    if onmck is None or not onmck.items:
        return [
            _result(
                "strict.onmck.supplier_prices",
                "Сравнение цен поставщиков в ОНМЦК",
                "manual_review",
                "strict",
                "ОНМЦК или строки расчёта не извлечены.",
                fields=["nmck_justification.items[].supplier_prices"],
            )
        ]

    summary_lines: list[str] = []
    incomplete: list[str] = []
    for index, item in enumerate(onmck.items, 1):
        prices = [
            normalize_decimal(price.unit_price)
            for price in item.supplier_prices
            if normalize_decimal(price.unit_price) is not None
        ]
        if not prices:
            incomplete.append(_item_label(item))
            continue
        coefficient = _variation_coefficient(prices)
        coefficient_text = f"{coefficient:.2f}%" if coefficient is not None else "не рассчитан"
        price_text = ", ".join(_format_decimal(price) for price in prices)
        summary_lines.append(
            f"№{index} {_item_label(item)} | коэффициент вариации: {coefficient_text} | Цены: [{price_text}]"
        )

    total_prices = _supplier_total_prices(onmck.items)
    if total_prices:
        coefficient = _variation_coefficient(total_prices)
        coefficient_text = f"{coefficient:.2f}%" if coefficient is not None else "не рассчитан"
        price_text = ", ".join(_format_decimal(price) for price in total_prices)
        summary_lines.append(f"№ ИТОГО | коэффициент вариации: {coefficient_text} | Цены: [{price_text}]")

    status = "manual_review" if incomplete else "passed"
    message = (
        "В части строк не хватает цен поставщиков."
        if incomplete
        else "Цены поставщиков в ОНМЦК сведены для сравнения."
    )
    return [
        _result(
            "strict.onmck.supplier_prices",
            "Сравнение цен поставщиков в ОНМЦК",
            status,
            "strict",
            message,
            documents=["nmck_justification"],
            fields=["nmck_justification.items[].supplier_prices[].unit_price"],
            details={
                "incomplete_items": incomplete,
                "summary_lines": summary_lines,
            },
        )
    ]


def _variation_coefficient(values: list[Decimal]) -> float | None:
    if len(values) < 2:
        return None
    numbers = [float(value) for value in values]
    mean = sum(numbers) / len(numbers)
    if mean == 0:
        return None
    variance = sum((value - mean) ** 2 for value in numbers) / (len(numbers) - 1)
    return sqrt(variance) / mean * 100


def _supplier_total_prices(items: list[NmckItem]) -> list[Decimal]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for item in items:
        quantity = normalize_decimal(item.quantity)
        for price in item.supplier_prices:
            unit_price = normalize_decimal(price.unit_price)
            row_total = normalize_decimal(price.row_total)
            if row_total is None and quantity is not None and unit_price is not None:
                row_total = quantity * unit_price
            if row_total is not None:
                totals[price.source_id] += row_total
    return [totals[key] for key in sorted(totals)]


def _format_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _supplier_label(value: str | None) -> str:
    text = str(value or "").strip()
    match = re.fullmatch(r"(?i)supplier[_\s-]*(\d+)", text)
    if match:
        return f"Поставщик{match.group(1)}"
    return text


def _check_codes(package: ProcurementPackageExtraction, code_type: str) -> list[CheckResult]:
    values = _document_code_sets(package, code_type)
    nonempty = {name: codes for name, codes in values.items() if codes}
    title = f"Сверка {code_type.upper()} между документами"
    if len(nonempty) < 2:
        return [
            _result(
                f"strict.codes.{code_type}",
                title,
                "manual_review",
                "strict",
                f"Недостаточно {code_type.upper()}-кодов для сверки.",
                fields=[f"*.{code_type}_codes", f"*.items[].{code_type}_code"],
                details={"codes": {key: sorted(value) for key, value in values.items()}},
            )
        ]
    expected = next(iter(nonempty.values()))
    passed = all(codes == expected for codes in nonempty.values())
    union_codes = set().union(*nonempty.values())
    missing_by_document = {
        name: sorted(union_codes - codes)
        for name, codes in nonempty.items()
        if union_codes - codes
    }
    summary_lines = [
        f"{DOCUMENT_LABELS.get(name, name)}: {', '.join(sorted(codes))}"
        for name, codes in values.items()
        if codes
    ]
    if missing_by_document:
        summary_lines.extend(
            f"{DOCUMENT_LABELS.get(name, name)}: не найдены {', '.join(codes)}"
            for name, codes in missing_by_document.items()
        )
    return [
        _result(
            f"strict.codes.{code_type}",
            title,
            "passed" if passed else "failed",
            "strict",
            f"{code_type.upper()}-коды совпадают между документами." if passed else f"Найдены расхождения {code_type.upper()}-кодов между документами.",
            documents=list(nonempty),
            fields=[f"*.{code_type}_codes", f"*.items[].{code_type}_code"],
            details={
                "codes": {key: sorted(value) for key, value in values.items()},
                "missing_by_document": missing_by_document,
                "empty_documents": [key for key, codes in values.items() if not codes],
                "summary_lines": summary_lines,
            },
        )
    ]


def _document_code_sets(package: ProcurementPackageExtraction, code_type: str) -> dict[str, set[str]]:
    field = f"{code_type}_code"
    schedule_field = f"{code_type}_codes"
    if code_type == "okpd2":
        schedule_codes = set()
        if package.schedule_application:
            schedule_codes.update(_normalized_codes(getattr(package.schedule_application, schedule_field, [])))
            schedule_codes.update(_okpd2_codes_from_ktru(getattr(package.schedule_application, "ktru_codes", [])))
        return {
            "schedule_application": schedule_codes,
            "purchase_description": _item_codes(package.purchase_description.items if package.purchase_description else [], field)
            | _item_okpd2_from_ktru(package.purchase_description.items if package.purchase_description else []),
            "contract_draft": _item_codes(package.contract_draft.items if package.contract_draft else [], field)
            | _item_okpd2_from_ktru(package.contract_draft.items if package.contract_draft else []),
            "nmck_justification": _item_codes(package.nmck_justification.items if package.nmck_justification else [], field)
            | _item_okpd2_from_ktru(package.nmck_justification.items if package.nmck_justification else []),
        }
    return {
        "schedule_application": {
            normalize_code(code)
            for code in (getattr(package.schedule_application, schedule_field, []) if package.schedule_application else [])
            if normalize_code(code)
        },
        "purchase_description": _item_codes(package.purchase_description.items if package.purchase_description else [], field),
        "contract_draft": _item_codes(package.contract_draft.items if package.contract_draft else [], field),
        "nmck_justification": _item_codes(package.nmck_justification.items if package.nmck_justification else [], field),
    }


def _item_codes(items: list[Any], field: str) -> set[str]:
    return {normalize_code(getattr(item, field, None)) for item in items if normalize_code(getattr(item, field, None))}


def _normalized_codes(codes: list[Any]) -> set[str]:
    return {normalize_code(code) for code in codes if normalize_code(code)}


def _item_okpd2_from_ktru(items: list[Any]) -> set[str]:
    return _okpd2_codes_from_ktru([getattr(item, "ktru_code", None) for item in items])


def _okpd2_codes_from_ktru(codes: list[Any]) -> set[str]:
    derived = set()
    for code in codes:
        normalized = normalize_code(code)
        if normalized and len(normalized) >= 21 and normalized[12] == "-":
            derived_code = normalize_code(normalized[:12])
            if derived_code:
                derived.add(derived_code)
    return derived


def _check_funding_source(package: ProcurementPackageExtraction) -> list[CheckResult]:
    schedule_value = package.schedule_application.funding_source_text if package.schedule_application else None
    contract_value = package.contract_draft.funding_source if package.contract_draft else None
    schedule_norm = normalize_text(schedule_value)
    contract_norm = normalize_text(contract_value)
    if not schedule_norm or not contract_norm:
        status = "manual_review"
        message = "Источник финансирования отсутствует в одном из документов."
    elif schedule_norm == contract_norm or schedule_norm in contract_norm or contract_norm in schedule_norm:
        status = "passed"
        message = "Источник финансирования совпадает по нормализованному тексту."
    else:
        status = "failed"
        message = "Источник финансирования различается между заявкой и контрактом."
    return [
        _result(
            "strict.funding_source",
            "Источник финансирования",
            status,
            "strict",
            message,
            documents=["schedule_application", "contract_draft"],
            fields=["schedule_application.funding_source_text", "contract_draft.funding_source"],
            details={"schedule_application": schedule_value, "contract_draft": contract_value},
        )
    ]


def _check_securities(package: ProcurementPackageExtraction) -> list[CheckResult]:
    schedule = package.schedule_application
    contract = package.contract_draft
    schedule_contract_security = schedule.contract_security if schedule else None
    schedule_warranty_security = schedule.warranty_security if schedule else None
    contract_security = getattr(contract, "contract_security", None) if contract else None
    warranty_security = getattr(contract, "warranty_security", None) if contract else None

    present = [
        value
        for value in (
            schedule_contract_security,
            schedule_warranty_security,
            contract_security,
            warranty_security,
        )
        if value is not None
    ]
    if not present:
        status = "manual_review"
        message = "Данные об обеспечениях не извлечены из заявки или проекта контракта."
    elif contract_security and contract_security.is_not_required:
        status = "passed"
        message = "Обеспечение исполнения контракта не предусмотрено; это зафиксировано в проекте контракта."
    elif contract_security:
        status = "passed"
        message = "Обеспечение исполнения контракта извлечено из проекта контракта."
    else:
        status = "manual_review"
        message = "В заявке есть данные об обеспечениях, но в проекте контракта они не извлечены."
    return [
        _result(
            "strict.securities",
            "Обеспечения",
            status,
            "strict",
            message,
            documents=["schedule_application", "contract_draft"],
            fields=[
                "schedule_application.application_security",
                "schedule_application.contract_security",
                "schedule_application.warranty_security",
                "contract_draft.contract_security",
                "contract_draft.warranty_security",
            ],
            details={
                "schedule_contract_security": _security_details(schedule_contract_security),
                "schedule_warranty_security": _security_details(schedule_warranty_security),
                "contract_security": _security_details(contract_security),
                "warranty_security": _security_details(warranty_security),
            },
        )
    ]


def _security_details(value: Any) -> dict[str, Any] | None:
    return value.model_dump(mode="json") if value is not None and hasattr(value, "model_dump") else None


def _check_contract_attachments(package: ProcurementPackageExtraction) -> list[CheckResult]:
    contract = package.contract_draft
    if contract is None:
        return [
            _result(
                "strict.contract.attachments",
                "Приложения контракта",
                "manual_review",
                "strict",
                "Проект контракта отсутствует.",
                fields=["contract_draft.referenced_attachments"],
            )
        ]
    if not contract.referenced_attachments:
        return [
            _result(
                "strict.contract.attachments",
                "Приложения контракта",
                "manual_review",
                "strict",
                "В проекте контракта не извлечён список приложений.",
                documents=["contract_draft"],
                fields=["contract_draft.referenced_attachments"],
            )
        ]
    failures = []
    for attachment in contract.referenced_attachments:
        if attachment.attachment_kind == "purchase_description" and not contract.items:
            failures.append(f"Приложение №{attachment.number} '{attachment.title_raw}' требует таблицу ООЗ.")
        elif attachment.attachment_kind == "contract_specification" and not contract.specification_items:
            failures.append(f"Приложение №{attachment.number} '{attachment.title_raw}' требует таблицу спецификации.")
    if failures:
        status = "failed"
        message = "В контракте есть ссылки на приложения, но соответствующие данные не найдены."
    else:
        status = "passed"
        message = "Приложения контракта имеют корректные номера и названия; ООЗ и спецификация найдены, форма акта не проверяется на этом этапе."
    return [
        _result(
            "strict.contract.attachments",
            "Приложения контракта",
            status,
            "strict",
            message,
            documents=["contract_draft"],
            fields=[
                "contract_draft.referenced_attachments",
                "contract_draft.items",
                "contract_draft.specification_items",
            ],
            details={
                "referenced": [item.model_dump(mode="json") for item in contract.referenced_attachments],
                "failures": failures,
            },
        )
    ]


def _semantic_manual_checks(package: ProcurementPackageExtraction) -> list[CheckResult]:
    checks = [
        ("semantic.subject", "Предмет закупки", ["purchase_subject", "subject"]),
        ("semantic.delivery_term", "Срок поставки", ["delivery_term_text"]),
        ("semantic.delivery_place", "Место поставки", ["delivery_place"]),
        ("semantic.stages", "Этапы исполнения", ["stages", "stage_execution_terms"]),
        ("semantic.warranty", "Гарантии", ["warranty_text", "warranty_requirements_text"]),
        ("semantic.procurement_method", "Способ закупки и основание ЕП", ["procurement_method_raw", "single_supplier_basis_text"]),
        ("semantic.smp_preferences", "СМП/СОНКО", ["smp_preference", "subcontract_smp_sonko_required"]),
    ]
    return [
        _result(
            check_id,
            title,
            "manual_review",
            "semantic",
            "Semantic/LLM-сверка для этого пункта будет подключена отдельным этапом.",
            fields=fields,
        )
        for check_id, title, fields in checks
    ]


def _external_manual_checks(package: ProcurementPackageExtraction) -> list[CheckResult]:
    checks = [
        ("manual.commercial_offers.content", "Проверка КП", ["commercial_offers"]),
        ("manual.commercial_offers.onmck", "Сверка КП с ОНМЦК", ["commercial_offers", "nmck_justification.price_sources"]),
        ("manual.ktru.characteristics", "КТРУ-характеристики", ["purchase_description.items[].characteristics"]),
        ("manual.ktru.additional", "Дополнительные характеристики КТРУ", ["purchase_description.items[].characteristics"]),
        ("manual.national_regime_1875", "Национальный режим / ПП №1875", ["schedule_application.national_regime_raw"]),
    ]
    return [
        _result(
            check_id,
            title,
            "manual_review",
            "manual_review",
            "Проверка требует внешнего или нормативного слоя и на текущем этапе только фиксируется.",
            fields=fields,
        )
        for check_id, title, fields in checks
    ]


def external_manual_checks_with_replacements(
    package: ProcurementPackageExtraction,
    replacements: list[CheckResult],
) -> list[CheckResult]:
    by_id = {item.check_id: item for item in replacements}
    return [by_id.get(item.check_id, item) for item in _external_manual_checks(package)]


def _item_label(item: PurchaseItem | NmckItem) -> str:
    return str(item.name or item.row_number or "позиция")
