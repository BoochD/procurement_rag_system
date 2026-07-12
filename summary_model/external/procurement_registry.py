from __future__ import annotations

import re
from pathlib import Path

from services.procurement_reference_registry import ProcurementReferenceRegistry
from summary_model.domain.models import (
    Finding,
    FindingSeverity,
    FindingStatus,
    ProcurementPackage,
)
from summary_model.validation.normalization import normalize_code, normalize_text


TAG_RE = re.compile(r"</?(?:b|u|ins|ok|warn|error)>", re.I)
NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?")
LOOKALIKE_LATIN_TO_CYRILLIC = str.maketrans(
    {
        "A": "А", "a": "а", "B": "В", "b": "в", "C": "С", "c": "с",
        "E": "Е", "e": "е", "H": "Н", "h": "н", "K": "К", "k": "к",
        "M": "М", "m": "м", "O": "О", "o": "о", "P": "Р", "p": "р",
        "T": "Т", "t": "т", "X": "Х", "x": "х", "Y": "У", "y": "у",
    }
)


def _plain_message(value: str) -> str:
    return TAG_RE.sub("", value or "").strip()


def _characteristic_values(value) -> list[str]:
    raw = value.normalized_value if value is not None else None
    if raw is None:
        return []
    return [part.strip() for part in re.split(r"[;\n\r]+", str(raw)) if part.strip()]


def _extracted_text(value) -> str:
    if value is None:
        return ""
    raw = value.normalized_value if value.normalized_value is not None else value.raw_value
    return normalize_text(raw)


def _extra_characteristics_policy(package: ProcurementPackage) -> bool | None:
    labels = []
    for document in (package.plan, package.request, package.explanatory_note):
        if document is None:
            continue
        labels.extend(
            (
                _extracted_text(getattr(document, "procurement_method", None)),
                _extracted_text(getattr(document, "single_supplier_basis", None)),
            )
        )
    combined = " ".join(label for label in labels if label)
    if "часть 12 статьи 93" in combined or "ч. 12 ст. 93" in combined:
        return False
    if "единствен" in combined:
        return True
    return None


def _value_allowed(actual: str, allowed: list[str]) -> bool:
    normalized_actual = normalize_text(actual)
    if not normalized_actual:
        return False
    aliased_actual = normalized_actual.translate(LOOKALIKE_LATIN_TO_CYRILLIC)
    actual_numbers = NUMBER_RE.findall(normalized_actual)
    actual_number = (
        float(actual_numbers[0].replace(",", "."))
        if len(actual_numbers) == 1
        else None
    )
    for candidate in allowed:
        raw_candidate = " ".join(
            str(candidate).replace("\xa0", " ").lower().split()
        )
        normalized_candidate = normalize_text(candidate)
        if normalized_candidate == normalized_actual:
            return True
        if normalized_candidate.translate(LOOKALIKE_LATIN_TO_CYRILLIC) == aliased_actual:
            return True
        candidate_range = raw_candidate.replace("≤", "<=").replace("≥", ">=")
        comparisons = re.findall(
            r"(<=|>=|<|>)\s*(-?\d+(?:[.,]\d+)?)",
            candidate_range,
        )
        if actual_number is not None and comparisons:
            if all(
                {
                    "<": actual_number < float(bound.replace(",", ".")),
                    "<=": actual_number <= float(bound.replace(",", ".")),
                    ">": actual_number > float(bound.replace(",", ".")),
                    ">=": actual_number >= float(bound.replace(",", ".")),
                }[operator]
                for operator, bound in comparisons
            ):
                return True
        candidate_numbers = NUMBER_RE.findall(normalized_candidate)
        if (
            actual_number is not None
            and len(candidate_numbers) == 1
            and actual_number == float(candidate_numbers[0].replace(",", "."))
        ):
            return True
    return False


