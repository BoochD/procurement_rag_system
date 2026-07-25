# Summary Model Final Requirements Plan

This document consolidates the checklist from `Алгоритм от 17.07.2026 пояснения.docx`
with the current `summary_model` implementation. It describes the target
extraction schemas, required table roles, checks, and external integrations.

The plan is intentionally practical: keep deterministic parsers simple, use the
plan schedule application as the primary comparison source, and use LLM/VLM only
where table or text structure is too variable.

## Core Rule

`Заявка в план-график` is the main ground truth for package-level comparisons.

When the same fact is present in multiple documents, checks compare other
documents against the plan first, then report cross-document consistency:

- subject of procurement;
- OKPD2 and KTRU codes;
- NMCK / contract price;
- delivery/service/work terms;
- delivery/service/work place;
- contract execution term;
- stages;
- SMP/SONKO requirements and percentage;
- securities;
- national-regime fields.

If the plan contains only aggregate information, the report must say that the
plan has an aggregate value and compare item-level documents against each other
without pretending the plan has per-item detail.

## Required Package Documents

The package-level schema must support these document types:

- `purchase_request`: Обращение о проведении закупки.
- `schedule_application`: Заявка в план-график.
- `nmck_justification`: Обоснование НМЦК / ОНМЦК.
- `purchase_description`: Описание объекта закупки.
- `contract_draft`: Проект контракта.
- `explanatory_note`: Пояснительная записка.
- `commercial_offer`: КП, usually missing or requiring OCR/VLM.

Required completeness report:

- Обращение о проведении закупки: found / missing.
- Не менее 3 КП: found count, or explicitly “КП не приложены”.
- ОНМЦК: found / missing.
- ООЗ: found / missing.
- Заявка в план-график: found / missing.
- Проект контракта: found / missing.
- Пояснительная записка: found / missing.

The model should classify uploaded files itself. The web form may remain
partially filled, but extraction must not rely on users choosing perfect labels.

## Target Extraction Schemas

Current models in `summary_model/extraction_models.py` already cover the
baseline: money, terms, stages, raw fields, attachments, items, characteristics,
ONMCK items, contract specification items, and commercial offers.

The following schema additions or stricter conventions are still needed.

### Shared Source And Evidence

Each important field should carry enough evidence for the report:

- raw text;
- normalized value;
- source document label;
- table row / paragraph evidence when available.

Current fields usually have raw values but not always a structured source
object. Do not introduce a large evidence framework now; add `evidence` strings
where missing and keep debug artifacts for deeper inspection.

### Money

All money comparisons must use `Decimal` with two decimal places.

Need to preserve kopecks from:

- `2 109 514 рублей 00 копеек`;
- `2 109 514,00`;
- `350 000 рублей 00 копеек`.

Contract price extraction must ignore unrelated long numbers such as KBK,
account numbers, registry numbers, and legal references. Contract price should
come only from explicit price phrases or specification totals.

### Schedule Application

Current `ScheduleApplicationSchema` has raw fields, subject, codes, NMCK,
method, basis, funding, delivery, execution term, stages, SMP/SONKO and
securities. It needs stronger conventions for:

- aggregate quantity, for example `1 услуга`;
- stage quantities, for example `1 этап: услуги - 1 усл. ед.`;
- goods and rights supplied as part of services;
- national-regime plan fields;
- additional participant requirements for purchases over 20 million RUB.

Suggested small additions:

- `aggregate_quantity_text`;
- `stage_deliverables: list[StageDeliverable]`;
- `included_goods: list[PurchaseItem]`;
- `national_regime_fields: list[RawField]`;
- `additional_participant_requirements_text`.

Do not force every simple plan into item-level structure. For old simple plans,
empty `stage_deliverables` and `included_goods` are valid.

Implementation note: these fields are optional schema extensions. Older simple
packages can leave them empty. Parsers should fill them only when the source
clearly contains aggregate quantity, stage deliverables, or goods/rights inside
services.

### Stages

Current `ProcurementStage` is a good base. It should be used consistently in:

- plan schedule application;
- purchase request;
- ONMCK;
- purchase description;
- contract draft.

Need to support:

