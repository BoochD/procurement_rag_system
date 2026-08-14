from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shared_modules.llm_models import OPENAI_VLM_MODEL, get_chatGPT_client
from summary_model.domain.models import DocumentIR, DocumentType, TableIR
from summary_model.extraction.structured_recovery import (
    StructuredRecovery,
    parse_json_object,
    recover_model,
)
from summary_model.tables.models import ParsedTable
from summary_model.tables.table_compactor import build_compact_markdown
from summary_model.tables.utils import clean_text
from summary_model.vlm_lab.candidates import (
    justification_candidate_reasons,
    rank_table_candidates,
    table_complexity_score,
    table_role,
)
from summary_model.vlm_lab.models import VlmTableExtraction, VlmTableRole
from summary_model.vlm_lab.prompts import VLM_TABLE_PROMPT_VERSION, vlm_table_prompt, vlm_user_context
from summary_model.vlm_lab.table_image import render_table_image


@dataclass
class VlmFallbackOptions:
    enabled: bool = False
    output_dir: Path | None = None
    model: str = OPENAI_VLM_MODEL
    max_tables_per_document: int = 4
    min_complexity_score: int = 35
    max_width: int = 2600
    font_size: int = 18
    force_roles: set[VlmTableRole] = field(default_factory=set)


@dataclass
class VlmFallbackRepairer:
    options: VlmFallbackOptions
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=lambda: {
        "enabled": False,
        "calls": 0,
        "errors": [],
        "tables_considered": 0,
        "tables_repaired": 0,
        "recovered_calls": 0,
        "partial_calls": 0,
        "recovery_warnings": [],
        "duration_seconds": 0.0,
        "usage": [],
    })
    _cache: dict[tuple[str, str], ParsedTable] = field(default_factory=dict)
    _failed_cache: set[tuple[str, str]] = field(default_factory=set)

    def repair_document_tables(
        self,
        ir: DocumentIR,
        document_type: DocumentType,
        tables: list[ParsedTable],
    ) -> list[ParsedTable]:
        if not self.options.enabled:
            return tables
        started = time.perf_counter()
        self.metrics["enabled"] = True
        by_id = {table.table_id: table for table in tables}
        source_by_id = _source_tables(ir)
        supports_justifications = document_type == DocumentType.OOZ
        ranked = [
            candidate
            for candidate in rank_table_candidates(tables)
            if _role_allowed_for_document(document_type, candidate.role)
        ]
        forced = []
        dual_base = []
        for candidate in (ranked if supports_justifications else []):
            reasons = justification_candidate_reasons(
                by_id[candidate.table_id],
                source_by_id.get(candidate.table_id),
            )
            if not reasons:
                continue
            if candidate.role == "purchase_description":
                dual_base.append(candidate)
            forced.append(
                candidate.model_copy(
                    update={
                        "role": "additional_characteristics_justification",
                        "confidence": max(candidate.confidence, 0.95),
                        "reasons": list(dict.fromkeys([*candidate.reasons, *reasons])),
                    }
                )
            )
        regular = [
            candidate
            for candidate in ranked
            if _should_send_to_vlm(
                by_id[candidate.table_id],
                candidate.role,
                min_complexity_score=self.options.min_complexity_score,
                force=candidate.role in self.options.force_roles,
            )
        ][: self.options.max_tables_per_document]
        candidates = _unique_candidates([*dual_base, *regular, *forced])
        self.metrics["tables_considered"] = int(self.metrics["tables_considered"]) + len(candidates)
        if not candidates:
            self.metrics["duration_seconds"] = round(float(self.metrics["duration_seconds"]) + time.perf_counter() - started, 3)
            return tables

        repaired_by_id: dict[str, ParsedTable] = {}
        for candidate in candidates:
            original = by_id[candidate.table_id]
            table = repaired_by_id.get(original.table_id, original)
            cache_key = (original.table_id, candidate.role)
            if cache_key in self._cache:
                repaired_by_id[original.table_id] = _merge_role_result(
                    table, self._cache[cache_key], candidate.role
                )
                continue
            if cache_key in self._failed_cache:
                continue
            source = source_by_id.get(original.table_id)
            if source is None:
                self._warn(f"{ir.file_name}: исходная таблица {table.table_id} не найдена для VLM.")
                continue
            known_items = _known_items_payload(
                [repaired_by_id.get(item.table_id, item) for item in tables]
            )
            repaired = self._repair_one(
                ir, document_type, original, source, candidate.role, known_items
            )
            if repaired is not None:
                self._cache[cache_key] = repaired
                repaired_by_id[original.table_id] = _merge_role_result(
                    table, repaired, candidate.role
                )
            elif candidate.role == "additional_characteristics_justification":
                fallback = _unextracted_justification_table(original)
                self._cache[cache_key] = fallback
                repaired_by_id[original.table_id] = _merge_role_result(
                    table, fallback, candidate.role
                )

        self.metrics["duration_seconds"] = round(float(self.metrics["duration_seconds"]) + time.perf_counter() - started, 3)
        return [repaired_by_id.get(table.table_id, table) for table in tables]

    def _repair_one(
        self,
        ir: DocumentIR,
        document_type: DocumentType,
        table: ParsedTable,
        source: TableIR,
        role: VlmTableRole,
        known_items: list[dict[str, Any]],
    ) -> ParsedTable | None:
        output_dir = self._table_output_dir(ir, table, role)
        image_info = render_table_image(
            source,
            output_dir / f"table_{table.table_index}.png",
            max_width=self.options.max_width,
            font_size=self.options.font_size,
        )
        payload = _payload(
            ir, document_type, table, source, image_info, role, known_items
        )
        prompt = vlm_table_prompt(role)
        _write_json(output_dir / "payload.json", payload)
        (output_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        _write_json(output_dir / "schema.json", VlmTableExtraction.model_json_schema())

        try:
            response = _call_vlm(
                image_path=Path(image_info["path"]),
                role=role,
                prompt=prompt,
                payload=payload,
                model=self.options.model,
            )
            self.metrics["calls"] = int(self.metrics["calls"]) + 1
            _write_json(output_dir / "vlm_raw.json", response)
            usage = response.get("usage")
            if usage:
                self.metrics["usage"].append(
                    {
                        "file_name": ir.file_name,
                        "table_index": table.table_index,
                        "role": role,
                        "usage": usage,
                    }
                )
            extraction, recovery = _parse_response(
                response,
                role=role,
                table_title=table.title,
            )
            if recovery.status == "recovered":
                self.metrics["recovered_calls"] = int(self.metrics["recovered_calls"]) + 1
            elif recovery.status == "partial":
                self.metrics["partial_calls"] = int(self.metrics["partial_calls"]) + 1
            if recovery.all_warnings:
                self.metrics["recovery_warnings"].append(
                    {
                        "file_name": ir.file_name,
                        "table_index": table.table_index,
                        "warnings": recovery.all_warnings,
                        "lossy_warnings": recovery.lossy_warnings,
                    }
                )
            for warning in recovery.lossy_warnings:
                self._warn(
                    f"{ir.file_name}, table {table.table_index}: "
                    f"ответ VLM восстановлен частично: {warning}"
                )
            _write_json(output_dir / "vlm_result.json", extraction.model_dump(mode="json", exclude_none=True))
        except Exception as error:
            message = f"{ir.file_name}, table {table.table_index}: VLM fallback failed: {error}"
            self._warn(message)
            self.metrics["errors"].append(message)
            self._failed_cache.add((table.table_id, role))
            return None

        compact_json = _compact_json_from_vlm(extraction, document_type)
        if _is_single_price_service_stage_table(table, source):
            _normalize_single_price_service_rows(compact_json)
        if (
            extraction.table_role == "additional_characteristics_justification"
            and recovery.lossy_warnings
        ):
            compact_json["justification_extraction"] = {
                "status": "partial",
                "warnings": list(dict.fromkeys([
                    *(compact_json.get("justification_extraction", {}).get("warnings") or []),
                    *recovery.lossy_warnings,
                ])),
            }
        if not _has_useful_result(extraction, compact_json):
            message = _empty_result_warning(ir.file_name, table.table_index, extraction)
            self._warn(message)
            self._failed_cache.add((table.table_id, role))
            return None
        repaired = table.model_copy(deep=True)
        repaired.table_type = _table_type_from_role(extraction.table_role, document_type, compact_json)
        repaired.compact_json = compact_json
        repaired.parser_warnings = list(dict.fromkeys([
            *repaired.parser_warnings,
            *extraction.warnings,
            "Table compact_json repaired by VLM fallback.",
        ]))
        repaired.compact_markdown = build_compact_markdown(repaired)
        self.metrics["tables_repaired"] = int(self.metrics["tables_repaired"]) + 1
        return repaired

    def _table_output_dir(
        self, ir: DocumentIR, table: ParsedTable, role: VlmTableRole
    ) -> Path:
        root = self.options.output_dir or Path("runtime/vlm_fallback")
        safe_file = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in ir.file_name)[:120]
        path = root / safe_file / f"table_{table.table_index}_{role}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _warn(self, message: str) -> None:
        self.warnings.append(message)


