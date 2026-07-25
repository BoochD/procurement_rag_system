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
    normalize_money,
    normalize_text,
    normalize_unit,
)
from summary_model.extraction_models import (
    CommercialOfferItem,
    ContractDraftSchema,
    NmckItem,
    PriceSource,
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
    stage_results: list[CheckResult] | None = None,
    penalty_results: list[CheckResult] | None = None,
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
    results.extend(_check_onmck_stage_prices(package))
    results.extend(_check_commercial_offer_content(package))
    results.extend(_check_commercial_offers_against_onmck(package))
    results.extend(_check_codes(package, "okpd2"))
    results.extend(_check_codes(package, "ktru"))
    results.extend(_check_plan_ground_truth(package, stage_results=stage_results))
    results.extend(_check_funding_source(package))
    results.extend(_check_securities(package))
    results.extend(penalty_results if penalty_results is not None else _check_contract_penalties(package))
    results.extend(_check_smp_sonko_subcontract(package))
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
        message = "Коммерческие предложения не приложены или их меньше трёх."
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


def _check_commercial_offer_content(package: ProcurementPackageExtraction) -> list[CheckResult]:
    offers = list(package.commercial_offers or [])
    if not offers:
        return [
            _result(
                "manual.commercial_offers.content",
                "Проверка КП",
                "manual_review",
                "manual_review",
                "Коммерческие предложения не приложены. Содержательная проверка КП невозможна.",
                documents=["commercial_offers"],
                fields=["commercial_offers"],
                details={"summary_lines": ["КП не приложены."]},
            )
        ]

    summary_lines: list[str] = []
    missing: list[str] = []
    warnings: list[str] = []
    for index, offer in enumerate(offers, 1):
        label = _commercial_offer_label(index, offer)
        summary_lines.append(
            (
                f"{label}: поставщик {offer.supplier_name or 'не найден'}; "
                f"ИНН {offer.inn or 'не найден'}; "
                f"исх. № {offer.outgoing_number or 'не найден'}; "
                f"дата {_format_date(getattr(offer, 'outgoing_date', None) or offer.offer_date)}; "
                f"сумма {_format_money(_money_amount(offer.total_amount))}; "
                f"позиций {len(offer.items)}"
            )
        )
        for field_name, title in (
            ("supplier_name", "поставщик"),
            ("inn", "ИНН"),
            ("outgoing_number", "исходящий номер"),
        ):
            if not getattr(offer, field_name, None):
                missing.append(f"{label}: не найден {title}")
        if not (getattr(offer, "outgoing_date", None) or offer.offer_date):
            missing.append(f"{label}: не найдена дата КП/исходящего письма")
        if not offer.items:
            missing.append(f"{label}: не распознаны позиции ТРУ")
        if _money_amount(offer.total_amount) is None:
            missing.append(f"{label}: не распознана итоговая сумма")
        if offer.advance_payment_text:
            warnings.append(f"{label}: найден авансовый платёж - {offer.advance_payment_text}")
        else:
            summary_lines.append(f"{label}: авансовый платёж не найден")
        if not offer.delivery_term_text:
            warnings.append(f"{label}: срок поставки/оказания услуг не распознан")
        if not offer.delivery_place:
            warnings.append(f"{label}: место поставки/оказания услуг не распознано")
        if not offer.vat_text and offer.vat_rate is None and offer.vat_amount is None:
            warnings.append(f"{label}: НДС не распознан")
        else:
            summary_lines.append(f"{label}: НДС - {_vat_summary(offer)}")
        for item in offer.items:
            if item.trademark:
                warnings.append(
                    f"{label}: найден товарный знак '{item.trademark}' по позиции '{item.name or item.row_number}'"
                )

    if missing:
        status = "manual_review"
        message = "Часть обязательных реквизитов или строк КП не распознана."
    elif warnings:
        status = "warning"
        message = "КП распознаны, но часть условий требует проверки."
    else:
        status = "passed"
        message = "КП распознаны и содержат основные реквизиты."
    return [
        _result(
            "manual.commercial_offers.content",
            "Проверка КП",
            status,
            "manual_review",
            message,
            documents=["commercial_offers"],
            fields=[
                "commercial_offers[].supplier_name",
                "commercial_offers[].inn",
                "commercial_offers[].outgoing_number",
                "commercial_offers[].items",
                "commercial_offers[].total_amount",
            ],
            details={
                "summary_lines": summary_lines + warnings + missing,
                "missing": missing,
                "warnings": warnings,
            },
        )
    ]


def _check_commercial_offers_against_onmck(package: ProcurementPackageExtraction) -> list[CheckResult]:
    offers = list(package.commercial_offers or [])
    onmck = package.nmck_justification
    if not offers:
        return [
            _result(
                "manual.commercial_offers.onmck",
                "Сверка КП с ОНМЦК",
                "manual_review",
                "manual_review",
                "Коммерческие предложения не приложены. Сверка КП с ОНМЦК невозможна.",
                documents=["commercial_offers", "nmck_justification"],
                fields=["commercial_offers", "nmck_justification.price_sources"],
                details={"summary_lines": ["КП не приложены."]},
            )
        ]
    if onmck is None or not onmck.items:
        return [
            _result(
                "manual.commercial_offers.onmck",
                "Сверка КП с ОНМЦК",
                "manual_review",
                "manual_review",
                "ОНМЦК не распознана. Сверка КП с ОНМЦК невозможна.",
                documents=["commercial_offers", "nmck_justification"],
                fields=["commercial_offers", "nmck_justification.items"],
            )
        ]

    offer_by_source, source_warnings = _match_offers_to_price_sources(offers, onmck.price_sources)
    ooz_items = list(package.purchase_description.items if package.purchase_description else [])
    summary_lines = list(source_warnings)
    failures: list[str] = []
    manual: list[str] = []

    for nmck_item in onmck.items:
        item_label = _item_label(nmck_item)
        offer_prices: list[tuple[str, Decimal]] = []
        for supplier_price in nmck_item.supplier_prices:
            offer = offer_by_source.get(supplier_price.source_id)
            source_label = _supplier_label(supplier_price.source_id)
            if offer is None:
                manual.append(f"{item_label}: для {source_label} не найдено соответствующее КП")
                continue
            offer_item, reason = _match_offer_item(nmck_item, offer.items)
            if offer_item is None:
                manual.append(f"{item_label}: {_commercial_offer_name(offer)} - {reason}")
                continue
            offer_unit_price = _money(offer_item.unit_price)
            nmck_unit_price = _money(supplier_price.unit_price)
            if offer_unit_price is not None:
                offer_prices.append((supplier_price.source_id, offer_unit_price))
            if offer_unit_price is not None and nmck_unit_price is not None and offer_unit_price != nmck_unit_price:
                failures.append(
                    f"{item_label}: {_commercial_offer_name(offer)} цена за единицу {_format_money(offer_unit_price)} не совпадает с ОНМЦК {_format_money(nmck_unit_price)}"
                )
            _compare_offer_item_to_reference(
                item_label=item_label,
                offer=offer,
                offer_item=offer_item,
                nmck_item=nmck_item,
                ooz_items=ooz_items,
                failures=failures,
                manual=manual,
            )
            _check_offer_row_total(item_label, offer, offer_item, failures, manual)

        selected = _money(nmck_item.selected_min_unit_price)
        if offer_prices and selected is not None:
            minimum = min(price for _, price in offer_prices)
            price_text = ", ".join(
                f"{_supplier_label(source_id)} = {_format_money(price)}"
                for source_id, price in offer_prices
            )
            if selected != minimum:
                failures.append(
                    f"{item_label}: в ОНМЦК выбрана минимальная цена {_format_money(selected)}, фактический минимум по КП {_format_money(minimum)}"
                )
            summary_lines.append(
                f"{item_label}: выбранная минимальная цена ОНМЦК {_format_money(selected)}; цены КП: {price_text}"
            )
        coefficient = _variation_coefficient([price for _, price in offer_prices])
        if coefficient is None:
            manual.append(f"{item_label}: коэффициент вариации по КП не рассчитан, недостаточно цен")
        else:
            summary_lines.append(f"{item_label}: коэффициент вариации по КП {coefficient:.2f}%")

    if failures:
        status = "failed"
        message = "В сверке КП с ОНМЦК/ООЗ найдены расхождения."
    elif manual:
        status = "manual_review"
        message = "Часть строк КП требует ручной проверки."
    else:
        status = "passed"
        message = "КП согласованы с ОНМЦК и ООЗ по распознанным данным."
    return [
        _result(
            "manual.commercial_offers.onmck",
            "Сверка КП с ОНМЦК",
            status,
            "manual_review",
            message,
            documents=["commercial_offers", "nmck_justification", "purchase_description"],
            fields=[
                "commercial_offers[].items",
                "nmck_justification.price_sources",
                "nmck_justification.items[].supplier_prices",
                "purchase_description.items",
            ],
            details={
                "summary_lines": summary_lines + manual + failures,
                "failures": failures,
                "manual_review": manual,
            },
        )
    ]


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
    return normalize_money(getattr(value, "amount", None))


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
        f"{DOCUMENT_LABELS.get(key, key)}: {_format_money(value)}"
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
        if _money(calculated) != _money(declared):
            failed.append({"item": _item_label(item), "expected": _format_money(calculated), "actual": _format_money(declared)})
    row_sum = sum((_money(item.row_total_declared) or Decimal("0.00")) for item in onmck.items)
    total = _money_amount(onmck.total_amount)
    plan_total = _money_amount(package.schedule_application.nmck if package.schedule_application else None)
    total_mismatch = total is not None and _money(row_sum) != total
    plan_mismatch = plan_total is not None and _money(row_sum) != plan_total
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
                    f"сумма строк: {_format_money(row_sum)}",
                    f"итог ОНМЦК: {_format_money(total)}" if total is not None else "итог ОНМЦК: не найден",
                    f"НМЦК в заявке: {_format_money(plan_total)}" if plan_total is not None else "НМЦК в заявке: не найден",
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
        quantity = normalize_decimal(item.quantity)
        line_prefix = _item_label(item)
        if quantity is not None:
            line_prefix += f"; количество {_format_decimal(quantity)} {item.unit or ''}".rstrip()
        if selected != minimum:
            failed.append({"item": _item_label(item), "expected": _format_decimal(minimum), "actual": _format_decimal(selected)})
        min_source_id = next((source_id for source_id, price in source_prices if price == minimum), None)
        min_source_label = source_labels.get(min_source_id, _supplier_label(min_source_id)) if min_source_id else "поставщик не определён"
        item_summary_lines[-1] = (
            f"{line_prefix}: выбранная минимальная цена {_format_decimal(selected)} "
            f"({min_source_label}); цены поставщиков: {price_text}; "
            f"{'ОК' if selected == minimum else 'ОШИБКА'}"
        )
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
    source_labels = {
        source.source_id: _supplier_label(source.supplier_name_raw or source.raw_header or source.source_id)
        for source in onmck.price_sources
    }
    for index, item in enumerate(onmck.items, 1):
        price_pairs = [
            (price.source_id, normalize_decimal(price.unit_price))
            for price in item.supplier_prices
            if normalize_decimal(price.unit_price) is not None
        ]
        prices = [price for _source_id, price in price_pairs if price is not None]
        if not prices:
            incomplete.append(_item_label(item))
            continue
        coefficient = _variation_coefficient(prices)
        coefficient_text = f"{coefficient:.2f}%" if coefficient is not None else "не рассчитан"
        price_text = ", ".join(
            f"{source_labels.get(source_id, _supplier_label(source_id))} = {_format_decimal(price)}"
            for source_id, price in price_pairs
            if price is not None
        )
        summary_lines.append(
            f"№{index} {_item_label(item)} | коэффициент вариации: {coefficient_text} | Цены: {price_text}"
        )

    total_price_pairs = _supplier_total_prices(onmck.items)
    if total_price_pairs:
        total_prices = [price for _source_id, price in total_price_pairs]
        coefficient = _variation_coefficient(total_prices)
        coefficient_text = f"{coefficient:.2f}%" if coefficient is not None else "не рассчитан"
        price_text = ", ".join(
            f"{source_labels.get(source_id, _supplier_label(source_id))} = {_format_decimal(price)}"
            for source_id, price in total_price_pairs
        )
        summary_lines.append(f"№ ИТОГО | коэффициент вариации: {coefficient_text} | Цены: {price_text}")

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


def _check_onmck_stage_prices(package: ProcurementPackageExtraction) -> list[CheckResult]:
    onmck = package.nmck_justification
    stages = list(getattr(onmck, "stages", []) or []) if onmck else []
    items = list(getattr(onmck, "items", []) or []) if onmck else []
    if not stages:
        return [
            _result(
                "strict.onmck.stage_prices",
                "Стоимость этапов в ОНМЦК",
                "not_applicable",
                "strict",
                "Этапы в ОНМЦК не извлечены.",
                fields=["nmck_justification.stages"],
            )
        ]

    items_by_stage: dict[str, list[NmckItem]] = defaultdict(list)
    for item in items:
        stage_number = _stage_prefix(getattr(item, "row_number", None))
        if stage_number:
            items_by_stage[stage_number].append(item)

    failed: list[str] = []
    incomplete: list[str] = []
    summary_lines: list[str] = []
    for stage in stages:
        number = clean_stage_number(getattr(stage, "stage_number", None))
        declared = _money_amount(getattr(stage, "price", None))
        child_items = items_by_stage.get(number or "", [])
        child_sum = sum(
            (_money(getattr(item, "row_total_declared", None)) or Decimal("0.00"))
            for item in child_items
        )
        label = f"Этап {number or '?'}"
        if getattr(stage, "stage_name", None):
            label += f": {_short_report_text(getattr(stage, 'stage_name'), limit=80)}"
        if not child_items:
            incomplete.append(f"{label}: вложенные строки не найдены")
            summary_lines.append(f"{label}: вложенные строки не найдены; цена этапа {_format_money(declared)}")
            continue
        if declared is None:
            incomplete.append(f"{label}: цена этапа не найдена")
            summary_lines.append(f"{label}: сумма вложенных строк {_format_money(child_sum)}, цена этапа не найдена")
            continue
        if _money(child_sum) != declared:
            failed.append(f"{label}: ожидалось {_format_money(child_sum)}, указано {_format_money(declared)}")
        summary_lines.append(
            f"{label}: вложенных строк {len(child_items)}, сумма строк {_format_money(child_sum)}, цена этапа {_format_money(declared)}"
        )

    total = _money_amount(getattr(onmck, "total_amount", None))
    stage_total = sum((_money_amount(getattr(stage, "price", None)) or Decimal("0.00")) for stage in stages)
    if total is not None and _money(stage_total) != total:
        failed.append(f"Итог этапов: ожидалось {_format_money(total)}, сумма этапов {_format_money(stage_total)}")
    summary_lines.append(
        f"Итого по этапам: {_format_money(stage_total)}; итог ОНМЦК: {_format_money(total)}"
    )

    if failed:
        status = "failed"
        message = "В стоимости этапов ОНМЦК найдены расхождения."
    elif incomplete:
        status = "manual_review"
        message = "Стоимость части этапов ОНМЦК требует ручной проверки."
    else:
        status = "passed"
        message = "Стоимость этапов ОНМЦК согласована с вложенными строками и итогом."
    return [
        _result(
            "strict.onmck.stage_prices",
            "Стоимость этапов в ОНМЦК",
            status,
            "strict",
            message,
            documents=["nmck_justification"],
            fields=[
                "nmck_justification.stages[].price",
                "nmck_justification.items[].row_total_declared",
                "nmck_justification.total_amount",
            ],
            details={
                "summary_lines": summary_lines,
                "failed": failed,
                "incomplete": incomplete,
            },
        )
    ]


def _check_commercial_offer_content(package: ProcurementPackageExtraction) -> list[CheckResult]:
    offers = package.commercial_offers
    if not offers:
        return [
            _result(
                "strict.commercial_offers.content",
                "Коммерческие предложения",
                "manual_review",
                "strict",
                "Коммерческие предложения не приложены. Требуется не менее 3 КП.",
                fields=["commercial_offers"],
                details={
                    "count": 0,
                    "required": 3,
                    "summary_lines": ["Коммерческие предложения не приложены."],
                },
            )
        ]

    summary_lines = []
    for index, offer in enumerate(offers, 1):
        supplier = offer.supplier_name or f"Поставщик #{index}"
        number_date = f"№ {offer.outgoing_number or 'б/н'} от {offer.outgoing_date or 'б/д'}"
        total = _money_amount(offer.total_amount)
        total_str = f"{_format_money(total)}" if total is not None else "сумма не определена"
        summary_lines.append(
            f"КП №{index}: {supplier} ({number_date}) | Сумма: {total_str} | Позиций: {len(offer.items)}"
        )

    if len(offers) < 3:
        status = "failed"
        message = f"Приложено {len(offers)} КП из 3 необходимых."
    else:
        status = "passed"
        message = f"Приложено {len(offers)} Коммерческих Предложений (требование >= 3 выполнено)."

    return [
        _result(
            "strict.commercial_offers.content",
            "Коммерческие предложения",
            status,
            "strict",
            message,
            fields=["commercial_offers"],
            details={
                "count": len(offers),
                "required": 3,
                "summary_lines": summary_lines,
            },
        )
    ]


def _check_commercial_offers_against_onmck(package: ProcurementPackageExtraction) -> list[CheckResult]:
    offers = package.commercial_offers
    onmck = package.nmck_justification
    if not offers or not onmck:
        return [
            _result(
                "strict.commercial_offers.onmck_match",
                "Сверка КП с ОНМЦК",
                "manual_review",
                "strict",
                "Сверка КП с ОНМЦК невозможна (отсутствуют КП или ОНМЦК).",
                fields=["commercial_offers", "nmck_justification"],
            )
        ]

    summary_lines = []
    sources = onmck.price_sources or []
    source_labels = {
        s.source_id: s.supplier_name_raw or s.raw_header or s.source_id
        for s in sources
    }

    matched_count = 0
    for offer in offers:
        off_num = offer.outgoing_number
        off_date = offer.outgoing_date
        supplier = offer.supplier_name or "Неизвестный поставщик"
        matched_source = None
        for s in sources:
            if off_num and s.outgoing_letter_number and off_num in s.outgoing_letter_number:
                matched_source = s
                break
        if matched_source:
            matched_count += 1
            summary_lines.append(
                f"КП от {supplier} (№ {off_num or 'б/н'}): совпадает с источником цены ОНМЦК '{matched_source.source_id}'."
            )
        else:
            summary_lines.append(
                f"КП от {supplier} (№ {off_num or 'б/н'}): точный источник в таблице ОНМЦК не привязан автоматически (требуется визуальная сверка)."
            )

    status = "passed" if matched_count == len(offers) else "manual_review"
    message = "Сверка реквизитов и цен КП с ОНМЦК выполнена."

    return [
        _result(
            "strict.commercial_offers.onmck_match",
            "Сверка КП с ОНМЦК",
            status,
            "strict",
            message,
            fields=["commercial_offers", "nmck_justification"],
            details={
                "matched_sources": matched_count,
                "total_offers": len(offers),
                "summary_lines": summary_lines,
            },
        )
    ]


def _stage_prefix(value: Any) -> str | None:
    text = str(value or "").strip()
    match = re.match(r"(\d+)\.", text)
    return match.group(1) if match else None


def clean_stage_number(value: Any) -> str | None:
    text = str(value or "").strip()
    match = re.search(r"\d+", text)
    return match.group(0) if match else None


def _variation_coefficient(values: list[Decimal]) -> float | None:
    if len(values) < 2:
        return None
    numbers = [float(value) for value in values]
    mean = sum(numbers) / len(numbers)
    if mean == 0:
        return None
    variance = sum((value - mean) ** 2 for value in numbers) / (len(numbers) - 1)
    return sqrt(variance) / mean * 100


def _supplier_total_prices(items: list[NmckItem]) -> list[tuple[str, Decimal]]:
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
    return [(key, totals[key]) for key in sorted(totals)]


def _money(value: Any) -> Decimal | None:
    return normalize_money(value)


def _format_money(value: Decimal | None) -> str:
    if value is None:
        return "не найдено"
    return f"{_money(value) or value:.2f}"


def _format_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _supplier_label(value: str | None) -> str:
    text = str(value or "").strip()
    match = re.fullmatch(r"(?i)supplier[_\s-]*(\d+)", text)
    if match:
        return f"Поставщик{match.group(1)}"
    match = re.fullmatch(r"(?i)(поставщик|исполнитель)[\s_-]*(\d+)", text)
    if match:
        return f"{match.group(1).capitalize()}{match.group(2)}"
    return text


def _commercial_offer_label(index: int, offer: Any) -> str:
    return f"КП №{index} ({_commercial_offer_name(offer)})"


def _commercial_offer_name(offer: Any) -> str:
    return str(getattr(offer, "supplier_name", None) or getattr(offer, "document_title", None) or "поставщик не определён")


def _format_date(value: Any) -> str:
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else "не найдена"


def _vat_summary(offer: Any) -> str:
    parts = []
    if getattr(offer, "vat_text", None):
        parts.append(str(offer.vat_text))
    if getattr(offer, "vat_rate", None) is not None:
        parts.append(f"ставка {offer.vat_rate}%")
    if getattr(offer, "vat_amount", None) is not None:
        parts.append(f"сумма {_format_money(_money(offer.vat_amount))}")
    if getattr(offer, "vat_included", None) is not None:
        parts.append("включён" if offer.vat_included else "не включён")
    return "; ".join(parts) if parts else "не распознан"


def _match_offers_to_price_sources(
    offers: list[Any],
    sources: list[PriceSource],
) -> tuple[dict[str, Any], list[str]]:
    by_source: dict[str, Any] = {}
    warnings: list[str] = []
    unmatched = list(offers)
    for source in sources:
        source_number = normalize_text(source.outgoing_letter_number)
        source_date = source.outgoing_letter_date
        matched = None
        if source_number or source_date:
            candidates = [
                offer
                for offer in unmatched
                if (
                    (not source_number or normalize_text(getattr(offer, "outgoing_number", None)) == source_number)
                    and (not source_date or getattr(offer, "outgoing_date", None) == source_date or getattr(offer, "offer_date", None) == source_date)
                )
            ]
            if len(candidates) == 1:
                matched = candidates[0]
            elif len(candidates) > 1:
                warnings.append(f"{_supplier_label(source.source_id)}: несколько КП совпали по исходящему номеру/дате")
        if matched is None:
            index = _source_index(source.source_id)
            if index is not None and 0 <= index - 1 < len(offers):
                matched = offers[index - 1]
                warnings.append(
                    f"{_supplier_label(source.source_id)}: в ОНМЦК нет однозначного исходящего номера/даты, КП сопоставлено по порядку загрузки"
                )
        if matched is not None:
            by_source[source.source_id] = matched
            if matched in unmatched:
                unmatched.remove(matched)
        else:
            warnings.append(f"{_supplier_label(source.source_id)}: соответствующее КП не найдено")
    return by_source, warnings


def _source_index(source_id: str | None) -> int | None:
    match = re.search(r"(\d+)", str(source_id or ""))
    return int(match.group(1)) if match else None


def _match_offer_item(
    nmck_item: NmckItem,
    offer_items: list[CommercialOfferItem],
) -> tuple[CommercialOfferItem | None, str]:
    candidates = [item for item in offer_items if _same_code(item.ktru_code, nmck_item.ktru_code)]
    if not candidates and nmck_item.okpd2_code:
        candidates = [
            item
            for item in offer_items
            if _same_code(item.okpd2_code, nmck_item.okpd2_code)
            and _names_close(item.name, nmck_item.name)
        ]
    if not candidates:
        candidates = [item for item in offer_items if _names_close(item.name, nmck_item.name)]
    if len(candidates) == 1:
        return candidates[0], "позиция найдена"
    if len(candidates) > 1:
        return None, f"найдено несколько похожих строк КП для позиции '{_item_label(nmck_item)}'"
    return None, f"строка КП для позиции '{_item_label(nmck_item)}' не найдена"


def _compare_offer_item_to_reference(
    *,
    item_label: str,
    offer: Any,
    offer_item: CommercialOfferItem,
    nmck_item: NmckItem,
    ooz_items: list[PurchaseItem],
    failures: list[str],
    manual: list[str],
) -> None:
    if offer_item.quantity is None:
        manual.append(f"{item_label}: {_commercial_offer_name(offer)} количество в КП не распознано")
    elif nmck_item.quantity is not None and normalize_decimal(offer_item.quantity) != normalize_decimal(nmck_item.quantity):
        failures.append(
            f"{item_label}: {_commercial_offer_name(offer)} количество {offer_item.quantity} не совпадает с ОНМЦК {nmck_item.quantity}"
        )
    if not offer_item.unit:
        manual.append(f"{item_label}: {_commercial_offer_name(offer)} единица измерения в КП не распознана")
    elif nmck_item.unit and normalize_unit(offer_item.unit) != normalize_unit(nmck_item.unit):
        failures.append(
            f"{item_label}: {_commercial_offer_name(offer)} единица '{offer_item.unit}' не совпадает с ОНМЦК '{nmck_item.unit}'"
        )
    ooz_item = _match_reference_purchase_item(nmck_item, ooz_items)
    if ooz_item is not None:
        if offer_item.unit and ooz_item.unit and normalize_unit(offer_item.unit) != normalize_unit(ooz_item.unit):
            failures.append(
                f"{item_label}: {_commercial_offer_name(offer)} единица '{offer_item.unit}' не совпадает с ООЗ '{ooz_item.unit}'"
            )
        if offer_item.quantity is not None and ooz_item.quantity is not None and normalize_decimal(offer_item.quantity) != normalize_decimal(ooz_item.quantity):
            failures.append(
                f"{item_label}: {_commercial_offer_name(offer)} количество {offer_item.quantity} не совпадает с ООЗ {ooz_item.quantity}"
            )


def _check_offer_row_total(
    item_label: str,
    offer: Any,
    offer_item: CommercialOfferItem,
    failures: list[str],
    manual: list[str],
) -> None:
    quantity = normalize_decimal(offer_item.quantity)
    unit_price = _money(offer_item.unit_price)
    total = _money(offer_item.total_price)
    if total is None:
        manual.append(f"{item_label}: {_commercial_offer_name(offer)} итоговая стоимость строки КП не распознана")
        return
    if quantity is not None and unit_price is not None and _money(quantity * unit_price) != total:
        failures.append(
            f"{item_label}: {_commercial_offer_name(offer)} итог строки {_format_money(total)} не равен количество × цена {_format_money(quantity * unit_price)}"
        )


def _match_reference_purchase_item(
    item: NmckItem,
    reference_items: list[PurchaseItem],
) -> PurchaseItem | None:
    for reference in reference_items:
        if _same_code(reference.ktru_code, item.ktru_code):
            return reference
    for reference in reference_items:
        if _same_code(reference.okpd2_code, item.okpd2_code) and _names_close(reference.name, item.name):
            return reference
    matches = [reference for reference in reference_items if _names_close(reference.name, item.name)]
    return matches[0] if len(matches) == 1 else None


def _same_code(left: str | None, right: str | None) -> bool:
    return bool(left and right and normalize_code(left) == normalize_code(right))


def _names_close(left: str | None, right: str | None) -> bool:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm or not right_norm:
        return False
    return left_norm == right_norm or left_norm in right_norm or right_norm in left_norm


def _check_codes(package: ProcurementPackageExtraction, code_type: str) -> list[CheckResult]:
    values = _document_code_sets(package, code_type)
    names = _document_code_names(package, code_type)
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
    similar_by_document = (
        _similar_missing_codes(missing_by_document, values, names)
        if code_type == "okpd2"
        else {}
    )
    summary_lines = [
        f"{DOCUMENT_LABELS.get(name, name)}: {', '.join(sorted(codes))}"
        for name, codes in values.items()
        if codes
    ]
    if missing_by_document:
        for name, codes in missing_by_document.items():
            label = DOCUMENT_LABELS.get(name, name)
            for code in codes:
                code_text = _code_with_name(code, names.get("schedule_application", {}))
                similar = similar_by_document.get(name, {}).get(code, [])
                if similar:
                    similar_text = "; ".join(
                        _code_with_name(item, names.get(name, {})) for item in similar
                    )
                    summary_lines.append(f"{label}: не найден {code_text}; похожие найденные коды: {similar_text}")
                else:
                    summary_lines.append(f"{label}: не найден {code_text}")
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
                "similar_by_document": similar_by_document,
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
            "purchase_description": _document_level_codes(package.purchase_description, schedule_field)
            | _item_codes(package.purchase_description.items if package.purchase_description else [], field)
            | _item_okpd2_from_ktru(package.purchase_description.items if package.purchase_description else []),
            "contract_draft": _document_level_codes(package.contract_draft, schedule_field)
            | _item_codes(package.contract_draft.items if package.contract_draft else [], field)
            | _item_okpd2_from_ktru(package.contract_draft.items if package.contract_draft else []),
            "nmck_justification": _document_level_codes(package.nmck_justification, schedule_field)
            | _item_codes(package.nmck_justification.items if package.nmck_justification else [], field)
            | _item_okpd2_from_ktru(package.nmck_justification.items if package.nmck_justification else []),
        }
    return {
        "schedule_application": {
            normalize_code(code)
            for code in (getattr(package.schedule_application, schedule_field, []) if package.schedule_application else [])
            if normalize_code(code)
        },
        "purchase_description": _document_level_codes(package.purchase_description, schedule_field)
        | _item_codes(package.purchase_description.items if package.purchase_description else [], field),
        "contract_draft": _document_level_codes(package.contract_draft, schedule_field)
        | _item_codes(package.contract_draft.items if package.contract_draft else [], field),
        "nmck_justification": _document_level_codes(package.nmck_justification, schedule_field)
        | _item_codes(package.nmck_justification.items if package.nmck_justification else [], field),
    }


