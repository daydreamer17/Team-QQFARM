import type { RateMetric, SupplierComparisonResult } from '../api/types'

export function MatrixPaymentTerm({ supplier }: { supplier: SupplierComparisonResult }) {
  const term = supplier.payment_term
  if (!term) return <span className="muted">未记录</span>
  const comparable = term.parse_status === 'COMPARABLE'
  const reasons: Record<string, string> = {
    PAYMENT_TERMS_NOT_VERIFIED: '账期尚未核验，不参与排序',
    PAYMENT_TERMS_COMPLEX: '分期／预付款条款，暂不支持账期排序',
    PAYMENT_TERMS_UNSUPPORTED: '该付款条款暂不支持账期排序',
    PAYMENT_TERMS_MISSING: '账期信息缺失',
    PAYMENT_START_EVENT_MISSING: '缺少账期起算条件，请确认',
    PAYMENT_START_EVENT_CONFLICT: '账期起算条件冲突，请核对',
  }
  const messages = [...new Set(term.reason_codes.map((code) => reasons[code]).filter(Boolean))]
  const displayTerm = comparable && term.net_days !== null
    ? `${term.payment_start_event === 'INVOICE_DATE' ? '发票日后 ' : ''}${term.net_days} 天`
    : term.normalized_text || term.raw_text || '未提供'
  return <div className="matrix-cell-summary">
    <strong>{displayTerm}</strong>
    {!comparable && <small>{messages.join('；') || (term.parse_status === 'MISSING' ? '账期信息缺失' : '账期暂不可比，原因未记录')}</small>}
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
  if (!history) return <span className="muted">当时未记录历史表现</span>
  if (history.identity_match_status !== 'MATCHED') return <span className="muted">供应商身份待核验，历史表现不可比</span>
  if (history.history_availability_status !== 'AVAILABLE') {
    const label = ({ INSUFFICIENT_SAMPLE: '历史样本不足', NO_DATA: '无历史数据', OUT_OF_SCOPE: '历史数据不适用当前范围', NOT_RECORDED: '当时未记录历史表现' } as Record<string, string>)[history.history_availability_status]
    return <span className="muted">{label ?? '历史表现暂不可比'}</span>
  }
  return <div className="matrix-performance-block">
    <dl className="matrix-performance-details">
      <div><dt>评级</dt><dd><span className="matrix-grade">{history.overall_grade || '—'}</span></dd></div>
      <div><dt>准时率</dt><dd>{rateText(history.on_time)}</dd></div>
      <div><dt>拒收订单行率</dt><dd>{rateText(history.rejected_lines)}</dd></div>
    </dl>
    <details className="matrix-performance-samples">
      <summary>历史样本</summary>
      <p>准时：{history.on_time ? `${history.on_time.numerator} / ${history.on_time.denominator}` : '未记录'}</p>
      <p>拒收订单行：{history.rejected_lines ? `${history.rejected_lines.numerator} / ${history.rejected_lines.denominator}` : '未记录'}</p>
    </details>
  </div>
}