def _should_send_to_vlm(
    table: ParsedTable,
    role: VlmTableRole,
    *,
    min_complexity_score: int,
    force: bool = False,
) -> bool:
    if table.table_type in {"signature_table", "ignored_table"}:
        return False
    if role == "attachments":
        return False
    if role in {"generic", "unknown"}:
        return False
    if role == "nmck_calculation" and table.table_type == "nmck_staged_calculation_table":
        return True
    if force:
        return True
    if table.parser_warnings:
        return True
    if table.table_type in {"generic_table", "unknown"}:
        return True
    if _role_payload_empty(table, role):
        return True
    compact = table.compact_json or {}
    if compact.get("fallback_rows"):
        return True
    return table_complexity_score(table) >= min_complexity_score and _has_suspicious_compact_json(table, role)


def _role_allowed_for_document(
    document_type: DocumentType,
    role: VlmTableRole,
) -> bool:
    allowed_roles = {
        DocumentType.ONMCK: {"nmck_calculation"},
        DocumentType.OOZ: {
            "purchase_description",
            "contract_stages",
            "additional_characteristics_justification",
        },
        DocumentType.CONTRACT: {
            "contract_stages",
            "contract_specification",
            "attachments",
        },
        DocumentType.REQUEST: {"attachments"},
        DocumentType.EXPLANATORY_NOTE: {"attachments"},
    }
    return role in allowed_roles.get(document_type, set())


