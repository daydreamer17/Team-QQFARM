import type { ResultReason } from '../api/types'

const taskStatuses: Record<string, string> = {
  DRAFT: 'Draft', READY: 'Ready for Analysis', QUEUED: 'Queued', RUNNING: 'Analysing',
  NEEDS_INPUT: 'Action Required', COMPLETED: 'Completed', SUCCEEDED: 'Completed', FAILED: 'Failed',
  ABANDONED: 'Abandoned', PENDING: 'Queued', STALE: 'Stale',
}

const quoteStatuses: Record<string, string> = {
  FEASIBLE: 'Meets Requirements', INFEASIBLE: 'Does Not Meet Requirements', PENDING: 'Action required',
}

const summaryStatuses: Record<string, string> = {
  PENDING: 'Queued for Generation', RUNNING: 'Generating', SUCCEEDED: 'Generated', FAILED: 'Failed', STALE: 'Stale',
}

const dispositions: Record<string, string> = {
  RECOMMENDATION_AVAILABLE: 'Recommendation Available', NO_FEASIBLE_QUOTES: 'No Feasible Quotations',
  PENDING_REVIEW: 'Information still requires confirmation', SINGLE_FEASIBLE_QUOTE: 'Only one quotation meets the requirements',
  TIED_RECOMMENDATION: 'Tied recommendations',
}

const fields: Record<string, string> = {
  supplier_name: 'Supplier Name', supplier_country: 'Supplier Country', category: 'Procurement Category', item: 'Item Description',
  manufacturer: 'Manufacturer', manufacturer_part_number: 'Manufacturer part number', package: 'Package', revision: 'Version',
  condition: 'Item condition', unit_price: 'Unit Price', currency: 'Currency', base_unit: 'Procurement Unit',
  price_basis_quantity: 'Price basis quantity', price_basis_unit: 'Price basis unit',
  packaging_type: 'Packaging type', units_per_pack: 'Units per pack',
  order_multiple_units: 'Order multiple', moq_quantity: 'Minimum order quantity', moq_unit: 'MOQ unit',
  shipping_fee_status: 'Shipping Fee Status', shipping_fee_amount: 'Shipping Fee Amount',
  other_fees_status: 'Other Fees Status', other_fees_amount: 'Other Fees Amount', tax_mode: 'Tax Treatment',
  lead_time_days: 'Lead time (days)', day_basis: 'Day basis', delivery_semantics: 'Delivery commitment',
  start_event: 'Lead-time start event', delivery_date: 'Delivery date', delivery_deadline: 'Delivery deadline',
  payment_terms: 'Payment terms', quote_date: 'Quotation Date', valid_until: 'Valid Until',
}

const quoteFieldGroups: Record<string, string> = {
  identity: 'Identity',
  specification: 'Specifications',
  specifications: 'Specifications',
  pricing: 'Pricing',
  packaging: 'Packaging',
  moq: 'MOQ',
  fees: 'Fees',
  delivery: 'Delivery',
  commercial: 'Commercial',
  '\u8eab\u4efd': 'Identity',
  '\u89c4\u683c': 'Specifications',
  '\u4ef7\u683c': 'Pricing',
  '\u5305\u88c5': 'Packaging',
  '\u8d39\u7528': 'Fees',
  '\u4ea4\u671f': 'Delivery',
  '\u5546\u52a1': 'Commercial',
}

const reasons: Record<string, string> = {
  BUDGET_EXCEEDED: 'The total cost exceeds the procurement budget.',
  CURRENCY_MISMATCH: 'The quotation currency does not match the procurement currency.',
  UNIT_PRICE_CURRENCY_MISMATCH: 'The unit-price currency does not match the quotation currency.',
  FEE_CURRENCY_MISMATCH: 'The fee currency does not match the quotation currency.',
  DELIVERY_DEADLINE_EXCEEDED: 'The expected delivery date is later than the required delivery deadline.',
  ORDER_MULTIPLE_UNIT_MISMATCH: 'The order-multiple unit does not match the comparison unit and requires confirmation.',
  MOQ_PACKAGING_UNIT_MISMATCH: 'The MOQ unit does not match the quoted packaging.',
  PRICE_BASIS_UNIT_MISMATCH: 'The price basis unit cannot be converted to the procurement unit.',
  REQUIREMENT_UNIT_CONVERSION_UNSUPPORTED: 'The procurement quantity unit cannot currently be converted.',
  SUBSTITUTE_COMPATIBILITY_REVIEW_REQUIRED: 'This quotation includes a substitute part and requires compatibility review.',
  TAX_CONVERSION_REQUIRED: 'More information is required to compare this tax treatment.',
  TAX_CALCULATION_UNSUPPORTED: 'The tax terms are outside the scope of automatic calculation.',
  FEE_AMOUNT_UNKNOWN: 'A fee amount is unconfirmed, so confirmed total cost cannot yet be calculated.',
  FEE_STATUS_UNSUPPORTED: 'The fee status is not recognised and requires review.',
  ZERO_FEE_STATUS_AMOUNT_CONFLICT: 'The fee status conflicts with the entered amount.',
  QUOTE_EXPIRED: 'The quotation has expired.',
  QUOTE_EXPIRES_BEFORE_ORDER: 'The quotation expires before the planned order date.',
  SHIPMENT_IS_NOT_ARRIVAL: 'The supplier only commits to a shipment date, which cannot confirm the arrival date.',
  DAY_BASIS_UNSUPPORTED: 'The quoted day basis is not currently supported.',
  START_EVENT_UNSUPPORTED: 'The lead-time start event is not currently supported.',
  PLANNED_ORDER_DATE_REQUIRED: 'A planned order date is required to calculate expected delivery.',
  FIELD_MISSING: 'Information required for comparison is missing.', FIELD_CONFLICT: 'The quotation contains conflicting information.',
  FIELD_VALUE_INVALID: 'A quotation field is invalid.', CONFIRMED_FEASIBLE: 'No unresolved quotation issues remain.',
  CONFIRMED_INFEASIBLE: 'Confirmed non-compliance is sufficient to exclude this quotation.',
  COST_BOUND_NOT_PROVEN: 'Unknown fees could still change the ranking.',
  COST_LOWER_BOUND_DOMINATED: 'Even at its lowest possible cost, this quotation would not become the preferred option.',
  NO_CONFIRMED_FEASIBLE_BASELINE: 'There is no confirmed feasible quotation to use as a baseline.',
  UNKNOWN_FEES_CAN_CHANGE_WINNER_OR_TIE: 'Unknown fees could change the preferred option or create a tie.',
  RANKING_UNSUPPORTED: 'The current ranking preference cannot yet be evaluated automatically.',
}