class ProcurementRegistryAdapter:
    def __init__(
        self,
        registry_dir: str | Path = "data/parsed_tables",
        *,
        live_ktru: bool = True,
        registry=None,
    ) -> None:
        self.registry = registry or ProcurementReferenceRegistry(Path(registry_dir))
        self.live_ktru = live_ktru
        self._legal_characteristics: dict[str, dict] = {}

    def validate(self, package: ProcurementPackage) -> list[Finding]:
        findings: list[Finding] = []
        documents = [
            document for document in (package.plan, package.ooz, package.contract)
            if document
        ]
        seen_okpd: set[str] = set()
        seen_ktru: set[str] = set()
        valid_ktru: dict[str, bool] = {}
        allow_extra_characteristics = _extra_characteristics_policy(package)

        for document in documents:
            for item in document.items:
                item_name = normalize_text(item.name)
                for value in item.okpd2:
                    code = normalize_code(value)
                    if not code or code in seen_okpd:
                        continue
                    seen_okpd.add(code)
                    try:
                        result = self.registry.check_okpd2(code, item_name or None)
                        findings.append(
                            Finding(
                                rule_id=f"registry.okpd2.{code}",
                                severity=FindingSeverity.WARNING if result.found else FindingSeverity.INFO,
                                status=FindingStatus.PASSED,
                                title=f"Проверка ОКПД2 {code}",
                                message=_plain_message(result.message),
                                documents=[document.document_id],
                                actual=code,
                                evidence=value.evidence,
                                source="external",
                            )
                        )
                    except Exception as error:
                        findings.append(
                            Finding(
                                rule_id=f"registry.okpd2.{code}",
                                severity=FindingSeverity.MANUAL_REVIEW,
                                status=FindingStatus.UNCERTAIN,
                                title=f"Проверка ОКПД2 {code}",
                                message=f"Registry check failed: {error}",
                                documents=[document.document_id],
                                actual=code,
                                evidence=value.evidence,
                                source="external",
                            )
                        )

                for value in item.ktru:
                    code = normalize_code(value)
                    if not code:
                        continue
                    if not self.live_ktru:
                        if code not in seen_ktru:
                            seen_ktru.add(code)
                            findings.append(
                                Finding(
                                    rule_id=f"registry.ktru.{code}",
                                    severity=FindingSeverity.INFO,
                                    status=FindingStatus.SKIPPED,
                                    title=f"Проверка КТРУ {code}",
                                    message="Live KTRU checks are disabled.",
                                    documents=[document.document_id],
                                    evidence=value.evidence,
                                    source="external",
                                )
                            )
                        continue
                    if code not in seen_ktru:
                        seen_ktru.add(code)
                        try:
                            result = self.registry.check_ktru(code, item_name or None)
                            valid_ktru[code] = bool(result.found)
                            unavailable = (
                                not result.found
                                and "не удалось получить карточку"
                                in _plain_message(result.message).casefold()
                            )
                            message = (
                                (
                                    "Внешний сервис zakupki.gov.ru недоступен: "
                                    f"карточка КТРУ {code} не получена. "
                                    f"Причина: {_plain_message(result.message)}. "
                                    "Это не является ошибкой документа."
                                )
                                if unavailable
                                else _plain_message(result.message)
                            )
                            findings.append(
                                Finding(
                                    rule_id=f"registry.ktru.{code}",
                                    severity=(
                                        FindingSeverity.INFO
                                        if result.found
                                        else FindingSeverity.MANUAL_REVIEW
                                        if unavailable
                                        else FindingSeverity.WARNING
                                    ),
                                    status=(
                                        FindingStatus.PASSED
                                        if result.found
                                        else FindingStatus.UNCERTAIN
                                        if unavailable
                                        else FindingStatus.FAILED
                                    ),
                                    title=f"Проверка КТРУ {code}",
                                    message=message,
                                    documents=[document.document_id],
                                    actual=code,
                                    evidence=value.evidence,
                                    source="external",
                                )
                            )
                        except Exception as error:
                            valid_ktru[code] = False
                            findings.append(
                                Finding(
                                    rule_id=f"registry.ktru.{code}",
                                    severity=FindingSeverity.MANUAL_REVIEW,
                                    status=FindingStatus.UNCERTAIN,
                                    title=f"Проверка КТРУ {code}",
                                    message=f"Live KTRU check failed: {error}",
                                    documents=[document.document_id],
                                    evidence=value.evidence,
                                    source="external",
                                )
                            )
                    if (
                        document is package.ooz
                        and valid_ktru.get(code)
                        and item.characteristics
                    ):
                        findings.extend(
                            self._validate_characteristics(
                                document.document_id,
                                code,
                                item,
                                allow_extra_characteristics=allow_extra_characteristics,
                            )
                        )
        return findings

    def _validate_characteristics(
        self,
        document_id: str,
        code: str,
        item,
        *,
        allow_extra_characteristics: bool | None,
    ) -> list[Finding]:
        if code not in self._legal_characteristics:
            self._legal_characteristics[code] = (
                self.registry.get_ktru_characteristics_detailed(code)
            )
        legal = self._legal_characteristics[code]
        lookup = {normalize_text(name): (name, payload) for name, payload in legal.items()}
        present: set[str] = set()
        findings: list[Finding] = []

        for characteristic in item.characteristics:
            normalized_name = normalize_text(characteristic.name)
            present.add(normalized_name)
            legal_entry = lookup.get(normalized_name)
            if legal_entry is None:
                if allow_extra_characteristics is True:
                    continue
                findings.append(
                    Finding(
                        rule_id=f"registry.ktru.{code}.characteristic.extra",
                        severity=(
                            FindingSeverity.ERROR
                            if allow_extra_characteristics is False
                            else FindingSeverity.MANUAL_REVIEW
                        ),
                        status=(
                            FindingStatus.FAILED
                            if allow_extra_characteristics is False
                            else FindingStatus.UNCERTAIN
                        ),
                        title=f"Дополнительная характеристика КТРУ {code}",
                        message=(
                            (
                                f"Характеристика {characteristic.name.raw_value!r} отсутствует "
                                "в карточке КТРУ и не допускается для выбранного способа закупки."
                            )
                            if allow_extra_characteristics is False
                            else (
                                f"Характеристика {characteristic.name.raw_value!r} отсутствует "
                                "в карточке КТРУ; способ закупки не позволяет автоматически "
                                "определить допустимость."
                            )
                        ),
                        documents=[document_id],
                        actual=characteristic.name.normalized_value,
                        evidence=characteristic.name.evidence,
                        source="external",
                    )
                )
                continue

            legal_name, payload = legal_entry
            allowed = list(payload.get("values") or [])
            actual_values = _characteristic_values(characteristic.value)
            invalid = [value for value in actual_values if not _value_allowed(value, allowed)]
            findings.append(
                Finding(
                    rule_id=f"registry.ktru.{code}.characteristic.{normalized_name}",
                    severity=FindingSeverity.ERROR if invalid else FindingSeverity.INFO,
                    status=FindingStatus.FAILED if invalid else FindingStatus.PASSED,
                    title=f"Характеристика КТРУ: {legal_name}",
                    message=(
                        f"Недопустимые значения: {', '.join(invalid)}."
                        if invalid
                        else "Значения соответствуют карточке КТРУ."
                    ),
                    documents=[document_id],
                    expected=allowed,
                    actual=actual_values,
                    evidence=characteristic.value.evidence,
                    source="external",
                )
            )

        for normalized_name, (legal_name, payload) in lookup.items():
            if payload.get("required") and normalized_name not in present:
                findings.append(
                    Finding(
                        rule_id=f"registry.ktru.{code}.required.{normalized_name}",
                        severity=FindingSeverity.ERROR,
                        status=FindingStatus.FAILED,
                        title=f"Обязательная характеристика КТРУ: {legal_name}",
                        message="Обязательная характеристика отсутствует в ООЗ.",
                        documents=[document_id],
                        expected=legal_name,
                        source="external",
                    )
                )
        return findings
