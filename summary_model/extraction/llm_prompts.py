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
- Treat okpd2_codes/ktru_codes as the deterministic full code inventory.
  Do not remove them. Use subject_codes for the main service/work/procurement
  subject codes and included_goods for goods/software rights supplied inside a
  service.
- If paragraph text conflicts with parsed table data, preserve the table data
  and add a parser_warnings entry describing the conflict.
- If a value is not found, leave it null or an empty list.
""".strip()


DOCUMENT_LLM_PROMPTS: dict[DocumentType, str] = {
    DocumentType.PLAN: """
Build ScheduleApplicationSchema.
Focus on fixed plan/key-value fields, aggregate quantity, OKPD2/KTRU codes,
NMCK, funding source, delivery place, delivery and execution terms, securities, SMP/SONKO and
national-regime fields. Preserve raw_fields and parsed stages from known_extracted.
Preserve subject_codes and included_goods from known_extracted. If a plan field
contains a main service OKPD2 before a phrase like "Товар и неисключительные
права, поставляемые в рамках оказания услуг", keep the main code in
subject_codes and keep the following goods/rights in included_goods.
If the plan describes a total quantity like "1 услуга", put it into
aggregate_quantity_text. If a cell contains "Разбивка по этапам", extract each
stage deliverable into stage_deliverables. If it separately lists goods or
non-exclusive rights supplied within the service, put them into included_goods
instead of merging them with stage quantities.
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
text, variation coefficient and stage descriptions when missing.
""".strip(),
    DocumentType.OOZ: """
Build PurchaseDescriptionSchema.
Preserve parsed purchase items and characteristics. Use paragraph text for
purchase subject, delivery place, delivery term and warranty requirements.
Preserve okpd2_codes/ktru_codes and subject_codes from known_extracted.
Preserve parsed stages when an OOZ stage table is present.
Do not invent missing KTRU/OKPD2 codes.
If the text contains justification for additional characteristics not present in
KTRU, copy the exact justification into additional_characteristics_justification_text.
If a trademark is specified with a reason, copy the reason into
trademark_justification_text. If the procurement includes transfer of exclusive
or non-exclusive rights, describe it in rights_transfer_text and list explicitly
required license/sub-license/rights-transfer documents in required_rights_documents.
""".strip(),
    DocumentType.CONTRACT: """
Build ContractDraftSchema.
Keep product-description items separate from specification_items. Use paragraph
text for contract number, subject, price, funding source, delivery place,
delivery term, stages, warranty text and attachments. Do not merge specification rows
into purchase-description items.
Preserve okpd2_codes/ktru_codes and subject_codes from known_extracted.
Extract the responsibility/penalty section into penalty_clauses and peni_clauses.
If known_extracted contains responsibility_section_text, preserve it and use it
as the main source for penalties and peni.
Use exact contract wording in raw_text. Classify clauses only when explicit:
- supplier value-obligation fine: party=supplier, obligation_kind=value_obligation;
- supplier non-value obligation fine: party=supplier, obligation_kind=non_value_obligation;
- customer fine: party=customer;
- delay peni formula: obligation_kind=delay_peni and place it in peni_clauses;
- SMP/SONKO subcontracting fine: obligation_kind=smp_sonko_subcontract.
If the clause states a percent like "10 процентов" or "5%", fill percent.
If it states a fixed amount like "1000 рублей", fill amount.
For subcontract_smp_sonko_* fields use only explicit contract clauses about
attracting subcontractors/co-executors from SMP/SONKO. Do not infer these fields
from generic legal references or from the plan. If the contract has no such
clause, leave these fields null; absence is valid when the plan does not require
SMP/SONKO subcontracting. If the contract explicitly says it is not required,
set subcontract_smp_sonko_required=false and keep the source phrase in raw.
Search this mostly in sections named "Права и обязанности Сторон",
"Обязанности Поставщика/Исполнителя", or nearby numbered clauses. Phrases like
"Привлечь к исполнению Контракта соисполнителей из числа субъектов малого
предпринимательства..." are explicit SMP/SONKO subcontracting clauses.
""".strip(),
    DocumentType.EXPLANATORY_NOTE: """
Build ExplanatoryNoteSchema.
Extract subject, NMCK, procurement method and justification text from paragraph
text. Use parsed tables only as supporting context.
""".strip(),
    DocumentType.COMMERCIAL_OFFER: """
Build CommercialOfferSchema.
Extract supplier name, INN, outgoing number/date, offer date, purchase subject,
offered items, quantities, units, unit prices, row totals, total amount, VAT,
delivery term, delivery place and advance payment terms.
Do not invent missing positions or recalculate VAT if the document is ambiguous.
If a trademark or model is visible, put it into trademark/model on the item.
If delivery term, delivery place, advance payment or VAT cannot be determined,
leave the field null and add parser_warnings.
Use parsed item tables when available, but keep CommercialOfferItem fields.
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
