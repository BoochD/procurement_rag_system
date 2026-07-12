from __future__ import annotations

from decimal import Decimal
from statistics import mean, stdev

from summary_model.domain.models import (
    Finding,
    FindingSeverity,
    FindingStatus,
    OnmckSummary,
)
from .normalization import normalize_decimal


def validate_onmck_prices(summary: OnmckSummary) -> list[Finding]:
    findings: list[Finding] = []
    for entry in summary.items:
        prices = [
            normalize_decimal(price.unit_price)
            for price in entry.supplier_prices
        ]
        numeric = [price for price in prices if price is not None]
        if not numeric:
            continue

        minimum = min(numeric)
        selected = normalize_decimal(entry.selected_unit_price)
        entry.minimum_unit_price = entry.minimum_unit_price or entry.selected_unit_price
        if entry.minimum_unit_price is not None:
            entry.minimum_unit_price.normalized_value = minimum

        findings.append(
            Finding(
                rule_id="price.minimum_selected",
                severity=FindingSeverity.ERROR if selected is not None and selected != minimum else FindingSeverity.INFO,
                status=FindingStatus.FAILED if selected is not None and selected != minimum else FindingStatus.PASSED,
                title="Минимальная цена позиции",
                message=(
                    f"Выбрана цена {selected}, минимальная цена {minimum}."
                    if selected is not None and selected != minimum
                    else f"Минимальная цена позиции: {minimum}."
                ),
                documents=[summary.document_id],
                expected=minimum,
                actual=selected,
                source="deterministic",
            )
        )

        if len(numeric) >= 2 and mean(numeric) != 0:
            coefficient = Decimal(str(round(float(stdev(numeric) / mean(numeric) * 100), 2)))
            entry.variation_coefficient = coefficient
            findings.append(
                Finding(
                    rule_id="price.variation",
                    severity=FindingSeverity.WARNING if coefficient >= 33 else FindingSeverity.INFO,
                    status=FindingStatus.FAILED if coefficient >= 33 else FindingStatus.PASSED,
                    title="Коэффициент вариации",
                    message=f"Коэффициент вариации: {coefficient}%.",
                    documents=[summary.document_id],
                    expected="< 33%",
                    actual=f"{coefficient}%",
                    source="deterministic",
                )
            )
    return findings