- stage number/name;
- service/work/delivery term;
- execution end date;
- stage price;
- stage quantity text;
- result/deliverable text.

For complicated tables, use text-preserving extraction first. A missing stage
list is valid only when the source document does not contain stages. If stages
are present but not parsed, keep fallback rows and return manual review.

### Purchase Description / OOZ

Current `PurchaseDescriptionSchema` has items, delivery, stages, warranty, and
additional-characteristics justification. It needs conventions for:

- item characteristics with KTRU flag: standard vs additional;
- characteristic unit;
- justification of additional characteristics;
- trademark indication and justification;
- transfer of exclusive/non-exclusive rights;
- license/sub-license documents or rights-transfer acts.

Suggested small additions:

- `trademark_justification_text`;
- `rights_transfer_text`;
- `required_rights_documents: list[RequestAttachment]`;
- `technical_part_warnings: list[str]` only if parser found unstructured
  technical blocks that were intentionally not checked.

### Contract Draft

Current `ContractDraftSchema` already separates embedded OOZ items from
specification items. It needs stronger extraction/reporting for:

- price with kopecks and source phrase;
- funding source;
- delivery/service/work place;
- delivery/service/work term;
- separate contract execution term;
- stages with price and term;
- warranty term;
- securities;
- SMP/SONKO conditions and percentage;
- referenced and actual attachments.

Later, if examples are provided, add:

- penalties text/check inputs;
- standard contract / standard terms fields;
- bank/treasury support fields.

These are not immediate parser targets unless source examples exist.

### Commercial Offers

Current `CommercialOfferSchema` is minimal. Full KP support is a separate OCR/VLM
stage. Target fields:

- supplier name;
- INN and requisites;
- outgoing number and date;
- item names;
- quantities;
- units;
- unit prices;
- totals;
- VAT terms;
- delivery term;
- delivery place;
- advance payment;
- scan/OCR/VLM status.

Until OCR/VLM is implemented, the report must say “КП не приложены” or
“КП не распознаны; сверка с КП невозможна”.

## Required Table Roles

The table layer should keep simple robust parsers and avoid broad guessing.
Table detection must use document type, nearby title/context, normalized header
roles, and content signals. Exact wording is a hint, not a hard dependency.

### Deterministic Table Roles

- `schedule_application_table`
  - Fixed plan table / key-value form.
  - Must parse raw fields, subject, codes, NMCK, delivery/place, execution term,
    SMP/SONKO, securities, national-regime fields.
  - For stages embedded inside a cell, extract text and parse simple stage rows.

- `request_attachments_table`
  - Small table or text block starting with `Приложение`.
  - Must support semicolon-separated and newline-separated lists.

- `ooz_items_table`
  - Product/service/right rows plus characteristics.
  - Must support KTRU/OKPD2 combined with name in one column.
  - Must not create confident items from weak rows.

- `contract_specification_table`
  - Contract appendix/specification with quantity, unit prices, VAT, total.
  - Totals rows go to totals, not items.

- `contract_stages_table`
  - Stages table in contract or OOZ.
  - Header variants: `этап`, `номер этапа`, `№ этапа`, `срок оказания`,
    `срок выполнения`, `срок поставки`, `стоимость этапа`, `результат`.

- `nmck_calculation_table`
  - Simple supplier matrix.
  - Supplier columns may say `Поставщик`, `Исполнитель`, or equivalent minimal
    variants.

- `nmck_staged_calculation_table`
  - ONMCK with parent stage rows and child item/service rows.
  - Must parse stage totals, item prices, selected minimum, declared totals, and
    final NMCK.

- `contract_attachments_table`
  - Contract appendix list and actual appendix headings.
  - These tables are needed for attachment checks even when there are many of
    them in a contract. They should not be used as item/price/characteristic
    tables unless their headers also match a product, specification, or stage
    role.

- `signature_table` / `ignored_table`
  - Signature/approval/service tables. Keep debug only.

- `generic_table` / `unknown`
  - Preserve compact rows for LLM/VLM fallback. Do not invent structured items.

### Tables To Ignore

Some tables should be kept in debug artifacts but not passed to exact checks or
VLM:

