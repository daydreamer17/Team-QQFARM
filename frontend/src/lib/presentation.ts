import type { ResultReason } from '../api/types'

const taskStatuses: Record<string, string> = {
  DRAFT: '草稿', READY: '待分析', QUEUED: '等待处理', RUNNING: '分析中',
  NEEDS_INPUT: '需要确认', COMPLETED: '已完成', SUCCEEDED: '已完成', FAILED: '处理失败',
  ABANDONED: '已废弃', PENDING: '等待处理', STALE: '已失效',
}

const quoteStatuses: Record<string, string> = {
  FEASIBLE: '符合采购要求', INFEASIBLE: '不符合采购要求', PENDING: '等待确认',
}

const summaryStatuses: Record<string, string> = {
  PENDING: '等待生成', RUNNING: '正在生成', SUCCEEDED: '已生成', FAILED: '生成失败', STALE: '已失效',
}

const dispositions: Record<string, string> = {
  RECOMMENDATION_AVAILABLE: '已有推荐方案', NO_FEASIBLE_QUOTES: '没有符合要求的报价',
  PENDING_REVIEW: '仍有信息需要确认', SINGLE_FEASIBLE_QUOTE: '仅有一份报价符合要求',
  TIED_RECOMMENDATION: '存在并列推荐方案',
}

const fields: Record<string, string> = {
  supplier_name: '供应商名称', supplier_country: '供应商所在国家', category: '采购类别', item: '商品名称',
  manufacturer: '制造商', manufacturer_part_number: '制造商料号', package: '封装', revision: '版本',
  condition: '物料状态', unit_price: '单价', currency: '币种', base_unit: '采购计量单位',
  price_basis_quantity: '单价对应数量', price_basis_unit: '计价单位',
  packaging_type: '包装类型', units_per_pack: '每包装数量',
  order_multiple_units: '订购倍数', moq_quantity: '最低订购量', moq_unit: '最低订购量单位',
  shipping_fee_status: '运费状态', shipping_fee_amount: '运费金额',
  other_fees_status: '其他费用状态', other_fees_amount: '其他费用金额', tax_mode: '税费方式',
  lead_time_days: '交期天数', day_basis: '交期计算方式', delivery_semantics: '交付承诺',
  start_event: '交期起算时间', delivery_date: '交付日期', delivery_deadline: '交付截止日期',
  payment_terms: '付款条件', quote_date: '报价日期', valid_until: '报价有效期',
}

const reasons: Record<string, string> = {
  BUDGET_EXCEEDED: '总成本超过采购预算。',
  CURRENCY_MISMATCH: '报价币种与采购任务币种不一致。',
  UNIT_PRICE_CURRENCY_MISMATCH: '单价币种与报价币种不一致。',
  FEE_CURRENCY_MISMATCH: '费用币种与报价币种不一致。',
  DELIVERY_DEADLINE_EXCEEDED: '预计到货日期晚于要求的交付截止日期。',
  ORDER_MULTIPLE_UNIT_MISMATCH: '订购倍数的单位与采购比较单位不一致，需要确认。',
  MOQ_PACKAGING_UNIT_MISMATCH: '最低订购量单位与报价包装方式不一致。',
  PRICE_BASIS_UNIT_MISMATCH: '计价单位无法换算为采购比较单位。',
  REQUIREMENT_UNIT_CONVERSION_UNSUPPORTED: '采购数量单位暂时无法换算。',
  SUBSTITUTE_COMPATIBILITY_REVIEW_REQUIRED: '该报价涉及替代料，需要人工确认兼容性。',
  TAX_CONVERSION_REQUIRED: '当前税费方式需要补充换算信息后才能比较。',
  TAX_CALCULATION_UNSUPPORTED: '当前税费条件超出自动计算范围。',
  FEE_AMOUNT_UNKNOWN: '费用金额尚未确认，暂时无法计算确定总成本。',
  FEE_STATUS_UNSUPPORTED: '费用状态无法识别，需要人工确认。',
  ZERO_FEE_STATUS_AMOUNT_CONFLICT: '费用状态与填写金额互相冲突。',
  QUOTE_EXPIRED: '报价已经失效。',
  QUOTE_EXPIRES_BEFORE_ORDER: '报价有效期早于计划下单日期。',
  SHIPMENT_IS_NOT_ARRIVAL: '供应商只承诺发货时间，不能据此确认到货时间。',
  DAY_BASIS_UNSUPPORTED: '交期采用了当前不支持的日历口径。',
  START_EVENT_UNSUPPORTED: '交期起算条件暂不支持自动计算。',
  PLANNED_ORDER_DATE_REQUIRED: '缺少计划下单日期，无法计算预计到货时间。',
  FIELD_MISSING: '缺少影响比较的必要信息。', FIELD_CONFLICT: '报价中的信息互相冲突。',
  FIELD_VALUE_INVALID: '报价字段值无效。', CONFIRMED_FEASIBLE: '没有未解决的报价问题。',
  CONFIRMED_INFEASIBLE: '已确认的不符合项足以排除该报价。',
  COST_BOUND_NOT_PROVEN: '未知费用仍可能改变排序。',
  COST_LOWER_BOUND_DOMINATED: '即使按最低可能成本计算，该报价也不会成为最优方案。',
  NO_CONFIRMED_FEASIBLE_BASELINE: '目前没有已确认可行的报价作为比较基准。',
  UNKNOWN_FEES_CAN_CHANGE_WINNER_OR_TIE: '未知费用可能改变最优方案或造成并列。',
  RANKING_UNSUPPORTED: '当前排序偏好暂不支持自动证明。',
}

