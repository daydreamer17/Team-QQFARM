import { render, screen } from '@testing-library/react'
import { describe, expect, test } from 'vitest'
import type { SupplierComparisonResult, SupplierHistorySnapshot } from '../src/api/types'
import { MatrixPaymentTerm, MatrixSupplierPerformance } from '../src/components/SupplierMatrixDetails'

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
  test('shows normalized payment terms with original start-event wording', () => {
    render(<MatrixPaymentTerm supplier={{ ...base, payment_term: {
      raw_text: 'Net 60 from invoice', normalized_text: 'Net 60', net_days: 60,
      payment_start_event: 'INVOICE', parse_status: 'COMPARABLE', reason_codes: [],
    } }} />)
    expect(screen.getByText('Net 60')).toBeInTheDocument()
    expect(screen.getByText('Net 60 from invoice')).toBeInTheDocument()
  })
  test('does not imply an unverified payment term is sortable', () => {
    render(<MatrixPaymentTerm supplier={{ ...base, payment_term: {
      raw_text: 'Net 30', normalized_text: 'Net 30', net_days: 30,
      payment_start_event: null, parse_status: 'INCOMPARABLE', reason_codes: ['PAYMENT_TERMS_NOT_VERIFIED'],
    } }} />)
    expect(screen.getByText('账期尚未核验，不参与排序')).toBeInTheDocument()
  })
  test('shows grade and rates with sample counts, preserving a real zero', () => {
    render(<MatrixSupplierPerformance supplier={{ ...base, history_snapshot: history }} />)
    expect(screen.getByText('A')).toBeInTheDocument()
    expect(screen.getByText('95.0%')).toBeInTheDocument()
    expect(screen.getByText('0.0%')).toBeInTheDocument()
    const disclosure = screen.getByText('历史样本').closest('details')!
    expect(disclosure).not.toHaveAttribute('open')
    expect(screen.getByText('准时：95 / 100')).toBeInTheDocument()
  })
  test.each(['INSUFFICIENT_SAMPLE', 'OUT_OF_SCOPE', 'NO_DATA', 'NOT_RECORDED'])('does not present %s as usable history', (status) => {
    render(<MatrixSupplierPerformance supplier={{ ...base, history_snapshot: { ...history, history_availability_status: status } }} />)
    expect(screen.queryByText('95.0%')).not.toBeInTheDocument()
  })
  test('does not use unmatched supplier history', () => {
    render(<MatrixSupplierPerformance supplier={{ ...base, history_snapshot: { ...history, identity_match_status: 'REVIEW_REQUIRED' } }} />)
    expect(screen.getByText('供应商身份待核验，历史表现不可比')).toBeInTheDocument()
    expect(screen.queryByText('A')).not.toBeInTheDocument()
  })
  test('handles legacy results without fabricated zero values', () => {
    render(<><MatrixPaymentTerm supplier={base} /><MatrixSupplierPerformance supplier={base} /></>)
    expect(screen.getByText('未记录')).toBeInTheDocument()
    expect(screen.getByText('当时未记录历史表现')).toBeInTheDocument()
  })
  test('does not turn missing rates into 0%', () => {
    render(<MatrixSupplierPerformance supplier={{ ...base, history_snapshot: { ...history, on_time: null, rejected_lines: null } }} />)
    expect(screen.getAllByText('—')).toHaveLength(2)
  })
  test.each([
    ['PAYMENT_TERMS_COMPLEX', '分期／预付款条款，暂不支持账期排序'],
    ['PAYMENT_START_EVENT_MISSING', '缺少账期起算条件，请确认'],
    ['PAYMENT_START_EVENT_CONFLICT', '账期起算条件冲突，请核对'],
  ])('explains backend reason %s without mislabelling it as unverified', (code, message) => {
    render(<MatrixPaymentTerm supplier={{ ...base, payment_term: {
      raw_text: 'terms', normalized_text: null, net_days: null,
      payment_start_event: null, parse_status: 'INCOMPARABLE', reason_codes: [code],
    } }} />)
    expect(screen.getByText(message)).toBeInTheDocument()
    expect(screen.queryByText('账期尚未核验，不参与排序')).not.toBeInTheDocument()
  })
})
