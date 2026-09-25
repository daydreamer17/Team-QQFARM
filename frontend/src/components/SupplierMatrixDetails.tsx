import type { RateMetric, SupplierComparisonResult } from '../api/types'

export function MatrixPaymentTerm({ supplier }: { supplier: SupplierComparisonResult }) {
  const term = supplier.payment_term
  if (!term) return <span className="muted">Not recorded</span>
  const comparable = term.parse_status === 'COMPARABLE'
  const reasons: Record<string, string> = {
    PAYMENT_TERMS_NOT_VERIFIED: 'Payment terms have not been evaluated and are excluded from ranking.',
    PAYMENT_TERMS_COMPLEX: 'Instalment or advance-payment clauses are not supported for payment-term ranking.',
    PAYMENT_TERMS_UNSUPPORTED: 'This payment clause is not supported for payment-term ranking.',
    PAYMENT_TERMS_MISSING: 'Payment terms are missing.',
    PAYMENT_START_EVENT_MISSING: 'The payment-term start event is missing. Please confirm it.',
    PAYMENT_START_EVENT_CONFLICT: 'The payment-term start event conflicts with another value. Please review it.',
  }
  const messages = [...new Set(term.reason_codes.map((code) => reasons[code]).filter(Boolean))]
  const displayTerm = comparable && term.net_days !== null
    ? `${term.payment_start_event === 'INVOICE_DATE' ? 'Net ' : ''}${term.net_days} days${term.payment_start_event === 'INVOICE_DATE' ? ' from invoice date' : ''}`
    : term.normalized_text || term.raw_text || 'Not provided'
  return <div className="matrix-cell-summary">
    <strong>{displayTerm}</strong>
    {!comparable && <small>{messages.join('; ') || (term.parse_status === 'MISSING' ? 'Payment terms are missing.' : 'Payment terms are not currently comparable; no reason was recorded.')}</small>}
  </div>
}

function rateText(metric: RateMetric | null) {
  if (!metric || metric.rate === null || metric.rate.trim() === '' || metric.denominator <= 0) return '—'
  const rate = Number(metric.rate)
  if (!Number.isFinite(rate) || rate < 0 || rate > 1) return '—'
  return `${(rate * 100).toFixed(1)}%`
}

export function MatrixSupplierPerformance({ supplier }: { supplier: SupplierComparisonResult }) {
  const history = supplier.history_snapshot
  if (!history) return <span className="muted">Historical performance was not recorded at the time.</span>
  if (history.identity_match_status !== 'MATCHED') return <span className="muted">Supplier identity requires verification, so historical performance is not comparable.</span>
  if (history.history_availability_status !== 'AVAILABLE') {
    const label = ({ INSUFFICIENT_SAMPLE: 'Insufficient historical sample', NO_DATA: 'No historical data', OUT_OF_SCOPE: 'Historical data is not applicable to the current scope', NOT_RECORDED: 'Historical performance was not recorded at the time' } as Record<string, string>)[history.history_availability_status]
    return <span className="muted">{label ?? 'Historical performance is not currently comparable.'}</span>
  }
  return <div className="matrix-performance-block">
    <dl className="matrix-performance-details">
      <div><dt>Grade</dt><dd><span className="matrix-grade">{history.overall_grade || '—'}</span></dd></div>
      <div><dt>On-time Rate</dt><dd>{rateText(history.on_time)}</dd></div>
      <div><dt>Rejected Order-line Rate</dt><dd>{rateText(history.rejected_lines)}</dd></div>
    </dl>
    <details className="matrix-performance-samples">
      <summary>Historical Records</summary>
      <p>On time: {history.on_time ? `${history.on_time.numerator} / ${history.on_time.denominator}` : 'Not recorded'}</p>
      <p>Rejected order lines: {history.rejected_lines ? `${history.rejected_lines.numerator} / ${history.rejected_lines.denominator}` : 'Not recorded'}</p>
    </details>
  </div>
}
