# Development inputs

Runtime-safe development inputs for `MCU-DEMO-001`. Supplier B intentionally omits shipping.

## quote_V1

- `quotes.csv` is the fixed-schema CSV baseline.
- The three supplier PDF files form one PDF input branch.
- The three supplier CSV files form one heterogeneous-header CSV input branch.
- Use exactly one branch at a time. Do not combine the fixed baseline, PDFs, and heterogeneous CSVs as additional suppliers.

## quote_V2

- The supplier PDF and CSV pairs are revised development inputs with more realistic field aliases, omissions, and layouts.
- The procurement requirement PDF and CSV describe the same demand through two input branches.
- `system_evidence_v2.json` is an illustrative system/evidence record, not a supplier document and not a hidden answer source.

Do not enrich runtime inputs from `evaluation/reference`, generator manifests, or unmasked expected answers.