- signature and approval tables;
- requisites/signature blocks;
- tables of definitions, abbreviations, or term explanations;
- empty template tables;
- purely legal boilerplate with no quantities, prices, terms, addresses, codes,
  characteristics, stages, or attachment names;
- acceptance-act form body, unless the current check is only verifying that the
  form is referenced as an attachment.

These tables may still remain in debug exports. They should not enter final
schemas, LLM payloads, or VLM queue by default.

### VLM Fallback Candidates

Use VLM for important tables only when deterministic output is unreliable.
Candidate triggers:

- relevant table is `generic_table` or `unknown`;
- parser warnings on an important table;
- many fallback rows;
- suspicious items, very long names, missing codes/units/quantities;
- multi-level headers with nested rows and row spans;
- staged ONMCK or stage table where deterministic parser cannot recover stage
  numbers/prices/terms;
- contract/OOZ appendix table that is visually clear but XML parsing loses
  structure.

VLM payload should include:

- one rendered long image of the selected DOCX table when possible;
- role hint: purchase description, contract stages, ONMCK, specification,
  attachments, or generic;
- compact deterministic parser output as a hint;
- strict instruction to keep formal explanatory rows as notes, not items.

For very long tables, first try one long rendered image. If provider limits
force slicing later, repeat the table title/header in every slice and ask VLM to
preserve row continuation numbers.

Do not send a table to VLM just because it is long. It must be relevant to an
exact check. Relevance comes from nearby heading/header/content signals:

- product description or characteristics;
- specification/price;
- ONMCK/supplier prices;
- stages;
- application/contract attachments;
- commercial-offer items.

If a table is both complex and irrelevant, classify it as ignored/debug-only.

## Required Checks

Checks work over `ProcurementPackageExtraction`, not source DOCX.

### Strict Checks

- Completeness of required documents and КП count.
- Request attachments vs uploaded files.
- Contract attachments: numbers and titles; content validation only for OOZ and
  specification tables, acceptance act form listed only.
- Schedule application filled fields; negative values like `нет` and
  `отсутствует` are valid filled values when the field is boolean-like.
- Subject: compare plan vs request, OOZ, contract, explanatory note.
- OKPD2 sets: compare plan vs OOZ, contract, ONMCK.
- KTRU sets: compare plan vs OOZ, contract, ONMCK.
- Item names, quantities, units:
  - item-level documents compare by KTRU, then OKPD2 + normalized name, then
    normalized name;
  - if plan has only aggregate quantity, report aggregate separately.
- NMCK/contract price with kopecks across plan, request, ONMCK, contract,
  explanatory note.
- ONMCK arithmetic:
  - quantity;
  - supplier unit prices;
  - selected minimum unit price;
  - row totals;
  - stage totals for staged ONMCK;
  - final total.
- ONMCK coefficient of variation per item and total when source prices allow it.
- Delivery/service/work term against plan.
- Delivery/service/work place against plan.
- Contract execution term against plan.
- Stages against plan:
  - same number/order of stages;
  - comparable term/result/price if present;
  - if stages are present in one document and absent in another, report manual
    review or error with source values.
- Funding source against plan.
- Securities against plan, including valid `not required`.
- SMP/SONKO:
  - preference;
  - subcontracting obligation;
  - subcontracting percentage;
  - compare plan and contract.
- Warranty term: compare OOZ and contract, and plan if present.

### Data Sources For Checks

Checks should read these fields first:

- subject: `schedule_application.purchase_subject` compared with request, OOZ,
  contract and note subject fields;
- delivery/service/work term: `schedule_application.delivery_term_text`
  compared with request, OOZ and contract delivery term fields;
- place: `schedule_application.delivery_place` compared with OOZ and contract;
- contract execution term: `schedule_application.contract_execution_term_text`
  compared with contract execution term;
- stages: `schedule_application.stages` compared with request, OOZ, contract
  and ONMCK stages;
- warranty: OOZ warranty text compared with contract warranty text;
- SMP/SONKO: plan raw/normalized fields compared with contract raw/normalized
  fields;
