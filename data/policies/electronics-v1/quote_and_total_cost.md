# Fictional Electronics Quote and Total Cost Policy

This synthetic policy exists only for the NUS-ISS hackathon demonstration.

## [QTE-001] Required commercial fields
Every supplier quote must state the quote currency, unit price, quoted quantity, minimum order quantity, shipping charge status, payment terms, quote date, and validity end date.

## [QTE-002] Supplier and quote identity
Every quote must include a supplier name, supplier identifier, and supplier quote reference that can be matched to confirmed procurement records.

## [QTE-003] Dates and validity
The quote date and validity end date must be explicit. A quote evaluated on or after its validity end date requires review before comparison.

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
