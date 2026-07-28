# Project Guide

This guide is the compact system map for future agent work. Code is the source of truth when this document and implementation disagree.

The target replacement architecture and phased migration plan are documented in `docs/summary_model_migration.md`.

## Project Context

The project checks procurement document packs with deterministic parsing, local and live registry checks, retrieval, and LLM-assisted comparison. Users upload a procurement pack through the web UI and receive a structured analysis in the browser plus a downloadable `.docx` report.

Main use case:

1. User uploads documents in the Django UI.
2. Django sends a Celery task with base64-encoded file contents.
3. Worker saves temporary files and calls the AI/RAG service.
4. The service parses documents, checks registries, runs LLM comparisons, and assembles a tagged text report.
5. Worker converts tagged text to `.docx` and returns task data to the UI.

Key document entities:

- `plan`: procurement plan request; mandatory baseline document.
- `contract`: draft contract.
- `ooz`: procurement object description.
- `zapiska`: explanatory note.
- `onmck`: initial maximum contract price document.
- `obrasheniye`: procurement request/appeal document.

## Entrypoints

- Web app: `web/manage.py`, `web/textprocessor/urls.py`, `web/fileprocessor/urls.py`.
- Upload/result UI: `web/fileprocessor/views.py`, `web/fileprocessor/templates/fileprocessor/index.html`, `web/fileprocessor/templates/fileprocessor/result.html`.
- Celery app: `celery-worker/celery_app.py`.
- Celery task: `celery-worker/tasks.py`, task name `rag_worker.process_document_query`.
- Main pipeline: `latest_model/ai_service.py`.
- Docker stack: `docker-compose.yml`.
- Container default command: `Dockerfile`.

## Component Responsibilities

- `web/fileprocessor/views.py`: validates the required upload, serializes documents, starts Celery, stores task metadata in session, polls results, and serves the downloaded report.
- `celery-worker/tasks.py`: validates task input, decodes files, manages temporary paths, calls `AIService.process_query`, and converts `ai_response` to `.docx`.
- `latest_model/ai_service.py`: orchestrates all checks and final report sections.
- `summary_model/`: independent schema-first replacement pipeline with ordered DOCX ingestion, document-specific extraction, deterministic rules, registry adapters, CLI artifacts, and report generation. It is not connected to the worker. Its async API is `aprocess_package`; extraction and three semantic analyzers share an LLM concurrency limit of three, while `process_package` remains the synchronous CLI wrapper.
- `summary_model/extraction_pipeline.py` and `summary_model/extraction_cli.py`: independent typed extraction layer. It reads DOCX files, classifies document types, builds parsed table artifacts, normalizes fields into `ProcurementPackageExtraction`, and writes debug artifacts without running external registry checks or package-level LLM analyzers. The CLI also writes per-document LLM payload JSON with paragraphs, compact parsed tables, and deterministic known extraction.
- `summary_model/extraction/llm_document_extractor.py`: optional document-level LLM canonicalization layer for the new extraction pipeline. It consumes per-document `llm_payloads`, returns the same strict Pydantic document schemas, and restores deterministic parsed items/codes/prices if an LLM response drops them.
- `summary_model/tables/`: table parsing layer on top of `TableIR v4`. It classifies tables, builds logical rows, generates `compact_markdown`/`compact_json`, and exports physical/logical/compact debug views for parser review. Contract description/characteristic tables and contract specification/price tables are separate table types; signature/service tables are debug-only and must not enter working extraction schemas. ONMCK supplier columns are grouped as supplier pairs: unit price plus row total. Weak or template-like rows should remain fallback/debug rows rather than becoming fake structured items.
- Summary `Document IR` schema `4.0.0` stores tables as column definitions plus
  physical rows and merged-cell spans. Dense matrices are reconstructed only
  in memory for deterministic parsing. LLM calls receive a compact textual
  `TABLE/HEADER/SCOPE/ROW` projection, never serialized cells or a dense matrix.
  Runtime artifacts from older IR schema versions must be regenerated.
- `summary_model` reports use `ProcurementPackage` for document-by-document
  overviews of codes, items, quantities, NMCK, delivery terms, addresses, and
  supplier prices. `Finding` supplies errors and manual-review details rather
  than a flat list of every successful low-level rule.
