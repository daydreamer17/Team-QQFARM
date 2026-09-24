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
    expect(screen.getByText('发票日后 60 天')).toBeInTheDocument()
    expect(screen.queryByText('Net 60')).not.toBeInTheDocument()
    expect(screen.queryByText('Net 60 from invoice')).not.toBeInTheDocument()
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
      label: '推荐依据',
      detail: '历史准时率最高（100.0%）',
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

    expect(explanation.label).toBe('未选原因')
    expect(explanation.detail).toContain('历史准时率比推荐供应商低 14.0 个百分点')
    expect(explanation.detail).toContain('总成本比推荐供应商低 SGD 200.00')
  })

  test('explains the cost difference when lowest confirmed cost is the ranking rule', () => {
    const explanation = supplierSelectionExplanation(
      { ...base, quote_id: 'higher-cost', total_cost: '7100.00' },
      recommendedSupplier,
      'LOWEST_CONFIRMED_TOTAL_COST',
      'SGD',
      false,
    )

    expect(explanation.detail).toBe('总成本比推荐供应商高 SGD 200.00')
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

    expect(explanation.label).toBe('制度排除')
    expect(explanation.tone).toBe('danger')
    expect(explanation.detail).toBe(
      '制度检查不通过：RoHS 合规、供应商资质；已从推荐候选中排除。商业比较：预计到货比推荐供应商晚 5 天；总成本比推荐供应商低 SGD 400.00',
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

    expect(explanation.label).toBe('制度待复核')
    expect(explanation.detail).toContain('供应商资质尚未完成核验')
    expect(explanation.detail).toContain('有已核验合格候选时不优先推荐')
    expect(explanation.detail).not.toContain('制度检查不通过')
  })
})