def _role_payload_empty(table: ParsedTable, role: VlmTableRole) -> bool:
    compact = table.compact_json or {}
    if role in {"purchase_description", "contract_specification"}:
        return not compact.get("items")
    if role == "contract_stages":
        return not compact.get("stages")
    if role == "nmck_calculation":
        return not compact.get("items") or not compact.get("price_sources")
    if role == "additional_characteristics_justification":
        return not compact.get("additional_characteristics_justifications")
    if role == "attachments":
        return not compact.get("attachments")
    return False


def _has_suspicious_compact_json(table: ParsedTable, role: VlmTableRole) -> bool:
    compact = table.compact_json or {}
    if role == "contract_stages":
        return bool(compact.get("fallback_rows"))
    if role == "purchase_description":
        return any(
            len(clean_text(item.get("name"))) > 600 or item.get("parser_warnings")
            for item in compact.get("items", [])
            if isinstance(item, dict)
        )
    if role == "nmck_calculation":
        return any(
            not item.get("supplier_prices") or not item.get("selected_min_unit_price_raw")
            for item in compact.get("items", [])
            if isinstance(item, dict)
        )
    return False


def _source_tables(ir: DocumentIR) -> dict[str, TableIR]:
    return {
        block.table.table_id: block.table
        for block in ir.blocks
        if block.table is not None
    }


