from __future__ import annotations

import json

from summary_model.domain.models import Finding


SOURCE_PRIORITY = {
    "external": 0,
    "deterministic": 1,
    "llm": 2,
}


def _finding_key(finding: Finding) -> tuple:
    evidence = tuple(
        (
            item.document_id,
            item.block_id,
            item.table_id,
            item.row,
            item.column,
        )
        for item in finding.evidence
    )
    return (
        finding.rule_id,
        tuple(sorted(finding.documents)),
        evidence,
        json.dumps(finding.actual, ensure_ascii=False, sort_keys=True, default=str),
    )


def merge_findings(*groups: list[Finding]) -> list[Finding]:
    selected: dict[tuple, Finding] = {}
    authoritative: dict[tuple, int] = {}
    for finding in sorted(
        (item for group in groups for item in group),
        key=lambda item: SOURCE_PRIORITY[item.source],
    ):
        full_key = _finding_key(finding)
        authority_key = full_key[:-1]
        priority = SOURCE_PRIORITY[finding.source]
        existing_priority = authoritative.get(authority_key)
        if existing_priority is not None and existing_priority < priority:
            continue
        authoritative.setdefault(authority_key, priority)
        selected.setdefault(full_key, finding)
    return list(selected.values())
