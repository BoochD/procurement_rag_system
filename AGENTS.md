# Agent Guide

This repository is a Procurement RAG Legal Checker. It accepts procurement document packs in a Django UI, sends them to a Celery worker, extracts structured data from `.docx` files, checks registry data, runs LLM-assisted checks, and returns both an HTML result and a generated `.docx` report.

Read this file and `docs/project_guide.md` before making changes.

## Working Modes

- Explore mode: inspect files, commands, tests, and data without changing anything.
- Planning mode: prepare a scoped plan before changes, especially for medium- and high-risk work.
- Patching mode: edit only the files needed for the approved change and keep unrelated refactors out.
- Review mode: check the change for architecture, data-contract, prompt, and report-format risks.
- Test mode: run the smallest relevant checks first; broaden tests when shared behavior or pipeline contracts changed.

For high-risk tasks, use this sequence: Explore -> Planning -> Review -> Patching -> Test.

## Risk Levels

- Low risk: documentation, comments, focused tests, small local fixes with no pipeline contract change.
- Medium risk: prompts, retrieval behavior, report text/structure, registry checks, new validations.
- High risk: document-processing pipeline, data structures passed between components, Celery/Django interfaces, parsing logic that affects multiple document types, report generation contracts.

## Architecture Map

- `web/`: Django project and UI. `web/fileprocessor/views.py` accepts uploads, serializes files as base64, submits Celery task `rag_worker.process_document_query`, polls task status, and serves generated reports.
- `celery-worker/`: Celery worker. `tasks.py` validates uploaded documents, writes temporary files, calls `latest_model.ai_service.get_ai_service().process_query(...)`, and builds the `.docx` result.
- `latest_model/`: main AI/RAG pipeline. `ai_service.py` orchestrates parsing, registry checks, smart LLM checks, RAG checks, characteristic checks, price checks, and final report assembly.
- `shared_modules/`: reusable parsing, retrieval, LLM, and embedding adapters.
- `services/`: procurement registry integration. `procurement_reference_registry.py` reads local PP 1875 data and fetches/parses live KTRU pages.
- `data/parsed_tables/`: local parsed registry artifacts used by OKPD/PP 1875 checks.
- `tests/`: pytest coverage for local registry artifacts, OKPD matching, KTRU parsing/validation, and live KTRU behavior.

## Critical Invariants

- The Celery task name is `rag_worker.process_document_query`; Django and worker must agree on it.
- Uploaded documents are passed from web to worker as JSON-serializable dictionaries with `key`, `label`, `name`, and `content_b64`.
- `plan` is the only mandatory uploaded document in both web and worker code.
- Worker output must include `ai_response`; downloadable reports depend on `result_file_b64` and `result_file_name`.
- Report markup uses simple tags: `<b>`, `<u>`, `<ins>`, `<ok>`, `<warn>`, `<error>`. The HTML template renders these tags and `build_result_docx_bytes` maps them to Word formatting.
- `latest_model.ai_service.AIService.process_query` is the main orchestration boundary. Avoid bypassing it unless the task explicitly changes the pipeline.
- `data/parsed_tables/pp1875.sqlite` and `okpd_index.json` are runtime inputs for registry checks.
- Live KTRU checks depend on `zakupki.gov.ru` and can be flaky or unavailable.
- Default LangChain LLM provider is OpenAI-compatible via `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `OPENAI_MODEL`.
- Russian text appears in code, prompts, templates, and tests. Preserve existing encoding and verify readable output when touching text-heavy files.

## Testing Rules

- Prefer `pytest tests` for the full suite.
- For local registry work, start with `pytest tests/okpd_tests`.
- For KTRU parser/validation work, start with `pytest tests/ktru_tests/test_parsing.py tests/ktru_tests/test_validation.py`.
- Live KTRU tests may require network and should be treated separately from deterministic local tests.
- For every new feature, add a focused test when practical. If testing is not practical, explain why in the final report.
- For LLM logic, prefer unit tests, fixture-based tests, monkeypatching/mocking external calls, and pipeline smoke tests over live model calls.

## Documentation Rules

Update documentation when a change affects architecture, pipeline behavior, component contracts, report format, validation rules, commands, or test strategy.

If a task changes document-checking logic or report output, explicitly review risks, side effects, and possible regressions.

## Forbidden Actions Without Explicit Need

- Do not rewrite large project areas.
- Do not do broad refactors together with feature work.
- Do not change several independent subsystems in one task.
- Do not remove existing behavior without impact analysis.
- Do not install dependencies, run migrations, start Docker services, or perform git write operations unless explicitly requested.

## Useful Commands

- Run app stack: `docker-compose up --build -d`
- Stop app stack: `docker-compose down`
- Django dev server only: `python web/manage.py runserver 0.0.0.0:8000`
- Worker only: `cd celery-worker && celery -A celery_app worker --loglevel=info --pool=threads --concurrency=4`
- Full tests: `pytest tests`
- Local OKPD tests: `pytest tests/okpd_tests`
- Deterministic KTRU parser/validation tests: `pytest tests/ktru_tests/test_parsing.py tests/ktru_tests/test_validation.py`

