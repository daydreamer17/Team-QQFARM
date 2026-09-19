# Quote V9 preference-sensitive fixtures

This folder contains 10 synthetic, single-line PDF/CSV quote pairs, one supplemental
USD CSV quote, and three matching procurement requirements.
All files are test data, not real commercial offers.

## Supplemental USD CSV fixture

- `v9_supplier_k_usd.csv` is QFN-32 / R1 / NEW and totals USD 5,700.00.
- At 1 USD = 1.61 SGD with two-decimal ROUND_HALF_UP rounding, its comparison
  total is SGD 9,177.00.
- The current deterministic backend still reports `CURRENCY_MISMATCH`; this
  fixture verifies the planned fixed-rate conversion behavior.

## Recommended task groups (maximum five quotes per task)

- Preference trade-off: A, B, C, D, E.
- Boundary conditions: F, G, H, I, J.
- Near-tie sensitivity: B, C, D, E, G.

## Expected behavior

- Cost-only: C and E tie at SGD 9,200.00 and may be jointly recommended.
- Cost then fastest: E wins the C/E tie.
- Fastest: A wins Group 1; G wins the near-tie group.
- A tolerance of SGD 15 above the minimum plus fastest preference makes D win the near-tie group.
- H is late, I is over budget, and J has the wrong package.

## Current implementation limits

`procurement_requirement_v9_cost.csv` is compatible with the current deterministic preference value.
The other two requirements are target-regression fixtures: the current backend does not yet implement
FASTEST_CONFIRMED_DELIVERY or secondary-preference tie-breaking. Supplier D is also a target-regression
fixture because the current backend rejects currency mismatch; the intended demo conversion is fixed at
1 USD = 1.61 SGD, rounded to two decimals with ROUND_HALF_UP.

The independent expected answers are stored under `evaluation/reference/quote_V9/` and must not be
mounted into the runtime Agent.
