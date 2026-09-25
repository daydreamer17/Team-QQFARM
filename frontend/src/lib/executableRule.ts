const supported = ['APPROVED_SUPPLIER', 'ROHS_COMPLIANCE', 'AMOUNT_APPROVAL']
const fields = ['supplier_id', 'manufacturer', 'manufacturer_part_number']
const stages = { BEFORE_RECOMMENDATION: 'Before recommendation', BEFORE_PUBLICATION: 'Before publication', AFTER_SELECTION: 'After supplier selection' }

export function validateExecutableRule(value: Record<string, unknown>, controlCode: string): string | null {
  if (!('version' in value)) return null
  if (value.version !== 'compliance-rule/1.0' || value.control_code !== controlCode || !supported.includes(controlCode)) return 'The rule version or check type does not match.'
  if (typeof value.reviewed_by !== 'string' || !value.reviewed_by.trim() || typeof value.reviewed_at !== 'string' || !value.reviewed_at || !Number.isFinite(Date.parse(value.reviewed_at))) return 'Enter the reviewer and confirm each execution rule.'
  if (value.date_basis !== 'EVALUATED_AT' || !Object.keys(stages).includes(String(value.execution_stage))) return 'Select an execution stage. The date basis must be the current assessment time.'
  if (['missing_outcome', 'expired_outcome', 'mismatch_outcome'].some((key) => !['FAIL', 'REVIEW_REQUIRED'].includes(String(value[key])))) return 'Specify the outcome for missing, expired or out-of-scope evidence.'
  if (!Array.isArray(value.matching_fields) || new Set(value.matching_fields).size !== value.matching_fields.length || value.matching_fields.some((field) => !fields.includes(field))) return 'The matching scope is invalid.'
  if (controlCode === 'AMOUNT_APPROVAL') {
    if (value.matching_fields.length || value.monetary_basis !== 'TOTAL_COST' || !/^[A-Z]{3}$/.test(String(value.currency))
      || typeof value.threshold !== 'string' || !/^\d+(\.\d+)?$/.test(value.threshold)
      || !['GT', 'GTE', 'LT', 'LTE'].includes(String(value.operator)) || typeof value.action !== 'string' || !value.action.trim()) return 'Enter the currency, amount threshold, comparison operator and triggered action.'
  } else if (!(controlCode === 'ROHS_COMPLIANCE' ? fields : ['supplier_id']).every((field) => (value.matching_fields as unknown[]).includes(field))) return 'Required matching fields are missing for this check type.'
  return null
}
