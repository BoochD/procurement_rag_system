# Agent Handoff: Procurement RAG Legal Checker

This file is both a handoff prompt and an operating manual for the next coding
agent. Read `AGENTS.md`, this file, and `docs/project_guide.md` before changing
code. When documentation disagrees with implementation, inspect the code and
update the documentation as part of the same task.

## Copy-Paste Handoff Prompt

You are taking over active development of the Procurement RAG Legal Checker in
`procurement_rag_system`. The production path is the `summary_model` pipeline,
connected to Django/Celery through `summary_model.web_service`. Your goal is to
make extraction, legal checks, commercial-offer comparison, and report output
reliable on heterogeneous Russian procurement document packs.

Work as a careful senior engineer:

1. Read `AGENTS.md`, `docs/agent_handoff.md`, and `docs/project_guide.md`.
2. Check `git status --short --branch` before touching files. The worktree may
   contain user-owned fixture changes. Never revert, stage, or commit them
   unless explicitly asked.
3. Treat the plan application (ПГ) as the comparison baseline, but distinguish
   independent fields inside it: general delivery term, contract execution
   term, and the structured stage table are not interchangeable.
4. Inspect source documents and runtime artifacts before proposing a fix. Do
   not infer a parser failure only from report wording.
5. Prefer a small correction in an existing parser, prompt, merge, check, or
   renderer. Do not add generic recovery layers or fixture-specific guards.
6. Deterministic code owns exact arithmetic, dates, codes, field identity,
   aggregation, and final validation. LLM/VLM owns reading ambiguous text and
   visually complex tables. Never ask an LLM to recompute values that code can
   calculate exactly.
7. A structured-output response may still arrive wrapped, partially invalid,
   or provider-mutated. Preserve useful data locally and visibly, but do not
   let a global normalizer silently delete domain records.
8. Run the smallest relevant test first, then a deterministic pack smoke, then
   a live pack only when model behavior is part of the change. Do not use live
   KTRU unless the task is specifically about KTRU/PP 1875.
9. Compare the final `report.txt` with `checks.json` and
   `extraction_result.final.json`. A successful CLI exit is not proof that the
   result is correct.
10. Keep public report language in Russian. Keep technical warnings concise in
    the report and preserve full diagnostics in JSON artifacts.

Before declaring a task complete, state exactly what was changed, which tests
or packs were run, what was not run, and any remaining uncertainty. Suggest a
focused commit message after a meaningful milestone, but do not commit or push
without an explicit user request.

## What The System Does

The application receives a procurement document pack and produces:

- normalized typed document schemas;
- deterministic and LLM-assisted validation results;
- live/local KTRU and PP 1875 results when enabled;
- comparisons against the plan application;
- ONMCK arithmetic and supplier-price checks;
- commercial-offer extraction and matching;
- a Russian text/HTML report and a generated DOCX report;
- detailed JSON and VLM/LLM diagnostic artifacts for CLI runs.

The mandatory upload is the plan application. Other supported roles are:

- `plan`: заявка в план-график;
- `obrasheniye`: обращение о проведении закупки;
- `onmck`: обоснование НМЦК/ОЦК;
- `ooz`: описание объекта закупки;
- `contract`: проект контракта;
- `zapiska`: пояснительная записка;
- `commercial_offer`: one or more commercial offers.

DOCX is the primary format for plan, ONMCK, OOZ, and contract. PDF is supported
for commercial offers, requests, and explanatory notes. Commercial offers also
accept supported image formats in the CLI path.

## Production Flow

The production web flow is:

1. `web/fileprocessor/views.py` accepts uploads and sends JSON-serializable
   dictionaries with `key`, `label`, `name`, and `content_b64`.
2. Celery task `rag_worker.process_document_query` in
   `celery-worker/tasks.py` decodes files into a temporary directory.
3. The worker calls `summary_model.web_service.process_uploaded_documents`.
4. `summary_model.web_service` splits DOCX, commercial-offer media, and short
   PDF documents, then orchestrates extraction and checks.