- Warranty comparison uses explicit terms from the OOZ warranty section,
  including an OOZ embedded in the contract. A contract clause that only
  refers to an appendix does not confirm that warranty terms match.
- `latest_model/docs_parsing.py`: extracts plan, contract, OOZ, explanatory note, ONMCK, characteristics, and price information from documents.
- `shared_modules/parser_functions.py`: low-level `.docx` parsing helpers, table normalization, OKPD/KTRU parsing, and ONMCK price extraction.
- `latest_model/check_registry.py`: bridges parsed plan/OOZ data to registry checks and characteristic comparison.
- `services/procurement_reference_registry.py`: local PP 1875 lookup, OKPD matching, live KTRU fetch/parsing, and KTRU characteristic extraction.
- `shared_modules/retriever.py`: text splitting plus BM25/FAISS retriever creation. The active RAG path uses `BM25TextRetriever`.
- `latest_model/rag_processing.py`: invokes the RAG prompt per plan point with retrieved context.
- `latest_model/smart_processing.py`: invokes the smart comparison prompt for OKPD/KTRU/product/quantity checks.
- `latest_model/prompts.py`: prompt contracts and output formats for default, smart, and RAG checks.
- `shared_modules/llm_models.py`: LLM client/model factories; the active pipeline uses the OpenAI-compatible provider.
- `shared_modules/embeddings.py`: embedding model factories for optional FAISS retrieval.

## Pipeline Details

`AIService.process_query` currently performs these stages:

1. Determine available and missing documents.
2. Parse plan table values, treating the plan as the baseline.
3. Extract focused points from contract, OOZ, zapiska, and ONMCK.
4. Check KTRU against live `zakupki.gov.ru` data and OKPD against local PP 1875 data.
5. Run smart LLM comparison for OKPD, KTRU, product names, and quantities.
6. Compare OOZ characteristics against KTRU characteristics and PP 1875 extra-characteristic rules.
7. Build BM25 retrieval over uploaded document text and run RAG checks for plan points about delivery dates, contract price, delivery place, and procurement object name.
8. Parse ONMCK supplier prices and flag coefficient of variation >= 33%.
9. Assemble the final tagged text report and highlight error labels.

Report sections are assembled in `latest_model/ai_service.py` in this order:

- package completeness and performed/skipped checks;
- KTRU check through `zakupki.gov.ru`;
- OKPD check against PP 1875;
- internal document-pack analysis;
- OOZ characteristic comparison against KTRU site data;
- ONMCK supplier price comparison.

## Existing Tests

`tests/okpd_tests` covers local PP 1875 artifacts and OKPD registry behavior:

- artifact existence for raw HTML, manifest, index JSON, SQLite, and table files;
- manifest/index/table schema checks;
- known OKPD positions;
- exact, normalized, mismatch, parent-match, invalid-code, and row lookup scenarios.

`tests/ktru_tests` covers KTRU behavior:

- code validation and URL building;
- HTML parsing for common info and characteristics;
- live KTRU registry checks and live response messages.

Live KTRU tests catch `requests.RequestException` and skip on network failures, but they still depend on external availability.

## Manual Fixtures And Notebooks

- `doci_primery/` contains real-world procurement document packs used for manual parser and pipeline checks. It is fixture material, not an automated test suite.
- `latest_model/latest_test.ipynb`, `shared_modules/testing.ipynb`, and `etc/test_parser_LLM.ipynb` are exploratory notebooks. Their saved outputs may be stale, contain failed external calls, or depend on local paths and credentials.
- Root-level notebooks such as `test_consultant.ipynb` are also experimental unless their behavior is moved into `tests/`.
- Do not treat notebook execution as proof of regression safety. Record the exact document pack and observed parser output when using a notebook for manual verification.

## Commands

Runtime commands found in project docs/config:

- `docker-compose up --build -d`
- `docker-compose down`
- `python web/manage.py migrate`
- `python web/manage.py runserver 0.0.0.0:8000`
- `cd celery-worker && celery -A celery_app worker --loglevel=info --pool=threads --concurrency=4`
- `celery -A celery_app worker -l info -P solo`

Testing commands inferred from the pytest suite:

- `pytest tests`
- `pytest tests/okpd_tests`
- `pytest tests/ktru_tests/test_parsing.py tests/ktru_tests/test_validation.py`
- `pytest tests/ktru_tests` when live/network tests are acceptable.
- `pytest tests/summary_model_tests`

