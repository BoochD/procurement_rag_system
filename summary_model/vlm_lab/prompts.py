from __future__ import annotations

from summary_model.vlm_lab.models import VlmTableRole


VLM_TABLE_PROMPT_VERSION = "vlm-table-prompts-0.1.0"


ROLE_NOTES: dict[VlmTableRole, str] = {
    "purchase_description": (
        "Extract procurement items, codes, characteristics, trademarks, and explicit "
        "trademark justifications. Store a visible trademark separately in trademark "
        "and its explicit justification in trademark_justification_text. Leave both "
        "fields null when they are not visible. Do not turn explanatory "
        "justification paragraphs into separate items. If a row only justifies "
        "additional characteristics, put it into notes or warnings. Formal rows "
        "such as 'Предоставляемые лицензии ... необходимы ...', compatibility "
        "explanations, manufacturer-letter references, legal references to PP "
        "145, and business-need explanations are not product items and are not "
        "characteristics unless the same row has a clear characteristic name and "
        "value in characteristic columns."
    ),
    "contract_stages": (
        "Extract execution/service stages. Preserve stage numbers, terms, "
        "results, quantities, and prices when present."
    ),
    "nmck_calculation": (
        "Extract NMCK calculation rows, supplier/executor prices, selected "
        "minimum unit prices, row totals, parent stage numbers, and totals."
    ),
    "contract_specification": (
        "Extract specification items with name, unit, quantity, unit price, "
        "total price, and totals. Ignore empty template rows."
    ),
    "additional_characteristics_justification": (
        "Extract only explicit justifications from the selected justification table or block. "
        "For this role, explanatory reasons belong in justifications, not item notes. "
        "Put the visible table title or application area into scope_text. Create one "
        "justification for each visible row or bullet, preserve the full reason in "
        "justification_text, linked visible characteristic names in characteristic_names, "
        "and a short exact quote in evidence_text. Link each reason to a visible item "
        "using item_name, item_row_number, item_okpd2_code and item_ktru_code. The payload "
        "may contain known_items from other item tables in the same OOZ; use them only "
        "when the link is explicit or uniquely determined. If several items are possible, "
        "leave the item link empty and add a warning instead of guessing. Exclude normative preambles, PP 145 "
        "references and text outside the selected justification block. Do not evaluate "
        "whether the reason is legally sufficient."
    ),
    "attachments": "Extract attachment numbers and titles.",
    "generic": "Extract rows only if a clear procurement structure is visible.",
    "unknown": "Describe what the table contains and keep uncertain rows unparsed.",
}


def vlm_table_prompt(role: VlmTableRole) -> str:
    return f"""
You parse Russian procurement DOCX tables from an image.

Return strictly valid JSON matching the provided schema.
Target table role: {role}

Rules:
- The target role is already selected by the deterministic pipeline. Always
  return that exact value in table_role. If the image contradicts the metadata,
  keep the target role, leave role-specific arrays empty and explain it in warnings.
- Fill only the fields relevant to the target role:
  purchase_description -> items with characteristics;
  contract_stages -> stages;
  nmck_calculation -> nmck_items and totals;
  contract_specification -> items and totals;
  additional_characteristics_justification -> justifications;
  attachments -> attachments;
  generic/unknown -> unparsed_rows and warnings unless a clear structure is visible.
- Do not invent values that are not visible in the image or textual context.
- Preserve row numbers when visible.
- Preserve OKPD2 and KTRU codes exactly.
- Preserve quantities, units, prices, dates, and stage numbers as raw text.
- If a row continues the previous item, attach it to the previous item instead of creating a fake item.
- If the target role is additional_characteristics_justification, store explicit
  reasons in justifications. For other roles, explanatory text is not a product item.
- Except for the additional_characteristics_justification role, formal explanatory rows are not items:
  phrases like "предоставляемые лицензии необходимы",
  "расширение функциональности", "полностью совместимо", "на основании информационного письма",
  "обоснование применения дополнительных характеристик", and references to legal rules should go
  into item notes or table warnings only.
- Do not create a characteristic from an explanatory row unless it has a visible characteristic name
  and a visible characteristic value in the table columns.
- For contract_specification, put prices into unit_price_without_vat_raw,
  unit_price_with_vat_raw, total_without_vat_raw, vat_amount_raw and
  total_price_raw when those columns are visible.
- If the table is too unclear, fill unparsed_rows and warnings instead of guessing.
- Do not perform external checks, arithmetic checks, or cross-document comparisons.

Role-specific instruction:
{ROLE_NOTES.get(role, ROLE_NOTES["unknown"])}
""".strip()


def vlm_user_context() -> str:
    return """
The attached image is a rendered table from a DOCX procurement document.
The payload includes document type, table metadata, parser output, and warnings.
Use the image as the main source of truth for table structure.
Use parser output only as a hint; it may contain mistakes for complex tables.
""".strip()