def _payload(
    ir: DocumentIR,
    document_type: DocumentType,
    table: ParsedTable,
    source: TableIR,
    image_info: dict[str, int | str],
    role: VlmTableRole,
    known_items: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = {
        "schema_version": "vlm-fallback-payload-0.1.0",
        "prompt_version": VLM_TABLE_PROMPT_VERSION,
        "document": {
            "document_id": ir.document_id,
            "file_name": ir.file_name,
            "document_type": document_type.value,
        },
        "table": {
            "table_id": table.table_id,
            "block_id": table.block_id,
            "table_index": table.table_index,
            "table_type": table.table_type,
            "target_role": role,
            "title": table.title,
            "row_count": table.row_count,
            "col_count": table.col_count,
            "header_rows": table.header_rows,
            "headers": source.header_labels(),
            "context_before": source.context_before[-4:],
            "context_after": source.context_after[:2],
            "parser_warnings": table.parser_warnings,
            "compact_json": table.compact_json,
        },
        "image": image_info,
    }
    if role == "additional_characteristics_justification":
        payload["known_items"] = known_items
    return payload


def _call_vlm(
    *,
    image_path: Path,
    role: VlmTableRole,
    prompt: str,
    payload: dict[str, Any],
    model: str,
) -> dict[str, Any]:
    client = get_chatGPT_client()
    image_data = base64.b64encode(image_path.read_bytes()).decode("ascii")
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": vlm_user_context()},
                    {"type": "text", "text": json.dumps(payload, ensure_ascii=False, default=str)},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_data}"}},
                ],
            },
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "vlm_table_extraction",
                "schema": VlmTableExtraction.model_json_schema(),
                "strict": True,
            },
        },
    )
    return response.model_dump(mode="json")


def _parse_response(
    response: dict[str, Any],
    *,
    role: VlmTableRole,
    table_title: str | None,
) -> tuple[VlmTableExtraction, StructuredRecovery]:
    content = response["choices"][0]["message"]["content"]
    data = parse_json_object(content)
    if data is None:
        raise ValueError("ответ VLM не содержит корректный JSON-объект")
    if isinstance(data, dict):
        data["table_role"] = role
        if table_title:
            data.setdefault("table_title", table_title)
        if role == "nmck_calculation":
            _normalize_nmck_response(data)
    recovery = recover_model(VlmTableExtraction, data)
    if not isinstance(recovery.value, VlmTableExtraction):
        raise ValueError(
            "Ответ VLM не удалось привести к VlmTableExtraction: "
            f"{recovery.error or 'неизвестная ошибка валидации'}"
        )
    return recovery.value, recovery


def _normalize_nmck_response(data: dict[str, Any]) -> None:
    """Move legacy NMCK summary objects out of the generic totals list."""
    normalized_totals: list[dict[str, Any]] = []
    existing = data.get("nmck_totals")
    if isinstance(existing, dict):
        normalized_totals.append(existing)
    elif isinstance(existing, list):
        normalized_totals.extend(item for item in existing if isinstance(item, dict))

    legacy_totals = data.get("totals")
    legacy_text: list[str] = []
    if isinstance(legacy_totals, dict):
        legacy_totals = [legacy_totals]
    elif legacy_totals is None:
        legacy_totals = []
    elif not isinstance(legacy_totals, list):
        legacy_totals = [legacy_totals]
    for value in legacy_totals:
        if isinstance(value, dict) and any(
            key in value
            for key in ("quantity_raw", "supplier_totals_raw", "nmck_total_raw")
        ):
            normalized_totals.append(value)
        elif value is not None:
            legacy_text.append(str(value))

    data["nmck_totals"] = normalized_totals
    data["totals"] = legacy_text