5. `summary_model.extraction_pipeline.extract_package` performs parser-first
   extraction into `ProcurementPackageExtraction`.
6. `summary_model.vlm_fallback.VlmFallbackRepairer` optionally repairs complex
   table roles. Commercial offers and short PDFs use their dedicated VLM paths.
7. Document-level LLM extraction fills missing text/scalar fields and merges
   with deterministic results. Deterministic table data remains authoritative.
8. Semantic, stage, penalty, and commercial-offer matcher LLM checks run when
   enabled. Their outputs are compact check results, not replacements for
   domain schemas.
9. KTRU/PP 1875 checks run when enabled.
10. `summary_model.checks.runner.run_checks` assembles strict, external, and
    LLM-assisted results.
11. `summary_model.checks.report.build_checks_report_text` renders the report.
12. The Celery worker applies report markup and creates `analysis_result.docx`.

The closest CLI equivalent to production is:

```powershell
python -W ignore -B -m summary_model.full_pipeline_cli `
  --input-dir <pack> `
  --output-dir <output>
```

Do not confuse this with `python -m summary_model.cli`. That module runs an
older independent `summary_model.service` flow and is useful only when a task
explicitly targets that API. For web parity use `full_pipeline_cli`.

One default differs and must be checked deliberately: `full_pipeline_cli`
enables table VLM unless `--no-vlm-tables` is passed, while the Celery worker
enables it only when `SUMMARY_WITH_VLM_TABLES=1` (worker default is off).
Commercial-offer and short-document VLM default to on in both paths. For exact
deployment parity, inspect the worker environment and pass matching CLI flags.

## Key Modules And Ownership

### Ingestion and document classification

- `summary_model/ingestion/docx_reader.py`: ordered paragraphs, top-level
  tables, and tables nested inside DOCX cells. Nested tables matter for plan
  stage tables embedded in a form cell.
- `summary_model/ingestion/table_normalizer.py`: physical table normalization.
- `summary_model/classification/document_classifier.py`: document type.
- `summary_model/full_pipeline_cli.py`: filename-based upload-role discovery for
  fixture directories. Always inspect `inputs.json`; a wrongly ignored or
  duplicated file invalidates the run.

### Table layer

- `summary_model/tables/table_classifier.py`: table role classification using
  both content and document type.
- `summary_model/tables/table_logical_rows.py`: logical rows and column mapping.
- `summary_model/tables/table_compactor.py`: compact payloads for LLM/VLM.
- `summary_model/tables/debug_export.py`: physical/logical/compact artifacts.
- `summary_model/vlm_fallback.py`: role-specific VLM prompts, response recovery,
  merge, cache/debug artifacts, and metrics.

Important table rules:

- document type is a strong classification signal;
- ONMCK calculation tables exist only in ONMCK documents;
- simple non-staged price matrices may be parsed deterministically;
- complex or staged ONMCK tables should be classified correctly and sent to
  the ONMCK VLM role rather than patched with layout-specific arithmetic;
- OOZ item/characteristic and additional-justification roles may both apply to
  the same physical table; role-specific VLM calls and cache keys must remain
  separate;
- signature tables must not become stage, item, or price tables;
- nested stage tables in plan/contract cells must not be lost through
  `cell.text`-only ingestion.

### Typed extraction and merge

- `summary_model/extraction_models.py`: production typed schemas, including
  plan, OOZ, ONMCK, contract, commercial offers, stages, prices, and evidence.
- `summary_model/extraction_pipeline.py`: deterministic document-specific
  extraction and package assembly.
- `summary_model/extraction/llm_payloads.py`: data sent to general document LLM.
- `summary_model/extraction/llm_prompts.py`: document-specific extraction rules.
- `summary_model/extraction/llm_document_extractor.py`: structured call, local
  recovery, and merge with deterministic schemas.
- `summary_model/extraction/structured_recovery.py`: provider-response recovery.

Merge invariant: preserve a reliable deterministic value. Let LLM/VLM fill a
missing value or replace a value only where the role explicitly owns the whole
complex table. Do not pass already repaired ONMCK item/price/stage matrices to a
general LLM and let it rewrite them.