def _document_level_codes(document: Any, field: str) -> set[str]:
    if document is None:
        return set()
    return _normalized_codes(list(getattr(document, field, []) or []))


def _item_codes(items: list[Any], field: str) -> set[str]:
    return {normalize_code(getattr(item, field, None)) for item in items if normalize_code(getattr(item, field, None))}


def _document_code_names(package: ProcurementPackageExtraction, code_type: str) -> dict[str, dict[str, set[str]]]:
    documents = {
        "schedule_application": package.schedule_application,
        "purchase_description": package.purchase_description,
        "contract_draft": package.contract_draft,
        "nmck_justification": package.nmck_justification,
    }
    result: dict[str, dict[str, set[str]]] = {}
    for name, document in documents.items():
        names_by_code: dict[str, set[str]] = defaultdict(set)
        if document is None:
            result[name] = names_by_code
            continue
        _add_reference_names(names_by_code, getattr(document, "subject_codes", []) or [], code_type)
        _add_item_names(names_by_code, getattr(document, "items", []) or [], code_type)
        _add_item_names(names_by_code, getattr(document, "included_goods", []) or [], code_type)
        _add_item_names(names_by_code, getattr(document, "specification_items", []) or [], code_type)
        result[name] = names_by_code
    return result


def _add_reference_names(target: dict[str, set[str]], references: list[Any], code_type: str) -> None:
    for reference in references:
        if getattr(reference, "code_type", None) != code_type:
            continue
        code = normalize_code(getattr(reference, "code", None))
        name = _clean_report_name(getattr(reference, "name", None))
        if code and name:
            target[code].add(name)