Independent summary pipeline:

- `python -m summary_model.cli --input-dir "doci_primery/PACK_06_05" --output-dir "runtime/summary_runs/PACK_06_05"`
- Add `--no-llm --no-external` for deterministic local verification.

Independent typed extraction pipeline:

- `python -m summary_model.extraction_cli --input-dir "doci_primery/PACK_06_05" --output-dir "runtime/extraction_runs/PACK_06_05"`
- Creates `extraction_result.json`, `documents/*.json`, `tables/*.json`, `llm_payloads/*.json`, `debug/tables/<file>/table_N_physical.md`, `debug/tables/<file>/table_N_logical.json`, `debug/tables/<file>/table_N_compact.md`, and `run.json`.
- Add `--with-llm` to run live document-level LLM canonicalization. This additionally creates `llm_documents/*.json` and `extraction_result.llm.json`. It is opt-in because it uses the configured OpenAI-compatible model.
- Does not run PP 1875, live KTRU, legal checks, package-level semantic analyzers, or commercial-offer OCR.

Commercial-offer VLM lab:

- `python -m summary_model.commercial_offer_lab.run --input <offer_1.pdf> <offer_2.pdf> <offer_3.pdf> --model <vlm_model>`
- Uses the production visual VLM path for every PDF and writes per-model
  `commercial_offers.json`, `checks.json`, `report.txt`, and `run.json` under
  `runtime/commercial_offer_lab/<model>/` by default.
- To exercise the same commercial-offer matcher and ONMCK comparison as the full
  pipeline, add `--package <extraction_result.json> --matcher-model <text_model>`.
  The lab then writes `matcher_payload.json`, raw/normalized matcher responses,
  accepted decisions, metrics, and the rendered commercial-offer report.
- Text structured extraction uses `OPENAI_MODEL` (`gpt-5-mini` by default).
  Visual document/table extraction uses the separate `OPENAI_VLM_MODEL`
  (`gpt-5.4-mini` by default). Lab `--model` remains an explicit per-run
  override and does not change production configuration.

Commercial-offer arithmetic:

- Every recognized row is checked as `quantity * unit price = row total`.
- If a printed row total is absent but quantity and unit price are available,
  the calculated total participates in the offer-total check and one compact
  manual-review note records that the printed row totals were not verified.
- A row equal to the whole offer total is removed as an aggregate row only when
  the declared or calculated totals of all remaining rows independently equal
  the same declared total.
  Otherwise it is retained and reported for review.
- The report shows one compact arithmetic row per offer. Complete VLM warnings
  remain in `checks.json`; the public report shows only prioritized summaries.
- ONMCK rows and each offer are matched one-to-one before comparison. The
  deterministic matcher uses codes, normalized names, trademark/model markers,
  and quantities that can be confirmed from ONMCK supplier totals. The text LLM
  receives only pairs left unresolved by those rules and cannot replace an
  already established deterministic match.
- The commercial-offer report explicitly summarizes six checks: purchase
  subject against OOZ; quantity against OOZ and ONMCK; supplier unit price
  against ONMCK; unit against OOZ and ONMCK; and row/offer totals when the VLM
  extracted enough numeric data; plus basic VAT arithmetic when the tax base,
  rate, amount, and inclusion mode are explicit. Missing or ambiguous VAT data
  and arithmetic differences produce manual review rather than a hard failure.
  Missing reference values likewise produce manual review rather than a silent
  pass. The trademark table is rendered in the commercial-offer section and
  remains informational only.
- Supplier IDs present in ONMCK price rows remain authoritative even when the
  corresponding `price_sources` entry is missing or its letter number is
  truncated. A positional offer fallback is allowed only for the expected,
  unused offer and records a warning. Remaining item rows may be matched in
  order only when at least two rows align on supplier price, quantity, and unit.
- In the public report, semantic results replace duplicate strict text results
  for subject, delivery term/place, and warranty when the semantic check ran
  successfully. Strict results remain available in `checks.json` for diagnosis.
- Procurement methods are compared across the plan, request, and explanatory
  note before declaring that single-supplier justification is unnecessary.

Structured-output recovery lab:

- `python -m summary_model.structured_output_lab.run` writes an offline example
  of normalizing Russian dates, money, VAT text, and string lists before a
  strict Pydantic schema.
