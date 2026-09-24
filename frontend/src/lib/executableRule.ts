const supported = ['APPROVED_SUPPLIER', 'ROHS_COMPLIANCE', 'AMOUNT_APPROVAL']
const fields = ['supplier_id', 'manufacturer', 'manufacturer_part_number']
const stages = { BEFORE_RECOMMENDATION: '推荐前', BEFORE_PUBLICATION: '发布前', AFTER_SELECTION: '选择供应商后' }

export function validateExecutableRule(value: Record<string, unknown>, controlCode: string): string | null {
  if (!('version' in value)) return null
  if (value.version !== 'compliance-rule/1.0' || value.control_code !== controlCode || !supported.includes(controlCode)) return '规则版本或检查类型不匹配。'
  if (typeof value.reviewed_by !== 'string' || !value.reviewed_by.trim() || typeof value.reviewed_at !== 'string' || !value.reviewed_at || !Number.isFinite(Date.parse(value.reviewed_at))) return '请填写审核人并逐项确认执行规则。'
  if (value.date_basis !== 'EVALUATED_AT' || !Object.keys(stages).includes(String(value.execution_stage))) return '请选择执行阶段，日期必须以本次评估时间为准。'
  if (['missing_outcome', 'expired_outcome', 'mismatch_outcome'].some((key) => !['FAIL', 'REVIEW_REQUIRED'].includes(String(value[key])))) return '请指定缺失、过期和范围不符时的处理。'
  if (!Array.isArray(value.matching_fields) || new Set(value.matching_fields).size !== value.matching_fields.length || value.matching_fields.some((field) => !fields.includes(field))) return '匹配范围无效。'
  if (controlCode === 'AMOUNT_APPROVAL') {
    if (value.matching_fields.length || value.monetary_basis !== 'TOTAL_COST' || !/^[A-Z]{3}$/.test(String(value.currency))
      || typeof value.threshold !== 'string' || !/^\d+(\.\d+)?$/.test(value.threshold)
      || !['GT', 'GTE', 'LT', 'LTE'].includes(String(value.operator)) || typeof value.action !== 'string' || !value.action.trim()) return '请填写准确的币种、金额门槛、比较条件与触发动作。'
  } else if (!(controlCode === 'ROHS_COMPLIANCE' ? fields : ['supplier_id']).every((field) => (value.matching_fields as unknown[]).includes(field))) return '当前检查类型缺少必需的匹配字段。'
  return null
}
