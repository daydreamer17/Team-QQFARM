# Production E2E: full_flow_demo4

In progress, 2026-09-26. This report is not a claim of full-flow completion.

## Authoritative target

- Lightsail: `47.131.76.216`, deployed code `df4e910`.
- Task: `task_1c6551e7fa06463e9578d712f3c0fc07`, **E2E Demo – QW-MCU9 Singapore PDF**.
- Policy: `full-flow-demo4-electronics_sg`, published version
  `2026.11-compliance-v2`, index `pidx-5921ecf1e4ff23fc2afc60ff`.
  Three clauses, Electronics / SG, SiliconFlow BGE-M3 1024-dimensional index.
- Task requirement inspected against `confirmed_requirement.json`: all fields
  match, including 1,000 pieces, SGD 8,000, no substitutes, 2026-11-15 deadline,
  and lowest confirmed total cost as the original requirement. The subsequently
  applied decision profile overrides ranking with fastest confirmed delivery.
- Current task revision: 24; result `artifact_7d18fd7e629544c399d2d09dec81001f`.
- Includes Lumos088's `8efb3bd` (2026-09-26 16:10 +08:00) as an ancestor.

## PDF quotation acceptance

All four PDFs were selected through the browser file picker, extracted,
compared against source PDF text, manually reviewed and formally submitted.
Task revision advanced from 1 to 5. No CSV was uploaded into this task.

| Supplier | Price / basis | Freight / other | Lead days | Payment |
| --- | --- | --- | --- | --- |
| SUP-029 Great Wall | SGD 6.20 / 1 piece | 200 / 100 | 10 | Net 15 |
| SUP-022 Redwood | SGD 6.50 / 1 piece | 150 / 50 | 8 | Net 60 |
| SUP-023 Schwarzwald | SGD 6.60 / 1 piece | 200 / 100 | 5 | Net 45 |
| SUP-024 Sterling | SGD 680.00 / 100 pieces | 200 / 100 | 7 | Net 30 |

All use QW-MCU9-DEMO, QFN-32, R1, NEW, tax excluded, MOQ/order multiple
100 pieces, dates 2026-10-28 through 2026-11-30. Schwarzwald charges are on
PDF page 2. Sterling's 100-piece price basis was preserved.

Human-review findings, not silently accepted:
- Three supplier names included their system IDs; IDs were separated from names.
- Redwood's model response inferred supplier country from the destination and
  a category from product text. Those unsupported optional values were cleared;
  its item description was restored from the original quotation.
- Missing country/category information was not invented.

## Compliance preparation

Eight initial synthetic evidence files were uploaded through the browser and
facts checked against their actual text and the entry guide. Each save was
verified before continuing; task revision became 13. The supplied wrong part,
revoked admission and expired certificate were preserved intentionally.
Compliance job `job_a0cb90060bdf479cac2610ad657c169a` was started once.

### Discovered compliance-entry regression

The initial compliance job failed with `review_required`: supplier-name
corrections disappeared from the downstream review. Read-only database inspection
confirmed graph `graph_7d645fac8dc64074ae4aea7d3b109033` had null provider/model/
prompt metadata and four newly extracted document executions (four calls each).
The compliance entry bypassed the submitted-draft seeding used by the regular
run endpoint. This consumed 16 unnecessary extraction calls and reintroduced
the original names containing supplier IDs.

Fix `506e305` passes the API's effective model configuration into the compliance
graph, rejects carrying executions from a mismatched configuration, and seeds
missing executions from the immutable reviewed submissions. Existing carried
executions are not duplicated. Regression tests cover both an empty preparation
graph and a stale graph with an uncorrected batch. The full suite before the
additional stale-graph parameter passed 1061 tests; focused final coverage passed
38 tests with two PostgreSQL integration skips. Production revalidation pending.

Production revalidation **passed** after deploying `506e305`: graph
`graph_2959612108c6467ab08a3ce1a0f3a835` reused exactly four submitted batch IDs
(verified via read-only SQL). Final full regression: **1062 passed, 62 skipped**.
The failed UI had no retry button, so the same supported `/compliance/start`
endpoint was called once with an idempotency key for recovery; all evidence
uploads and replacements used the browser forms.

Initial assessment: Redwood verified; Schwarzwald admission failed; Great Wall
part scope mismatch; Sterling expired certificate. Replacements were submitted
one at a time. Verified supplier count increased 1 -> 2 -> 3 -> 4. All three
old records remain superseded, and new records are version 2 linked to predecessors.
Sterling's SGD 7,100 threshold triggered AFTER_SELECTION approval; its missing
approval remained a deferred item, not a fabricated approval. Confirmation
advanced the task to revision 17 and generated result
`artifact_c71dc45325bb49349c99d649dd8f6d17`.

## Initial comparison verified

Great Wall recommended at SGD 6,500, followed by Redwood 6,700, Schwarzwald
6,900 and Sterling 7,100. All quantities are 1,000 pieces. Arrival dates match
source PDFs: 2026-11-12, 11-10, 11-07 and 11-09 respectively. History grades,
on-time/rejection rates and reasons for non-selection are visible.

Outstanding review detail: normalization shortened payment terms to Net N,
dropping the source's "from invoice" start event. The comparison correctly
flags missing start events; they must be confirmed from source, not guessed.

## Remaining acceptance work

- Summary sections, Markdown/Word export and consistency.
- Summary/export visual and mobile checks; failed-draft cleanup pending permission.

## Assistant gateway compatibility

