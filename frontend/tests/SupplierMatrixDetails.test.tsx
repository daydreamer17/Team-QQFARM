import { render, screen } from '@testing-library/react'
import { describe, expect, test } from 'vitest'
import type { SupplierComparisonResult, SupplierHistorySnapshot } from '../src/api/types'
import { MatrixPaymentTerm, MatrixSupplierPerformance } from '../src/components/SupplierMatrixDetails'
import { policyAwareSupplierSelectionExplanation, supplierSelectionExplanation } from '../src/lib/resultComparison'

const base: SupplierComparisonResult = {
  quote_id: 'q1', quote_version: 1, supplier_name: 'Example', status: 'FEASIBLE',
  goods_cost: '100', total_cost: '100', known_cost_subtotal: '100', actual_quantity: 10,
  estimated_arrival_date: null, failed_reasons: [], pending_reasons: [],
}
const history: SupplierHistorySnapshot = {
  quote_id: 'q1', supplier_id: 's1', supplier_name: 'Example', identity_match_status: 'MATCHED',
  history_availability_status: 'AVAILABLE', overall_grade: 'A',
  on_time: { rate: '0.95', numerator: 95, denominator: 100 },
  rejected_lines: { rate: '0', numerator: 0, denominator: 100 }, evidence_refs: [],
}

describe('frozen supplier matrix details', () => {
  test('shows a single localized payment term without repeating raw text', () => {
    render(<MatrixPaymentTerm supplier={{ ...base, payment_term: {
      raw_text: 'Net 60 from invoice', normalized_text: 'Net 60', net_days: 60,
      payment_start_event: 'INVOICE_DATE', parse_status: 'COMPARABLE', reason_codes: [],
    } }} />)
    expect(screen.getByText('Net 60 days from invoice date')).toBeInTheDocument()
    expect(screen.queryByText('Net 60')).not.toBeInTheDocument()
    expect(screen.queryByText('Net 60 from invoice')).not.toBeInTheDocument()
  })
  test('does not imply an unverified payment term is sortable', () => {
    render(<MatrixPaymentTerm supplier={{ ...base, payment_term: {
      raw_text: 'Net 30', normalized_text: 'Net 30', net_days: 30,
      payment_start_event: null, parse_status: 'INCOMPARABLE', reason_codes: ['PAYMENT_TERMS_NOT_VERIFIED'],
    } }} />)
    expect(screen.getByText('Payment terms have not been evaluated and are excluded from ranking.')).toBeInTheDocument()
  })
  test('shows grade and rates with sample counts, preserving a real zero', () => {
    render(<MatrixSupplierPerformance supplier={{ ...base, history_snapshot: history }} />)
    expect(screen.getByText('A')).toBeInTheDocument()
    expect(screen.getByText('95.0%')).toBeInTheDocument()
    expect(screen.getByText('0.0%')).toBeInTheDocument()
    const disclosure = screen.getByText('Historical Records').closest('details')!
    expect(disclosure).not.toHaveAttribute('open')
    expect(screen.getByText('On time: 95 / 100')).toBeInTheDocument()
  })
  test.each(['INSUFFICIENT_SAMPLE', 'OUT_OF_SCOPE', 'NO_DATA', 'NOT_RECORDED'])('does not present %s as usable history', (status) => {
    render(<MatrixSupplierPerformance supplier={{ ...base, history_snapshot: { ...history, history_availability_status: status } }} />)
    expect(screen.queryByText('95.0%')).not.toBeInTheDocument()
  })
  test('does not use unmatched supplier history', () => {
    render(<MatrixSupplierPerformance supplier={{ ...base, history_snapshot: { ...history, identity_match_status: 'REVIEW_REQUIRED' } }} />)
    expect(screen.getByText('Supplier identity requires verification, so historical performance is not comparable.')).toBeInTheDocument()
    expect(screen.queryByText('A')).not.toBeInTheDocument()
  })
  test('handles legacy results without fabricated zero values', () => {
    render(<><MatrixPaymentTerm supplier={base} /><MatrixSupplierPerformance supplier={base} /></>)
    expect(screen.getByText('Not recorded')).toBeInTheDocument()
    expect(screen.getByText('Historical performance was not recorded at the time.')).toBeInTheDocument()
  })
  test('does not turn missing rates into 0%', () => {
    render(<MatrixSupplierPerformance supplier={{ ...base, history_snapshot: { ...history, on_time: null, rejected_lines: null } }} />)
    expect(screen.getAllByText('—')).toHaveLength(2)
  })
  test.each([
    ['PAYMENT_TERMS_COMPLEX', 'Instalment or advance-payment clauses are not supported for payment-term ranking.'],
    ['PAYMENT_START_EVENT_MISSING', 'The payment-term start event is missing. Please confirm it.'],
    ['PAYMENT_START_EVENT_CONFLICT', 'The payment-term start event conflicts with another value. Please review it.'],
  ])('explains backend reason %s without mislabelling it as unverified', (code, message) => {
    render(<MatrixPaymentTerm supplier={{ ...base, payment_term: {
      raw_text: 'terms', normalized_text: null, net_days: null,
      payment_start_event: null, parse_status: 'INCOMPARABLE', reason_codes: [code],
    } }} />)
    expect(screen.getByText(message)).toBeInTheDocument()
    expect(screen.queryByText('Payment terms have not been evaluated and are excluded from ranking.')).not.toBeInTheDocument()
  })
})

