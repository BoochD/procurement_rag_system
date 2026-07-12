from __future__ import annotations

from summary_model.domain.models import DocumentType


LLM_EXTRACTION_PROMPT_VERSION = "extraction-llm-prompts-1.0.0"

COMMON_CANONICAL_EXTRACTION_PROMPT = """
You extract a final typed procurement document schema from compact parser output.

Use the input sections this way:
- known_extracted is the deterministic parser draft.
- tables[].compact_json is the main structured source for parsed tables.
- tables[].compact_markdown is fallback context for uncertain tables only.
- plain_text_blocks contain untouched paragraph text and should be used for
  delivery terms, addresses, warranties, grounds, contract clauses and other
  text-derived fields.

Rules:
- Return only data supported by the payload.
- Do not run external registry checks.
- Do not recalculate arithmetic; preserve parsed calculation fields.
- Do not silently drop parsed items, codes, quantities, prices, supplier prices
  or characteristics from known_extracted.
- If paragraph text conflicts with parsed table data, preserve the table data
  and add a parser_warnings entry describing the conflict.
- If a value is not found, leave it null or an empty list.
""".strip()


DOCUMENT_LLM_PROMPTS: dict[DocumentType, str] = {
    DocumentType.PLAN: """
Build ScheduleApplicationSchema.
Focus on fixed plan/key-value fields, aggregate quantity, OKPD2/KTRU codes,
NMCK, funding source, delivery and execution terms, securities, SMP/SONKO and
national-regime fields. Preserve raw_fields from known_extracted.
""".strip(),
    DocumentType.REQUEST: """
Build PurchaseRequestSchema.
Extract request number/date if present, procurement subject, NMCK, procurement
method, single-supplier basis, delivery terms, stages and attachment list.
Use parsed attachment tables when available.
""".strip(),
    DocumentType.ONMCK: """
Build NmckJustificationSchema.
Preserve parsed price_sources, items, supplier_prices, selected minimum price
and calculated fields. Use paragraph text only for method, subject, total amount
text and variation coefficient when missing.
""".strip(),
    DocumentType.OOZ: """
Build PurchaseDescriptionSchema.
Preserve parsed purchase items and characteristics. Use paragraph text for
purchase subject, delivery place, delivery term and warranty requirements.
Do not invent missing KTRU/OKPD2 codes.
""".strip(),
    DocumentType.CONTRACT: """
Build ContractDraftSchema.
Keep product-description items separate from specification_items. Use paragraph
text for contract number, subject, price, funding source, delivery place,
delivery term, warranty text and attachments. Do not merge specification rows
into purchase-description items.
""".strip(),
    DocumentType.EXPLANATORY_NOTE: """
Build ExplanatoryNoteSchema.
Extract subject, NMCK, procurement method and justification text from paragraph
text. Use parsed tables only as supporting context.
""".strip(),
    DocumentType.COMMERCIAL_OFFER: """
Build CommercialOfferSchema.
Extract supplier name, INN, outgoing number/date, offered items and total amount.
Use parsed item tables when available.
""".strip(),
    DocumentType.UNKNOWN: """
Build the closest matching extraction schema if the document type is clear from
payload. If it is not clear, return the schema requested by the tool with empty
unknown fields and parser_warnings explaining the uncertainty.
""".strip(),
}


def prompt_for_document_type(document_type: DocumentType) -> str:
    document_prompt = DOCUMENT_LLM_PROMPTS.get(
        document_type,
        DOCUMENT_LLM_PROMPTS[DocumentType.UNKNOWN],
    )
    return f"{COMMON_CANONICAL_EXTRACTION_PROMPT}\n\n{document_prompt}"


def prompt_versions() -> dict[str, str]:
    return {
        "common_canonical_extraction": LLM_EXTRACTION_PROMPT_VERSION,
        "schedule_application": LLM_EXTRACTION_PROMPT_VERSION,
        "purchase_request": LLM_EXTRACTION_PROMPT_VERSION,
        "nmck_justification": LLM_EXTRACTION_PROMPT_VERSION,
        "purchase_description": LLM_EXTRACTION_PROMPT_VERSION,
        "contract_draft": LLM_EXTRACTION_PROMPT_VERSION,
        "explanatory_note": LLM_EXTRACTION_PROMPT_VERSION,
        "commercial_offer": LLM_EXTRACTION_PROMPT_VERSION,
    }
