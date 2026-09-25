# Procurement history data provenance

Both CSV files in this directory are synthetic procurement history data. They
are retained as source material for the supplier-history feature and are not
current supplier quotations.

## Upstream source

- Project: `procurement-spend-analysis-dashboard`
- Repository: <https://github.com/dytcoke23/procurement-spend-analysis-dashboard>
- Upstream file: <https://github.com/dytcoke23/procurement-spend-analysis-dashboard/blob/main/data/purchase_orders.csv>
- Upstream license: MIT; the license text is reproduced in `LICENSE`.

## Files retained by this project

| File | Purpose | Rows including header | SHA-256 |
|---|---|---:|---|
| `purchase_orders.csv` | Complete synthetic upstream history used to generate the published MCU-9 supplier-history snapshot. | 47,129 | `fba22a467e5dd800a0af960930d2ca5e2ecbd9d2bc747e37304867d8d031ae21` |
| `electronics_purchase_orders.csv` | Earlier derived Electronics subset retained for reproducibility; runtime code must not combine it with the complete source. | 6,124 | `6b584e32f15de78d11e651a5e34eb7637e63a0041205a82e796a932f54875cf3` |

The files have different hashes and scopes. `purchase_orders.csv` is the
authoritative generator input. `electronics_purchase_orders.csv` is preserved
only as a historical derived artifact and must not be read as an additional
source or counted together with the complete file.
