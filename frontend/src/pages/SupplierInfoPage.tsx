import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api, ApiClientError } from '../api/client'
import type { SupplierInformationEntry } from '../api/types'
import { rankingCriterionLabel } from '../lib/rankingCriteria'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'
import { ComplianceAssessmentDetails } from '../components/ComplianceAssessmentDetails'

const percent = (value: string | null | undefined) => value === null || value === undefined
  ? null
  : Number(value) * 100

function availabilityLabel(status: string) {
  return ({
    AVAILABLE: '历史可用',
    INSUFFICIENT_SAMPLE: '样本不足',
    NO_DATA: '无历史数据',
    OUT_OF_SCOPE: '不适用当前范围',
    NOT_RECORDED: '当时未记录',
  } as Record<string, string>)[status] ?? status
}

function errorText(error: unknown) {
  return error instanceof ApiClientError ? error.message : '供应商信息读取失败。'
}

function decisionStatusLabel(entry: SupplierInformationEntry) {
  const statuses = entry.quotes
    .map((quote) => quote.evaluation?.status)
    .filter((status): status is string => Boolean(status))
  if (statuses.includes('FEASIBLE')) return '本次可行'
  if (statuses.includes('PENDING')) return '本次待确认'
  if (statuses.includes('INFEASIBLE')) return '本次不符合'
  return '尚未比较'
}

