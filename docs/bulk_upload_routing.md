# Automatic upload routing

## Scope

The upload page keeps the existing document fields and the separate commercial
offer control. A new bulk control accepts several files at once or additional
files in later selections, classifies them locally on the application server,
and places confident candidates into the existing fields.

This feature is only a UI convenience. It does not change the upload payload,
Celery task contract, `summary_model.web_service`, or the production
`DocumentClassifier` used during extraction.

## Supported destinations

| Upload field | Document type | Formats |
| --- | --- | --- |
| `plan` | Schedule application / plan | DOCX |
| `contract` | Draft contract | DOCX |
| `ooz` | Purchase description | DOCX |
| `zapiska` | Explanatory note | DOCX, PDF |
| `onmck` | NMCK justification | DOCX |
| `obrasheniye` | Procurement request | DOCX, PDF |

Commercial offers remain in their dedicated control. If the bulk router detects
one, it explains where the file should be uploaded instead of silently mixing it
with the main documents.

## Routing policy

The router uses deterministic evidence from the file name, document headings,
paragraphs, and table text. A numeric prefix such as `1.` is not a reliable
signal. Exact headings and combinations of document-specific fields carry more
weight than isolated words.

The result has three states:

- confident: assign automatically;
- review: assign when the destination is free and show a visible warning icon;
- unknown: keep in the unresolved list and ask the user to choose a field.

An automatically routed file never overwrites an occupied field. Reports,
generated analysis files, temporary Word files, unsupported formats, and
ambiguous files remain unresolved.

## Web API

`POST /classify-uploads/` accepts multipart field `files` and returns one result
per file in input order. Classification does not call LLM/VLM or external APIs
and does not retain uploaded files after the response.

```json
{
  "files": [
    {
      "name": "3. заявка_в_ПГ.docx",
      "document_type": "plan",
      "target_field": "plan",
      "confidence": 0.96,
      "auto_assign": true,
      "needs_review": false,
      "reason": "Найдена заявка в план-график и характерные поля формы."
    }
  ]
}
```

## Regression requirements

- Contract files containing an embedded OOZ must remain contracts.
- Attachment names quoted inside a request must not turn it into another type.
- NMCK wording containing `цена контракта` must not turn it into a contract.
- Generated analysis reports must not be routed as source documents.
- Sequential selections must preserve earlier assignments.
- Manual selections must not be overwritten by automatic routing.