describe('supplier selection explanations', () => {
  const recommendedSupplier: SupplierComparisonResult = {
    ...base,
    quote_id: 'recommended',
    supplier_name: 'Recommended',
    total_cost: '6900.00',
    history_snapshot: {
      ...history,
      quote_id: 'recommended',
      on_time: { rate: '1', numerator: 10, denominator: 10 },
    },
  }

  test('states the active ranking reason for the recommended supplier', () => {
    expect(supplierSelectionExplanation(
      recommendedSupplier,
      recommendedSupplier,
      'HIGHEST_HISTORICAL_ON_TIME_RATE',
      'SGD',
      true,
    )).toEqual({
      label: 'Basis for Recommendation',
      detail: 'Highest historical on-time rate (100.0%)',
      tone: 'good',
    })
  })

  test('compares an unselected supplier with the recommendation and keeps the cost trade-off', () => {
    const explanation = supplierSelectionExplanation(
      {
        ...base,
        quote_id: 'alternative',
        supplier_name: 'Alternative',
        total_cost: '6700.00',
        history_snapshot: {
          ...history,
          quote_id: 'alternative',
          on_time: { rate: '0.86', numerator: 86, denominator: 100 },
        },
      },
      recommendedSupplier,
      'HIGHEST_HISTORICAL_ON_TIME_RATE',
      'SGD',
      false,
    )

    expect(explanation.label).toBe('Reason not selected')
    expect(explanation.detail).toContain('Historical on-time rate is 14.0 percentage points lower than the recommended supplier')
    expect(explanation.detail).toContain('Total cost is SGD 200.00 lower than the recommended supplier')
  })

  test('explains the cost difference when lowest confirmed cost is the ranking rule', () => {
    const explanation = supplierSelectionExplanation(
      { ...base, quote_id: 'higher-cost', total_cost: '7100.00' },
      recommendedSupplier,
      'LOWEST_CONFIRMED_TOTAL_COST',
      'SGD',
      false,
    )

    expect(explanation.detail).toBe('Total cost is SGD 200.00 higher than the recommended supplier')
  })

  test('puts a policy exclusion before commercial trade-offs', () => {
    const explanation = policyAwareSupplierSelectionExplanation(
      { ...base, quote_id: 'excluded', total_cost: '6500.00', estimated_arrival_date: '2026-11-12' },
      { ...recommendedSupplier, estimated_arrival_date: '2026-11-07' },
      'FASTEST_CONFIRMED_DELIVERY',
      'SGD',
      false,
      {
        quote_id: 'excluded', quote_version: 1, supplier_name: 'Excluded',
        status: 'NON_COMPLIANT', eligibility: 'EXCLUDED',
        checks: [
          { control_code: 'ROHS_COMPLIANCE', status: 'FAIL', citation_ids: [] },
          { control_code: 'APPROVED_SUPPLIER', status: 'REVIEW_REQUIRED', citation_ids: [] },
        ],
      },
    )

    expect(explanation.label).toBe('Excluded by Policy')
    expect(explanation.tone).toBe('danger')
    expect(explanation.detail).toBe(
      'Compliance review failed: RoHS compliance, Supplier eligibility. Excluded from recommendation candidates. Commercial comparison: Expected delivery is 5 days later than the recommended supplier; Total cost is SGD 400.00 lower than the recommended supplier',
    )
  })

  test('does not mislabel a pending policy review as a failure', () => {
    const explanation = policyAwareSupplierSelectionExplanation(
      { ...base, quote_id: 'review', total_cost: '6700.00', estimated_arrival_date: '2026-11-10' },
      { ...recommendedSupplier, estimated_arrival_date: '2026-11-07' },
      'FASTEST_CONFIRMED_DELIVERY',
      'SGD',
      false,
      {
        quote_id: 'review', quote_version: 1, supplier_name: 'Review',
        status: 'REVIEW_REQUIRED', eligibility: 'UNVERIFIED',
        checks: [{ control_code: 'APPROVED_SUPPLIER', status: 'REVIEW_REQUIRED', citation_ids: [] }],
      },
    )

    expect(explanation.label).toBe('Policy review required')
    expect(explanation.detail).toContain('Supplier eligibility have not been fully verified')
    expect(explanation.detail).toContain('It is not prioritised while verified eligible candidates are available')
    expect(explanation.detail).not.toContain('Compliance ReviewFailed')
  })
})
