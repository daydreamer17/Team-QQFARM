# Deployment alignment audit — 2026-09-26

## Established observations

- Local `.env` selects SiliconFlow / DeepSeek-V4-Flash. The supplied production
  runtime report selects the organiser gateway / Claude Sonnet. Local success
  therefore does not establish compatibility with the production provider.
- `docker ... permission denied ... docker.sock` happens before a container or
  model request starts. It is independent of JSON/model extraction failures.
- Previous production failures reported approximately 10 KB of model content,
  `finish_reason=stop`, but no valid single `candidates` JSON object. This does
  **not** establish empty output, insufficient model intelligence, or unsupported
  schemas. The synthetic response must be inspected before another protocol change.
- `git pull` updates checkout files, not the Python package installed in running
  images. A script bind-mounted from the checkout can otherwise import older code.

## Confirmed configuration/code defects addressed

| Area | Defect | Change |
| --- | --- | --- |
| API/worker | `.env` is fully loaded by local dev scripts, but Compose only passes explicitly listed variables | Shared runtime environment forwards requirement, summary, explanation, agent, retries and worker settings |
| API metadata | Requirement model override was passed only to worker | Same override reaches API and worker |
| Credential selection | Secondary clients could default to SiliconFlow despite a different main credential name | Inherit the configured main credential unless explicitly overridden |
| Optional overrides | Empty Compose values defeated some `getenv(default)` fallbacks | Empty model/endpoint/provider overrides fall back consistently |
| Quote generation | ORGANIZER silently capped configured 8192 tokens to 4096 | Honor configured token budget; no unverified provider-specific cap |
| Quote validation | Valid-looking JSON could pass despite explicit provider truncation/refusal termination | Reject incomplete responses before field validation, retaining bounded retries |
| Diagnostics | Handwritten Docker command lacked deployment script's sudo fallback; could mix current script with old package | One wrapper handles Docker permissions and explicitly loads checkout source |
| Deployment health | WEB_PORT was configurable but health check always used port 80; curl had no timeout | Discover published port and bound checks |

No API key values are written into source, reports, or tests. No production data,
database, credentials, security groups, or model provider selection were changed.

## Verification and remaining evidence

- Compose rendering tests use dummy credentials and compare API/worker overrides.
- Regression tests cover blank-value inheritance, truncation rejection, budget
  forwarding and secret-safe configuration reports.
- Initial backend run found four legacy policy-workflow failures, also
  reproduced in an isolated archive of unmodified `b12cd75`. The teammate's
  subsequent `8efb3bd` fixes that legacy routing. This audit fast-forwarded to
  that latest main without conflicts before final verification.
- Final backend run: **1041 passed, 62 skipped**; no failures. Skips include
  live-provider and PostgreSQL integration cases. Two existing warnings concern
  tests deliberately supplying unsupported ranking enum values.
- Paid live provider and PostgreSQL integration tests are not covered by that
  ordinary test run. Mocked tests are not proof of gateway compatibility.
- Final frontend suite: **125 passed**. The teammate's `8efb3bd` corrects the
  earlier two localized-text expectations; this audit made no frontend edits.
  Production frontend build also succeeds, with an existing large-chunk warning.

## Single server-side diagnostic

From `/opt/Team-QQFARM`, after pulling this commit:

```sh
bash deploy/lightsail-diagnose.sh
```

This prints API/worker effective nonsecret model configurations, package versions
and source hashes, then runs exactly one model request using the checked-in
synthetic Great Wall PDF. It prints that synthetic response (redacting known keys)
and JSON/field-schema validation status. It does not rebuild, restart, change
database records or retry the gateway call. The checkout report can be compared
with running container reports to detect stale code/configuration. Validation
does not yet establish the full evidence-grounding or user workflow outcome.

## Live gateway diagnosis and fix

After the user downloaded the instance's SSH key, direct SSH access succeeded.
The synthetic diagnostic returned 12 concatenated `candidates` objects in one
`message.content`, with differing fields/values. The same behavior reproduced
with `json_object` and streaming (one content delta containing the whole repeated
output). This establishes the immediate parser failure, not the gateway's
internal implementation or the model's general intelligence.

A forced `submit_quote_candidates` tool call returned one explicit arguments
object. Larger canonical-key arguments returned `{}` with 1024 completion tokens
in two runs. Compact wire keys (`f/r/v/u/s/ids`) reduced the price-group response
to 434 completion tokens and passed. The code expands names only: field values,
units, validation status and evidence IDs are unchanged. Normal JSON-schema
requests remain in use for LOCAL providers; ORGANIZER quote extraction uses
the verified forced-tool format.

Ambiguous/missing/wrong tools, malformed JSON, explicit truncation, duplicate
alias/canonical keys and unsupported evidence remain rejected. Draft prose is
never selected or merged into results. The tool is an output schema, not an
executable external action.

The complete Great Wall synthetic PDF passed all four groups and evidence
grounding in four calls without retries (completion tokens: 337, 434, 555, 333).
Production upload validation is the next acceptance check after image rebuild.

### Production acceptance result — not yet resolved

Commit `f51dcad` was deployed successfully; API, database and web health checks
passed. The final backend regression run was **1050 passed, 62 skipped**.

A real browser upload of the same synthetic PDF created
`draft_da91f230374841d99597b8c1658c6fb9`. It **failed** after four model calls with
`model_response_invalid` (invalid chat-completions envelope). Four calls alone
do not identify the failing group because group attempts share the budget.
The uploaded document hash matches earlier tests. The deployed adapter and
isolated probe adapter have identical SHA-256 hashes; runtime nonsecret model
configuration matches the intended organizer settings.

A subsequent single delivery/payment/date-group request returned one valid
forced tool call and decoded successfully. This does not prove that group caused
the failed upload, nor does it establish reliable end-to-end behavior. Production
failure artifacts were not enabled, so the exact rejected response is unavailable.
Next investigation must capture a restricted failure artifact (without exposing
credentials) and classify the actual malformed envelope before changing parsing
or retry behavior. Do not report the production upload issue as fixed.

### Follow-up: captured missing-tool response

With a new user-approved maximum of 20 model calls, the first controlled call
reproduced the rejection: the gateway omitted `tool_calls` despite forced
`tool_choice`, and returned one complete fenced JSON object in `message.content`
with `finish_reason=stop`. Its compact-key candidates were otherwise valid.

Commit `dd45be4` accepts this observed transport variation only when there are
no tool calls and the finish reason is `stop` or `end_turn`. It decodes the entire
plain/fenced JSON document, expands key names, and applies the existing schema
and source-grounding checks. It never searches prose or chooses among multiple
answers. Wrong/multiple tools still fail even if content contains valid JSON.

An isolated full extraction passed in five calls, including one bounded retry
and one actual content-only response. Regression suite: **1060 passed, 62
skipped**, with two existing enum warnings. Calls used before production upload:
**6/20**. The revised service was deployed successfully with readiness passing.

Production browser acceptance then **passed**: uploading the same PDF created
`draft_14b50b0a0bd44045b514c644d146b173`, status `REVIEW_REQUIRED`, 30 fields,
`error_message=null`, and four calls without retries. The browser displayed the
review form with 27 identified fields and two absent fields. One actionable
business-validation item remains: supplier name includes `(SUP-029)` and must
be separated from its ID. No automatic human confirmation or formal submission
was performed. This verifies upload-to-review, not the remaining compliance,
analysis or summary workflow. Total paid model calls in this follow-up: **10/20**.
