# QuoteWise English Style Guide

This guide defines how English should be written across the QuoteWise UI, API-facing messages, AI responses and exported procurement reports. The target locale is **English (Singapore), `en-SG`**.

## 1. General voice

- Use clear, professional procurement English.
- Prefer short, direct sentences in the UI.
- Explain what happened and what the user can do next.
- Do not describe a recommendation as an approval.
- Do not describe missing evidence as non-compliance unless a deterministic check returned `FAIL`.
- Do not hide uncertainty. Use “Pending Confirmation”, “Review Required” or “Not Evaluated” as appropriate.
- Address the user only when it makes an action clearer; avoid unnecessary “you” and “your”.

## 2. English variant

Use Singapore/British spelling where there is a visible difference.

| Use | Avoid |
|---|---|
| Centre | Center |
| Normalised | Normalized in user-facing prose |
| Authorised | Authorized in user-facing prose |
| Analyse | Analyze as a verb |
| Catalogue | Catalog in user-facing prose |

Code identifiers, API values and third-party names retain their original spelling, such as `normalized_value`, `authorization` fields or library APIs.

## 3. Capitalisation

- Use sentence case for navigation, buttons, form labels, table headers and status labels.
- Use Title Case only for formal report chapter headings.
- Preserve official names and acronyms: QuoteWise, API, CSV, PDF, RAG, RoHS, MOQ, SGD.

| Context | Correct | Avoid |
|---|---|---|
| Button | Create procurement task | Create Procurement Task |
| Field label | Delivery deadline | Delivery Deadline |
| Status | Pending confirmation | Pending Confirmation |
| Report chapter | Executive Summary | Executive summary |
| Product name | Supplier Selection Workspace | Supplier selection workspace |

The dictionaries use title-style wording so terms are easy to review. During implementation, apply sentence case according to the component context.

## 4. Dates and times

Use an unambiguous `en-SG` presentation format.

| Value | Display |
|---|---|
| Date | `25 Sep 2026` |
| Date with time | `25 Sep 2026, 14:30` |
| Date range | `1 Jan 2026 – 31 Dec 2026` |
| API date | Keep `2026-09-25` |
| API timestamp | Keep ISO 8601 with timezone |

- Use the Singapore timezone when the product explicitly presents an evaluation or business date.
- Do not convert date-only values through browser timezone logic.
- Do not use ambiguous numeric forms such as `09/10/2026`.

## 5. Money and numbers

| Value | Display |
|---|---|
| Singapore dollars | `SGD 7,000.00` |
| Other currency | `USD 1,250.00` |
| Quantity | `1,000 pieces` |
| Percentage | `92.7%` |
| Difference | `SGD 200.00 higher` |

- Use ISO currency codes rather than `$` or `S$` in comparisons and reports.
- Keep money as decimal strings in APIs and calculations.
- Use a thousands separator in user-facing values.
- Do not display an unknown amount as zero.
- Match singular and plural where practical: `1 piece`, `2 pieces`.

## 6. Buttons and actions

- Start action buttons with a verb: “Upload quotation”, “Save and recheck”, “View source”.
- Use “View” for in-app navigation or preview, and “Download” for saving a file.
- Use “Cancel” to leave an uncommitted action.
- Use “Close” to dismiss a completed view or dialog.
- Use “Retry” only when repeating the same request is safe.
- Destructive actions must name the object: “Abandon task”, “Deactivate quotation”.
- Confirmation dialogs must state whether the action is reversible.

## 7. Status wording

- Use a noun or concise state label, not a full sentence.
- Do not translate raw program values inside JSON, logs or test expectations.
- Use the context-specific labels in `03_状态与枚举字典.md`.
- Distinguish these three outcomes:
  - `Failed`: execution attempted and failed.
  - `Review required`: a human judgement is needed.
  - `Not evaluated`: the check has not been performed.
- Distinguish quote feasibility from policy eligibility. A quotation may meet procurement requirements but remain unverified under policy.

## 8. Procurement authority and safety

Use wording that preserves the system’s authority boundary.

| Intended meaning | Use | Avoid |
|---|---|---|
| Current result favours a supplier | Supplier A is currently recommended. | Supplier A is approved. |
| Evidence is absent | The required evidence has not been provided. | The supplier is non-compliant. |
| Value is unknown | The amount remains unconfirmed. | The amount is zero. |
| Draft report | Preliminary recommendation | Final decision |
| Human decision | Formal approval | AI approval |

Every formal procurement report must include this boundary statement or an equivalent natural translation:

> This report provides decision support and does not replace formal approval by an authorised procurement representative.

## 9. Source documents and citations

- Never translate, rewrite or silently correct user-uploaded source documents.
- Preserve supplier names, manufacturer names, part numbers, IDs, filenames and quoted source text.
- An English explanation may appear next to a non-English source excerpt, but it must be identified as an explanation rather than source evidence.
- Display compact citation markers such as `[1]` in narrative text.
- Show the full `RESULT:`, `QUOTE:` or `POLICY:` identifier in the citation list or details view, not inline in every sentence.
- Do not change hashes, source IDs, page numbers, text block IDs or CSV row/column locations.

## 10. AI assistant and report prose

- Prefer one conclusion followed by its strongest supporting facts.
- State uncertainty and missing evidence explicitly.
- Separate deterministic facts from model-generated explanation.
- Do not invent supplier credit, market reputation, approvals, prices or dates.
- Use supplier names in prose and stable IDs in citations or technical details.
- Recommendations should normally use “currently”, “based on the current confirmed inputs” or similar scope wording.
- Communication suggestions must be drafts for human review and must never claim that a message was sent.

## 11. Errors and recovery guidance

User-visible errors should follow this pattern:

```text
What failed. Why, if known. What the user can do next.
```

Examples:

- `Unable to parse the requirements document. Check that the file contains selectable text and try again.`
- `The task was updated while this page was open. Refresh the page before submitting your changes.`
- `The quotation contains conflicting values. Review the source document and confirm the applicable value.`

Avoid exposing stack traces, provider credentials, storage paths or raw internal exceptions.

## 12. Placeholders

- Keep placeholder names in English and do not translate them.
- Preserve every placeholder from the source message in the translated message.
- Use descriptive names: `{supplierName}`, `{count}`, `{start}`, `{end}`, `{total}`.
- Do not build English sentences by concatenating translated fragments where word order may change.

Example:

```text
Showing {start}–{end} of {total}
```

## 13. Content that remains untranslated

- API field names and enum values
- Database column names and persisted historical payloads
- Task, quotation, document, result, job and citation IDs
- Supplier and manufacturer legal names
- Manufacturer part numbers and product codes
- User-uploaded files and their source excerpts
- File hashes and evidence locations
- Existing Chinese demo fixtures when they are intended to test multilingual extraction

If an English-only demo is required, create a separate fixture rather than overwriting the original evidence.

## 14. Implementation checks

Before declaring the English version complete:

1. Confirm all runtime UI strings come from the English catalogue.
2. Confirm backend user-visible errors and generated reports use the approved wording.
3. Confirm API values and database values have not changed.
4. Check that placeholders match between source and target messages.
5. Search runtime source for unintended Chinese text, excluding fixtures and source evidence.
6. Visually check long table headers, buttons, status badges and the AI assistant at supported viewport sizes.
7. Generate Markdown, Word and PDF reports and inspect dates, amounts, headings and citations.
