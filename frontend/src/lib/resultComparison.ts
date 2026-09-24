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
  if (difference === 0) return '总成本与推荐供应商相同'
  return `总成本比推荐供应商${difference > 0 ? '高' : '低'} ${moneyText(currency, difference)}`
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
    if (recommended) return '已确认总成本最低'
    const supplierCost = numericValue(supplier.total_cost)
    const primaryCost = numericValue(primary.total_cost)
    if (supplierCost === null || primaryCost === null) return '总成本数据不足，未达到当前排序标准'
    const difference = supplierCost - primaryCost
    if (difference === 0) return '总成本与推荐供应商相同，按后续排序条件未优先'
    return `总成本比推荐供应商${difference > 0 ? '高' : '低'} ${moneyText(currency, difference)}`
  }
  if (ranking === 'FASTEST_CONFIRMED_DELIVERY') {
    if (recommended) return '预计到货时间最早'
    const difference = daysBetween(supplier.estimated_arrival_date, primary.estimated_arrival_date)
    if (difference === null) return '到货时间未优先于推荐供应商'
    if (difference === 0) return '预计到货时间与推荐供应商相同'
    return `预计到货比推荐供应商${difference > 0 ? '晚' : '早'} ${Math.abs(difference)} 天`
  }
  if (ranking === 'LONGEST_CONFIRMED_PAYMENT_TERM') {
    const days = supplier.payment_term?.net_days ?? null
    const primaryDays = primary.payment_term?.net_days ?? null
    if (recommended) return days === null ? '已确认账期最长' : `已确认账期最长（${days} 天）`
    if (days === null || primaryDays === null) return '账期未优先于推荐供应商'
    const difference = days - primaryDays
    if (difference === 0) return '账期与推荐供应商相同'
    return `账期比推荐供应商${difference > 0 ? '长' : '短'} ${Math.abs(difference)} 天`
  }
  if (ranking === 'HIGHEST_SUPPLIER_PERFORMANCE') {
    const grade = supplier.history_snapshot?.overall_grade
    const primaryGrade = primary.history_snapshot?.overall_grade
    if (recommended) return grade ? `综合历史等级最高（${grade}）` : '综合历史等级最高'
    return grade && primaryGrade
      ? `综合历史等级为 ${grade}，推荐供应商为 ${primaryGrade}`
      : '综合历史表现未优先于推荐供应商'
  }
  if (ranking === 'HIGHEST_HISTORICAL_ON_TIME_RATE') {
    const rate = rateValue(supplier.history_snapshot?.on_time?.rate)
    const primaryRate = rateValue(primary.history_snapshot?.on_time?.rate)
    if (recommended) return rate === null ? '历史准时率最高' : `历史准时率最高（${percentage(rate)}）`
    if (rate === null || primaryRate === null) return '历史准时率数据不足，未达到当前排序标准'
    const difference = rate - primaryRate
    if (difference === 0) return '历史准时率与推荐供应商相同'
    return `历史准时率比推荐供应商${difference > 0 ? '高' : '低'} ${Math.abs(difference * 100).toFixed(1)} 个百分点`
  }
  if (ranking === 'LOWEST_HISTORICAL_REJECTED_LINE_RATE') {
    const rate = rateValue(supplier.history_snapshot?.rejected_lines?.rate)
    const primaryRate = rateValue(primary.history_snapshot?.rejected_lines?.rate)
    if (recommended) return rate === null ? '历史拒收订单行率最低' : `历史拒收订单行率最低（${percentage(rate)}）`
    if (rate === null || primaryRate === null) return '历史拒收率数据不足，未达到当前排序标准'
    const difference = rate - primaryRate
    if (difference === 0) return '历史拒收订单行率与推荐供应商相同'
    return `历史拒收订单行率比推荐供应商${difference > 0 ? '高' : '低'} ${Math.abs(difference * 100).toFixed(1)} 个百分点`
  }
  return recommended ? '当前排序下优先' : '当前排序下未优先于推荐供应商'
}

export function supplierSelectionExplanation(
  supplier: SupplierComparisonResult,
  primary: SupplierComparisonResult | undefined,
  ranking: string | undefined,
  currency: string | undefined,
  recommended: boolean,
): SupplierSelectionExplanation {
  if (supplier.failed_reasons.length > 0) {
    return { label: '不符合要求', detail: reasonText(supplier.failed_reasons[0]), tone: 'danger' }
  }
  if (supplier.pending_reasons.length > 0) {
    return { label: '待确认', detail: reasonText(supplier.pending_reasons[0]), tone: 'warning' }
  }
  if (!primary) {
    return { label: '可参与比较', detail: '满足当前采购硬性条件', tone: 'neutral' }
  }
  const reason = criterionReason(supplier, primary, ranking, currency, recommended)
  const tradeoff = ranking === 'LOWEST_CONFIRMED_TOTAL_COST' || recommended
    ? null
    : costTradeoff(supplier, primary, currency)
  return {
    label: recommended ? '推荐依据' : '未选原因',
    detail: [reason, tradeoff].filter(Boolean).join('；'),
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
  const issueText = issues.length > 0 ? issues.join('、') : '制度要求'
  if (assessment.eligibility === 'EXCLUDED' || assessment.status === 'NON_COMPLIANT') {
    return {
      label: '制度排除',
      detail: `制度检查不通过：${issueText}；已从推荐候选中排除。商业比较：${commercial.detail}`,
      tone: 'danger',
    }
  }
  if (assessment.eligibility === 'UNVERIFIED'
      || assessment.status === 'REVIEW_REQUIRED'
      || assessment.status === 'NOT_EVALUATED') {
    return {
      label: '制度待复核',
      detail: `${issueText}尚未完成核验；有已核验合格候选时不优先推荐。商业比较：${commercial.detail}`,
      tone: 'warning',
    }
  }
  if (assessment.eligibility === 'VERIFIED' || assessment.status === 'COMPLIANT') {
    return {
      ...commercial,
      detail: `制度检查已通过；${commercial.detail}`,
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