def _add_item_names(target: dict[str, set[str]], items: list[Any], code_type: str) -> None:
    field = f"{code_type}_code"
    for item in items:
        code = normalize_code(getattr(item, field, None))
        name = _clean_report_name(getattr(item, "name", None))
        if code and name:
            target[code].add(name)
        if code_type == "okpd2":
            ktru_code = normalize_code(getattr(item, "ktru_code", None))
            if ktru_code and len(ktru_code) >= 21 and ktru_code[12] == "-":
                derived = normalize_code(ktru_code[:12])
                if derived and name:
                    target[derived].add(name)


def _similar_missing_codes(
    missing_by_document: dict[str, list[str]],
    values: dict[str, set[str]],
    names: dict[str, dict[str, set[str]]],
) -> dict[str, dict[str, list[str]]]:
    result: dict[str, dict[str, list[str]]] = {}
    for document_name, missing_codes in missing_by_document.items():
        present_codes = values.get(document_name, set())
        document_names = names.get(document_name, {})
        for missing in missing_codes:
            similar = [
                code
                for code in sorted(present_codes)
                if _okpd2_codes_are_similar(missing, code)
                or _names_overlap(names.get("schedule_application", {}).get(missing, set()), document_names.get(code, set()))
            ]
            if similar:
                result.setdefault(document_name, {})[missing] = similar
    return result