const controls: Record<string, string> = {
  APPROVED_SUPPLIER: '供应商准入要求', ROHS_COMPLIANCE: 'RoHS 要求', AMOUNT_APPROVAL: '金额审批要求',
}

const validationStatuses: Record<string, string> = {
  EXTRACTED: '已提取', VERIFIED: '已核验', MISSING: '缺失', CONFLICT: '有冲突',
}

const origins: Record<string, string> = {
  DOCUMENT: '报价文件', USER_INPUT: '人工补充', USER_CORRECTION: '人工校正', DERIVED: '系统计算',
}

export function taskStatusLabel(value: string) { return taskStatuses[value] ?? '处理中' }
export function quoteStatusLabel(value: string) { return quoteStatuses[value] ?? '状态待确认' }
export function summaryStatusLabel(value: string) { return summaryStatuses[value] ?? '处理中' }
export function dispositionLabel(value: string) { return dispositions[value] ?? '比较结果已更新' }
export function fieldLabel(value: string) { return fields[value] ?? '相关信息' }
export function controlLabel(value: string) { return controls[value] ?? '其他制度要求' }
export function validationStatusLabel(value: string) { return validationStatuses[value] ?? '待核验' }
export function originLabel(value: string | null) { return value ? (origins[value] ?? '系统记录') : '未记录' }

export function reasonText(reason: Pick<ResultReason, 'code' | 'message'>) {
  if (reasons[reason.code]) return reasons[reason.code]
  if (reason.code.endsWith('_MISMATCH')) return '报价内容与采购要求不一致。'
  return reason.message || '该报价存在需要处理的问题。'
}

export function impactStatusLabel(value: string) {
  const labels: Record<string, string> = {
    NO_ISSUE: '无需处理', REQUIRES_INVESTIGATION: '需要调查', NON_BLOCKING: '不影响当前推荐',
    UNDETERMINED: '尚不能确定',
  }
  return labels[value] ?? '待确认'
}

export function policyStatusLabel(value: string) {
  const labels: Record<string, string> = {
    OK: '已找到制度依据', NO_EVIDENCE: '未找到制度依据', CONFLICT: '制度依据有冲突', ERROR: '制度检索失败',
  }
  return labels[value] ?? '需要确认'
}

export function cleanSummaryText(value: string, supplierNames: Map<string, string>) {
  let text = value
  for (const [id, name] of supplierNames) text = text.replaceAll(id, name)
  return text
    .replace(/QUOTE:quote_[a-zA-Z0-9_-]+/g, '报价依据')
    .replace(/POLICY:CIT-[a-zA-Z0-9_-]+/g, '制度依据')
    .replace(/Task Revision/gi, '任务版本')
    .replace(/\bRev\s*(\d+)\b/gi, '第 $1 版')
}
