# full_flow_demo4

An independent MCU commercial trade-off development and test package. All quotations and policy materials are synthetic. This package is not a copy of demo3.

## Getting started

1. Upload and publish `policy/electronics_sg/`. Complete the human review using `upload_metadata.json` and `reviewed_clauses.json`.
2. When creating a task, bind the newly published Electronics/SG policy and upload `requirement/procurement_requirement.txt` (the PDF and Markdown versions are equivalent). Verify the result against `confirmed_requirement.json`. Create a separate task without a policy only when regressing the legacy no-policy baseline.
3. Upload either the four PDFs in `quotes/pdf/` or the four CSV files in `quotes/csv/`. Do not mix the two sets. Use the supplier IDs listed in the manifest.
4. Review every field and formally submit all four quotations. The PDF path uses live model extraction; the fixed CSV path does not require a model.
5. Open Compliance Review. For the original workflow, upload the eight files in `compliance_evidence/initial/`, then use the three files in `corrections/` with the Replace Evidence action. To test compliant and non-compliant evidence for any supplier and evidence type, use the corresponding 24 files under `compliance_evidence/paired_scenarios/`. Confirm the compliance review before proceeding to decision comparison.
6. Use a new task for each `variants/` case. Replace only the specified supplier's quotation and retain the other three primary quotations. Do not upload all variants together.
7. Historical data may be bound to the existing `synthetic-mcu9-supplier-performance / 2026-08-06-v1` snapshot. Do not add fabricated ratings. `policy/unrelated_office_eu/` is a scope-isolation negative example and must not be bound to an SG electronics procurement task.

Values for entering policy evidence are listed in `compliance_evidence/entry_guide.json`. This file assists manual entry but does not replace reading the source documents. See `docs/TESTING.en.md` for the complete workflow. Expected results are stored under `evaluation/reference/full_flow_demo4/`; these offline acceptance materials must not be uploaded to the runtime Agent.

## Data boundaries

- Fixed evaluation date: 2 November 2026. Quotations remain valid through 30 November 2026. If a future rerun falls outside this period, create a separately versioned dataset with independent expectations instead of allowing the current date to change the result automatically.
- Preserve the original MCU identifier, single-item procurement scope, and primary/secondary ordering across six metrics. This package does not test new component selection or substitute compatibility.
- Natural language explains policies and preferences only. Deterministic code calculates monetary values, hard constraints, evidence matching, and states; humans confirm evidence facts.
- This directory contains no reference recommendation, manually supplied answer, or model evaluation output.

## Regeneration

```bash
PYTHONPATH=src .venv/bin/python data/generate_full_flow_demo4.py
```

The script rebuilds only this dataset and its independent layout holdout. It does not modify demo1/2/3, the database, or other code. The generator does not import reference answers or the calculation engine.
