# Fictional Electronics Quote and Total Cost Policy

This synthetic policy exists only for the NUS-ISS hackathon demonstration.

## [QTE-001] Required commercial fields
Every supplier quote must provide currency, unit price and price basis, quantity and unit, minimum order quantity, shipping charge status, payment terms, and a confirmed validity end. Quote date is required to resolve relative validity; it may be absent when an explicit validity end is confirmed.

## [QTE-002] Supplier and quote identity
Every quote must include a supplier name and quote reference. A supplier identifier need not be printed: the system must establish an auditable exact match to a confirmed supplier master before approval or RoHS checks. System identity is not an extracted quote fact.

## [QTE-003] Dates and validity
A confirmed explicit validity end or an unambiguous relative period with confirmed quote date is required. Date-only valid-until remains valid through that day in Asia/Singapore, with next day's 00:00 as the exclusive upper bound. Explicit timestamps retain precision and timezone. Expired or ambiguous quotes require review before comparison.

## [QTE-004] Missing charge status
Shipping and other material charges must be represented as a known amount, confirmed zero, or not provided. Blank text must not be interpreted as zero.

## [TCO-001] Landed cost components
Total landed cost must include extended item price, shipping, applicable duties, non-recoverable taxes, and other mandatory charges supported by the quote.

## [TCO-002] Conditional tax treatment
Recoverable tax must be kept separate from non-recoverable cost. When tax treatment is unknown, the comparison remains pending review.

## [TCO-003] Currency conversion
Quotes in different currencies must use an approved exchange-rate snapshot identified by source, rate, and effective timestamp before totals are compared.

## [TCO-004] Incomplete total cost
If a material landed-cost component is not provided and has not been confirmed as zero, the supplier total is pending and no final recommendation may rely on that total.