### Dedicated visual extraction

- `summary_model/commercial_offer_vlm.py`: PDF/image commercial offers.
- `summary_model/short_document_vlm.py`: short PDF request and explanatory note.
- `summary_model/vlm_lab/`: isolated single-table experiments.

### Checks

- `summary_model/checks/runner.py`: deterministic checks and result aggregation.
- `summary_model/checks/semantic_llm.py`: semantic comparisons without exact
  arithmetic.
- `summary_model/checks/stage_llm.py`: supplementary stage interpretation.
- `summary_model/checks/penalty_llm.py`: penalty clause review.
- `summary_model/checks/commercial_offer_llm.py`: unresolved offer-row matching.
- `summary_model/checks/ktru_adapter.py`: KTRU and PP 1875 integration.
- `summary_model/checks/national_regime.py`: national-regime logic.
- `summary_model/checks/additional_characteristics.py`: KTRU characteristics and
  OOZ justifications.
- `summary_model/checks/report.py`: public report rendering and deduplication.

LLM-assisted checks return a compact check envelope with status, explanation,
and evidence. They must not be converted back into extraction domain records
and sent through a broad schema recovery pass.

### Web and deployment

- `web/fileprocessor/views.py`: upload, task polling, result/download response.
- `celery-worker/tasks.py`: production task and DOCX report generation.
- `summary_model/web_service.py`: actual production orchestration boundary.
- `summary_model/report_markup.py`: report status markup.
- `docker-compose.yml`, `Dockerfile`: deployment.
- `shared_modules/llm_models.py`: provider URL, model names, API client factory.

Relevant environment variables include:

- `OPENAI_API_KEY`, `OPENAI_BASE_URL`;
- `OPENAI_MODEL`, `OPENAI_NANO_MODEL`, `OPENAI_VLM_MODEL`,
  `OPENAI_FAST_MODEL`;
- `SUMMARY_WITH_VLM_TABLES`;
- `SUMMARY_WITH_VLM_COMMERCIAL_OFFERS`;
- `SUMMARY_WITH_VLM_SHORT_DOCUMENTS`;
- `SUMMARY_LLM_CONCURRENCY`;
- `SUMMARY_VLM_MAX_TABLES_PER_DOCUMENT`;
- `SUMMARY_VLM_MAX_COMMERCIAL_OFFER_PAGES`;
- `SUMMARY_VLM_MAX_SHORT_DOCUMENT_PAGES`;
- `KTRU_TIMEOUT_SECONDS`, and locally `KTRU_VERIFY_TLS` where applicable.

Never print API-key values into logs, reports, commits, or chat responses.
`full_pipeline_cli` loads `web/.env` when `python-dotenv` is installed.

## Domain Invariants That Must Stay Separate

### Plan baseline and dates

The plan is the package baseline, but its fields are separate facts:

- general delivery/service term;
- contract execution term;
- structured execution-stage table;
- inline stage list inside the general delivery term.

Required behavior:

- compare general delivery terms with general delivery terms;
- compare structured stages with structured stages by stage number, service
  start/end, quantity/result, and price where available;
- separately report contradictions inside the plan, such as six inline stages
  versus five table stages or different dates for the same stage;
- do not inject an incomplete inline-only stage into the structured plan-stage
  baseline;
- do not substitute delivery term for contract execution term;
- distinguish service end from the later legal execution/acceptance end.

### ONMCK

The system should extract and verify:

- item/stage rows, quantities, and units;
- every supplier/source and its letter number/date;
- unit price and row total per source;
- selected minimum price;
- supplier totals and final NMCK;
- arithmetic, minimum choice, supplier-price completeness, coefficient of
  variation, and staged totals.

For a single merged `Цена услуги` column with quantity one, the same visible
value is both unit price and row total. For paired columns, keep unit price and
row total distinct. The printed total row must still be read and compared with
calculated aggregates; do not replace verification with calculation alone.

### Commercial offers

The commercial-offer flow is:

1. VLM extracts supplier, requisites, subject, VAT, total, and visible rows.
2. Deterministic matching uses source identity, names/codes, quantity/unit, and
   prices where meaningful.
3. The matcher LLM receives only unresolved pairs and may confirm a row only
   with non-price support.
4. Strict code validates quantity, unit, unit price, row arithmetic, offer
   total, source number/date, VAT arithmetic, and ONMCK correspondence.

Price comparison includes both goods and service rows. The separate
`Количество и единицы измерения` table is for non-stage OOZ item rows; stage
quantities stay in price/quantity and dedicated stage comparisons. Do not let a
unit/OOZ issue overwrite an otherwise correct price-match status.

### OOZ, KTRU, and additional characteristics

- Detailed characteristics and their justifications use the standalone OOZ as
  the source of truth.
- A physical OOZ table may be processed once as items/characteristics and again
  as justifications. Cache and artifact identity must include the role.
- Justifications must be linked to the correct item, not copied to every item
  sharing a KTRU code.
- Contract-embedded OOZ data may support contract code/address completeness,
  but complex characteristic legality remains based on standalone OOZ.
- Trademark and trademark justification are informational fields and do not
  change legal check status.
- Live KTRU registry existence should check codes from the plan. Characteristic
  comparison requires OOZ item data and is a separate result.
- PP 1875 report text must state the actual appendix/position and whether the
  relevant plan field is meaningfully filled, not merely contain its heading.

### Contract-specific checks

- Penalties use a dedicated LLM check with cited clauses and deterministic legal
  expectations. If the plan does not require SMP/SONKO subcontracting, absence
  of a non-attraction fine is not an error.
- Standard SMP/SONKO terms under Government Decree No. 1466 are required only
  when the plan establishes the subcontracting requirement/percentage.
- Warranty matching requires actual warranty values. A contract reference to
  an appendix alone is not proof of equality.
- Contract attachments embedded in the same DOCX count as present. Do not
  require separate uploaded files for an appendix already physically embedded.
- Delivery addresses should be read from explicit markers and from an embedded
  OOZ section. Exact house/building differences are conflicts; formatting and
  administrative-prefix differences alone are not.

## How To Investigate A Document Pack

### 1. Inventory files and expected roles

```powershell
$pack=(Resolve-Path -LiteralPath "doci_primery\<PACK>").Path
Get-ChildItem -LiteralPath $pack -File | Sort-Object Name | Select-Object Name,Length
```

Check filenames, duplicate roles, generated `analysis_result*.docx` files, and
temporary Word files. After a CLI run, inspect `inputs.json` first.

### 2. Inspect source content before running models

For DOCX, inspect paragraphs, ordinary tables, merged headers, and nested tables.
`python-docx` `cell.text` does not expose nested table contents; use the existing
`read_docx`/table debug path or walk cell XML/tables explicitly. For PDF, render
or inspect the actual pages; text extraction alone may miss visual columns.

Before a regression run, write a temporary expectation file such as:

```text
runtime/expected_<pack>.md
```

Record deliberate source errors with document, section/table, expected status,
and expected explanation. This prevents accepting a plausible but wrong report.

### 3. Run production-parity CLI

Full live run without KTRU:

```powershell
$env:KTRU_VERIFY_TLS="0"
$pack=(Resolve-Path -LiteralPath "doci_primery\<PACK>" -ErrorAction Stop).Path
$out="runtime\full_pipeline_runs\<PACK>_live_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
New-Item -ItemType Directory -Path $out -Force | Out-Null
python -W ignore -B -m summary_model.full_pipeline_cli `
  --input-dir $pack `
  --output-dir $out `
  --no-ktru 2>&1 | Tee-Object -FilePath "$out\console.log"
```

Full live run with KTRU:

```powershell
python -W ignore -B -m summary_model.full_pipeline_cli `
  --input-dir $pack `
  --output-dir $out `
  --ktru-timeout 30 2>&1 | Tee-Object -FilePath "$out\console.log"
```

Deterministic smoke without paid/network layers:

```powershell
python -W ignore -B -m summary_model.full_pipeline_cli `
  --input-dir $pack `
  --output-dir $out `
  --no-llm-extraction `
  --no-semantic-llm `
  --no-ktru `
  --no-vlm-tables `
  --no-vlm-commercial-offers `
  --no-vlm-short-documents
```

Use the explicit environment interpreter when PowerShell resolves the wrong
Python (a common symptom is `ModuleNotFoundError: No module named 'docx'`):

```powershell
$py="C:\Users\egorg\anaconda3\envs\myenv\python.exe"
& $py -B -m summary_model.full_pipeline_cli --input-dir $pack --output-dir $out --no-ktru
```

### 4. Read artifacts in this order

1. `run.json`: completed/failed, duration, options, error.
2. `inputs.json`: selected and ignored files and assigned roles.
3. `warnings.json` and `report_with_warnings.txt`: provider/recovery failures.
4. `metrics.json`: calls, duration, tokens, errors, recovery counts.
5. `extraction_result.final.json`: what the pipeline actually extracted.
6. `checks.json`: check IDs, statuses, evidence, and full details.
7. `report.txt`: public wording, ordering, tables, duplicates, and omissions.
8. `vlm_tables/`: prompt role, raw response, normalized response, and visual
   source for any suspect table.

`report.txt` is the clean public report. `report_with_warnings.txt` appends the
technical warnings that the web worker also exposes to the user. Inspect both.

Diagnose in that order. A wrong report can originate in ingestion,
classification, extraction, merge, check logic, aggregation, or rendering.
Do not edit a prompt until the failing layer is identified.

### 5. Compare expected and actual outcomes

For every deliberate fixture error answer:

- Was the source value extracted?
- Was it attached to the correct document/item/stage/source?
- Did the check compare the correct fields?
- Is the status legally and technically appropriate?
- Does the report explain the exact mismatch in Russian?
- Is the same concept duplicated elsewhere with a conflicting status?

## Isolated Labs And Rechecks

### Extraction only

```powershell
python -B -m summary_model.extraction_cli `
  --input-dir <input> `
  --output-dir <output>
```

Add `--with-vlm` for complex tables and `--with-llm` for document-level LLM
extraction. This CLI is useful for table/debug artifacts but is not a complete
report run.

### Re-run checks over an existing package JSON

```powershell
python -B -m summary_model.checks_cli `
  --input <run>\extraction_result.final.json `
  --output-dir <output>
```

Add `--with-llm` or `--with-ktru` only when needed. This avoids paying to
re-extract documents while iterating on check/report logic.

### ONMCK lab

All ONMCK fixtures:

```powershell
python -B -m summary_model.nmck_lab.run `
  --input-root "doci_primery" `
  --output-dir "runtime\nmck_lab_all" `
  --live
```

For one document, copy only that ONMCK DOCX into a temporary input directory and
run the same command. Inspect `summary.json`, per-document `nmck.json`,
`tables.json`, `checks.json`, and `vlm_tables` artifacts.

### Commercial-offer lab

VLM extraction only:

```powershell
python -B -m summary_model.commercial_offer_lab.run `
  --input <offer1.pdf> <offer2.pdf> <offer3.pdf> `
  --output-dir "runtime\commercial_offer_lab"
```

Production matcher and ONMCK/OOZ comparison:

```powershell
python -B -m summary_model.commercial_offer_lab.run `
  --input <offer1.pdf> <offer2.pdf> <offer3.pdf> `
  --package <run>\extraction_result.final.json `
  --output-dir "runtime\commercial_offer_lab_with_matcher"
```

Inspect raw/normalized matcher responses and accepted decisions. Equal price
alone must never confirm a row.

## Fixture Packs And Their Main Regression Value

The fixture directories are manual regression material. Inspect their current
contents because files are edited over time.

- `закупка_для_примера_расширение_ЦОД_с_лицензиями`: largest mixed pack;
  staged ONMCK with goods inside a service stage, complex OOZ characteristics,
  justifications, trademarks, three commercial offers, warranties, penalties,
  KTRU/PP 1875, and deliberately erroneous variants.