def _okpd2_codes_are_similar(left: str, right: str) -> bool:
    left_parts = str(left or "").split(".")
    right_parts = str(right or "").split(".")
    if len(left_parts) < 3 or len(right_parts) < 3:
        return False
    return left_parts[:3] == right_parts[:3] and left != right


def _names_overlap(left_names: set[str], right_names: set[str]) -> bool:
    for left in left_names:
        for right in right_names:
            if left and right and (left in right or right in left):
                return True
    return False


def _code_with_name(code: str, names_by_code: dict[str, set[str]]) -> str:
    names = sorted(name for name in names_by_code.get(code, set()) if name)
    if not names:
        return code
    return f"{code} - {names[0]}"


def _clean_report_name(value: str | None) -> str:
    return " ".join(str(value or "").split())


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


def _check_plan_ground_truth(
    package: ProcurementPackageExtraction,
    *,
    stage_results: list[CheckResult] | None = None,
) -> list[CheckResult]:
    return [
        _check_subject_against_plan(package),
        _check_text_against_plan(
            package,
            check_id="strict.plan.delivery_term",
            title="Срок поставки / оказания услуг",
            schedule_value=getattr(package.schedule_application, "delivery_term_text", None) if package.schedule_application else None,
            candidates=[
                ("purchase_request", getattr(package.purchase_request, "delivery_term_text", None) if package.purchase_request else None),
                ("purchase_description", getattr(package.purchase_description, "delivery_term_text", None) if package.purchase_description else None),
                ("contract_draft", getattr(package.contract_draft, "delivery_term_text", None) if package.contract_draft else None),
            ],
            fields=[
                "schedule_application.delivery_term_text",
                "purchase_request.delivery_term_text",
                "purchase_description.delivery_term_text",
                "contract_draft.delivery_term_text",
            ],
        ),
        _check_text_against_plan(
            package,
            check_id="strict.plan.delivery_place",
            title="Место поставки / оказания услуг",
            schedule_value=getattr(package.schedule_application, "delivery_place", None) if package.schedule_application else None,
            candidates=[
                ("purchase_description", getattr(package.purchase_description, "delivery_place", None) if package.purchase_description else None),
                ("contract_draft", getattr(package.contract_draft, "delivery_place", None) if package.contract_draft else None),
            ],
            fields=[
                "schedule_application.delivery_place",
                "purchase_description.delivery_place",
                "contract_draft.delivery_place",
            ],
        ),
        _check_text_against_plan(
            package,
            check_id="strict.plan.contract_execution_term",
            title="Срок исполнения контракта",
            schedule_value=getattr(package.schedule_application, "contract_execution_term_text", None) if package.schedule_application else None,
            candidates=[
                ("contract_draft", getattr(package.contract_draft, "contract_execution_term_text", None) if package.contract_draft else None),
            ],
            fields=[
                "schedule_application.contract_execution_term_text",
                "contract_draft.contract_execution_term_text",
            ],
        ),
        *(stage_results if stage_results is not None else [_check_stages_against_plan(package)]),
        _check_warranty_between_ooz_and_contract(package),
    ]


