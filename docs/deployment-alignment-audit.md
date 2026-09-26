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

Production diagnosis remains incomplete until this output is available. Browser
terminal control failed in this session and direct SSH had no accepted key.
