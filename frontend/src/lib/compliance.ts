import type { PolicyComplianceCheck, PolicyComplianceCheckStatus, ComplianceStage } from '../api/types'
export function aggregateControlStatus(checks: PolicyComplianceCheck[], control: string): PolicyComplianceCheckStatus {
  const statuses = checks.filter((check) => check.control_code === control).map((check) => check.status)
  return (['FAIL', 'REVIEW_REQUIRED', 'NOT_EVALUATED', 'PASS'] as const).find((status) => statuses.includes(status))
    ?? (statuses.length ? 'NOT_APPLICABLE' : 'NOT_EVALUATED')
}
export function complianceStageLabel(stage?: ComplianceStage) {
  if (stage?.status === 'DISABLED' && stage.confirmed) return '未启用'
  if (stage?.confirmed) return stage.pending_count ? '已处理·有待补充' : '已处理'
  return ({ PROCESSING: '检查中', BLOCKED: '需处理制度依据', AWAITING_CONFIRMATION: '等待确认' } as Record<string, string>)[stage?.status ?? ''] ?? '未完成'
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
  return ({ COMPLIANT: '已核验', NON_COMPLIANT: '不符合要求', REVIEW_REQUIRED: '待补充或复核', NOT_EVALUATED: '尚未核验', VERIFIED: '已核验候选', UNVERIFIED: '未核验候选', EXCLUDED: '制度排除', NOT_STARTED: '尚未开始', PROCESSING: '检查中', BLOCKED: '制度依据待处理', AWAITING_CONFIRMATION: '等待确认', PROCESSED: '已处理', DISABLED: '未启用' } as Record<string, string>)[status] ?? status
}
export function checkStatusLabel(status: PolicyComplianceCheckStatus) {
  return ({ PASS: '通过', FAIL: '不通过', REVIEW_REQUIRED: '待复核', NOT_APPLICABLE: '此阶段不适用', NOT_EVALUATED: '未执行' })[status]
}
export function executionStageLabel(stage?: string) {
  return ({ BEFORE_RECOMMENDATION: '推荐前', BEFORE_PUBLICATION: '发布前', AFTER_SELECTION: '选择供应商后' } as Record<string, string>)[stage ?? ''] ?? '未指定阶段'
}
export function complianceReasonLabel(code: string) {
  return ({ EVIDENCE_MISSING: '缺少对应证明材料', COVERAGE_UNCONFIRMED: '尚未人工确认材料覆盖范围', VALIDITY_UNSPECIFIED: '材料有效期未说明', EVIDENCE_EXPIRED: '材料已过期', EVIDENCE_SCOPE_MISMATCH: '材料中的供应商或产品范围不匹配', EVIDENCE_CONFLICT: '多份材料互相冲突，请核对并替换', EVIDENCE_NOT_YET_EFFECTIVE: '材料尚未生效', EVIDENCE_CONFIRMED: '材料已核对', EXECUTABLE_PARAMETERS_REQUIRED: '条款缺少经人工审核的执行参数', SUPPLIER_IDENTITY_UNCONFIRMED: '供应商身份待确认', EXECUTION_STAGE_DEFERRED: '将在指定阶段检查', AMOUNT_UNKNOWN: '金额尚未确定', CURRENCY_MISMATCH: '金额币种不匹配', AMOUNT_RULE_EVALUATED: '金额条件已检查', RULE_NOT_YET_REVIEWED: '规则审核时间晚于本次评估', QUOTE_NOT_FEASIBLE: '报价尚未满足采购要求' } as Record<string, string>)[code] ?? code
}