def _compact_json_from_vlm(
    extraction: VlmTableExtraction,
    document_type: DocumentType,
) -> dict[str, Any]:
    role = extraction.table_role
    if role == "purchase_description":
        return {"items": [_purchase_item_payload(item) for item in extraction.items]}
    if role == "contract_stages":
        return {
            "stages": [_stage_payload(stage) for stage in extraction.stages],
            "fallback_rows": [{"raw_text": row} for row in extraction.unparsed_rows],
            "totals": [{"raw_text": total} for total in extraction.totals],
        }
    if role == "nmck_calculation":
        items = [_nmck_item_payload(item) for item in extraction.nmck_items]
        source_ids = sorted({
            price["source_id"]
            for item in items
            for price in item.get("supplier_prices", [])
            if price.get("source_id")
        })
        return {
            "price_sources": [
                {"source_id": source_id, "raw_header": _source_label(source_id)}
                for source_id in source_ids
            ],
            "items": items,
            "stages": [_stage_payload(stage) for stage in extraction.stages],
            "nmck_totals": [
                {
                    "label": total.label,
                    "unit": total.unit,
                    "quantity_raw": total.quantity_raw,
                    "supplier_totals_raw": total.supplier_totals_raw,
                    "nmck_total_raw": total.nmck_total_raw,
                    "notes": total.notes,
                }
                for total in extraction.nmck_totals
            ],
            "totals": [{"raw_text": total} for total in extraction.totals],
        }
    if role == "contract_specification":
        return {
            "items": [_specification_item_payload(item) for item in extraction.items],
            "totals": [{"raw_text": total} for total in extraction.totals],
        }
    if role == "additional_characteristics_justification":
        return {
            "additional_characteristics_justifications": [
                {
                    "item_name": item.item_name,
                    "item_row_number": item.item_row_number,
                    "item_okpd2_code": item.item_okpd2_code,
                    "item_ktru_code": item.item_ktru_code,
                    "scope_text": item.scope_text,
                    "characteristic_names": item.characteristic_names,
                    "justification_text": item.justification_text,
                    "evidence_text": item.evidence_text,
                    "parser_warnings": item.warnings,
                }
                for item in extraction.justifications
                if clean_text(item.justification_text) or clean_text(item.evidence_text)
            ],
            "justification_extraction": {
                "status": "complete",
                "warnings": extraction.warnings,
            },
        }
    if role == "attachments":
        return {
            "attachments": [
                {"row_index": index, "title_raw": title}
                for index, title in enumerate(extraction.attachments, start=1)
                if clean_text(title)
            ]
        }
    return {"rows": [{"raw_text": row} for row in extraction.unparsed_rows]}


def _is_single_price_service_stage_table(table: ParsedTable, source: TableIR) -> bool:
    if table.table_type != "nmck_staged_calculation_table":
        return False
    headers = " ".join(source.header_labels()).casefold()
    return "цена услуги" in headers and "цена за ед" not in headers and "стоимость" not in headers


def _normalize_single_price_service_rows(compact_json: dict[str, Any]) -> None:
    """A single service-price cell is both unit and row total for quantity one."""
    for item in compact_json.get("items", []):
        if not isinstance(item, dict) or not _quantity_is_one(item.get("quantity_raw")):
            continue
        for price in item.get("supplier_prices", []):
            if not isinstance(price, dict):
                continue
            unit_price = clean_text(price.get("raw_unit_price"))
            if unit_price:
                price["raw_row_total"] = unit_price


def _quantity_is_one(value: object) -> bool:
    return clean_text(value).replace(" ", "").replace(",", ".") in {"1", "1.0", "1.00"}


def _empty_result_warning(
    file_name: str,
    table_index: int,
    extraction: VlmTableExtraction,
) -> str:
    if extraction.table_role == "contract_specification":
        return (
            f"{file_name}, table {table_index}: спецификация распознана как пустая "
            "или шаблонная; заполненные позиции спецификации не найдены."
        )
    if extraction.table_role == "additional_characteristics_justification":
        return (
            f"{file_name}, table {table_index}: таблица обоснований найдена, "
            "но явные обоснования из неё не извлечены."
        )
    return f"{file_name}, table {table_index}: VLM не вернула полезные структурированные строки."


def _purchase_item_payload(item) -> dict[str, Any]:
    return {
        "row_index": item.row_index,
        "row_number": item.row_number,
        "name": item.name,
        "okpd2_code": item.okpd2_code,
        "ktru_code": item.ktru_code,
        "trademark": item.trademark,
        "trademark_justification_text": item.trademark_justification_text,
        "unit": item.unit,
        "quantity_raw": item.quantity_raw,
        "characteristics": [
            {
                "row_index": characteristic.row_index,
                "name": characteristic.name,
                "value": characteristic.value,
                "unit": characteristic.unit,
                "is_additional": characteristic.is_additional,
                "source_note": characteristic.source_note,
            }
            for characteristic in item.characteristics
        ],
        "notes": item.notes,
        "parser_warnings": ["Extracted by VLM fallback."],
    }