- Add `--live` to test the configured LangChain/OpenAI-compatible provider with
  `include_raw=True`; add `--compare-direct` to make a second, old-style call
  without raw recovery. Artifacts are stored under
  `runtime/structured_output_lab/<model>/`.

Full web-pipeline diagnostics:

- `python -m summary_model.full_pipeline_cli --input-dir <pack> --output-dir <output>`
  runs the same `summary_model.web_service` orchestration used by the Celery
  worker, including document LLM extraction, semantic/stage/penalty checks,
  VLM table fallback, commercial-offer VLM parsing, KTRU and PP 1875 checks.
- The CLI recognizes the current Russian document file names, ignores generated
  `analysis_result*.docx` files, prefers DOCX over a duplicate non-offer PDF,
  and records every selected or ignored file in `inputs.json`.
- Diagnostic artifacts include the final merged extraction schema in
  `extraction_result.final.json`, structured checks, LLM/VLM metrics, warnings,
  and report text. A pipeline exception writes `error.json` and a failed
  `run.json` before the exception is re-raised.
- Use `--no-vlm-tables`, `--no-ktru`, `--no-semantic-llm`, or
  `--no-llm-extraction` only for focused diagnostics. Production model names
  still come exclusively from `shared_modules/llm_models.py`.

### Structured LLM output recovery

All LangChain structured-output calls in `summary_model` request
`include_raw=True`. The strict Pydantic domain schemas remain the final
contract, but a provider response is processed in four explicit steps:

1. retain the raw tool-call arguments or JSON message content;
2. normalize safe representation differences such as Russian dates, formatted
   decimals, scalar values returned for list fields, and textual VAT markers;
3. validate against the original strict schema;
4. when necessary, remove only an invalid optional field or the single nested
   row whose required field is unusable, then validate again.

Safe, lossless conversions are recorded in LLM/VLM runtime metrics and do not
produce public warnings. Removed fields or rows produce a targeted warning
with the field path and short raw value. A document or commercial offer is not
replaced by an empty fallback merely because one nested field failed
validation.

The recovery layer also accepts a small explicit alias set for commercial
offers and VLM table rows, Russian textual dates, ruble/kopeck money phrases,
million/thousand suffixes, and Russian decimal separators. If an invalid
numeric field has a matching `*_raw` or `raw` field, the original value is
preserved there before the typed field is removed. This alias set is tied to
known schemas; it must not grow into semantic field guessing.

Schema mismatch with valid recoverable JSON is not a reason for a paid retry.
Retries remain available for transport/provider failures and responses whose
JSON is missing or genuinely malformed. If recovery still cannot produce the
strict top-level schema, the existing deterministic fallback is used.

Deterministic extraction remains authoritative during document-level LLM
canonicalization. Non-empty plan fields, ONMCK totals/items/stages, and parsed
OOZ/contract tables cannot be replaced by empty or conflicting LLM values. The
LLM fills fields that deterministic parsing did not find, primarily prose facts
such as delivery terms, addresses, warranties, and contract clauses.

ONMCK calculation tables, items, price sources, supplier prices and stages are
not sent to the general document-level LLM after deterministic/VLM parsing.
That pass may fill only missing prose/scalar fields; merge always restores the
authoritative parsed calculation data.

Contract SMP/SONKO subcontracting is selected from a complete numbered clause,
without relying on a fixed clause number. If the LLM fills a previously missing
raw clause but omits its normalized percentage, postprocessing extracts an
explicit form such as `90 (девяноста) процентов` locally before checks run.

The complete deterministic contract responsibility section is authoritative.
The general document-level LLM neither rewrites that section nor extracts
penalty/peni clauses. A dedicated penalty LLM receives the complete section,
the plan NMCK, stage presence and expected statutory thresholds. If the section
is absent, contains no penalty terms, or the dedicated call fails, the check is
manual review rather than a result inferred from a weaker general extraction.

The exact procurement name is extracted deterministically from the section
headed `Описание объекта закупки`. Supported forms include `Наименование
закупки`, `Наименование объекта закупки`, `Наименование`, and an unlabelled
first content line after the heading. In a contract, the main legal wording
remains in `contract.subject`, while the exact appendix name is stored in
`contract.embedded_purchase_description.purchase_subject` and is preferred for
comparison with the plan. The document-level LLM fills this field only when the
deterministic parser did not find it. Real document disagreement is preserved;
for example, the current `MONOBLOCK_PACK` standalone OOZ says `поставка
ноутбуков`, while its contract appendix says `поставка моноблоков`.