def _check_subject_against_plan(package: ProcurementPackageExtraction) -> CheckResult:
    schedule_value = getattr(package.schedule_application, "purchase_subject", None) if package.schedule_application else None
    candidates = [
        ("purchase_request", getattr(package.purchase_request, "purchase_subject", None) if package.purchase_request else None),
        ("purchase_description", getattr(package.purchase_description, "purchase_subject", None) if package.purchase_description else None),
        ("contract_draft", getattr(package.contract_draft, "subject", None) if package.contract_draft else None),
        ("explanatory_note", getattr(package.explanatory_note, "subject", None) if package.explanatory_note else None),
    ]
    return _check_text_against_plan(
        package,
        check_id="strict.plan.subject",
        title="Предмет закупки",
        schedule_value=schedule_value,
        candidates=candidates,
        fields=[
            "schedule_application.purchase_subject",
            "purchase_request.purchase_subject",
            "purchase_description.purchase_subject",
            "contract_draft.subject",
            "explanatory_note.subject",
        ],
    )


def _check_text_against_plan(
    package: ProcurementPackageExtraction,
    *,
    check_id: str,
    title: str,
    schedule_value: str | None,
    candidates: list[tuple[str, str | None]],
    fields: list[str],
) -> CheckResult:
    summary_lines = [f"Заявка в план-график: {schedule_value or 'не найдено'}"]
    present_candidates = [(name, value) for name, value in candidates if value]
    summary_lines.extend(f"{DOCUMENT_LABELS.get(name, name)}: {value}" for name, value in present_candidates)
    if not schedule_value:
        status = "manual_review"
        message = f"В заявке в план-график не найдено поле для сверки: {title.lower()}."
    elif not present_candidates:
        status = "manual_review"
        message = f"В других документах не найдены значения для сверки с заявкой: {title.lower()}."
    else:
        mismatches = [
            (name, value)
            for name, value in present_candidates
            if not _text_values_match(schedule_value, value)
        ]
        if not mismatches:
            status = "passed"
            message = f"{title} совпадает с заявкой в план-график по нормализованному тексту."
        else:
            status = "warning"
            message = f"{title} требует сверки: формулировки отличаются от заявки в план-график."
    return _result(
        check_id,
        title,
        status,
        "strict",
        message,
        documents=["schedule_application", *[name for name, _value in present_candidates]],
        fields=fields,
        details={"summary_lines": summary_lines},
    )


def _text_values_match(left: str | None, right: str | None) -> bool:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm or not right_norm:
        return False
    return left_norm == right_norm or left_norm in right_norm or right_norm in left_norm


def _check_stages_against_plan(package: ProcurementPackageExtraction) -> CheckResult:
    schedule = package.schedule_application
    plan_stages = list(getattr(schedule, "stages", []) or []) if schedule else []
    has_plan_stages = bool(plan_stages) or bool(getattr(schedule, "has_stages", None))
    docs = [
        ("purchase_request", list(getattr(package.purchase_request, "stages", []) or []) if package.purchase_request else []),
        ("purchase_description", list(getattr(package.purchase_description, "stages", []) or []) if package.purchase_description else []),
        ("contract_draft", list(getattr(package.contract_draft, "stages", []) or []) if package.contract_draft else []),
        ("nmck_justification", list(getattr(package.nmck_justification, "stages", []) or []) if package.nmck_justification else []),
    ]
    summary_lines = [f"Заявка в план-график: {_stages_summary(has_plan_stages, plan_stages)}"]
    summary_lines.extend(f"{DOCUMENT_LABELS.get(name, name)}: {_stages_summary(bool(stages), stages)}" for name, stages in docs)
    if not has_plan_stages and not any(stages for _name, stages in docs):
        status = "passed"
        message = "Этапы исполнения не предусмотрены в извлечённых данных."
    elif has_plan_stages and not plan_stages:
        status = "manual_review"
        message = "В заявке есть признак этапов, но структурированный список этапов не извлечён."
    else:
        comparison = _compare_stage_sets(plan_stages, docs)
        summary_lines.extend(comparison["summary_lines"])
        if comparison["failed"]:
            status = "failed"
            message = "Этапы исполнения расходятся с заявкой в план-график."
        elif comparison["manual"]:
            status = "manual_review"
            message = "Этапы исполнения требуют ручной проверки."
        else:
            status = "passed"
            message = "Порядок и сроки этапов согласованы с заявкой в план-график."
    return _result(
        "strict.plan.stages",
        "Этапы исполнения",
        status,
        "strict",
        message,
        documents=["schedule_application", *[name for name, stages in docs if stages]],
        fields=[
            "schedule_application.stages",
            "purchase_request.stages",
            "purchase_description.stages",
            "contract_draft.stages",
            "nmck_justification.stages",
        ],
        details={"summary_lines": summary_lines},
    )


def _compare_stage_sets(
    plan_stages: list[Any],
    docs: list[tuple[str, list[Any]]],
) -> dict[str, list[str]]:
    plan_numbers = [clean_stage_number(getattr(stage, "stage_number", None)) for stage in plan_stages]
    plan_numbers = [number for number in plan_numbers if number]
    plan_by_number = {
        clean_stage_number(getattr(stage, "stage_number", None)): stage
        for stage in plan_stages
        if clean_stage_number(getattr(stage, "stage_number", None))
    }
    failed: list[str] = []
    manual: list[str] = []
    summary_lines: list[str] = ["Сверка этапов по ПГ:"]
    for document_name, stages in docs:
        if document_name == "purchase_request" and not stages:
            continue
        label = DOCUMENT_LABELS.get(document_name, document_name)
        if not stages:
            manual.append(f"{label}: этапы не извлечены")
            summary_lines.append(f"{label}: этапы не извлечены")
            continue
        doc_numbers = [clean_stage_number(getattr(stage, "stage_number", None)) for stage in stages]
        doc_numbers = [number for number in doc_numbers if number]
        if plan_numbers and doc_numbers and doc_numbers != plan_numbers:
            failed.append(f"{label}: порядок/номера этапов {', '.join(doc_numbers)} вместо {', '.join(plan_numbers)}")
        doc_by_number = {
            clean_stage_number(getattr(stage, "stage_number", None)): stage
            for stage in stages
            if clean_stage_number(getattr(stage, "stage_number", None))
        }
        for number, plan_stage in plan_by_number.items():
            doc_stage = doc_by_number.get(number)
            if doc_stage is None:
                failed.append(f"{label}: этап {number} не найден")
                continue
            match_status = _stage_terms_match(plan_stage, doc_stage)
            if match_status == "mismatch":
                failed.append(
                    f"{label}: срок этапа {number} отличается от ПГ "
                    f"({_stage_term_summary(plan_stage)} / {_stage_term_summary(doc_stage)})"
                )
            elif match_status == "unknown":
                manual.append(f"{label}: срок этапа {number} не удалось сравнить")
        summary_lines.append(f"{label}: {_stage_match_summary(plan_by_number, doc_by_number)}")
    return {"failed": failed, "manual": manual, "summary_lines": summary_lines}


