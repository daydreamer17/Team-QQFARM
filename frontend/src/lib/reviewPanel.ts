import { ApiClientError } from '../api/client'
import type { QuoteField, TaskQuote } from '../api/types'

const feeStatusFields = new Set(['shipping_fee_status', 'other_fees_status'])
const labels: Record<string, string> = {
  shipping_fee_status: 'Shipping Fee Status', other_fees_status: 'Other Fees Status', payment_terms: 'Payment terms',
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
    const fieldName = typeof row.field_name === 'string' ? row.field_name : 'Unknown Fields'
    const filename = typeof row.quote_id === 'string' ? filenames.get(row.quote_id) ?? row.quote_id : 'Quotation'
    const label = labels[fieldName] ?? fieldName
    return row.code === 'field_version_conflict'
      ? `${label} in ${filename} has changed. Review it again.`
      : `The corrected value for ${label} in ${filename} is invalid. Enter the confirmed value.`
  }).filter((item): item is string => item !== null).join('; ')
  return `Submission failed: ${summary || 'the corrections did not pass validation'}.`
}
