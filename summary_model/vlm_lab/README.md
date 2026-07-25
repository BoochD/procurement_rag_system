# VLM Table Lab

Experimental tools for testing vision-based parsing of difficult DOCX tables.
This lab is separate from the production extraction pipeline.
The same VLM table parser can also be used as an explicit fallback in the new
extraction pipeline with `--with-vlm`.

## What To Test

For procurement checks we usually care about these table roles:

- `purchase_description`: OOZ/product rows and characteristics.
- `contract_stages`: execution/service stages.
- `nmck_calculation`: supplier/executor prices, minimum prices, row totals.
- `contract_specification`: contract specification with quantity and prices.

Attachment lists are parsed deterministically from tables/text and are not sent
to automatic VLM fallback. Signature tables, empty templates, and purely
decorative tables should not be sent to VLM unless a human explicitly selects
them.

## How Pipeline Fallback Works

The deterministic parser still runs first. A table is sent to VLM only when it
is both important and suspicious:

- role is one of `purchase_description`, `contract_stages`,
  `nmck_calculation`, `contract_specification`;
- parser warnings are present, or the table stayed `generic_table`/`unknown`,
  or the parsed payload is empty for the expected role, or fallback rows remain.

The VLM receives one rendered table image per request. It does not parse the
whole document. It returns `VlmTableExtraction`, which is converted back into
the same `ParsedTable.compact_json` shape used by deterministic parsers.

Full extraction with VLM fallback:

```powershell
C:\Users\egorg\AppData\Local\Programs\Python\Python313\python.exe `
  -m summary_model.extraction_cli `
  --input-dir "doci_primery\PACK_06_05" `
  --output-dir "runtime\extraction_runs\PACK_06_05_vlm" `
  --with-vlm `
  --with-llm
```

Web/Celery keeps VLM off by default. Enable it explicitly:

```powershell
$env:SUMMARY_WITH_VLM_TABLES="1"
$env:SUMMARY_VLM_MAX_TABLES_PER_DOCUMENT="4"
```

## Build Payload Without Live VLM

```powershell
C:\Users\egorg\AppData\Local\Programs\Python\Python313\python.exe `
  -m summary_model.vlm_lab.single_table `
  --input "doci_primery\закупка_для_примера_расширение_ЦОД_с_лицензиями\5. Контракт_4.docx" `
  --type-hint contract `
  --target-role purchase_description `
  --query "описание объекта закупки характеристики программного обеспечения" `
  --output-dir "runtime\vlm_lab\COD_contract_ooz_auto"
```

Artifacts:

- `candidates.json`: ranked table candidates.
- `table_N.png`: rendered long table image.
- `payload.json`: table metadata and parser output.
- `prompt.txt`: VLM prompt.
- `schema.json`: expected response schema.

## Live VLM Test

Add `--with-vlm` only when you explicitly want to spend tokens and send the
table image to the configured OpenAI-compatible provider:

```powershell
C:\Users\egorg\AppData\Local\Programs\Python\Python313\python.exe `
  -m summary_model.vlm_lab.single_table `
  --input "doci_primery\закупка_для_примера_расширение_ЦОД_с_лицензиями\5. Контракт_4.docx" `
  --type-hint contract `
  --table-index 4 `
  --output-dir "runtime\vlm_lab\COD_contract_table_4_vlm" `
  --with-vlm
```

Live artifacts:

- `vlm_raw.json`: raw provider response.
- `vlm_result.json`: parsed `VlmTableExtraction`.