- NMCK: plan, request, ONMCK, contract and note money fields;
- ONMCK arithmetic: `nmck_justification.items[]` and `price_sources[]`;
- attachments: request/contract `attachments` plus uploaded `files`;
- item codes and quantities: `PurchaseItem`, `NmckItem` and
  `ContractSpecificationItem` fields.

If an exact field is missing but fallback table rows exist, the check should not
silently pass. It should return manual review and point to the parser warning or
fallback table.

### Semantic LLM Checks

Use one compact package-level LLM call only for semantic equivalence, not for
arithmetic or registry checks.

Semantic checks:

- procurement subject;
- delivery/service/work term;
- delivery/service/work place;
- stages;
- warranty;
- procurement method and single-supplier basis;
- SMP/SONKO wording.

The prompt must treat delivery term and contract execution term as different
entities.

### External / Registry Checks

Use existing adapters where possible.

- KTRU card/name:
  - fetch card from `zakupki.gov.ru`;
  - report URL, reference name, names from documents, and status.

- KTRU characteristics:
  - check values allowed by KTRU;
  - check mandatory and optional characteristics from EIS;
  - check characteristic units;
  - normalize visually similar Cyrillic/Latin symbols.

- Additional characteristics:
  - identify characteristics present in OOZ but absent from KTRU;
  - use old PP145/OKPD2 logic;
  - search exact OKPD2 first, then shorten parent code one segment/digit at a
    time as in the old pipeline;
  - report which OKPD2 or parent code was used;
  - if additional characteristics exist, verify justification text in OOZ.

- PP No. 1875 / national regime:
  - use local parsed registry artifacts;
  - report exact OKPD2, matched parent code, table/position, reference name, and
    a clear warning to apply national-regime requirements.

- Supplier/INN checks for КП:
  - later, after commercial-offer OCR/VLM.

### Deferred Checks

Do not implement without examples and legal rule clarification:

- penalties under Government Decree No. 1042;
- security sizes under 44-FZ articles;
- standard contract / standard conditions by OKPD2;
- bank/treasury support.

If these appear in a requirements list before examples are available, report
them as “не реализовано на текущем этапе; нужен пример/правило”.

## Report Requirements

The report should be a checking document, not a model summary.

- If a check passes, still show the values that were found.
- If a check cannot run, say exactly why.
- Do not show English or internal names such as `purchase_request`,
  `commercial_offer`, `has_stages`, `supplier_prices`.
- Use existing markup only:
  - `<big>` for major sections;
  - `<b>` for sub-check names;
  - `<ok>`, `<warn>`, `<error>` for statuses;
  - `<ins>` for important normative warnings.
- For KTRU characteristics, avoid a wall of text:
  - group by KTRU and item;
  - show every failed/manual-review characteristic;
  - summarize passed characteristic counts;
  - keep detailed rows available in `checks.json`.

## Implementation Gaps

Current implementation already covers many baseline checks. Main gaps:

1. Plan schema does not yet cleanly represent aggregate quantity, stage
   deliverables, and goods/rights supplied as part of services.
2. Stage checks need to compare against the plan as ground truth and clearly
   separate delivery/service/work terms from contract execution term.
3. Staged ONMCK needs stronger report detail for stage totals and per-row
   minimum unit prices.
4. Commercial offers require OCR/VLM before meaningful checks can run.
5. KTRU characteristic report needs more granular output for mandatory,
   optional, value, and unit checks.
6. Additional-characteristics logic must show the OKPD2/parent code and
   justification result.
7. Contract/OOZ trademark and rights-transfer fields are not yet represented
   strongly enough for the 106-million-license-style packages.
8. VLM lab exists, but it is not yet integrated as an automatic fallback for
   important complex tables.

## Suggested Work Order

1. Strengthen plan schedule extraction and schema for aggregate quantity,
   stages, stage deliverables, and included goods/rights.
2. Add deterministic/staged ONMCK checks and report details.
3. Implement stage comparison against plan, with manual-review fallback when a
   stage table is present but not parsed.
4. Improve KTRU characteristic and additional-characteristic report detail.
5. Add OOZ/contract fields for trademark and rights-transfer justification.
6. Integrate VLM fallback only for selected complex table candidates.
7. Start КП OCR/VLM extraction as a separate feature.
