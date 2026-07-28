from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from shared_modules.llm_models import OPENAI_FAST_MODEL, get_chatGPT_client
from summary_model.checks.normalization import normalize_decimal, normalize_text, normalize_unit
from summary_model.checks.runner import (
    _match_offer_items,
    _match_offers_to_price_sources,
    _offer_marker_support,
    _offer_names_support,
)
from summary_model.extraction.structured_recovery import parse_json_object, recover_model
from summary_model.extraction_models import ProcurementPackageExtraction


class CommercialOfferMatchDecision(BaseModel):
    nmck_item_index: int
    nmck_row_number: str | None = None
    source_id: str | None = None
    candidate_id: str | None
    offer_item_row_number: str | None = None
    status: Literal["confirmed", "ambiguous", "not_found"]
    evidence: str | None = None
    reason: str | None = None


class CommercialOfferMatchResponse(BaseModel):
    decisions: list[CommercialOfferMatchDecision] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def run_commercial_offer_matching_llm(
    package: ProcurementPackageExtraction,
    *,
    model: str = OPENAI_FAST_MODEL,
) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
    payload = _build_payload(package)
    metrics: dict[str, Any] = {
        "model": model,
        "called": False,
        "unmatched_rows": len(payload["unmatched_rows"]),
    }
    if not payload["unmatched_rows"]:
        return [], metrics

    client = get_chatGPT_client()
    metrics["called"] = True
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _prompt()},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "commercial_offer_matching",
                    "schema": CommercialOfferMatchResponse.model_json_schema(),
                    "strict": True,
                },
            },
        )
        metrics["usage"] = _usage(response)
        raw_content = response.choices[0].message.content
        metrics["raw_output"] = str(raw_content or "")
        metrics["raw_output_preview"] = str(raw_content or "")[:1000]
        raw, normalization_warnings = _normalize_match_response(raw_content, payload)
        metrics["normalization_warnings"] = normalization_warnings
        metrics["normalized_response"] = raw
        if raw is None:
            raise ValueError("matcher КП не вернул корректный JSON-объект")
        recovery = recover_model(CommercialOfferMatchResponse, raw)
        metrics["recovery_status"] = recovery.status
        metrics["recovery_warnings"] = recovery.all_warnings
        if not isinstance(recovery.value, CommercialOfferMatchResponse):
            metrics["error"] = recovery.error or "Ответ не прошёл валидацию."
            return None, metrics
        decisions = _validate_decisions(package, recovery.value.decisions, payload)
        return decisions, metrics
    except Exception as error:
        metrics["error"] = f"{type(error).__name__}: {error}"
        return None, metrics


