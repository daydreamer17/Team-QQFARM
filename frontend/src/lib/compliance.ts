import type { PolicyComplianceCheck, PolicyComplianceCheckStatus, ComplianceStage } from '../api/types'
export function aggregateControlStatus(checks: PolicyComplianceCheck[], control: string): PolicyComplianceCheckStatus {
  const statuses = checks.filter((check) => check.control_code === control).map((check) => check.status)
  return (['FAIL', 'REVIEW_REQUIRED', 'NOT_EVALUATED', 'PASS'] as const).find((status) => statuses.includes(status))
    ?? (statuses.length ? 'NOT_APPLICABLE' : 'NOT_EVALUATED')
}
export function groupComplianceChecks(checks: PolicyComplianceCheck[]) {
  return [...checks.reduce((groups, check) => {
    const current = groups.get(check.control_code) ?? []
    current.push(check)
    groups.set(check.control_code, current)
    return groups
  }, new Map<string, PolicyComplianceCheck[]>()).entries()]
}
export function complianceStageLabel(stage?: ComplianceStage) {
  if (stage?.status === 'DISABLED' && stage.confirmed) return 'Disabled'
  if (stage?.confirmed) return stage.pending_count ? 'Processed · follow-up required' : 'Processed'
  return ({ PROCESSING: 'Processing', BLOCKED: 'Policy evidence requires attention', AWAITING_CONFIRMATION: 'Action required' } as Record<string, string>)[stage?.status ?? ''] ?? 'Incomplete'
}
export function complianceCheckHref(taskId: string, quoteId: string, clauseId?: string) {
  const fragment = new URLSearchParams({ quote: quoteId })
  if (clauseId) fragment.set('check', clauseId)
  return `/tasks/${taskId}/compliance#${fragment}`
}
export function complianceAnchorId(quoteId: string, clauseId?: string) {
  return `compliance-${encodeURIComponent(quoteId)}${clauseId ? `-${encodeURIComponent(clauseId)}` : ''}`
}
export function complianceStatusLabel(status: string) {
  return ({ COMPLIANT: 'Verified', NON_COMPLIANT: 'Fail the check', REVIEW_REQUIRED: 'Evidence or Review Required', NOT_EVALUATED: 'Not Evaluated', VERIFIED: 'Verified Candidate', UNVERIFIED: 'Unverified Candidate', EXCLUDED: 'Excluded by Policy', NOT_STARTED: 'Not Started', PROCESSING: 'Processing', BLOCKED: 'Blocked by Policy Evidence', AWAITING_CONFIRMATION: 'Action required', PROCESSED: 'Processed', DISABLED: 'Disabled' } as Record<string, string>)[status] ?? status
}
export function compactComplianceStatusLabel(status: string) {
  return ({
    COMPLIANT: 'Verified',
    NON_COMPLIANT: 'Failed',
    REVIEW_REQUIRED: 'Pending',
    NOT_EVALUATED: 'Unchecked',
    VERIFIED: 'Verified',
    UNVERIFIED: 'Unverified',
    EXCLUDED: 'Excluded',
    NOT_STARTED: 'Not started',
    PROCESSING: 'Checking',
    BLOCKED: 'Blocked',
    AWAITING_CONFIRMATION: 'Pending',
    PROCESSED: 'Processed',
    DISABLED: 'Disabled',
  } as Record<string, string>)[status] ?? status
}
export function checkStatusLabel(status: PolicyComplianceCheckStatus) {
  return ({ PASS: 'Passed', FAIL: 'Failed', REVIEW_REQUIRED: 'Review required', NOT_APPLICABLE: 'Not applicable at this stage', NOT_EVALUATED: 'Not evaluated' })[status]
}
export function compactCheckStatusLabel(status: PolicyComplianceCheckStatus) {
  return ({ PASS: 'Passed', FAIL: 'Failed', REVIEW_REQUIRED: 'Review', NOT_APPLICABLE: 'N/A', NOT_EVALUATED: 'Unchecked' })[status]
}
export function executionStageLabel(stage?: string) {
  return ({ BEFORE_RECOMMENDATION: 'Before recommendation', BEFORE_PUBLICATION: 'Before publication', AFTER_SELECTION: 'After supplier selection' } as Record<string, string>)[stage ?? ''] ?? 'Stage not specified'
}
export function complianceReasonLabel(code: string) {
  return ({ EVIDENCE_MISSING: 'Supporting evidence is missing', COVERAGE_UNCONFIRMED: 'Evidence coverage has not been confirmed', VALIDITY_UNSPECIFIED: 'The evidence validity period is not stated', EVIDENCE_EXPIRED: 'The evidence has expired', EVIDENCE_SCOPE_MISMATCH: 'The evidence does not match the supplier or product scope', EVIDENCE_CONFLICT: 'The submitted evidence conflicts; review and replace it', EVIDENCE_NOT_YET_EFFECTIVE: 'The evidence is not yet effective', EVIDENCE_CONFIRMED: 'Evidence confirmed', EXECUTABLE_PARAMETERS_REQUIRED: 'The clause lacks reviewed execution parameters', SUPPLIER_IDENTITY_UNCONFIRMED: 'Supplier identity requires confirmation', EXECUTION_STAGE_DEFERRED: 'This check will run at the specified stage', AMOUNT_UNKNOWN: 'The amount is not yet confirmed', CURRENCY_MISMATCH: 'The amount currency does not match', AMOUNT_RULE_EVALUATED: 'The amount condition has been evaluated', AMOUNT_RULE_NOT_TRIGGERED: 'The amount approval threshold was not reached', AMOUNT_APPROVAL_MISSING: 'The threshold was reached but approval evidence is missing', AMOUNT_APPROVAL_SCOPE_MISMATCH: 'The approval record applies to another supplier', AMOUNT_APPROVAL_CONFLICT: 'The amount approval records conflict; review and replace them', AMOUNT_APPROVAL_CURRENCY_MISMATCH: 'The approval currency does not match the procurement currency', AMOUNT_APPROVAL_INSUFFICIENT: 'The approved amount does not cover this option', AMOUNT_APPROVAL_REJECTED: 'The approval record states that approval was not granted', AMOUNT_APPROVAL_CONFIRMED: 'The approval record has been verified and covers this option', RULE_NOT_YET_REVIEWED: 'The rule was reviewed after this assessment', QUOTE_NOT_FEASIBLE: 'The quotation does not yet meet procurement requirements' } as Record<string, string>)[code] ?? code
}