def _stage_payload(stage) -> dict[str, Any]:
    return {
        "row_index": stage.row_index,
        "stage_number": stage.stage_number,
        "stage_name": stage.stage_name,
        "result_text": stage.result_text,
        "service_term_text": stage.service_term_text,
        "execution_end_text": stage.execution_end_text,
        "price_raw": stage.price_raw,
        "quantity_text": stage.quantity_text,
        "warnings": stage.notes,
    }


def _nmck_item_payload(item) -> dict[str, Any]:
    return {
        "row_index": item.row_index,
        "row_number": item.row_number,
        "parent_stage_number": item.parent_stage_number,
        "name": item.name,
        "unit": item.unit,
        "quantity_raw": item.quantity_raw,
        "supplier_prices": [
            {
                "source_id": _supplier_source_id(price.supplier_label, index),
                "raw_unit_price": price.unit_price_raw,
                "raw_row_total": price.row_total_raw,
            }
            for index, price in enumerate(item.supplier_prices, start=1)
        ],
        "selected_min_unit_price_raw": item.selected_min_unit_price_raw,
        "row_total_declared_raw": item.row_total_declared_raw,
        "raw_text": " ".join(part for part in (item.row_number, item.name) if part),
    }


def _specification_item_payload(item) -> dict[str, Any]:
    return {
        "row_index": item.row_index,
        "row_number": item.row_number,
        "name": item.name,
        "description": item.description,
        "unit": item.unit,
        "quantity_raw": item.quantity_raw,
        "raw_unit_price_without_vat": item.unit_price_without_vat_raw,
        "raw_unit_price_with_vat": item.unit_price_with_vat_raw,
        "raw_total_without_vat": item.total_without_vat_raw,
        "vat_rate": item.vat_rate,
        "raw_vat_amount": item.vat_amount_raw,
        "raw_total_price": item.total_price_raw,
        "notes": item.notes,
        "parser_warnings": ["Extracted by VLM fallback."],
    }


def _supplier_source_id(label: str | None, index: int) -> str:
    text = clean_text(label).casefold()
    match = re.search(r"(?:поставщик|исполнитель)\s*(\d+)", text)
    if match:
        return f"supplier_{match.group(1)}"
    return f"supplier_{index}"


def _source_label(source_id: str) -> str:
    if source_id.startswith("supplier_"):
        suffix = source_id.rsplit("_", 1)[1]
        if suffix.isdigit():
            return f"Поставщик{suffix}"
    return source_id


def _table_type_from_role(
    role: VlmTableRole,
    document_type: DocumentType,
    compact_json: dict[str, Any],
) -> str:
    if role == "purchase_description":
        return "ooz_items_table"
    if role == "contract_stages":
        return "contract_stages_table"
    if role == "nmck_calculation":
        if compact_json.get("stages") or any(
            item.get("parent_stage_number") for item in compact_json.get("items", [])
        ):
            return "nmck_staged_calculation_table"
        return "nmck_calculation_table"
    if role == "contract_specification":
        return "contract_specification_table"
    if role == "additional_characteristics_justification":
        return "additional_characteristics_justification_table"
    if role == "attachments":
        return "contract_attachments_table" if document_type == DocumentType.CONTRACT else "request_attachments_table"
    return "generic_table"


def _has_useful_result(
    extraction: VlmTableExtraction,
    compact_json: dict[str, Any],
) -> bool:
    role = extraction.table_role
    if role in {"purchase_description", "contract_specification"}:
        return bool(compact_json.get("items"))
    if role == "contract_stages":
        return bool(compact_json.get("stages"))
    if role == "nmck_calculation":
        return bool(compact_json.get("items"))
    if role == "additional_characteristics_justification":
        return bool(compact_json.get("additional_characteristics_justifications"))
    if role == "attachments":
        return bool(compact_json.get("attachments"))
    return bool(compact_json.get("rows"))