def _normalize_match_response(
    content: Any,
    payload: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    warnings: list[str] = []
    raw = _parse_match_json(content)
    if isinstance(raw, list):
        raw = {"decisions": raw, "warnings": []}
        warnings.append("Верхнеуровневый массив matcher-а обёрнут в decisions.")
    if not isinstance(raw, dict):
        return None, warnings

    normalized_decisions = []
    candidates = _candidate_lookup(payload)
    for position, value in enumerate(raw.get("decisions") or []):
        if not isinstance(value, dict):
            warnings.append(f"Решение matcher-а {position + 1} пропущено: ожидался объект.")
            continue
        decision = dict(value)
        nmck_index = decision.get("nmck_item_index")
        candidate_id = str(decision.get("candidate_id") or "")
        candidate = candidates.get(candidate_id)
        if candidate is not None:
            decision["source_id"] = candidate["source_id"]
            decision["offer_item_row_number"] = candidate.get("row_number")
        source_id = decision.get("source_id")
        if not source_id:
            sources = {
                str(row["source_id"])
                for row in payload.get("unmatched_rows", [])
                if row.get("nmck_item_index") == nmck_index
            }
            if len(sources) == 1:
                decision["source_id"] = sources.pop()
                warnings.append(
                    f"Для строки ОНМЦК {nmck_index} восстановлен единственный source_id."
                )
            else:
                warnings.append(
                    f"Решение matcher-а для строки ОНМЦК {nmck_index} пропущено: source_id неоднозначен."
                )
                continue
        if not decision.get("offer_item_row_number") and "offer_item_index" in decision:
            row_number = _row_number_from_alias(
                payload,
                str(decision["source_id"]),
                decision.get("offer_item_index"),
            )
            if row_number is not None:
                decision["offer_item_row_number"] = row_number
                warnings.append(
                    f"Для строки ОНМЦК {nmck_index} offer_item_index восстановлен как номер строки КП."
                )
            else:
                decision["status"] = "ambiguous"
                decision["reason"] = (
                    str(decision.get("reason") or "").strip()
                    + " Номер позиции КП из offer_item_index неоднозначен."
                ).strip()
        decision.pop("offer_item_index", None)
        if not decision.get("candidate_id"):
            matching_candidates = [
                item
                for item in candidates.values()
                if item["source_id"] == str(decision.get("source_id") or "")
                and str(item.get("row_number") or "")
                == str(decision.get("offer_item_row_number") or "")
            ]
            if len(matching_candidates) == 1:
                decision["candidate_id"] = matching_candidates[0]["candidate_id"]
        if decision.get("status") == "confirmed" and not decision.get("candidate_id"):
            decision["status"] = "ambiguous"
            decision["reason"] = (
                str(decision.get("reason") or "").strip()
                + " Модель не указала однозначный candidate_id строки КП."
            ).strip()
        decision.setdefault("candidate_id", None)
        normalized_decisions.append(decision)

    raw["decisions"] = normalized_decisions
    raw.setdefault("warnings", [])
    return raw, warnings


def _parse_match_json(content: Any) -> Any:
    if isinstance(content, (dict, list)):
        return content
    text = str(content or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return parse_json_object(text)


def _row_number_from_alias(
    payload: dict[str, Any],
    source_id: str,
    alias: Any,
) -> str | None:
    offers = [
        offer
        for offer in payload.get("offers", [])
        if str(offer.get("source_id")) == source_id
    ]
    if len(offers) != 1:
        return None
    items = list(offers[0].get("items") or [])
    exact = [
        item
        for item in items
        if str(item.get("row_number") or "") == str(alias)
    ]
    if len(exact) == 1:
        return str(exact[0]["row_number"])
    try:
        index = int(alias)
    except (TypeError, ValueError):
        return None
    positional = {
        candidate
        for candidate in (index, index - 1)
        if 0 <= candidate < len(items)
    }
    if len(positional) != 1:
        return None
    row_number = items[positional.pop()].get("row_number")
    return str(row_number) if row_number not in (None, "") else None


def _candidate_lookup(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item["candidate_id"]): {
            "candidate_id": str(item["candidate_id"]),
            "source_id": str(offer.get("source_id") or ""),
            "item_index": index,
            "row_number": item.get("row_number"),
        }
        for offer in payload.get("offers", [])
        for index, item in enumerate(offer.get("items") or [])
        if item.get("candidate_id")
    }


def _build_payload(package: ProcurementPackageExtraction) -> dict[str, Any]:
    onmck = package.nmck_justification
    offers = list(package.commercial_offers or [])
    if onmck is None or not offers:
        return {"unmatched_rows": [], "offers": []}
    offer_by_source, _warnings = _match_offers_to_price_sources(offers, onmck.price_sources)
    matches_by_source = {
        source_id: _match_offer_items(onmck.items, offer.items)[0]
        for source_id, offer in offer_by_source.items()
    }
    unmatched_rows: list[dict[str, Any]] = []
    for nmck_item_index, nmck_item in enumerate(onmck.items):
        for supplier_price in nmck_item.supplier_prices:
            offer = offer_by_source.get(supplier_price.source_id)
            if offer is None:
                continue
            if nmck_item_index in matches_by_source.get(supplier_price.source_id, {}):
                continue
            unmatched_rows.append({
                "nmck_item_index": nmck_item_index,
                "nmck_row_number": nmck_item.row_number,
                "source_id": supplier_price.source_id,
                "name": nmck_item.name,
                "quantity": nmck_item.quantity,
                "unit": nmck_item.unit,
                "ktru_code": nmck_item.ktru_code,
                "okpd2_code": nmck_item.okpd2_code,
                "unit_price": supplier_price.unit_price,
            })
    used_sources = {str(row["source_id"]) for row in unmatched_rows}
    return {
        "unmatched_rows": unmatched_rows,
        "offers": [
            {
                "source_id": source_id,
                "supplier": offer.supplier_name,
                "items": [
                    _offer_item_payload(
                        item,
                        candidate_id=f"{source_id}:item:{index}",
                    )
                    for index, item in enumerate(offer.items)
                ],
            }
            for source_id, offer in offer_by_source.items()
            if str(source_id) in used_sources
        ],
    }


def _offer_item_payload(item: Any, *, candidate_id: str) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "row_number": item.row_number,
        "name": item.name,
        "quantity": item.quantity,
        "unit": item.unit,
        "trademark": item.trademark,
        "model": item.model,
        "ktru_code": item.ktru_code,
        "okpd2_code": item.okpd2_code,
        "unit_price": item.unit_price,
        "evidence_text": item.evidence_text,
    }


