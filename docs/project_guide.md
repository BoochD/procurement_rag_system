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

## Architectural Constraints

- The web/worker contract is JSON over Celery and should remain base64-safe.
- Only `plan` is mandatory; all other documents must be handled as optional and reflected as skipped checks when absent.
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
