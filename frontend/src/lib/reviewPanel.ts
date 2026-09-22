import { ApiClientError } from '../api/client'
import type { QuoteField, TaskQuote } from '../api/types'

const feeStatusFields = new Set(['shipping_fee_status', 'other_fees_status'])
const labels: Record<string, string> = {
  shipping_fee_status: '运费状态', other_fees_status: '其他费用状态', payment_terms: '付款条件',
}

export function canConfirmReviewField(field: QuoteField | undefined) {
  return Boolean(
    field && field.raw_value !== null && field.raw_value !== undefined &&
    field.normalized_value !== null && field.normalized_value !== undefined &&
    !(feeStatusFields.has(field.field_name) && field.normalized_value === 'UNKNOWN'),
  )
}

export function fieldCorrectionErrorMessage(error: unknown, quotes: TaskQuote[]) {
  if (!(error instanceof ApiClientError) || error.code !== 'field_correction_batch_invalid') return null
  const errors = Array.isArray(error.details.errors) ? error.details.errors : []
  const filenames = new Map(quotes.map((quote) => [quote.quote_id, quote.original_filename]))
  const summary = errors.map((item) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return null
    const row = item as Record<string, unknown>
    const fieldName = typeof row.field_name === 'string' ? row.field_name : '未知字段'
    const filename = typeof row.quote_id === 'string' ? filenames.get(row.quote_id) ?? row.quote_id : '报价'
    const label = labels[fieldName] ?? fieldName
    return row.code === 'field_version_conflict'
      ? `${filename} 的“${label}”已更新，请重新核对`
      : `${filename} 的“${label}”修正值无效，请填写已确认的实际值`
  }).filter((item): item is string => item !== null).join('；')
  return `提交失败：${summary || '修正内容未通过校验'}。`
}