def _validate_decisions(
    package: ProcurementPackageExtraction,
    decisions: list[CommercialOfferMatchDecision],
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    onmck = package.nmck_justification
    offers = list(package.commercial_offers or [])
    if onmck is None:
        return []
    offer_by_source, _warnings = _match_offers_to_price_sources(offers, onmck.price_sources)
    requested = {
        (row["nmck_item_index"], str(row["source_id"]))
        for row in payload["unmatched_rows"]
    }
    seen: set[tuple[int, str]] = set()
    candidate_lookup = _candidate_lookup(payload)
    result: list[dict[str, Any]] = []
    for decision in decisions:
        key = (decision.nmck_item_index, str(decision.source_id or ""))
        if key not in requested or key in seen:
            continue
        seen.add(key)
        payload = decision.model_dump(mode="json")
        nmck_item = (
            onmck.items[decision.nmck_item_index]
            if 0 <= decision.nmck_item_index < len(onmck.items)
            else None
        )
        offer = offer_by_source.get(decision.source_id)
        if decision.status != "confirmed" or nmck_item is None or offer is None:
            result.append(payload)
            continue
        candidate = candidate_lookup.get(str(decision.candidate_id or ""))
        candidates = []
        if candidate is not None and candidate["source_id"] == str(decision.source_id):
            index = int(candidate["item_index"])
            if 0 <= index < len(offer.items):
                candidates = [offer.items[index]]
        elif decision.offer_item_row_number:
            candidates = [
                item
                for item in offer.items
                if str(item.row_number or "") == str(decision.offer_item_row_number)
            ]
        if len(candidates) != 1:
            payload["status"] = "ambiguous"
            payload["reason"] = "Подтверждение отклонено: candidate_id не определяет одну строку КП."
        elif not _has_non_price_support(nmck_item, candidates[0], offer.items):
            payload["status"] = "ambiguous"
            payload["reason"] = "Подтверждение отклонено: нет уникальных признаков кроме цены."
        result.append(payload)
    return result


def _has_non_price_support(nmck_item: Any, offer_item: Any, offer_items: list[Any]) -> bool:
    if _same_nonempty_code(nmck_item.ktru_code, offer_item.ktru_code):
        return True
    if _same_nonempty_code(nmck_item.okpd2_code, offer_item.okpd2_code):
        return True
    if _offer_names_support(nmck_item.name, offer_item.name):
        return True
    if _offer_marker_support(nmck_item, offer_item):
        return True
    quantity = normalize_decimal(nmck_item.quantity)
    unit = normalize_unit(nmck_item.unit)
    if quantity is None or not unit:
        return False
    same_shape = [
        item
        for item in offer_items
        if normalize_decimal(item.quantity) == quantity and normalize_unit(item.unit) == unit
    ]
    return len(same_shape) == 1 and same_shape[0] is offer_item


def _same_nonempty_code(left: Any, right: Any) -> bool:
    left_code = normalize_text(left).replace(" ", "")
    right_code = normalize_text(right).replace(" ", "")
    return bool(left_code and right_code and left_code == right_code)


def _usage(response: Any) -> dict[str, Any] | None:
    usage = getattr(response, "usage", None)
    return usage.model_dump(mode="json") if usage is not None else None


def _prompt() -> str:
    return """
Сопоставь только строки ОНМЦК, которые детерминированный matcher не смог связать
с позициями уже распознанных коммерческих предложений. Для каждой строки используй только КП
с тем же source_id.

Приоритет доказательств: точный КТРУ/ОКПД2; затем содержательное название,
модель или товарный знак; затем уникальная в этом КП комбинация количества и единицы.
Верни confirmed только при уникальном соответствии по таким признакам. Совпадение только цены
никогда не является доказательством. Не изменяй цены и не придумывай позиции.
Если данных мало или подходят две строки, верни ambiguous; если подходящей строки нет — not_found.

Верни только JSON-объект без Markdown и без пояснений вокруг него. Верхний уровень всегда
имеет поля decisions и warnings. Для confirmed обязательно дословно скопируй candidate_id
выбранной строки КП из payload. Не придумывай candidate_id, source_id, offer_item_row_number
или offer_item_index. Для ambiguous и not_found candidate_id должен быть null.

Точный пример формата:
{"decisions":[{"nmck_item_index":2,"nmck_row_number":"1.2.3","source_id":"supplier_3","candidate_id":"supplier_3:item:4","offer_item_row_number":"1.2.3","status":"confirmed","evidence":"Совпали количество, единица и модель","reason":null}],"warnings":[]}

Примеры: точный КТРУ и единственная строка КП с этим кодом — confirmed. Абстрактный «тип №1»
и единственная программная строка с теми же количеством и единицей — confirmed только если в КП нет
второй строки с такой же комбинацией. Две похожие строки или совпадение только цены — ambiguous.
""".strip()
