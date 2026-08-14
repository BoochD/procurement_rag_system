from __future__ import annotations

from summary_model.vlm_lab.models import VlmTableRole


VLM_TABLE_PROMPT_VERSION = "vlm-table-prompts-0.1.7"


ROLE_NOTES: dict[VlmTableRole, str] = {
    "purchase_description": (
        "Extract procurement items, codes, characteristics, trademarks, and explicit "
        "trademark justifications. Store a visible trademark separately in trademark "
        "and its explicit justification in trademark_justification_text. Leave both "
        "fields null when they are not visible. Do not turn explanatory "
        "For each product, extract its quantity and unit: read a separate unit column "
        "when present, or use the quantity-header qualifier (for example, 'Количество, "
        "штук' means unit 'Штука'). Do not use units of technical characteristics as "
        "the product unit. "
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
        "minimum unit prices, row totals, parent stage numbers, and summary rows. "
        "First classify every numbered row. A whole number such as '1.' or '2.' whose "
        "name says 'этап' is an execution stage. A child number such as '2.1' or '2.2' "
        "is an item inside stage 2. If a stage has child rows, return the parent only "
        "in stages and return its children only in nmck_items: never duplicate the "
        "parent amount in nmck_items. If a numbered stage has no child rows and is "
        "itself a service, return that same row in both stages and nmck_items. Preserve "
        "the visible number, full name, full term, and selected minimum/contract price "
        "in stage.price_raw. For an all-service stage table, stages MUST contain one "
        "non-empty object for every numbered row returned in nmck_items; do not return "
        "stages: [] when the visible names contain '(1 этап)', '(2 этап)' and so on. "
        "Do not invent child goods rows. "
        "Read each executor block strictly left to right and copy supplier_label exactly "
        "from the corresponding header. Never move a price into the next executor block. "
        "In a supplier block with two subcolumns, 'Цена за ед. товара' and "
        "'Стоимость товаров', read unit_price_raw from the first and row_total_raw from "
        "the second. "
        "In the one-column service-stage form, every executor has exactly one visible "
        "column named 'Цена услуги'. If quantity is 1, that one visible cell is both "
        "unit_price_raw and row_total_raw for that SAME executor; do not look for a "
        "second column and do not take the next executor's price. Example: Исполнитель 1 "
        "= 4 790,00, Исполнитель 2 = 4 700,00, Исполнитель 3 = 4 562,77 returns "
        "(4 790,00, 4 790,00), (4 700,00, 4 700,00), (4 562,77, 4 562,77). "
        "Put a visible 'Итого' row only into nmck_totals, never into nmck_items or "
        "generic totals. supplier_totals_raw must contain exactly one visible total for "
        "every executor, in header order from left to right. Put the final cell under "
        "'Начальная (максимальная) цена контракта' separately into nmck_total_raw, even "
        "when it equals the last executor's total. "
        "In a supplier block with two subcolumns, 'Цена за ед. товара' and "
        "'Стоимость товаров', supplier_totals_raw contains ONLY the visible cell "
        "in the second subcolumn 'Стоимость товаров' of the final 'Итого' row. "
        "The cell under 'Цена за ед. товара' is NOT a supplier total, even if it "
        "contains a number in the 'Итого' row: it may be the sum of unit prices. "
        "For example, if the 'Итого' row shows 110 196 under 'Цена за ед. товара' "
        "and 350 000 under 'Стоимость товаров', return 350 000, never 110 196. "
        "Never copy unit prices, averages, coefficients, percentages, variation "
        "calculations, or other intermediate numbers into supplier_totals_raw. "
        "If the visible final row has neither supplier cost totals nor one service total "
        "per executor, leave supplier_totals_raw empty rather than guessing.\n"
        "Example standalone service-stage response entries: nmck_items contains "
        "{\"row_number\": \"1\", \"name\": \"Техническая поддержка (1 этап)\", "
        "\"quantity_raw\": \"1\", \"supplier_prices\": [{\"supplier_label\": "
        "\"Исполнитель 1\", \"unit_price_raw\": \"4 790,00\", "
        "\"row_total_raw\": \"4 790,00\"}]}; stages MUST also contain "
        "{\"stage_number\": \"1\", \"stage_name\": \"Техническая поддержка (1 этап)\", "
        "\"service_term_text\": \"с даты заключения контракта по 15.09.2026\", "
        "\"price_raw\": \"4 562,77\"}.\n"
        "Example product row: {\"row_number\": \"2.1\", \"parent_stage_number\": \"2\", "
        "\"name\": \"Сервер\", \"unit\": \"шт.\", \"quantity_raw\": \"4\", "
        "\"supplier_prices\": [{\"supplier_label\": \"Поставщик 1\", "
        "\"unit_price_raw\": \"10 300 000,00\", \"row_total_raw\": \"41 200 000,00\"}], "
        "\"selected_min_unit_price_raw\": \"10 245 000,00\", "
        "\"row_total_declared_raw\": \"40 980 000,00\"}.\n"
        "Example summary row: {\"label\": \"Итого\", \"unit\": \"шт.\", "
        "\"quantity_raw\": \"11\", \"supplier_totals_raw\": [\"1 661 000,00\", "
        "\"1 647 800,00\", \"1 652 200,00\"], \"nmck_total_raw\": \"1 647 800,00\"}."
    ),
    "contract_specification": (
        "Extract specification items with name, unit, quantity, unit price, "
        "total price, and totals. Ignore empty template rows."
    ),
    "additional_characteristics_justification": (
        "Extract only explicit justifications from the selected justification table or block. "
        "For this role, explanatory reasons belong in justifications, not item notes. "
        "Put the visible table title or application area into scope_text. Create one "
        "justification for each visible row or bullet. If the block contains three bullets, "
        "return three justifications; never collapse several visible reasons into one record. "
        "Preserve the full reason in "
        "justification_text, linked visible characteristic names in characteristic_names, "
        "and a short exact quote in evidence_text. Link each reason to a visible item "
        "using item_name, item_row_number, item_okpd2_code and item_ktru_code. The payload "
        "may contain known_items from other item tables in the same OOZ; use them only "
        "when the link is explicit or uniquely determined. If several items are possible, "
        "leave the item link empty and add a warning instead of guessing. When the table has one visible "
        "item and several reasons, link every reason to that item. Exclude normative preambles, PP 145 "
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
  nmck_calculation -> nmck_items, nmck_totals, and stages only when the NMCK rows are explicit stages;
  contract_specification -> items and totals;
  additional_characteristics_justification -> justifications;
  attachments -> attachments;
  generic/unknown -> unparsed_rows and warnings unless a clear structure is visible.
- Do not invent values that are not visible in the image or textual context.
- Preserve row numbers when visible.
- Preserve OKPD2 and KTRU codes exactly.
- Preserve quantities, units, prices, dates, and stage numbers as raw text.
- If a row continues the previous item, attach it to the previous item instead of creating a fake item.
- Return only keys declared in the supplied schema. In particular, a
  characteristic may contain only row_index, name, value, unit, is_additional,
  and source_note: never add raw_text or warnings to each characteristic.
- Do not add warnings to individual item rows. Use the single top-level warnings
  array only when it is needed. Close each item object before starting the next item.
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
