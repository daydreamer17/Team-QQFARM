import { describe, expect, test } from 'vitest'
import { ApiClientError } from '../src/api/client'
import type { QuoteField, TaskQuote } from '../src/api/types'
import { canConfirmReviewField, fieldCorrectionErrorMessage } from '../src/lib/reviewPanel'

const quote: TaskQuote = {
  quote_id: 'quote-1', quote_version: 1, supplier_id: 'SUP-022',
  document_id: 'doc-1', document_version: 1, original_filename: 'redwood_quote.pdf',
}

function field(fieldName: string, value: unknown): QuoteField {
  return {
    field_name: fieldName, field_version: 1, raw_value: String(value), normalized_value: value,
    unit: null, validation_status: 'EXTRACTED', origin: 'DOCUMENT', evidence: [],
  }
}

describe('ReviewPanel correction safeguards', () => {
  test('does not allow UNKNOWN fee status to be confirmed as a resolved value', () => {
    expect(canConfirmReviewField(field('shipping_fee_status', 'UNKNOWN'))).toBe(false)
    expect(canConfirmReviewField(field('other_fees_status', 'UNKNOWN'))).toBe(false)
    expect(canConfirmReviewField(field('shipping_fee_status', 'FREE'))).toBe(true)
    expect(canConfirmReviewField(field('payment_terms', 'Net 60 after invoice'))).toBe(true)
  })

  test('reports an invalid correction instead of claiming a version conflict', () => {
    const error = new ApiClientError(422, 'field_correction_batch_invalid', 'invalid', {
      errors: [{ quote_id: 'quote-1', field_name: 'shipping_fee_status', code: 'field_correction_invalid' }],
    })
    expect(fieldCorrectionErrorMessage(error, [quote])).toBe(
      'Submission failed: The corrected value for Shipping Fee Status in redwood_quote.pdf is invalid. Enter the confirmed value..',
    )
  })

  test('reports a real field version conflict accurately', () => {
    const error = new ApiClientError(409, 'field_correction_batch_invalid', 'conflict', {
      errors: [{ quote_id: 'quote-1', field_name: 'payment_terms', code: 'field_version_conflict' }],
    })
    expect(fieldCorrectionErrorMessage(error, [quote])).toBe(
      'Submission failed: Payment terms in redwood_quote.pdf has changed. Review it again..',
    )
  })
})
