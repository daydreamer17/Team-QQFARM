# Synthetic Compliance Review Evidence

All materials in this directory are fictitious and do not represent real suppliers, certifications, or procurement approvals. The three subdirectories validate supplier admission, RoHS, and amount approval. Each category provides an exceptional `v1` version and a corrected `v2` version.

Use the four quotations in `data/generated/demos/full_flow_demo4/quotes/csv/`. Set the task manufacturer to `QQ Demo Components` and the part number to `QW-MCU9-DEMO`. On the Compliance Review page, select the corresponding supplier and check, upload the file, enter fields according to the source, and confirm the human review.

| Supplier | ID | Primary v1 exception | Expected v2 result |
|---|---|---|---|
| Redwood Components | SUP-022 | Expired admission and insufficient approved amount | Valid after update |
| Schwarzwald Circuits | SUP-023 | Wrong RoHS part number and approval currency | Valid after correction |
| Sterling Components | SUP-024 | Suspended admission and expired RoHS evidence | Valid after reinstatement/renewal |
| Great Wall Components | SUP-029 | Admission ID mismatch, RoHS non-compliance, and rejected approval | Valid after correction |

To validate version replacement, upload `v1` first, then use Replace Evidence/Approval Record to upload `v2` for the same supplier. If both v1 and v2 are saved as new evidence without a replacement relationship, the system should report a conflict rather than automatically passing the most recently uploaded file.

## Paired validation materials

`paired-scenarios/` is a more direct frontend acceptance package. Each supplier has the following six files:

- `supplier-compliant.md` / `supplier-non-compliant-*.md`
- `rohs-compliant.md` / `rohs-non-compliant-*.md`
- `amount-compliant.md` / `amount-non-compliant-*.md`

Each filename states the expected result. After upload, the system should automatically populate the evidence ID, supplier ID, conclusion, expiry date, and either the RoHS material scope or approved amount. There is no need to upload `manifest.json`. The exceptional versions cover expiry, rejection/suspension, supplier or part-number mismatch, insufficient amount, and currency mismatch.