def _stage_terms_match(plan_stage: Any, document_stage: Any) -> str:
    plan_start = getattr(plan_stage, "service_start_date", None)
    plan_end = getattr(plan_stage, "service_end_date", None)
    doc_start = getattr(document_stage, "service_start_date", None)
    doc_end = getattr(document_stage, "service_end_date", None)
    if plan_start and plan_end and doc_start and doc_end:
        return "match" if (plan_start, plan_end) == (doc_start, doc_end) else "mismatch"
    plan_text = normalize_text(getattr(plan_stage, "service_term_text", None))
    doc_text = normalize_text(getattr(document_stage, "service_term_text", None))
    if plan_text and doc_text:
        return "match" if plan_text == doc_text or plan_text in doc_text or doc_text in plan_text else "mismatch"
    return "unknown"


def _stage_term_summary(stage: Any) -> str:
    start = getattr(stage, "service_start_date", None)
    end = getattr(stage, "service_end_date", None)
    if start and end:
        return f"{start}..{end}"
    return _short_report_text(getattr(stage, "service_term_text", None), limit=90) or "срок не найден"


def _stage_match_summary(plan_by_number: dict[str | None, Any], doc_by_number: dict[str | None, Any]) -> str:
    parts: list[str] = []
    for number, plan_stage in plan_by_number.items():
        doc_stage = doc_by_number.get(number)
        if doc_stage is None:
            parts.append(f"этап {number}: не найден")
            continue
        status = _stage_terms_match(plan_stage, doc_stage)
        if status == "match":
            parts.append(f"этап {number}: срок ОК")
        elif status == "mismatch":
            parts.append(f"этап {number}: срок отличается")
        else:
            parts.append(f"этап {number}: срок требует проверки")
    return "; ".join(parts)


def _stages_summary(has_stages: bool, stages: list[Any]) -> str:
    if not has_stages and not stages:
        return "этапы не найдены"
    if not stages:
        return "этапы указаны, но не распознаны структурированно"
    parts: list[str] = []
    for stage in stages[:5]:
        number = getattr(stage, "stage_number", None) or "?"
        name = _short_report_text(
            getattr(stage, "stage_name", None) or getattr(stage, "result_text", None) or "этап",
            limit=90,
        )
        term = _short_report_text(
            getattr(stage, "service_term_text", None) or getattr(stage, "execution_end_date", None) or "",
            limit=110,
        )
        price = getattr(getattr(stage, "price", None), "amount", None)
        price_text = f", стоимость {price}" if price is not None else ""
        term_text = f", срок {term}" if term else ""
        parts.append(f"{number}: {name}{term_text}{price_text}")
    if len(stages) > 5:
        parts.append(f"ещё {len(stages) - 5}")
    return "; ".join(parts)