export function SupplierInfoPage() {
  const { taskId = '' } = useParams()
  const [params] = useSearchParams()
  const resultId = params.get('result_id') ?? undefined
  const taskQuery = useQuery({ queryKey: ['tasks', taskId], queryFn: () => api.getTask(taskId) })
  const infoQuery = useQuery({
    queryKey: ['tasks', taskId, 'suppliers', resultId ?? 'current'],
    queryFn: () => api.getSupplierInformation(taskId, resultId),
  })
  const entries = useMemo(() => {
    const matched = infoQuery.data?.suppliers ?? []
    const representedQuoteIds = new Set(
      matched.flatMap((entry) => entry.quotes.map((quote) => quote.quote_id)),
    )
    const unresolved = (infoQuery.data?.unresolved_identity_quotes ?? [])
      .filter((row) => !representedQuoteIds.has(row.quote_id))
      .map((row) => ({
      supplier_identity_id: null,
      display_name: row.display_name ?? row.supplier_id ?? '身份待核验候选',
      supplier_id: row.supplier_id ?? null,
      identity_match_status: row.identity_match_status ?? 'REVIEW_REQUIRED',
      history_availability_status: row.history_availability_status ?? 'NOT_RECORDED',
      history_snapshot: row.history_snapshot ?? null,
      quotes: [row],
    } satisfies SupplierInformationEntry))
    return [...matched, ...unresolved]
  }, [infoQuery.data])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selected = entries.find((entry) => (entry.supplier_identity_id ?? entry.quotes[0]?.quote_id) === selectedId)
    ?? entries[0]
  const task = taskQuery.data
  const info = infoQuery.data

  if (taskQuery.isPending || infoQuery.isPending) return <main className="page"><section className="card loading-panel">正在读取供应商决策画像…</section></main>
  if (!task || !info || taskQuery.isError || infoQuery.isError) return <main className="page"><section className="card form-error">{errorText(taskQuery.error ?? infoQuery.error)}</section></main>

  const chartEntries = info.suppliers.filter((entry) => {
    const history = entry.history_snapshot
    return history?.identity_match_status === 'MATCHED'
      && history.history_availability_status === 'AVAILABLE'
      && history.on_time?.rate !== null
      && history.rejected_lines?.rate !== null
  })
  const chartRates = chartEntries.map((entry) => ({
    entry,
    onTime: Math.max(0, Math.min(100, percent(entry.history_snapshot?.on_time?.rate) ?? 0)),
    rejected: Math.max(0, Math.min(100, percent(entry.history_snapshot?.rejected_lines?.rate) ?? 0)),
  }))
  const minimumOnTime = chartRates.length ? Math.min(...chartRates.map((point) => point.onTime)) : 0
  const maximumRejected = chartRates.length ? Math.max(...chartRates.map((point) => point.rejected)) : 0
  const onTimeFloor = Math.max(0, Math.floor((minimumOnTime - 5) / 5) * 5)
  const rejectedCeiling = Math.min(100, Math.max(5, Math.ceil((maximumRejected + 1) / 5) * 5))
  const chartPoints = chartRates.map((point, index) => {
    const history = point.entry.history_snapshot!
    return {
      ...point,
      index,
      x: 90 + ((point.onTime - onTimeFloor) / Math.max(1, 100 - onTimeFloor)) * 840,
      y: 210 - (Math.min(point.rejected, rejectedCeiling) / rejectedCeiling) * 165,
      size: 8 + Math.sqrt(Math.min(history.on_time?.denominator ?? 0, history.rejected_lines?.denominator ?? 0)),
    }
  })
  const context = info.history_dataset_context ?? {}
  const excludedSupplierIds = info.effective_preferences?.excluded_supplier_ids ?? []
  const excludedQuoteCount = info.is_current
    ? task.quotes.filter((quote) => excludedSupplierIds.includes(quote.supplier_id)).length
    : excludedSupplierIds.length
  const scopeSummary = info.view_state === 'QUOTE_ONLY'
    ? `当前 ${info.quote_count} 份有效报价；比较尚未运行`
    : info.is_current
      ? `范围：${task.quotes.length} 份有效 − ${excludedQuoteCount} 份设置排除 = ${info.quote_count} 份参与比较`
      : `该冻结版本有 ${info.quote_count} 份报价参与比较${excludedSupplierIds.length ? `；排除 ${excludedSupplierIds.join('、')}` : ''}`

  return (
    <main className="page supplier-info-page">
      <TaskWorkspaceHeader
        taskId={task.task_id} scenarioId={task.scenario_id} title={task.task_name}
        subtitle={`${info.quote_count} 份报价 · ${info.matched_supplier_count} 家已匹配供应商`}
        status={task.status} revision={info.snapshot_revision} resultId={info.result_id}
        quoteCount={task.quotes.length} summaryComplete={task.summary_completed}
        progress={task.progress} active="suppliers"
        revisionContext={info.view_state === 'HISTORICAL_RESULT' ? 'historical' : 'current'}
      />

      <section className="supplier-context card">
        <div className="supplier-context-heading">
          <h2>供应商信息</h2>
          <p>{String(context.category ?? 'Electronics')} / {String(context.item ?? task.requirement.manufacturer_part_number)}</p>
          <p className="supplier-scope-copy">{scopeSummary}</p>
        </div>
        <div className="supplier-kpis" aria-label="供应商概览">
          <div><strong>{info.quote_count}</strong><span>{info.view_state === 'QUOTE_ONLY' ? '有效报价' : '参与比较'}</span></div>
          {excludedSupplierIds.length > 0 && <div><strong>{excludedQuoteCount}</strong><span>设置排除</span></div>}
          <div><strong>{info.matched_supplier_count}</strong><span>身份已匹配</span></div>
          <div><strong>{info.unresolved_identity_quote_count}</strong><span>身份待核验</span></div>
          <div><strong>{entries.filter((entry) => entry.history_availability_status !== 'AVAILABLE').length}</strong><span>历史不可比</span></div>
        </div>
        <div className="supplier-context-tags">
          <span>{info.view_state === 'QUOTE_ONLY' ? '比较尚未运行' : info.view_state === 'CURRENT_RESULT' ? '当前冻结结果' : '历史冻结结果'}</span>
          {context.as_of_date ? <span>截至 {String(context.as_of_date)}</span> : null}
          {context.is_synthetic ? <span>合成演示数据</span> : null}
        </div>
      </section>

      <section className="supplier-overview-grid">
        <article className="card supplier-list-card">
          <div className="section-heading"><div><p className="eyebrow">CURRENT SCOPE</p><h2>本次比较供应商</h2>{excludedSupplierIds.length > 0 && <p>不含已按当前设置排除的 {excludedSupplierIds.join('、')}</p>}</div></div>
          <div className="supplier-list">
            {entries.map((entry) => {
              const key = entry.supplier_identity_id ?? entry.quotes[0]?.quote_id
              const history = entry.history_snapshot
              return <button type="button" key={key} className={selected === entry ? 'supplier-row supplier-row-active' : 'supplier-row'} onClick={() => setSelectedId(key)}>
                <span><strong>{entry.display_name}</strong><small>{entry.supplier_id ?? '身份待核验'} · {decisionStatusLabel(entry)}</small></span>
                <span><b>{history?.overall_grade ?? '—'}</b><small>{availabilityLabel(entry.history_availability_status)}</small></span>
              </button>
            })}
          </div>
        </article>

        <article className="card supplier-chart-card">
          <div className="section-heading"><div><p className="eyebrow">HISTORICAL PERFORMANCE</p><h2>准时率 × 拒收订单行率</h2><p>越靠右下表现越优；圆点大小反映有效样本量。</p></div></div>
          {chartEntries.length ? <>
            <svg viewBox="0 0 1000 280" role="img" aria-label="供应商历史准时率和拒收订单行率散点图">
              {[0, 0.5, 1].map((ratio) => {
                const y = 210 - ratio * 165
                const value = ratio * rejectedCeiling
                return <g key={`y-${ratio}`}><line x1="90" y1={y} x2="950" y2={y} className="supplier-gridline" /><text x="76" y={y + 4} textAnchor="end">{value.toFixed(value % 1 ? 1 : 0)}%</text></g>
              })}
              {[0, 0.5, 1].map((ratio) => {
                const x = 90 + ratio * 840
                const value = onTimeFloor + ratio * (100 - onTimeFloor)
                return <g key={`x-${ratio}`}><line x1={x} y1="45" x2={x} y2="220" className="supplier-gridline" /><text x={x} y="241" textAnchor="middle">{value.toFixed(value % 1 ? 1 : 0)}%</text></g>
              })}
              <line x1="90" y1="220" x2="950" y2="220" className="supplier-axis" />
              <line x1="90" y1="45" x2="90" y2="220" className="supplier-axis" />
              <text className="supplier-axis-title" x="520" y="272" textAnchor="middle">历史准时率 → 越高越好</text>
              <text className="supplier-axis-title" x="22" y="134" textAnchor="middle" transform="rotate(-90 22 134)">拒收订单行率 ↑ 越低越好</text>
              {chartPoints.map(({ entry, index, x, y, size, onTime, rejected }) => {
                const history = entry.history_snapshot!
                const selectSupplier = () => setSelectedId(entry.supplier_identity_id)
                return <g key={entry.supplier_identity_id} tabIndex={0} role="button" aria-label={`${entry.display_name}，准时率 ${onTime.toFixed(1)}%，拒收订单行率 ${rejected.toFixed(1)}%`} onClick={selectSupplier} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectSupplier() } }}>
                  <circle cx={x} cy={y} r={size} className={`supplier-bubble grade-${history.overall_grade ?? 'N'}`} />
                  <text className="supplier-bubble-index" x={x} y={y + 4} textAnchor="middle">{index + 1}</text>
                  <title>{`${entry.display_name}：准时率 ${onTime.toFixed(1)}%，拒收订单行率 ${rejected.toFixed(1)}%，等级 ${history.overall_grade}`}</title>
                </g>
              })}
            </svg>
            <div className="supplier-chart-legend" aria-label="散点图供应商图例">
              {chartPoints.map(({ entry, index }) => <button type="button" key={entry.supplier_identity_id} onClick={() => setSelectedId(entry.supplier_identity_id)}><i className={`grade-${entry.history_snapshot?.overall_grade ?? 'N'}`} aria-hidden="true" /><b>{index + 1}</b><span>{entry.display_name}</span><small>等级 {entry.history_snapshot?.overall_grade ?? '—'}</small></button>)}
            </div>
            <table className="supplier-data-table"><thead><tr><th>供应商</th><th>等级</th><th>准时</th><th>拒收订单行</th></tr></thead><tbody>
              {chartEntries.map((entry) => <tr key={entry.supplier_identity_id}><td>{entry.display_name}</td><td>{entry.history_snapshot?.overall_grade}</td><td>{entry.history_snapshot?.on_time?.numerator}/{entry.history_snapshot?.on_time?.denominator}（{percent(entry.history_snapshot?.on_time?.rate)?.toFixed(1)}%）</td><td>{entry.history_snapshot?.rejected_lines?.numerator}/{entry.history_snapshot?.rejected_lines?.denominator}（{percent(entry.history_snapshot?.rejected_lines?.rate)?.toFixed(1)}%）</td></tr>)}
            </tbody></table>
          </> : <p className="audit-empty">当前没有身份已匹配且达到样本门槛的历史表现。不会用 0 值伪造坐标。</p>}
        </article>
      </section>

      {selected && <section className="card supplier-detail" aria-live="polite">
        <ComplianceAssessmentDetails assessments={selected.quotes.flatMap((quote) => quote.policy_assessment ? [quote.policy_assessment] : [])} taskId={taskId} resultId={resultId} historical={Boolean(resultId && resultId !== task.current_result_id)} />
        <div className="section-heading"><div><p className="eyebrow">SELECTED SUPPLIER</p><h2>{selected.display_name}</h2><p>{selected.identity_match_status === 'MATCHED' ? '与当前版本化历史目录匹配' : '身份尚未完成可信匹配'}</p></div><span className="signal-badge">{selected.history_snapshot?.overall_grade ? `MCU-9 历史等级 ${selected.history_snapshot.overall_grade}` : availabilityLabel(selected.history_availability_status)}</span></div>
        <div className="supplier-detail-grid">
          <div><h3>历史表现</h3><dl><div><dt>准时率</dt><dd>{percent(selected.history_snapshot?.on_time?.rate)?.toFixed(1) ?? '—'}%</dd></div><div><dt>拒收订单行率</dt><dd>{percent(selected.history_snapshot?.rejected_lines?.rate)?.toFixed(1) ?? '—'}%</dd></div><div><dt>数据版本</dt><dd>{String(context.dataset_version ?? '未记录')}</dd></div></dl></div>
          <div><h3>本次报价</h3>{selected.quotes.map((quote) => <dl key={quote.quote_id}><div><dt>报价版本</dt><dd>v{quote.quote_version}</dd></div><div><dt>硬性状态</dt><dd>{quote.evaluation ? decisionStatusLabel({ ...selected, quotes: [quote] }) : '尚未比较'}</dd></div><div><dt>确认总成本</dt><dd>{quote.evaluation?.total_cost ? `${task.requirement.currency} ${quote.evaluation.total_cost}` : '—'}</dd></div><div><dt>预计到货</dt><dd>{quote.evaluation?.estimated_arrival_date ?? '—'}</dd></div></dl>)}</div>
          <div><h3>当前排序影响</h3><dl><div><dt>主指标</dt><dd>{rankingCriterionLabel(info.effective_preferences?.primary_criterion)}</dd></div><div><dt>次指标</dt><dd>{rankingCriterionLabel(info.effective_preferences?.secondary_criterion)}</dd></div><div><dt>次指标触发</dt><dd>{info.ranking_trace?.secondary_applied ? '是' : '否'}</dd></div></dl></div>
        </div>
        {selected.quotes[0] && <div className="supplier-detail-links"><Link to={`/tasks/${taskId}/decision`}>查看决策矩阵</Link><Link to={`/tasks/${taskId}/compliance`}>查看制度检查</Link><Link to={`/tasks/${taskId}/quotes/new`}>查看报价证据</Link></div>}
      </section>}

      <details className="card supplier-source"><summary>数据来源与版本</summary><pre>{JSON.stringify({ context_sha256: info.context_sha256, snapshot_id: info.snapshot_id, result_id: info.result_id, history_binding: info.history_binding }, null, 2)}</pre></details>
    </main>
  )
}
