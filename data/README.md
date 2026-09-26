# Data Directory

The delivery retains only data used by the current runtime, demonstrations, and regression tests. Do not create complete dataset copies with names such as `quote_V1` or `quote_V2`; place new samples in stable directories according to their purpose.

## Directory responsibilities

- `contracts/`: Field contracts, schemas, and interface constraints.
- `policies/`: Publishable procurement policies and reviewed clauses.
- `examples/policy_rag/`: Minimal input examples for Policy RAG.
- `source/`: Upstream procurement data, licences, and source notes.
- `generated/demos/`: Current demonstration packages that users can complete by following their README files.
- `generated/fixtures/extraction/`: Specialised fixtures for parser boundaries, layouts, OCR, CSV, and related tests. These are not user demonstration data.
- `generated/fixtures/compliance/`: Compliance review materials and closed-loop test fixtures.
- `generated/supplier_history/`: Supplier history snapshots generated deterministically from source procurement data.

## Current entry points

- Full workflow: `generated/demos/full_flow_demo4/`
- Investigation Agent: `generated/demos/agent_investigation_demo/`
- Full-workflow generator: `generate_full_flow_demo4.py`
- Investigation Agent generator: `generate_agent_investigation_demo.py`

Content under `generated/fixtures/` is for automated testing only. Do not mount reference answers or holdout sets where the runtime Agent can access them.
