from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


CheckSeverity = Literal["error", "warning", "info", "manual_review"]
CheckStatus = Literal[
    "passed",
    "failed",
    "warning",
    "not_applicable",
    "manual_review",
    "skipped",
]
CheckMode = Literal["strict", "semantic", "manual_review"]


class CheckResult(BaseModel):
    check_id: str
    title: str
    severity: CheckSeverity
    status: CheckStatus
    mode: CheckMode
    documents: list[str] = Field(default_factory=list)
    fields_compared: list[str] = Field(default_factory=list)
    message: str
    report_text: str
    evidence: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class ProcurementChecksReport(BaseModel):
    package_id: str | None = None
    results: list[CheckResult] = Field(default_factory=list)
    errors_count: int = 0
    warnings_count: int = 0
    manual_review_count: int = 0
    passed_count: int = 0
    skipped_count: int = 0

    @classmethod
    def from_results(
        cls,
        *,
        package_id: str | None,
        results: list[CheckResult],
    ) -> "ProcurementChecksReport":
        return cls(
            package_id=package_id,
            results=results,
            errors_count=sum(1 for item in results if item.status == "failed"),
            warnings_count=sum(1 for item in results if item.status == "warning"),
            manual_review_count=sum(1 for item in results if item.status == "manual_review"),
            passed_count=sum(1 for item in results if item.status == "passed"),
            skipped_count=sum(1 for item in results if item.status == "skipped"),
        )