Stages have one comparison path: deterministic extraction and strict comparison
against the plan, followed by the dedicated stage LLM only for `manual_review`
ambiguity. Confirmed mismatches do not invoke the fallback. Stage LLM payloads
contain numbers, names, terms and quantities, but not prices; stage prices and
arithmetic remain an ONMCK check. The general semantic LLM no longer emits a
second `semantic.stages` result.

When the plan contains stages, the dedicated stage result is the only source
for delivery timing. The short `Срок поставки` line mirrors that result and the
general semantic LLM does not receive or emit a second delivery-term check. If
the plan has no stages, ordinary top-level delivery terms are compared instead.
A contract value that only refers the funding source to the structured EIS
form is manual review, not a confirmed mismatch with the plan.

Commercial-offer minimum-price checks never declare a selected minimum wrong
from an incomplete set of matched offers. If one offer row cannot be mapped to
the ONMCK position, the row remains manual review until the mapping is
unambiguous.

Unmatched commercial-offer rows may use one package-level text-only fallback
with `OPENAI_FAST_MODEL` (default `gemini-3.1-flash-lite`). The call receives
only compact ONMCK rows and already extracted offer positions. It cannot revise
deterministic matches and cannot confirm a match from price alone. Ambiguous,
invalid, or unavailable model output leaves the row at `manual_review`. The
matcher locally accepts a fenced JSON array and known field aliases only when
the source and offer row can be recovered unambiguously; checks still receive a
strict validated response.

Standalone OOZ tables that explicitly justify additional characteristics have the dedicated
VLM role `additional_characteristics_justification`. A strong title/context
signal or the column pair `дополнительная информация/характеристика` and
`обоснование` sends the complete physical table as one long PNG to the normal
VLM table path, independently of the regular complex-table limit. Weak words
such as `необходимость` or `совместимость` alone do not select a table. Failed
VLM extraction preserves a table-candidate record with a warning rather than
discarding the source.

An OOZ table may simultaneously be an item table and a justification table.
It is then processed once per role with separate `(table_id, role)` cache and
debug artifacts. Item/code/characteristic output and justification output are
merged into the same parsed table without either role replacing the other.
Justifications carry optional item name, row number, OKPD2 and KTRU links. An
unlinked justification may be applied automatically only when the target item
is unique; otherwise the affected characteristic remains manual review.

The standalone OOZ stores justification records in
`purchase_description.additional_characteristics_justifications` and is the
only source used by the KTRU check. Contract fields remain readable for schema
compatibility, but contract justification tables are not sent to this VLM role
and are not compared with the OOZ. Checks do not judge legal persuasiveness;
they verify explicit presence in the OOZ. Complete rows and decision cards stay
in `checks.json`; the public report shows one compact row per item/KTRU.

Product items also carry optional `trademark` and
`trademark_justification_text` fields. They do not affect KTRU or other legal
decisions. When present, the report lists the trademark and whether an explicit
justification was extracted.

Code completeness uses the plan as the required set and compares it with the
standalone OOZ and the contract's embedded OOZ. ONMCK is not a required code
source. KTRU roots are never inserted into the plan OKPD2 set; a code present
only as a KTRU-derived root in another document is a warning rather than an
exact OKPD2 match. Contract item tables are parsed for code completeness only;
KTRU characteristics and additional-characteristic justifications remain owned
by the standalone OOZ.

Application, contract-performance and warranty-security legality are reported
once from the plan and its NMCK. The report does not create duplicate numeric
comparisons against contract text or structured-EIS placeholders.

PP No. 1875 plan-field resolution is shared by the early plan check and the
KTRU additional-characteristic check. A special position (25, 26 or 32 of
appendix 1; 191-361 of appendix 2) forbids additional characteristics only when
the corresponding regime is confirmed in plan field 17.1 or 17.2. Missing or
ambiguous plan evidence is `manual_review`. Procurement from a single supplier
is retained as evidence but never automatically permits or forbids additional
characteristics.

Provider-specific fact wrappers such as `{raw_value, normalized_value,
confidence, evidence}` and a one-element list around a scalar fact are unwrapped
locally before Pydantic validation. This recovery does not make another paid
request. Safe conversions and discarded evidence metadata stay in runtime
metrics. The public technical-warning section groups actual lossy recovery by
document and never prints one warning per item characteristic.