The first recommendation question failed with `model_transport_error` during
intent routing. The ordinary content/JSON request exceeded the previous 60-second
timeout. A diagnostic at 180 seconds returned after 75 seconds but still failed
JSON validation (that first minimal diagnostic accidentally omitted the expected
recent_messages key, so it is not an equivalent semantic question). A second
properly populated synthetic request with a forced structured-output tool returned
valid intent arguments in 3.85 seconds. Two diagnostic calls used from the newly
authorized 20-call allowance.

Fix `4db79e8` uses the expected output schema as an ORGANIZER tool contract for
both intent routing and narrative, with strict single-tool/single-JSON decoding.
Local protocol and downstream schema, citation and factual validators are unchanged.
Production conversation timeout defaults to 180 seconds. Regression: 1071 passed,
62 skipped, two pre-existing enum warnings. Deployed successfully; UI regeneration
of the original failed question is now in progress.

Post-fix first question succeeded in one model routing call and showed two
clickable sources. Delivery-priority question succeeded in one call, previewing
Schwarzwald at SGD 6,900 arriving November 7. Read-only task check proved revision
17 and the Great Wall formal result stayed unchanged. Communication advice was
initially misclassified UNSUPPORTED; prompt clarification in e0ecc1d distinguishes
advice from actual supplier contact. Retest returned cited current-comparison facts,
although phrasing was factual context rather than a ready-to-send draft.

Scenario `scenario_7d4fa71a099f4b80b9bcd74a24f3c195` was explicitly generated and
applied. Application advanced revision 17 -> 18 and required compliance confirmation.
The first browser tab hung before submission; server state remained revision 17.
Recovered in a new tab, closed only the hung test tab, then confirmed once.

All four PDF quotes were revised through the UI to retain the original Net N
"from invoice" conditions. Revision drafts reused reviewed extraction; no new PDF
upload was made. All four quote versions are now 2, task revision 22.
e0ecc1d also prevents deterministic payment normalization from dropping suffixes
and preserves same-day-count source conditions when model normalization omits them.
Backend regression before the additional prompt wording: 1075 passed, 62 skipped.

Policy audit found old FF4-ROHS expired/mismatch outcomes incorrectly configured as
FAIL instead of REVIEW_REQUIRED. Published a new immutable version matching the
requested reviewed_clauses.json: `2026.11-compliance-v2`, import
`pfi_af99fac6beac470099c7a4b149a2e0f3`, index `pidx-5921ecf1e4ff23fc2afc60ff`.
Bound task to it at revision 23 with no requirement-field changes. Original version
remains in history. Current task is running the new compliance check with the eight
active evidence records; fastest-delivery decision profile remains applied.

Revision 24 comparison succeeded: `artifact_7d18fd7e629544c399d2d09dec81001f`,
Schwarzwald recommended at SGD 6,900, arrival 2026-11-07, 1,000 pieces. All four
payment terms display their invoice start event and no missing-start warning.
Historical revision 17 result is still accessible; clicking the assistant's
result citation navigated to its exact frozen result, not the newer recommendation.
History UI shows eight immutable quotation file versions, both comparison results,
24 task revisions and policy v2026.11-compliance-v2. Mobile width 390px screenshots
of comparison/history are readable; wide tables and tabs were verified scrollable.

Summary `summary_4af562f3f83e491586f2076d9ce448d3` first failed with two transport
timeouts. Its separate client still used content JSON rather than the verified
gateway tool contract. Fix df4e910 reuses the strict ORGANIZER transport with the
summary schema, keeps summary factual validation, and defaults production timeout
to 180 seconds. Full regression: 1076 passed, 62 skipped, two existing warnings.
Deployed, health ready, one UI retry started. Export and visual verification remain.

Seven failed Great Wall drafts were enumerated for cleanup. Automatic safety review
rejected bulk soft-discard despite the plan; no discard was executed. User confirmation
was requested asynchronously. Submitted quotations are unchanged by this cleanup step.

## Checkpoint: known remaining summary failure

The post-df4e910 retry failed `summary_model_output_invalid`; the summary is not
export-ready. Diagnostics showed a structured tool response containing title,
overview and disclaimer but no required sections. The gateway reported 32,230
prompt tokens and 1,024 completion tokens despite a larger requested output
budget. The model projection redundantly includes large compliance facts and
omits the applied decision profile: its overview described lowest cost while
the formal result correctly used fastest delivery. These are unresolved at this
checkpoint. Schema/reference checks were not relaxed and no partial response was
accepted as a valid summary. The next fix must compact model context and include
the authoritative decision profile, then revalidate generation and exports.

Navigation checks passed for requirements, decision, audit, reload, back and
forward. A fresh observation window reported no browser exceptions, failed
requests or HTTP errors. This does not imply that historical failed summary
requests succeeded. Comparison/history mobile checks passed; summary mobile QA
remains pending.

## Checkpoint regression rerun (2026-09-26 18:00 +08:00)

- `.venv/bin/python -m pytest -q`: 1,076 passed, 62 skipped, two enum
  serialization warnings, 49.78 seconds. Skips are opt-in paid live-model and
  dedicated PostgreSQL integration/recovery tests, not successful executions.
- `cd frontend && npm test`: 16 files, 125 tests passed.
- `cd frontend && npm run build`: TypeScript and production build passed;
  a non-blocking bundle-size warning remains (JavaScript chunk above 500 kB).
- `cd frontend && npm run lint`: passed.
- These automated results do not override the unresolved live summary/export
  acceptance failure documented above. No paid model calls were made for this rerun.