- `new_ex`: compact end-to-end regression pack with three offers and deliberate
  plan/stage differences; useful for offer quantity/unit tables, source
  requisites, attachments, addresses, and contract execution term.
- `ТП_новая закупка`: ONMCK consisting entirely of service stages with one
  supplier price column each, nested plan stage table, short explanatory-note
  PDF, three offers, and deliberate contradictions between plan fields.
- `закупка_ЭМ_поставка`: electronic-store/single-supplier procurement method,
  national-regime plan fields, embedded contract appendices, and ordinary term
  comparison.
- `Cartridges`: unusual OOZ/justification table layout and cartridge-specific
  characteristics.
- `PACK_06_05`: additional-characteristic justifications and a simple ONMCK
  matrix that previously exposed supplier-total recovery problems.
- `SHINY_PNEVMA_PACK`: three commercial offers and strict subject matching.
- `TRANSIVER_PACK`: three commercial offers, a compact ONMCK supplier matrix,
  address extraction, and code-format differences.
- `MONOBLOCK_PACK`: simple ONMCK totals/recovery and security percentage edge
  cases.
- `Данные для тестирования 01.06.26`: two-column additional-characteristic
  justification layout.
- `MEBEL_PACK`: an additional cross-domain pack; inspect before assuming which
  checks should apply.

Do not encode a pack name or a supplier name into production logic.

## Testing Strategy

Start narrow:

```powershell
& "C:\Users\egorg\anaconda3\envs\myenv\python.exe" -B -m pytest `
  tests\summary_model_tests\<focused_test>.py -q
```

Then broaden:

```powershell
& "C:\Users\egorg\anaconda3\envs\myenv\python.exe" -B -m pytest `
  tests\summary_model_tests -q
```

Full repository tests when the blast radius warrants it:

```powershell
& "C:\Users\egorg\anaconda3\envs\myenv\python.exe" -B -m pytest tests -q
```

Pytest may emit cache/temp ACL warnings on this Windows workspace. Distinguish
an ACL/cache warning from an actual assertion or import failure. Do not weaken a
test to hide an environment problem.

For prompt changes, use a fixture/lab run and inspect raw response artifacts.
For report-only changes, prefer `checks_cli` or focused rendering tests instead
of rerunning paid extraction.

## Change Discipline

Before editing:

- identify the first incorrect artifact in the pipeline;
- state the intended field owner and comparison source;
- list likely regressions;
- inspect tests around that exact function.

While editing:

- change existing functions before adding new layers;
- keep domain-specific recovery next to the domain parser;
- preserve evidence and warnings;
- do not let report formatting change check status;
- do not let one criterion, such as missing OOZ unit, overwrite independent
  commercial-offer price status;
- keep report sections deduplicated and Russian-facing;
- update `docs/project_guide.md` when behavior/contracts change.

After editing:

- run `git diff --check`;
- run focused tests;
- run one deterministic source pack;
- use a live lab/full run only if model behavior changed;
- inspect both JSON and rendered text;
- check `git status` and stage only intended files.

Never use destructive Git commands. Do not revert unrelated user changes. Do
not commit runtime output, API keys, generated reports, or fixture deletions
unless explicitly requested.

## Current Handoff Checklist

At the beginning of a new session run:

```powershell
git status --short --branch
git log -5 --oneline --decorate
```

At the time this handoff was written, the latest known stage fix taught DOCX
ingestion to expose nested stage tables and separated:

- delivery/service term comparison;
- contract execution term comparison;
- structured stage-to-stage comparison;
- internal contradictions between plan fields.

Verify current Git history rather than relying on that statement. Also inspect
the worktree for user-owned deleted fixtures under `doci_primery`; do not include
them in a code commit by accident.

When handing off again, record:

- last commit and whether it was pushed;
- dirty files intentionally left alone;
- exact packs and commands last run;
- output directories;
- passed/failed tests;
- unresolved functional issues and the first incorrect artifact for each.
