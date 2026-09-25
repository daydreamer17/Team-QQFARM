import type { PolicyComplianceSupplierAssessment, SupplierComparisonResult } from '../api/types'
import { controlLabel, reasonText } from './presentation'

export interface SupplierSelectionExplanation {
  label: string
  detail: string
  tone: 'good' | 'warning' | 'danger' | 'neutral'
}

function moneyText(currency: string | undefined, value: number) {
  return `${currency ?? ''} ${new Intl.NumberFormat('en-SG', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Math.abs(value))}`.trim()
}

function numericValue(value: string | null | undefined) {
  if (value === null || value === undefined || value.trim() === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function rateValue(value: string | null | undefined) {
  const parsed = numericValue(value)
  return parsed !== null && parsed >= 0 && parsed <= 1 ? parsed : null
}

function percentage(value: number) {
  return `${(value * 100).toFixed(1)}%`
}

function costTradeoff(
  supplier: SupplierComparisonResult,
  primary: SupplierComparisonResult,
  currency: string | undefined,
) {
  const supplierCost = numericValue(supplier.total_cost)
  const primaryCost = numericValue(primary.total_cost)
  if (supplierCost === null || primaryCost === null) return null
  const difference = supplierCost - primaryCost
  if (difference === 0) return 'Total cost is the same as the recommended supplier'
  return `Total cost is ${moneyText(currency, Math.abs(difference))} ${difference > 0 ? 'higher' : 'lower'} than the recommended supplier`
}

function daysBetween(value: string | null, baseline: string | null) {
  if (!value || !baseline) return null
  const current = Date.parse(`${value}T00:00:00Z`)
  const reference = Date.parse(`${baseline}T00:00:00Z`)
  if (!Number.isFinite(current) || !Number.isFinite(reference)) return null
  return Math.round((current - reference) / 86_400_000)
}

function criterionReason(
  supplier: SupplierComparisonResult,
  primary: SupplierComparisonResult,
  ranking: string | undefined,
  currency: string | undefined,
  recommended: boolean,
) {
  if (ranking === 'LOWEST_CONFIRMED_TOTAL_COST') {
    if (recommended) return 'Lowest confirmed total cost'
    const supplierCost = numericValue(supplier.total_cost)
    const primaryCost = numericValue(primary.total_cost)
    if (supplierCost === null || primaryCost === null) return 'Insufficient total-cost data for the current ranking criterion'
    const difference = supplierCost - primaryCost
    if (difference === 0) return 'Total cost is the same as the recommended supplier; later ranking criteria did not place it first'
    return `Total cost is ${moneyText(currency, Math.abs(difference))} ${difference > 0 ? 'higher' : 'lower'} than the recommended supplier`
  }
  if (ranking === 'FASTEST_CONFIRMED_DELIVERY') {
    if (recommended) return 'Earliest expected delivery date'
    const difference = daysBetween(supplier.estimated_arrival_date, primary.estimated_arrival_date)
    if (difference === null) return 'Delivery was not ranked ahead of the recommended supplier'
    if (difference === 0) return 'Expected delivery date is the same as the recommended supplier'
    return `Expected delivery is ${Math.abs(difference)} days ${difference > 0 ? 'later' : 'earlier'} than the recommended supplier`
  }
  if (ranking === 'LONGEST_CONFIRMED_PAYMENT_TERM') {
    const days = supplier.payment_term?.net_days ?? null
    const primaryDays = primary.payment_term?.net_days ?? null
    if (recommended) return days === null ? 'Longest confirmed payment term' : `Longest confirmed payment term (${days} days)`
    if (days === null || primaryDays === null) return 'Payment terms were not ranked ahead of the recommended supplier'
    const difference = days - primaryDays
    if (difference === 0) return 'Payment term is the same as the recommended supplier'
    return `Payment term is ${Math.abs(difference)} days ${difference > 0 ? 'longer' : 'shorter'} than the recommended supplier`
  }
  if (ranking === 'HIGHEST_SUPPLIER_PERFORMANCE') {
    const grade = supplier.history_snapshot?.overall_grade
    const primaryGrade = primary.history_snapshot?.overall_grade
    if (recommended) return grade ? `Highest supplier performance (${grade})` : 'Highest supplier performance'
    return grade && primaryGrade
      ? `Overall historical grade is ${grade}; the recommended supplier is ${primaryGrade}`
      : 'Overall historical performance was not ranked ahead of the recommended supplier'
  }
  if (ranking === 'HIGHEST_HISTORICAL_ON_TIME_RATE') {
    const rate = rateValue(supplier.history_snapshot?.on_time?.rate)
    const primaryRate = rateValue(primary.history_snapshot?.on_time?.rate)
    if (recommended) return rate === null ? 'Highest historical on-time rate' : `Highest historical on-time rate (${percentage(rate)})`
    if (rate === null || primaryRate === null) return 'Insufficient historical on-time data for the current ranking criterion'
    const difference = rate - primaryRate
    if (difference === 0) return 'Historical on-time rate is the same as the recommended supplier'
    return `Historical on-time rate is ${Math.abs(difference * 100).toFixed(1)} percentage points ${difference > 0 ? 'higher' : 'lower'} than the recommended supplier`
  }
  if (ranking === 'LOWEST_HISTORICAL_REJECTED_LINE_RATE') {
    const rate = rateValue(supplier.history_snapshot?.rejected_lines?.rate)
    const primaryRate = rateValue(primary.history_snapshot?.rejected_lines?.rate)
    if (recommended) return rate === null ? 'Lowest historical rejected order-line rate' : `Lowest historical rejected order-line rate (${percentage(rate)})`
    if (rate === null || primaryRate === null) return 'Insufficient historical rejected-line data for the current ranking criterion'
    const difference = rate - primaryRate
    if (difference === 0) return 'Historical rejected-line rate is the same as the recommended supplier'
    return `Historical rejected-line rate is ${Math.abs(difference * 100).toFixed(1)} percentage points ${difference > 0 ? 'higher' : 'lower'} than the recommended supplier`
  }
  return recommended ? 'Ranked first under the current criteria' : 'Not ranked ahead of the recommended supplier'
}

export function supplierSelectionExplanation(
  supplier: SupplierComparisonResult,
  primary: SupplierComparisonResult | undefined,
  ranking: string | undefined,
  currency: string | undefined,
  recommended: boolean,
): SupplierSelectionExplanation {
  if (supplier.failed_reasons.length > 0) {
    return { label: 'Fail the check', detail: reasonText(supplier.failed_reasons[0]), tone: 'danger' }
  }
  if (supplier.pending_reasons.length > 0) {
    return { label: 'Pending Confirmation', detail: reasonText(supplier.pending_reasons[0]), tone: 'warning' }
  }
  if (!primary) {
    return { label: 'Comparable', detail: 'Meets the current mandatory procurement requirements', tone: 'neutral' }
  }
  const reason = criterionReason(supplier, primary, ranking, currency, recommended)
  const tradeoff = ranking === 'LOWEST_CONFIRMED_TOTAL_COST' || recommended
    ? null
    : costTradeoff(supplier, primary, currency)
  return {
    label: recommended ? 'Basis for Recommendation' : 'Reason not selected',
    detail: [reason, tradeoff].filter(Boolean).join('; '),
    tone: 'good',
  }
}

function policyIssueLabels(assessment: PolicyComplianceSupplierAssessment) {
  return [...new Set(assessment.checks
    .filter((check) => ['FAIL', 'REVIEW_REQUIRED', 'NOT_EVALUATED'].includes(check.status))
    .map((check) => controlLabel(check.control_code)))]
}

export function withPolicyAssessment(
  commercial: SupplierSelectionExplanation,
  assessment: PolicyComplianceSupplierAssessment | undefined,
): SupplierSelectionExplanation {
  if (!assessment) return commercial

  const issues = policyIssueLabels(assessment)
  const issueText = issues.length > 0 ? issues.join(', ') : 'policy requirements'
  if (assessment.eligibility === 'EXCLUDED' || assessment.status === 'NON_COMPLIANT') {
    return {
      label: 'Excluded by Policy',
      detail: `Compliance review failed: ${issueText}. Excluded from recommendation candidates. Commercial comparison: ${commercial.detail}`,
      tone: 'danger',
    }
  }
  if (assessment.eligibility === 'UNVERIFIED'
      || assessment.status === 'REVIEW_REQUIRED'
      || assessment.status === 'NOT_EVALUATED') {
    return {
      label: 'Policy review required',
      detail: `${issueText} have not been fully verified. It is not prioritised while verified eligible candidates are available. Commercial comparison: ${commercial.detail}`,
      tone: 'warning',
    }
  }
  if (assessment.eligibility === 'VERIFIED' || assessment.status === 'COMPLIANT') {
    return {
      ...commercial,
      detail: `Compliance review passed. ${commercial.detail}`,
    }
  }
  return commercial
}

export function policyAwareSupplierSelectionExplanation(
  supplier: SupplierComparisonResult,
  primary: SupplierComparisonResult | undefined,
  ranking: string | undefined,
  currency: string | undefined,
  recommended: boolean,
  assessment: PolicyComplianceSupplierAssessment | undefined,
): SupplierSelectionExplanation {
  const commercial = supplierSelectionExplanation(supplier, primary, ranking, currency, recommended)
  return withPolicyAssessment(commercial, assessment)
}