def _short_report_text(value: str | None, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _check_warranty_between_ooz_and_contract(package: ProcurementPackageExtraction) -> CheckResult:
    ooz_value = getattr(package.purchase_description, "warranty_requirements_text", None) if package.purchase_description else None
    contract_value = getattr(package.contract_draft, "warranty_text", None) if package.contract_draft else None
    summary_lines = [
        f"ООЗ: {ooz_value or 'не найдено'}",
        f"Проект контракта: {contract_value or 'не найдено'}",
    ]
    if not ooz_value and not contract_value:
        status = "manual_review"
        message = "Гарантийные требования не извлечены из ООЗ и проекта контракта."
    elif not ooz_value or not contract_value:
        status = "manual_review"
        message = "Гарантийные требования найдены только в одном из документов."
    elif _text_values_match(ooz_value, contract_value):
        status = "passed"
        message = "Гарантийные требования совпадают по нормализованному тексту."
    else:
        status = "warning"
        message = "Гарантийные требования требуют сверки: формулировки отличаются."
    return _result(
        "strict.plan.warranty",
        "Гарантийные требования",
        status,
        "strict",
        message,
        documents=["purchase_description", "contract_draft"],
        fields=["purchase_description.warranty_requirements_text", "contract_draft.warranty_text"],
        details={"summary_lines": summary_lines},
    )


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


def _check_contract_penalties(package: ProcurementPackageExtraction) -> list[CheckResult]:
    contract = package.contract_draft
    if contract is None:
        return [
            _result(
                "strict.contract.penalties",
                "Штрафы, пени и неустойки",
                "manual_review",
                "strict",
                "Проект контракта отсутствует; штрафы и пени проверить невозможно.",
                fields=["contract_draft.penalty_clauses", "contract_draft.peni_clauses"],
            )
        ]

    contract_price = _money_amount(contract.price)
    if contract_price is None:
        contract_price = _money_amount(package.schedule_application.nmck if package.schedule_application else None)
    responsibility_section = getattr(contract, "responsibility_section_text", None)
    section_found = bool(normalize_text(responsibility_section))
    section_has_penalty_words = _has_penalty_words(responsibility_section)
    penalty_clauses = list(getattr(contract, "penalty_clauses", []) or [])
    peni_clauses = list(getattr(contract, "peni_clauses", []) or [])
    all_clauses = penalty_clauses + peni_clauses
    section_lines = [
        f"Глава ответственности: {'найдена' if section_found else 'не найдена'}",
        f"Слова штраф/пеня/неустойка: {'найдены' if section_has_penalty_words else 'не найдены'}",
    ]
    if not section_found and not all_clauses:
        return [
            _result(
                "strict.contract.penalties",
                "Штрафы, пени и неустойки",
                "manual_review",
                "strict",
                "Глава ответственности сторон не найдена в проекте контракта.",
                documents=["contract_draft"],
                fields=[
                    "contract_draft.responsibility_section_text",
                    "contract_draft.penalty_clauses",
                    "contract_draft.peni_clauses",
                ],
                details={"summary_lines": section_lines},
            )
        ]
    if section_found and not section_has_penalty_words:
        return [
            _result(
                "strict.contract.penalties",
                "Штрафы, пени и неустойки",
                "failed",
                "strict",
                "Глава ответственности найдена, но в ней не найдены слова штраф/пеня/неустойка.",
                documents=["contract_draft"],
                fields=[
                    "contract_draft.responsibility_section_text",
                    "contract_draft.penalty_clauses",
                    "contract_draft.peni_clauses",
                ],
                details={
                    "summary_lines": section_lines,
                    "responsibility_section_preview": _short_clause_text(responsibility_section or ""),
                },
            )
        ]
    if not all_clauses:
        return [
            _result(
                "strict.contract.penalties",
                "Штрафы, пени и неустойки",
                "manual_review",
                "strict",
                "Глава ответственности найдена и содержит штрафные формулировки, но структурированные штрафы не извлечены.",
                documents=["contract_draft"],
                fields=[
                    "contract_draft.responsibility_section_text",
                    "contract_draft.penalty_clauses",
                    "contract_draft.peni_clauses",
                ],
                details={
                    "summary_lines": section_lines,
                    "responsibility_section_preview": _short_clause_text(responsibility_section or ""),
                },
            )
        ]

    expected_supplier_percent = _supplier_value_penalty_percent(contract_price)
    expected_fixed_fine = _fixed_penalty_amount(contract_price)
    expected_smp_percent = Decimal("5") if _plan_requires_smp_sonko_subcontract(package) else None
    section_lines.extend(
        _expected_penalty_lines(
            contract_price,
            expected_supplier_percent,
            expected_fixed_fine,
            expected_smp_percent,
        )
    )

    findings: list[str] = []
    failures: list[str] = []
    manual: list[str] = []

    if expected_supplier_percent is not None:
        supplier_value = _find_penalty_clause_with_percent(
            all_clauses,
            expected_supplier_percent,
            party="supplier",
            kind="value_obligation",
        )
        if supplier_value is not None:
            findings.append(
                f"Штраф поставщика за стоимостное обязательство: {expected_supplier_percent}% - найден"
            )
        elif _section_contains_percent(responsibility_section, expected_supplier_percent):
            findings.append(
                f"Штраф поставщика за стоимостное обязательство: {expected_supplier_percent}% - найден в разделе ответственности"
            )
        elif not _find_penalty_clauses(all_clauses, party="supplier", kind="value_obligation"):
            failures.append("не найден штраф поставщика за неисполнение стоимостного обязательства")
        else:
            actual_values = _clause_percent_values(
                _find_penalty_clauses(all_clauses, party="supplier", kind="value_obligation")
            )
            manual.append(
                "штраф поставщика за стоимостное обязательство найден, но нужный порог "
                f"{expected_supplier_percent}% не выделен однозначно; найденные проценты: {_values_text(actual_values)}"
            )

    if expected_fixed_fine is not None:
        customer_fine = _find_penalty_clause_with_amount(
            all_clauses,
            expected_fixed_fine,
            party="customer",
        )
        if customer_fine is not None:
            findings.append(
                f"Штраф заказчика: {_format_money(expected_fixed_fine)} - найден"
            )
        elif _section_contains_money(responsibility_section, expected_fixed_fine):
            findings.append(
                f"Штраф заказчика: {_format_money(expected_fixed_fine)} - найден в разделе ответственности"
            )
        elif not _find_penalty_clauses(all_clauses, party="customer"):
            failures.append("не найден штраф заказчика")
        else:
            actual_values = _clause_amount_values(_find_penalty_clauses(all_clauses, party="customer"))
            manual.append(
                "штраф заказчика найден, но нужный порог "
                f"{_format_money(expected_fixed_fine)} не выделен однозначно; найденные суммы: {_values_text(actual_values)}"
            )

    if expected_fixed_fine is not None:
        supplier_non_value = _find_penalty_clause_with_amount(
            all_clauses,
            expected_fixed_fine,
            party="supplier",
            kind="non_value_obligation",
        )
        if supplier_non_value is not None:
            findings.append(
                f"Штраф поставщика за нестоимостное обязательство: {_format_money(expected_fixed_fine)} - найден"
            )
        elif _section_contains_money(responsibility_section, expected_fixed_fine):
            findings.append(
                f"Штраф поставщика за нестоимостное обязательство: {_format_money(expected_fixed_fine)} - найден в разделе ответственности"
            )
        elif not _find_penalty_clauses(all_clauses, party="supplier", kind="non_value_obligation"):
            manual.append("штраф поставщика за нестоимостное обязательство не найден отдельной строкой")
        else:
            actual_values = _clause_amount_values(
                _find_penalty_clauses(all_clauses, party="supplier", kind="non_value_obligation")
            )
            manual.append(
                "штраф поставщика за нестоимостное обязательство найден, но нужный порог "
                f"{_format_money(expected_fixed_fine)} не выделен однозначно; найденные суммы: {_values_text(actual_values)}"
            )

    peni = _find_penalty_clause(all_clauses, kind="delay_peni")
    if peni is None:
        manual.append("формула пеней за просрочку не найдена")
    else:
        findings.append("Пени за просрочку: формула найдена")

    if expected_smp_percent is not None:
        smp_clause = _find_penalty_clause_with_percent(
            all_clauses,
            expected_smp_percent,
            kind="smp_sonko_subcontract",
        )
        if smp_clause is not None:
            findings.append("Штраф за непривлечение СМП/СОНКО: 5% - найден")
        elif _section_contains_percent(responsibility_section, expected_smp_percent, markers=("смп", "сонко", "соисполн", "субподряд")):
            findings.append("Штраф за непривлечение СМП/СОНКО: 5% - найден в разделе ответственности")
        elif not _find_penalty_clauses(all_clauses, kind="smp_sonko_subcontract"):
            failures.append("в плане требуется СМП/СОНКО, но штраф за непривлечение СМП/СОНКО не найден")
        else:
            actual_values = _clause_percent_values(_find_penalty_clauses(all_clauses, kind="smp_sonko_subcontract"))
            manual.append(
                "штраф за непривлечение СМП/СОНКО найден, но 5% не выделены однозначно; "
                f"найденные проценты: {_values_text(actual_values)}"
            )

    if failures:
        status = "failed"
        message = "В штрафах/пенях проекта контракта найдены расхождения."
    elif manual:
        status = "manual_review"
        message = "Часть штрафов/пеней требует ручной проверки."
    else:
        status = "passed"
        message = "Штрафы и пени проекта контракта соответствуют базовым порогам ПП №1042."

    raw_lines = [
        _short_clause_text(clause.raw_text)
        for clause in all_clauses
        if getattr(clause, "raw_text", None)
    ]
    return [
        _result(
            "strict.contract.penalties",
            "Штрафы, пени и неустойки",
            status,
            "strict",
            message,
            documents=["contract_draft", "schedule_application"],
            fields=[
                "contract_draft.price.amount",
                "contract_draft.penalty_clauses",
                "contract_draft.peni_clauses",
                "schedule_application.subcontract_smp_sonko_required",
            ],
            details={
                "contract_price": str(contract_price) if contract_price is not None else None,
                "expected_supplier_value_percent": str(expected_supplier_percent) if expected_supplier_percent is not None else None,
                "expected_fixed_fine": str(expected_fixed_fine) if expected_fixed_fine is not None else None,
                "expected_smp_sonko_percent": str(expected_smp_percent) if expected_smp_percent is not None else None,
                "summary_lines": section_lines + findings + manual + failures,
                "failures": failures,
                "manual_review": manual,
                "clauses": raw_lines[:12],
            },
        )
    ]


def _has_penalty_words(text: str | None) -> bool:
    lowered = normalize_text(text)
    return bool(lowered and any(marker in lowered for marker in ("штраф", "пен", "неустойк")))


def _supplier_value_penalty_percent(price: Decimal | None) -> Decimal | None:
    if price is None:
        return None
    if price <= Decimal("3000000"):
        return Decimal("10")
    if price <= Decimal("50000000"):
        return Decimal("5")
    if price <= Decimal("100000000"):
        return Decimal("1")
    if price <= Decimal("500000000"):
        return Decimal("0.5")
    if price <= Decimal("1000000000"):
        return Decimal("0.4")
    if price <= Decimal("2000000000"):
        return Decimal("0.3")
    if price <= Decimal("5000000000"):
        return Decimal("0.25")
    if price <= Decimal("10000000000"):
        return Decimal("0.2")
    return Decimal("0.1")


def _fixed_penalty_amount(price: Decimal | None) -> Decimal | None:
    if price is None:
        return None
    if price <= Decimal("3000000"):
        return Decimal("1000.00")
    if price <= Decimal("50000000"):
        return Decimal("5000.00")
    if price <= Decimal("100000000"):
        return Decimal("10000.00")
    return Decimal("100000.00")


def _find_penalty_clause(
    clauses: list[Any],
    *,
    party: str | None = None,
    kind: str | None = None,
) -> Any | None:
    for clause in clauses:
        if party and _clause_party(clause) != party:
            continue
        if kind and _clause_kind(clause) != kind:
            continue
        return clause
    return None


def _expected_penalty_lines(
    contract_price: Decimal | None,
    supplier_percent: Decimal | None,
    fixed_fine: Decimal | None,
    smp_percent: Decimal | None,
) -> list[str]:
    lines = [
        "Проверяется раздел проекта контракта: Ответственность Сторон",
        f"НМЦК / цена контракта: {_format_money(contract_price)}",
    ]
    if supplier_percent is not None:
        lines.append(
            f"Ожидаемый штраф поставщика за стоимостное обязательство: {_format_decimal(supplier_percent)}%"
        )
    if fixed_fine is not None:
        lines.append(f"Ожидаемый штраф заказчика: {_format_money(fixed_fine)}")
        lines.append(
            f"Ожидаемый штраф поставщика за нестоимостное обязательство: {_format_money(fixed_fine)}"
        )
    if smp_percent is not None:
        lines.append(
            f"Ожидаемый штраф за непривлечение СМП/СОНКО: {_format_decimal(smp_percent)}%"
        )
    return lines


def _find_penalty_clauses(
    clauses: list[Any],
    *,
    party: str | None = None,
    kind: str | None = None,
) -> list[Any]:
    result = []
    for clause in clauses:
        if party and _clause_party(clause) != party:
            continue
        if kind and _clause_kind(clause) != kind:
            continue
        result.append(clause)
    return result


def _find_penalty_clause_with_percent(
    clauses: list[Any],
    expected: Decimal,
    *,
    party: str | None = None,
    kind: str | None = None,
) -> Any | None:
    for clause in _find_penalty_clauses(clauses, party=party, kind=kind):
        if any(_decimal_equal(value, expected) for value in _clause_percent_values_raw(clause)):
            return clause
    return None


def _find_penalty_clause_with_amount(
    clauses: list[Any],
    expected: Decimal,
    *,
    party: str | None = None,
    kind: str | None = None,
) -> Any | None:
    for clause in _find_penalty_clauses(clauses, party=party, kind=kind):
        if any(_decimal_equal(value, expected) for value in _clause_amount_values_raw(clause)):
            return clause
    return None


def _clause_party(clause: Any) -> str:
    party = getattr(clause, "party", None)
    if party in {"supplier", "customer"}:
        return party
    text = normalize_text(getattr(clause, "raw_text", ""))
    if "заказчик" in text:
        return "customer"
    if any(word in text for word in ("поставщик", "подрядчик", "исполнитель")):
        return "supplier"
    return "unknown"


def _clause_kind(clause: Any) -> str:
    kind = getattr(clause, "obligation_kind", None)
    if kind and kind != "unknown":
        return kind
    text = normalize_text(getattr(clause, "raw_text", ""))
    if "пени" in text or "пеня" in text:
        return "delay_peni"
    if "субподряд" in text or "соисполнител" in text or "смп" in text or "сонко" in text:
        return "smp_sonko_subcontract"
    if "нестоимост" in text or "стоимостного выражения" in text:
        return "non_value_obligation"
    if "штраф" in text:
        return "value_obligation"
    return "unknown"


def _clause_percent(clause: Any) -> Decimal | None:
    value = normalize_decimal(getattr(clause, "percent", None))
    if value is not None:
        return value
    values = _percent_values_in_text(str(getattr(clause, "raw_text", "") or ""))
    return values[0] if values else None


def _clause_amount(clause: Any) -> Decimal | None:
    value = normalize_money(getattr(clause, "amount", None))
    if value is not None:
        return value
    values = _money_values_in_text(str(getattr(clause, "raw_text", "") or ""))
    return values[-1] if values else None


def _clause_percent_values(clauses: list[Any]) -> list[str]:
    values = []
    for clause in clauses:
        for value in _clause_percent_values_raw(clause):
            values.append(_format_decimal(value) + "%")
    return sorted(set(values))


def _clause_amount_values(clauses: list[Any]) -> list[str]:
    values = []
    for clause in clauses:
        for value in _clause_amount_values_raw(clause):
            values.append(_format_money(value))
    return sorted(set(values))


def _clause_percent_values_raw(clause: Any) -> list[Decimal]:
    values = []
    explicit = normalize_decimal(getattr(clause, "percent", None))
    if explicit is not None:
        values.append(explicit)
    values.extend(_percent_values_in_text(str(getattr(clause, "raw_text", "") or "")))
    return _unique_decimals(values)


def _clause_amount_values_raw(clause: Any) -> list[Decimal]:
    values = []
    explicit = normalize_money(getattr(clause, "amount", None))
    if explicit is not None:
        values.append(explicit)
    values.extend(_money_values_in_text(str(getattr(clause, "raw_text", "") or "")))
    return _unique_decimals(values)


def _values_text(values: list[str]) -> str:
    return ", ".join(values) if values else "не найдены"


def _section_contains_percent(
    text: str | None,
    expected: Decimal,
    *,
    markers: tuple[str, ...] = (),
) -> bool:
    source = normalize_text(text)
    if not source:
        return False
    chunks = _marker_chunks(source, markers) if markers else [source]
    return any(_percent_in_text(chunk, expected) for chunk in chunks)


def _section_contains_money(text: str | None, expected: Decimal) -> bool:
    return any(_decimal_equal(value, expected) for value in _money_values_in_text(str(text or "")))


def _marker_chunks(text: str, markers: tuple[str, ...]) -> list[str]:
    if not markers:
        return [text]
    chunks = []
    for marker in markers:
        start = 0
        while True:
            index = text.find(marker, start)
            if index < 0:
                break
            chunks.append(text[max(0, index - 500) : index + 500])
            start = index + len(marker)
    return chunks


def _percent_in_text(text: str, expected: Decimal) -> bool:
    return any(_decimal_equal(value, expected) for value in _percent_values_in_text(text))


def _percent_values_in_text(text: str) -> list[Decimal]:
    values = [
        normalize_decimal(match)
        for match in re.findall(r"(\d+(?:[,.]\d+)?)\s*(?:%|процент)", text, flags=re.IGNORECASE)
    ]
    return _unique_decimals([value for value in values if value is not None])


def _money_values_in_text(text: str) -> list[Decimal]:
    values: list[Decimal] = []
    for match in re.finditer(
        r"((?:\d[\d\s\u00a0]*(?:[,.]\d{1,2})?))\s*(?:руб|российск)",
        text,
        flags=re.IGNORECASE,
    ):
        raw = match.group(1).strip()
        tokens = re.findall(r"\d+(?:[,.]\d{1,2})?", raw.replace("\u00a0", " "))
        if _looks_like_spaced_money(tokens):
            candidates = [raw]
        else:
            candidates = tokens
        for candidate in candidates:
            value = normalize_money(candidate)
            if value is not None:
                values.append(value)
    return _unique_decimals(values)


def _looks_like_spaced_money(tokens: list[str]) -> bool:
    if not tokens:
        return False
    if len(tokens) == 1:
        return True
    if len(tokens) <= 3 and all(re.fullmatch(r"\d{3}", token) for token in tokens[1:]):
        return True
    return False


def _unique_decimals(values: list[Decimal]) -> list[Decimal]:
    seen = set()
    unique = []
    for value in values:
        key = value.quantize(Decimal("0.01"))
        if key not in seen:
            seen.add(key)
            unique.append(value)
    return unique


def _decimal_equal(left: Decimal | None, right: Decimal | None) -> bool:
    if left is None or right is None:
        return False
    return left.quantize(Decimal("0.01")) == right.quantize(Decimal("0.01"))


def _plan_requires_smp_sonko_subcontract(package: ProcurementPackageExtraction) -> bool:
    schedule = package.schedule_application
    return bool(getattr(schedule, "subcontract_smp_sonko_required", None)) if schedule else False


def _short_clause_text(text: str) -> str:
    line = " ".join(str(text or "").split())
    return line[:500] + ("..." if len(line) > 500 else "")


def _check_smp_sonko_subcontract(package: ProcurementPackageExtraction) -> list[CheckResult]:
    schedule = package.schedule_application
    contract = package.contract_draft
    schedule_required = getattr(schedule, "subcontract_smp_sonko_required", None) if schedule else None
    contract_required = getattr(contract, "subcontract_smp_sonko_required", None) if contract else None
    schedule_percent = getattr(schedule, "subcontract_smp_sonko_percent", None) if schedule else None
    contract_percent = getattr(contract, "subcontract_smp_sonko_percent", None) if contract else None
    schedule_raw = getattr(schedule, "subcontract_smp_sonko_required_raw", None) if schedule else None
    contract_raw = getattr(contract, "subcontract_smp_sonko_required_raw", None) if contract else None

    if schedule is None or contract is None:
        status = "manual_review"
        message = "Заявка в план-график или проект контракта отсутствуют; условия СМП/СОНКО проверить невозможно."
    elif schedule_required is False:
        if contract_required is True:
            status = "failed"
            message = "В плане обязанность привлечения СМП/СОНКО отсутствует, но в проекте контракта она найдена."
        else:
            status = "passed"
            message = "В плане обязанность привлечения СМП/СОНКО отсутствует; в проекте контракта обязательное привлечение не найдено."
    elif schedule_required is True:
        if contract_required is not True:
            status = "failed"
            message = "В плане установлена обязанность привлечения СМП/СОНКО, но в проекте контракта она не найдена."
        elif schedule_percent is not None and contract_percent is None:
            status = "failed"
            message = "В плане указан процент привлечения СМП/СОНКО, но в проекте контракта процент не найден."
        elif (
            schedule_percent is not None
            and contract_percent is not None
            and normalize_decimal(schedule_percent) != normalize_decimal(contract_percent)
        ):
            status = "failed"
            message = "Процент привлечения СМП/СОНКО различается между планом и проектом контракта."
        else:
            status = "passed"
            message = "Условия привлечения СМП/СОНКО согласованы между планом и проектом контракта."
    elif schedule_raw:
        status = "manual_review"
        message = "В плане найдено поле про СМП/СОНКО, но значение не удалось однозначно нормализовать."
    else:
        status = "manual_review"
        message = "В плане не найдено поле об обязанности привлечения СМП/СОНКО."

    return [
        _result(
            "strict.smp_sonko_subcontract",
            "Привлечение СМП/СОНКО",
            status,
            "strict",
            message,
            documents=["schedule_application", "contract_draft"],
            fields=[
                "schedule_application.subcontract_smp_sonko_required",
                "schedule_application.subcontract_smp_sonko_percent",
                "contract_draft.subcontract_smp_sonko_required",
                "contract_draft.subcontract_smp_sonko_percent",
            ],
            details={
                "summary_lines": [
                    f"Заявка в план-график: {_smp_sonko_summary(schedule_required, schedule_percent, schedule_raw)}",
                    f"Проект контракта: {_smp_sonko_summary(contract_required, contract_percent, contract_raw)}",
                ],
                "schedule_application": schedule_raw,
                "contract_draft": contract_raw,
                "schedule_percent": schedule_percent,
                "contract_percent": contract_percent,
            },
        )
    ]


def _smp_sonko_summary(required: bool | None, percent: Decimal | None, raw: str | None) -> str:
    if required is True:
        base = "обязанность установлена"
    elif required is False:
        base = "обязанность отсутствует"
    elif raw:
        base = "значение требует проверки"
    else:
        base = "условие не найдено"
    if percent is not None:
        base += f", процент: {percent}"
    if raw:
        base += f" ({raw})"
    return base


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