def _unextracted_justification_table(table: ParsedTable) -> ParsedTable:
    result = table.model_copy(deep=True)
    if result.table_type != "ooz_items_table":
        result.table_type = "additional_characteristics_justification_table"
    result.compact_json = {
        **result.compact_json,
        "additional_characteristics_justifications": [],
        "justification_extraction": {
            "status": "contents_not_extracted",
            "warnings": ["contents_not_extracted"],
        },
    }
    result.parser_warnings = list(dict.fromkeys([
        *result.parser_warnings,
        "contents_not_extracted",
    ]))
    return result


def _unique_candidates(candidates: list[Any]) -> list[Any]:
    result = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        key = (candidate.table_id, candidate.role)
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _merge_role_result(
    base: ParsedTable,
    role_result: ParsedTable,
    role: VlmTableRole,
) -> ParsedTable:
    """Merge only the keys owned by one VLM role into the physical table."""
    if role == "nmck_calculation":
        return _merge_nmck_role_result(base, role_result)
    if role != "additional_characteristics_justification":
        merged = role_result.model_copy(deep=True)
        if role == "purchase_description" and base.table_type == "ooz_items_table":
            merged.compact_json["items"] = _preserve_coded_items(
                base.compact_json.get("items"),
                merged.compact_json.get("items"),
            )
        for key in (
            "additional_characteristics_justifications",
            "justification_extraction",
        ):
            if key in base.compact_json:
                merged.compact_json[key] = base.compact_json[key]
        merged.parser_warnings = list(dict.fromkeys([
            *base.parser_warnings,
            *merged.parser_warnings,
        ]))
        merged.compact_markdown = build_compact_markdown(merged)
        return merged

    merged = base.model_copy(deep=True)
    for key in (
        "additional_characteristics_justifications",
        "justification_extraction",
    ):
        if key in role_result.compact_json:
            merged.compact_json[key] = role_result.compact_json[key]
    if merged.table_type not in {"ooz_items_table"}:
        merged.table_type = "additional_characteristics_justification_table"
    merged.parser_warnings = list(dict.fromkeys([
        *merged.parser_warnings,
        *role_result.parser_warnings,
    ]))
    merged.compact_markdown = build_compact_markdown(merged)
    return merged


def _merge_nmck_role_result(base: ParsedTable, repaired: ParsedTable) -> ParsedTable:
    """Keep basic NMCK parsing deterministic; make staged NMCK VLM-owned."""
    if base.table_type == "nmck_staged_calculation_table":
        # Stage rows often combine a service description, period and one price
        # per supplier. The generic matrix parser has no reliable structure for
        # that shape, so the dedicated VLM extraction owns this table entirely.
        merged = repaired.model_copy(deep=True)
        source_headers = base.compact_json.get("price_sources") or []
        if source_headers:
            merged.compact_json["price_sources"] = source_headers
        base_stages = base.compact_json.get("stages") or []
        if base_stages and not merged.compact_json.get("stages"):
            merged.compact_json["stages"] = base_stages
            merged.table_type = "nmck_staged_calculation_table"
        merged.parser_warnings = list(dict.fromkeys([
            *base.parser_warnings,
            *repaired.parser_warnings,
        ]))
        merged.compact_markdown = build_compact_markdown(merged)
        return merged

    merged = base.model_copy(deep=True)
    base_items = [item for item in base.compact_json.get("items", []) if isinstance(item, dict)]
    repaired_items = [item for item in repaired.compact_json.get("items", []) if isinstance(item, dict)]
    matched: set[int] = set()
    items: list[dict[str, Any]] = []
    for base_item in base_items:
        match_index = _find_nmck_item_match(base_item, repaired_items, matched)
        if match_index is None:
            items.append(dict(base_item))
            continue
        matched.add(match_index)
        items.append(_merge_nmck_item(base_item, repaired_items[match_index]))
    # A staged table already has its hierarchy from the deterministic parser.
    # VLM can complete those rows, but must not add a stage aggregate as a new item.
    if base.table_type != "nmck_staged_calculation_table" or not base_items:
        items.extend(dict(item) for index, item in enumerate(repaired_items) if index not in matched)

    repaired_sources = repaired.compact_json.get("price_sources") or []
    merged.compact_json = {
        **base.compact_json,
        "items": items,
        "price_sources": base.compact_json.get("price_sources") or repaired_sources,
        "stages": base.compact_json.get("stages") or repaired.compact_json.get("stages", []),
        "nmck_totals": repaired.compact_json.get("nmck_totals") or base.compact_json.get("nmck_totals", []),
    }
    merged.parser_warnings = list(dict.fromkeys([
        *base.parser_warnings,
        *repaired.parser_warnings,
    ]))
    merged.compact_markdown = build_compact_markdown(merged)
    return merged


