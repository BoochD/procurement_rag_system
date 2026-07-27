# Summary Model Checks Plan

This document fixes the first checks/rules scope for the new `summary_model`
pipeline. The checks layer works only with `ProcurementPackageExtraction`
objects produced by extraction. It must not parse source DOCX files again.

## Source Data

- Runtime JSON: `extraction_result.llm.json`
- Python schema: `summary_model.extraction_models.ProcurementPackageExtraction`
- Checks input: already extracted Pydantic fields only

If a field required for a check is missing from the schema, the check must
return `manual_review` and name the missing field.

## Strict Checks

### Package Completeness

Compare:

- `purchase_request`
- `schedule_application`
- `nmck_justification`
- `purchase_description`
- `contract_draft`
- `explanatory_note`
- `commercial_offers_found_count`

Missing required documents are errors. Missing or fewer than three commercial
offers are manual review for this stage.

### Request Attachments

Compare:

- `purchase_request.attachments[].normalized_document_type`
- `package.files[].document_type`

An attachment listed in the request but missing from uploaded files is an error.
An uploaded file missing from the request attachment list is a warning, except
for the request document itself and commercial offers. The extraction layer may
derive the request attachment list from a small text/table block such as
`Приложение: 1. ...; 2. ...`.

### Schedule Application Completeness

Use:

- `schedule_application.raw_fields`
- `schedule_application.empty_fields`
- `schedule_application.negative_value_fields`

Empty fields are warnings. Negative values such as `нет`, `отсутствует`, or
`не предусмотрено` are valid filled values when the field meaning is boolean
or requirement-like; they must not create warnings by themselves.

### NMCK And Contract Price

Compare normalized money amounts:

- `schedule_application.nmck.amount`
- `purchase_request.nmck.amount`
- `nmck_justification.total_amount.amount`
- `contract_draft.price.amount`
- `explanatory_note.nmck.amount`

Different amounts are errors. Missing amounts are warnings/manual review.

### ONMCK Arithmetic

For each `nmck_justification.items[]` compare:

- `quantity`
- `selected_min_unit_price`
- `row_total_declared`
- `row_total_calculated`

Check `quantity * selected_min_unit_price == row_total_declared`.
Also compare the sum of declared rows with ONMCK total and plan NMCK.

### ONMCK Minimum Unit Price

For each ONMCK item compare:

- `supplier_prices[].unit_price`
- `selected_min_unit_price`
- `calculated_min_unit_price`

Selected price must equal the minimum supplier unit price.

### OKPD2 And KTRU

Compare code sets across:

- `schedule_application.okpd2_codes` / `ktru_codes`
- `purchase_description.items[].okpd2_code` / `ktru_code`
- `contract_draft.items[].okpd2_code` / `ktru_code`
- `nmck_justification.items[].okpd2_code` / `ktru_code`

This stage does not check code actuality in external registries.
Mismatch details must show which documents contain each code and which codes
are missing in each compared document. Empty documents are reported separately
so the reader can see whether the problem is missing extraction or a real code
set divergence.

### Report Detail Style

Successful strict checks should include short found values when useful, for
example document-level NMCK amounts or per-document code sets. Do not dump full
schemas into the text report; keep details compact and inspectable through
`checks.json` when deeper debugging is needed.

### Funding Source

Compare:

- `schedule_application.funding_source_text`
- `contract_draft.funding_source`

Use light text normalization. Clear mismatch is an error; missing value is
manual review.

### Securities

Use extracted schedule and contract fields:

- `application_security`
- `contract_security`
- `warranty_security`
- `contract_draft.contract_security`
- `contract_draft.warranty_security`

`is_not_required=True` is a valid extracted result, not a missing value.
If contract-side security data is absent but schedule data exists, return
manual review rather than inventing a mismatch.

### Normative Plan Checks: Securities And PP No. 1875

The report renders these checks immediately after package completeness. They
use the plan application as the source of truth and show the NMCK, actual
percentage, applicable range, and conclusion.

