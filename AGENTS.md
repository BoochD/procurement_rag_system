# Agent Guide

This repository is a Procurement RAG Legal Checker. It accepts procurement document packs in a Django UI, sends them to a Celery worker, extracts structured data from `.docx` files, checks registry data, runs LLM-assisted checks, and returns both an HTML result and a generated `.docx` report.

Current active development focus: the new independent `summary_model` extraction layer. The immediate goal is reliable parsing of procurement documents into strict typed schemas, using deterministic table parsers first and LLM extraction only to fill document-specific text fields that table/rule parsing cannot robustly extract.

Read this file and `docs/project_guide.md` before making changes.

## Working Modes

- Explore mode: inspect files, commands, tests, and data without changing anything.
- Planning mode: prepare a scoped plan before changes, especially for medium- and high-risk work.
- Patching mode: edit only the files needed for the approved change and keep unrelated refactors out.
- Review mode: check the change for architecture, data-contract, prompt, and report-format risks.
- Test mode: run the smallest relevant checks first; broaden tests when shared behavior or pipeline contracts changed.

For high-risk tasks, use this sequence: Explore -> Planning -> Review -> Patching -> Test.

Always reread this `AGENTS.md` at the start of a new coding task or after context compaction. If it conflicts with older assumptions, this file wins unless the user explicitly says otherwise.

## Engineering Style

- Prefer the simplest implementation that preserves the contract and is easy to inspect in runtime artifacts.
- Follow a Ponytail-style bias: keep the code tied back, neat, and out of the way. Do not add abstractions, frameworks, generic engines, or multi-layer configuration unless they remove real complexity now.
- Do not over-engineer speculative future needs. Leave clear extension points, but implement only the behavior required by the current document types and fixtures.
- Make parsers boring and explicit: small functions, readable names, deterministic transformations, and visible warnings when confidence is low.
- Prefer structured intermediate artifacts over clever prompt logic. If a table can be parsed deterministically, parse it before involving an LLM.
- Do not hide messy input behind broad catch-all logic. Preserve debug evidence so bad parsing can be inspected quickly.

## Risk Levels

- Low risk: documentation, comments, focused tests, small local fixes with no pipeline contract change.
- Medium risk: prompts, retrieval behavior, report text/structure, registry checks, new validations.
- High risk: document-processing pipeline, data structures passed between components, Celery/Django interfaces, parsing logic that affects multiple document types, report generation contracts.

## Architecture Map

- `web/`: Django project and UI. `web/fileprocessor/views.py` accepts uploads, serializes files as base64, submits Celery task `rag_worker.process_document_query`, polls task status, and serves generated reports.
- `celery-worker/`: Celery worker. `tasks.py` validates uploaded documents, writes temporary files, calls `latest_model.ai_service.get_ai_service().process_query(...)`, and builds the `.docx` result.
- `latest_model/`: main AI/RAG pipeline. `ai_service.py` orchestrates parsing, registry checks, smart LLM checks, RAG checks, characteristic checks, price checks, and final report assembly.
- `summary_model/`: independent structured extraction pipeline under development. It is invoked through its Python API or CLI and is not connected to web/Celery yet.
- `summary_model/extraction_pipeline.py`: new parser-first extraction entrypoint. It builds `ProcurementPackageExtraction` from DOCX documents without running external registry checks, legal validation, report generation, or package-level semantic LLM analyzers.
- `summary_model/extraction_cli.py`: CLI for the new extraction layer. It writes `extraction_result.json`, per-document JSON, parsed-table JSON, and table debug artifacts.
- `summary_model/extraction_models.py`: strict typed extraction schemas for procurement documents and normalized values.
- `summary_model/tables/`: deterministic table layer on top of `TableIR v4`; it classifies tables, builds logical rows, emits `compact_markdown` and `compact_json`, and exports physical/logical/compact debug artifacts.
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
- The LLM provider is OpenAI-compatible via `OPENAI_API_KEY` and `OPENAI_BASE_URL`; model names are fixed in `shared_modules/llm_models.py` and must not be overridden elsewhere.
- Russian text appears in code, prompts, templates, and tests. Preserve existing encoding and verify readable output when touching text-heavy files.
- The new extraction layer must remain independent from web/Celery until a separate explicit switch task.
- New extraction work must not call PP 1875, live KTRU, or package-level LLM analyzers unless the user explicitly asks for validation/report integration.
- For the new extraction layer, table parsing is the primary source for items, quantities, units, supplier prices, characteristics, and key-value plan fields.
- LLM extraction should receive compact table artifacts plus relevant text, not raw dense tables or debug `logical_rows`.
- `logical_rows` are debug/parser artifacts; final document schemas should use deduplicated item structures such as `item -> characteristics[]`.
- Contract product description tables and contract specification/price tables are separate concepts and must not be merged as one OOZ table.
- Signature/approval tables in fixed documents such as the plan application should be ignored or marked as non-working tables, not fed into extraction schemas or LLM prompts.

## Testing Rules

- Prefer `pytest tests` for the full suite.
- For local registry work, start with `pytest tests/okpd_tests`.
- For KTRU parser/validation work, start with `pytest tests/ktru_tests/test_parsing.py tests/ktru_tests/test_validation.py`.
- Live KTRU tests may require network and should be treated separately from deterministic local tests.
- For every new feature, add a focused test when practical. If testing is not practical, explain why in the final report.
- For LLM logic, prefer unit tests, fixture-based tests, monkeypatching/mocking external calls, and pipeline smoke tests over live model calls.
- For extraction-layer work, start with deterministic tests and CLI smoke runs. Do not use paid/live LLM calls just to verify table parsing.
- Useful extraction smoke command: `python -m summary_model.extraction_cli --input-dir "doci_primery/PACK_06_05" --output-dir "runtime/extraction_runs/PACK_06_05"`.

## Documentation Rules

Update documentation when a change affects architecture, pipeline behavior, component contracts, report format, validation rules, commands, or test strategy.

If a task changes document-checking logic or report output, explicitly review risks, side effects, and possible regressions.

## Forbidden Actions Without Explicit Need

- Do not rewrite large project areas.
- Do not do broad refactors together with feature work.
- Do not change several independent subsystems in one task.
- Do not remove existing behavior without impact analysis.
- Do not install dependencies, run migrations, start Docker services, or perform git write operations unless explicitly requested.
- Do not replace deterministic parsers with LLM prompts when the table structure can be parsed directly.
- Do not add runtime feature flags, caches, databases, or new service layers for the extraction pipeline unless the user explicitly approves that complexity.

## Useful Commands

- Run app stack: `docker-compose up --build -d`
- Stop app stack: `docker-compose down`
- Django dev server only: `python web/manage.py runserver 0.0.0.0:8000`
- Worker only: `cd celery-worker && celery -A celery_app worker --loglevel=info --pool=threads --concurrency=4`
- Full tests: `pytest tests`
- Local OKPD tests: `pytest tests/okpd_tests`
- Deterministic KTRU parser/validation tests: `pytest tests/ktru_tests/test_parsing.py tests/ktru_tests/test_validation.py`
- Summary pipeline without LLM/network: `python -m summary_model.cli --input-dir <pack> --output-dir <output> --no-llm --no-external`
- New extraction pipeline: `python -m summary_model.extraction_cli --input-dir <pack> --output-dir <output>`