const controls: Record<string, string> = {
  APPROVED_SUPPLIER: 'Supplier eligibility', ROHS_COMPLIANCE: 'RoHS compliance', AMOUNT_APPROVAL: 'Amount approval',
}

const validationStatuses: Record<string, string> = {
  EXTRACTED: 'Extracted', VERIFIED: 'Verified', MISSING: 'Missing', CONFLICT: 'Conflicting',
}

const origins: Record<string, string> = {
  DOCUMENT: 'Source quotation', USER_INPUT: 'User input', USER_CORRECTION: 'User correction', DERIVED: 'Derived',
}

export function taskStatusLabel(value: string) { return taskStatuses[value] ?? 'Processing' }
export function quoteStatusLabel(value: string) { return quoteStatuses[value] ?? 'Pending confirmation' }
export function summaryStatusLabel(value: string) { return summaryStatuses[value] ?? 'Processing' }
export function dispositionLabel(value: string) { return dispositions[value] ?? 'Comparison updated' }
export function fieldLabel(value: string) { return fields[value] ?? 'Related information' }
export function quoteFieldGroupLabel(value: string, groupId?: string) {
  const id = groupId?.trim().toLowerCase()
  return (id ? quoteFieldGroups[id] : undefined) ?? quoteFieldGroups[value.trim()] ?? value
}
export function quoteRuleText(value: string) {
  return /[\u3400-\u9fff]/.test(value)
    ? 'Use only values explicitly supported by the source. Ambiguous information requires review.'
    : value
}
export function quoteRelationMessage(kind: string, value: string) {
  if (!/[\u3400-\u9fff]/.test(value)) return value
  const messages: Record<string, string> = {
    ALL_OR_NONE: 'Complete all related fields together, or leave all of them blank.',
    MONEY_CURRENCY: 'All monetary amounts must use the quotation currency.',
    FEE_STATUS_AMOUNT: 'The fee status and amount must be consistent.',
    MOQ_PACKAGING: 'The MOQ unit must be consistent with the packaging method and units per pack.',
    DATE_ORDER: 'The quotation date cannot be later than the validity end date.',
  }
  return messages[kind] ?? 'The related quotation fields are inconsistent and require review.'
}
export function controlLabel(value: string) { return controls[value] ?? 'Other policy requirement' }
export function validationStatusLabel(value: string) { return validationStatuses[value] ?? 'Pending verification' }
export function originLabel(value: string | null) { return value ? (origins[value] ?? 'System record') : 'Not recorded' }

export function issueQuestionText(question: string, issueType: string) {
  if (!/[\u3400-\u9fff]/.test(question)) return question
  const fallbacks: Record<string, string> = {
    CONFIRM_MISSING: 'Confirm whether the source document omits this value.',
    SHIPPING_AMOUNT: 'Provide the confirmed shipping amount.',
    POLICY_EVIDENCE_REVIEW: 'Review the policy evidence and record the outcome.',
    BATCH_FIELD_REVIEW: 'Review and confirm the quotation fields in this batch.',
    PAYMENT_INFORMATION: 'Confirm when the quoted payment term begins.',
  }
  return fallbacks[issueType] ?? 'Manual review was requested for this item.'
}

export function reasonText(reason: Pick<ResultReason, 'code' | 'message'>) {
  if (reasons[reason.code]) return reasons[reason.code]
  if (reason.code.endsWith('_MISMATCH')) return 'The quotation does not match the procurement requirements.'
  return reason.message || 'This quotation has an issue that requires attention.'
}

export function impactStatusLabel(value: string) {
  const labels: Record<string, string> = {
    NO_ISSUE: 'No Issue', REQUIRES_INVESTIGATION: 'Investigation Required', NON_BLOCKING: 'Does Not Affect Current Recommendation',
    UNDETERMINED: 'Undetermined',
  }
  return labels[value] ?? 'Pending Confirmation'
}

export function policyStatusLabel(value: string) {
  const labels: Record<string, string> = {
    OK: 'Policy Evidence Found', NO_EVIDENCE: 'No Policy Evidence Found', CONFLICT: 'Conflicting Policy Evidence', ERROR: 'Policy Retrieval Failed',
  }
  return labels[value] ?? 'Action Required'
}

export function cleanSummaryText(value: string, supplierNames: Map<string, string>) {
  let text = value
  for (const [id, name] of supplierNames) text = text.replaceAll(id, name)
  return text
    .replace(/QUOTE:quote_[a-zA-Z0-9_-]+/g, 'quotation evidence')
    .replace(/POLICY:CIT-[a-zA-Z0-9_-]+/g, 'Policy Evidence')
    .replace(/Task Revision/gi, 'Task revision')
    .replace(/\bRev\s*(\d+)\b/gi, 'Revision $1')
}
