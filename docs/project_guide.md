# Project Guide

This guide is the compact system map for future agent work. Code is the source of truth when this document and implementation disagree.

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
- `latest_model/docs_parsing.py`: extracts plan, contract, OOZ, explanatory note, ONMCK, characteristics, and price information from documents.
- `shared_modules/parser_functions.py`: low-level `.docx` parsing helpers, table normalization, OKPD/KTRU parsing, and ONMCK price extraction.
- `latest_model/check_registry.py`: bridges parsed plan/OOZ data to registry checks and characteristic comparison.
- `services/procurement_reference_registry.py`: local PP 1875 lookup, OKPD matching, live KTRU fetch/parsing, and KTRU characteristic extraction.
- `shared_modules/retriever.py`: text splitting plus BM25/FAISS retriever creation. The active RAG path uses `BM25TextRetriever`.
- `latest_model/rag_processing.py`: invokes the RAG prompt per plan point with retrieved context.
- `latest_model/smart_processing.py`: invokes the smart comparison prompt for OKPD/KTRU/product/quantity checks.
- `latest_model/prompts.py`: prompt contracts and output formats for default, smart, and RAG checks.
- `shared_modules/llm_models.py`: OpenAI-compatible and GigaChat client/model factories.
- `shared_modules/embeddings.py`: embedding model factories for optional FAISS retrieval.

## Pipeline Details

`AIService.process_query` currently performs these stages:

1. Determine available and missing documents.
2. Parse plan table values, treating the plan as the baseline.
3. Extract focused points from contract, OOZ, zapiska, and ONMCK.
4. Check KTRU against live `zakupki.gov.ru` data and OKPD against local PP 1875 data.
5. Run smart LLM comparison for OKPD, KTRU, product names, and quantities.
6. Compare OOZ characteristics against KTRU characteristics and PP 1875 extra-characteristic rules.
7. Build BM25 retrieval over uploaded document text and run RAG checks for plan delivery/price-like points.
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

## Architectural Constraints

- The web/worker contract is JSON over Celery and should remain base64-safe.
- Only `plan` is mandatory; all other documents must be handled as optional and reflected as skipped checks when absent.
- Temporary uploaded files are worker-local and must be cleaned up.
- Registry checks rely on `data/parsed_tables`; changing artifact shape requires updating registry code and tests together.
- Plain-text OKPD fallback extraction must use exact OKPD2 regex matching and must not treat the OKPD-like prefix of a KTRU code as a standalone OKPD2 code.
- OOZ characteristic comparison first uses structured KTRU/characteristic tables, then falls back to matching plain-text KTRU entries to characteristic rows by product name when the table has no KTRU column.
- Live KTRU behavior must degrade gracefully when network/site access fails.
- LLM prompts define strict output formats consumed by HTML and DOCX rendering; prompt changes can affect report formatting.
- The report renderer only understands a small tag set. New tags require changes in both HTML rendering expectations and `build_result_docx_bytes`.
- Avoid coupling UI text, Celery payload keys, and AIService parameter names accidentally; they form the user-facing document contract.

## Documentation And Change Rules

- Documentation changes alone are low risk.
- Prompt, retrieval, report, registry, or validation changes are medium risk and need focused review plus tests or a clear test-gap note.
- Pipeline, data contract, parser, and cross-component interface changes are high risk and require Explore -> Planning -> Review -> Patching -> Test.
- After changing architecture, pipeline behavior, component contracts, report format, or validation rules, update `AGENTS.md` or this guide.
- If checking logic or report output changes, explicitly document risks, side effects, and potential regressions in the final response.