Commercial-offer VLM postprocessing treats only rows with quantity or price
data as commercial positions. A total row is removed only when the remaining
priced rows independently add up to the declared offer total. Text-only rows
from technical appendices are excluded from price arithmetic and matching, with
a compact diagnostic retained in parser warnings.

## Architectural Constraints

- The web/worker contract is JSON over Celery and should remain base64-safe.
- Only `plan` is mandatory; all other documents must be handled as optional and reflected as skipped checks when absent.
- In the web pipeline, a DOCX failure for an optional document is recorded as a
  technical warning and the remaining package is checked. A failure to read the
  mandatory plan document stops the task with a user-facing error; no partial
  report is produced.
- Optional LLM checks and local PP No. 1875 registry access must degrade to
  warnings or `manual_review`, never abort an otherwise parseable web package.
- LLM/VLM ingestion must preserve raw structured output long enough to recover
  safe type differences and isolate invalid nested fields. Checks only receive
  strict validated domain models, never arbitrary raw provider dictionaries.
- Temporary uploaded files are worker-local and must be cleaned up.
- Registry checks rely on `data/parsed_tables`; changing artifact shape requires updating registry code and tests together.
- Plain-text OKPD fallback extraction must use exact OKPD2 regex matching and must not treat the OKPD-like prefix of a KTRU code as a standalone OKPD2 code.
- Plain-text KTRU extraction accepts both `KTRU:` and combined `KTRU/(OKPD2):` headings and preserves the item name through the next line/entry delimiter.
- OOZ characteristic comparison first uses structured KTRU/characteristic tables, then falls back to matching plain-text KTRU entries to characteristic rows by product name when the table has no KTRU column.
- Live KTRU behavior must degrade gracefully when network/site access fails.
- A KTRU connection failure is reported as an unavailable external check, not
  as an error in procurement documents.
- Live KTRU requests ignore ambient proxy environment variables by default;
  set `KTRU_TRUST_ENV_PROXY=1` only when the configured proxy is intentional.
- The active public KTRU card route is `ktru-description.html`; the removed
  `commonInfo.html` route is retained only as a compatibility fallback.
- KTRU TLS verification can use an explicit Minцифры PEM bundle through
  `KTRU_CA_BUNDLE`. `KTRU_VERIFY_TLS=0` is available only as an explicit
  diagnostic opt-out and must not be the production default.
- The plan key-value table is a deterministic source for aggregate quantity
  and OKPD2/KTRU lists, including when its LLM extraction fails validation.
- KTRU characteristic comparison canonicalizes visually identical Latin and
  Cyrillic symbols. Document-local `item_id` values must never be used for
  matching positions across documents.
- Missing delivery periods or places trigger a focused structured LLM repair
  for that document. It preserves repeated occurrences with distinct evidence
  and is skipped when both fields are already populated.
- LLM prompts define strict output formats consumed by HTML and DOCX rendering; prompt changes can affect report formatting.
- The report renderer only understands a small tag set. New tags require changes in both HTML rendering expectations and `build_result_docx_bytes`.
- Avoid coupling UI text, Celery payload keys, and AIService parameter names accidentally; they form the user-facing document contract.

## Known Verification Gaps

- There is no automated end-to-end test for `AIService.process_query`; it requires document fixtures plus mocked LLM, registry, and network calls.
- The OKPD/KTRU plain-text fallbacks and OOZ characteristic name-matching fallback currently rely primarily on manual document-pack verification.
- Live KTRU tests can skip on network errors, so a green local run does not prove that `zakupki.gov.ru` integration is available.
- LLM output-format compliance is prompt-driven and is not covered by deterministic contract tests.

## Documentation And Change Rules

- Documentation changes alone are low risk.
- Prompt, retrieval, report, registry, or validation changes are medium risk and need focused review plus tests or a clear test-gap note.
- Pipeline, data contract, parser, and cross-component interface changes are high risk and require Explore -> Planning -> Review -> Patching -> Test.
- After changing architecture, pipeline behavior, component contracts, report format, or validation rules, update `AGENTS.md` or this guide.
- If checking logic or report output changes, explicitly document risks, side effects, and potential regressions in the final response.