def _find_nmck_item_match(
    base_item: dict[str, Any],
    repaired_items: list[dict[str, Any]],
    matched: set[int],
) -> int | None:
    for key in ("row_index", "row_number"):
        value = base_item.get(key)
        if value is None:
            continue
        for index, repaired_item in enumerate(repaired_items):
            if index not in matched and repaired_item.get(key) == value:
                return index
    return None


def _merge_nmck_item(base_item: dict[str, Any], repaired_item: dict[str, Any]) -> dict[str, Any]:
    result = dict(base_item)
    for key, value in repaired_item.items():
        if key == "supplier_prices":
            result[key] = _merge_nmck_supplier_prices(result.get(key), value)
        elif not _has_value(result.get(key)) and _has_value(value):
            result[key] = value
    return result


def _has_value(value: object) -> bool:
    if value is None or value == "" or value == [] or value == {}:
        return False
    return bool(clean_text(str(value)))


def _merge_nmck_supplier_prices(base_prices: object, repaired_prices: object) -> list[dict[str, Any]]:
    base_rows = [dict(item) for item in base_prices or [] if isinstance(item, dict)]
    repaired_rows = [item for item in repaired_prices or [] if isinstance(item, dict)]
    by_source = {
        str(item.get("source_id")): item
        for item in repaired_rows
        if item.get("source_id")
    }
    for item in base_rows:
        repaired = by_source.pop(str(item.get("source_id")), None)
        if repaired:
            for key, value in repaired.items():
                if not _has_value(item.get(key)) and _has_value(value):
                    item[key] = value
    base_rows.extend(by_source.values())
    return base_rows


def _preserve_coded_items(base_items: object, repaired_items: object) -> list[dict[str, Any]]:
    """A VLM repair may enrich coded OOZ rows, but must not delete them."""
    result = [dict(item) for item in repaired_items or [] if isinstance(item, dict)]
    for item in base_items or []:
        if not isinstance(item, dict):
            continue
        if not clean_text(item.get("okpd2_code")) and not clean_text(item.get("ktru_code")):
            continue
        if any(_same_purchase_item(item, candidate) for candidate in result):
            continue
        result.append(dict(item))
    return result


def _same_purchase_item(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_row = clean_text(left.get("row_number")).casefold()
    right_row = clean_text(right.get("row_number")).casefold()
    if left_row and right_row and left_row == right_row:
        return True
    left_name = clean_text(left.get("name")).casefold()
    right_name = clean_text(right.get("name")).casefold()
    same_name = bool(left_name and right_name and (left_name in right_name or right_name in left_name))
    if not same_name:
        return False
    return all(
        clean_text(left.get(field)).casefold() == clean_text(right.get(field)).casefold()
        for field in ("okpd2_code", "ktru_code")
    )


def _known_items_payload(tables: list[ParsedTable]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for table in tables:
        for item in (table.compact_json or {}).get("items", []):
            if not isinstance(item, dict):
                continue
            payload = {
                "name": item.get("name"),
                "row_number": item.get("row_number"),
                "okpd2_code": item.get("okpd2_code"),
                "ktru_code": item.get("ktru_code"),
            }
            key = tuple(clean_text(payload[field]).casefold() for field in (
                "name", "row_number", "okpd2_code", "ktru_code"
            ))
            if key == ("", "", "", "") or key in seen:
                continue
            seen.add(key)
            result.append(payload)
    return result


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