- Bid security (part 2 of article 44): from 0.5% to 1% when NMCK is at most
  20 million rubles; from 0.5% to 5% above 20 million rubles. A single-supplier
  purchase is not applicable for this basic check.
- Contract-performance security (article 96): from 0.5% to 30% when NMCK is
  at most 50 million rubles; from 10% to 30% above 50 million rubles.
- Warranty-obligation security (part 2.2 of article 96): no more than 10%.
- Cases requiring rules that are not yet extracted reliably, including advance
  payments and treasury support, remain manual review rather than a false pass.

The later internal checks compare the plan values with the contract. When the
contract says that a numeric size exists only in the structured EIS form, the
report states the short source reference (for example, `п. 8.2`) and returns
manual review; it does not reproduce the full clause.

For PP No. 1875, only OKPD2 codes from the plan are considered, including the
main subject and included goods. The local registry maps appendix 1 to plan row
17.1 (prohibition) and appendix 2 to row 17.2 (restriction). The check confirms
that the corresponding code or matched parent code is present in the required
row. Row 17.3 (advantages) is checked independently for presence and is not
inferred from appendices 1 or 2.

### Contract Attachments

Use:

- `contract_draft.referenced_attachments`
- `contract_draft.items`
- `contract_draft.specification_items`

Rules:

- `attachment_kind=purchase_description` requires contract description items.
- `attachment_kind=contract_specification` requires specification items.
- `attachment_kind=acceptance_act_form` only checks the referenced number/title;
  the form content itself is not validated in this stage.

## Semantic LLM Checks

Semantic checks do not run by default. They are enabled explicitly in
`summary_model.checks_cli` with `--with-llm` and use one structured LLM call
over compact fields from `ProcurementPackageExtraction`. They must not parse
DOCX again, call external registries, recalculate arithmetic, or re-check
OKPD2/KTRU/items/prices.

Checks:

- procurement subject;
- delivery term;
- delivery place;
- stages;
- warranty term;
- procurement method and single-supplier basis;
- SMP/SONKO participant preferences. The subcontracting duty and percentage
  are checked separately by a deterministic rule.

If `--with-llm` is not used, these checks remain `manual_review`. If the LLM
call fails, each semantic check returns `manual_review` with the LLM error.

## External / Later

The following checks remain in the report when the corresponding adapter is not
enabled:

- commercial offers;
- commercial offer numbers and prices vs ONMCK;
- KTRU characteristics;
- additional characteristics outside KTRU;
- PP No. 1875 / national regime;

KTRU characteristic checks can be enabled explicitly with
`summary_model.checks_cli --with-ktru`. The adapter works from
`ProcurementPackageExtraction.purchase_description.items[]` and reuses
`ProcurementReferenceRegistry` for live KTRU characteristics and PP No. 1875
lookups. It does not parse DOCX again.

Checks intentionally not shown in the current report scope:

- standard contract / standard terms;
- OKPD2 actuality outside the PP No. 1875/national-regime task.

## Implemented Stability Notes (2026-07-27)

- `Преференции СМП/СОНКО` and the duty to attract SMP/SONKO subcontractors are
  separate checks. An absent participant preference does not conflict with a
  subcontracting requirement; the latter is compared independently, including
  its percentage.
- Securities are reported separately: bid security, contract-performance
  security, and warranty-obligation security. A contract clause saying that a
  size exists only in the structured EIS form is `manual_review` when the
  uploaded file contains no numeric value.
- Plan rows `17.1 Запреты`, `17.2 Ограничения`, and `17.3 Преимущества` are
  checked explicitly for presence and shown in the report.
- For additional KTRU characteristics, the legal-rule OKPD2 source order is:
  official OKPD2 from the live KTRU card, explicit OOZ item code, then an
  unambiguous plan-matched code as fallback. The plan remains ground truth for
  cross-document discrepancy reporting, but it does not override official KTRU
  registry metadata for the registry rule itself.
- Scan-only commercial offers are extracted page by page and merged into one
  `CommercialOfferSchema`. Technical appendix rows without a unit or row price
  are not treated as commercial-offer price items.
