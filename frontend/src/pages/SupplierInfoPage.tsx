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
    AVAILABLE: 'Available',
    INSUFFICIENT_SAMPLE: 'Limited',
    NO_DATA: 'Unavailable',
    OUT_OF_SCOPE: 'Out of scope',
    NOT_RECORDED: 'Unrecorded',
  } as Record<string, string>)[status] ?? status
}

function errorText(error: unknown) {
  return error instanceof ApiClientError ? error.message : 'Unable to load supplier information.'
}

function decisionStatusLabel(entry: SupplierInformationEntry) {
  const statuses = entry.quotes
    .map((quote) => quote.evaluation?.status)
    .filter((status): status is string => Boolean(status))
  if (statuses.includes('FEASIBLE')) return 'Feasible'
  if (statuses.includes('PENDING')) return 'Pending'
  if (statuses.includes('INFEASIBLE')) return 'Infeasible'
  return 'Unanalysed'
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
      display_name: row.display_name ?? row.supplier_id ?? 'Candidate pending identity review',
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

  if (taskQuery.isPending || infoQuery.isPending) return <main className="page"><section className="card loading-panel">Loading supplier decision profiles…</section></main>
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
    ? `${info.quote_count} active · not analysed`
    : info.is_current
      ? `${task.quotes.length} active · ${excludedQuoteCount} excluded · ${info.quote_count} compared`
      : `${info.quote_count} frozen${excludedSupplierIds.length ? ` · excluded: ${excludedSupplierIds.join(', ')}` : ''}`

  return (
    <main className="page supplier-info-page">
      <TaskWorkspaceHeader
        taskId={task.task_id} scenarioId={task.scenario_id} title={task.task_name}
        subtitle={`${info.quote_count} quotations · ${info.matched_supplier_count} matched suppliers`}
        status={task.status} revision={info.snapshot_revision} resultId={info.result_id}
        quoteCount={task.quotes.length} summaryComplete={task.summary_completed}
        progress={task.progress} active="suppliers"
        revisionContext={info.view_state === 'HISTORICAL_RESULT' ? 'historical' : 'current'}
      />

      <section className="supplier-context card workspace-page-lead">
        <div className="supplier-context-heading workspace-page-lead-copy">
          <h2>Suppliers</h2>
          <p>{String(context.category ?? 'Electronics')} / {String(context.item ?? task.requirement.manufacturer_part_number)}</p>
          <p className="supplier-scope-copy">{scopeSummary}</p>
        </div>
        <div className="supplier-kpis" aria-label="Supplier overview">
          <div><strong>{info.quote_count}</strong><span>{info.view_state === 'QUOTE_ONLY' ? 'Active' : 'Compared'}</span></div>
          {excludedSupplierIds.length > 0 && <div><strong>{excludedQuoteCount}</strong><span>Excluded</span></div>}
          <div><strong>{info.matched_supplier_count}</strong><span>Matched</span></div>
          <div><strong>{info.unresolved_identity_quote_count}</strong><span>Unmatched</span></div>
          <div><strong>{entries.filter((entry) => entry.history_availability_status !== 'AVAILABLE').length}</strong><span>Unavailable</span></div>
        </div>
        <div className="supplier-context-tags">
          <span>{info.view_state === 'QUOTE_ONLY' ? 'Unanalysed' : info.view_state === 'CURRENT_RESULT' ? 'Frozen' : 'Historical'}</span>
          {context.as_of_date ? <span>{String(context.as_of_date)}</span> : null}
          {context.is_synthetic ? <span>Synthetic</span> : null}
        </div>
      </section>

      <section className="supplier-overview-grid">
        <article className="card supplier-list-card">
          <div className="section-heading"><div><p className="eyebrow">SCOPE</p><h2>Suppliers</h2>{excludedSupplierIds.length > 0 && <p>Excludes {excludedSupplierIds.join(', ')} under the current settings</p>}</div></div>
          <div className="supplier-list">
            {entries.map((entry) => {
              const key = entry.supplier_identity_id ?? entry.quotes[0]?.quote_id
              const history = entry.history_snapshot
              return <button type="button" key={key} className={selected === entry ? 'supplier-row supplier-row-active' : 'supplier-row'} onClick={() => setSelectedId(key)}>
                <span><strong>{entry.display_name}</strong><small>{entry.supplier_id ?? 'Unmatched'} · {decisionStatusLabel(entry)}</small></span>
                <span><b>{history?.overall_grade ?? '—'}</b><small>{availabilityLabel(entry.history_availability_status)}</small></span>
              </button>
            })}
          </div>
        </article>

        <article className="card supplier-chart-card">
          <div className="section-heading"><div><p className="eyebrow">HISTORICAL PERFORMANCE</p><h2>On-time rate vs rejected order-line rate</h2><p>Better performance appears towards the lower right; marker size reflects the valid sample size.</p></div></div>
          {chartEntries.length ? <>
            <svg viewBox="0 0 1000 280" role="img" aria-label="Supplier historical on-time and rejected order-line rates">
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
              <text className="supplier-axis-title" x="520" y="272" textAnchor="middle">Historical on-time rate → higher is better</text>
              <text className="supplier-axis-title" x="22" y="134" textAnchor="middle" transform="rotate(-90 22 134)">Rejected order-line rate ↑ lower is better</text>
              {chartPoints.map(({ entry, index, x, y, size, onTime, rejected }) => {
                const history = entry.history_snapshot!
                const selectSupplier = () => setSelectedId(entry.supplier_identity_id)
                return <g key={entry.supplier_identity_id} tabIndex={0} role="button" aria-label={`${entry.display_name}, on-time rate ${onTime.toFixed(1)}%, rejected order-line rate ${rejected.toFixed(1)}%`} onClick={selectSupplier} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectSupplier() } }}>
                  <circle cx={x} cy={y} r={size} className={`supplier-bubble grade-${history.overall_grade ?? 'N'}`} />
                  <text className="supplier-bubble-index" x={x} y={y + 4} textAnchor="middle">{index + 1}</text>
                  <title>{`${entry.display_name}: on-time rate ${onTime.toFixed(1)}%, rejected order-line rate ${rejected.toFixed(1)}%, grade ${history.overall_grade}`}</title>
                </g>
              })}
            </svg>
            <div className="supplier-chart-legend" aria-label="Supplier chart legend">
              {chartPoints.map(({ entry, index }) => <button type="button" key={entry.supplier_identity_id} onClick={() => setSelectedId(entry.supplier_identity_id)}><i className={`grade-${entry.history_snapshot?.overall_grade ?? 'N'}`} aria-hidden="true" /><b>{index + 1}</b><span>{entry.display_name}</span><small>Grade {entry.history_snapshot?.overall_grade ?? '—'}</small></button>)}
            </div>
            <table className="supplier-data-table"><thead><tr><th>Supplier</th><th>Grade</th><th>On time</th><th>Rejected order lines</th></tr></thead><tbody>
              {chartEntries.map((entry) => <tr key={entry.supplier_identity_id}><td>{entry.display_name}</td><td>{entry.history_snapshot?.overall_grade}</td><td>{entry.history_snapshot?.on_time?.numerator}/{entry.history_snapshot?.on_time?.denominator} ({percent(entry.history_snapshot?.on_time?.rate)?.toFixed(1)}%)</td><td>{entry.history_snapshot?.rejected_lines?.numerator}/{entry.history_snapshot?.rejected_lines?.denominator} ({percent(entry.history_snapshot?.rejected_lines?.rate)?.toFixed(1)}%)</td></tr>)}
            </tbody></table>
          </> : <p className="audit-empty">No identity-matched supplier currently meets the historical sample threshold. Zero values will not be used to fabricate chart positions.</p>}
        </article>
      </section>

      {selected && <section className="card supplier-detail" aria-live="polite">
        <ComplianceAssessmentDetails assessments={selected.quotes.flatMap((quote) => quote.policy_assessment ? [quote.policy_assessment] : [])} taskId={taskId} resultId={resultId} historical={Boolean(resultId && resultId !== task.current_result_id)} />
        <div className="section-heading"><div><p className="eyebrow">SELECTED</p><h2>{selected.display_name}</h2><p>{selected.identity_match_status === 'MATCHED' ? 'Matched' : 'Unmatched'}</p></div><span className="signal-badge">{selected.history_snapshot?.overall_grade ? `Grade ${selected.history_snapshot.overall_grade}` : availabilityLabel(selected.history_availability_status)}</span></div>
        <div className="supplier-detail-grid">
          <div><h3>Performance</h3><dl><div><dt>On-time</dt><dd>{percent(selected.history_snapshot?.on_time?.rate)?.toFixed(1) ?? '—'}%</dd></div><div><dt>Rejected</dt><dd>{percent(selected.history_snapshot?.rejected_lines?.rate)?.toFixed(1) ?? '—'}%</dd></div><div><dt>Dataset</dt><dd>{String(context.dataset_version ?? 'Unrecorded')}</dd></div></dl></div>
          <div><h3>Quotation</h3>{selected.quotes.map((quote) => <dl key={quote.quote_id}><div><dt>Version</dt><dd>v{quote.quote_version}</dd></div><div><dt>Status</dt><dd>{quote.evaluation ? decisionStatusLabel({ ...selected, quotes: [quote] }) : 'Unanalysed'}</dd></div><div><dt>Cost</dt><dd>{quote.evaluation?.total_cost ? `${task.requirement.currency} ${quote.evaluation.total_cost}` : '—'}</dd></div><div><dt>Delivery</dt><dd>{quote.evaluation?.estimated_arrival_date ?? '—'}</dd></div></dl>)}</div>
          <div><h3>Ranking</h3><dl><div><dt>Primary</dt><dd>{rankingCriterionLabel(info.effective_preferences?.primary_criterion)}</dd></div><div><dt>Secondary</dt><dd>{rankingCriterionLabel(info.effective_preferences?.secondary_criterion)}</dd></div><div><dt>Applied</dt><dd>{info.ranking_trace?.secondary_applied ? 'Yes' : 'No'}</dd></div></dl></div>
        </div>
        {selected.quotes[0] && <div className="supplier-detail-links"><Link to={`/tasks/${taskId}/decision`}>Analysis</Link><Link to={`/tasks/${taskId}/compliance`}>Compliance</Link><Link to={`/tasks/${taskId}/quotes/new`}>Quotation</Link></div>}
      </section>}

      <details className="card supplier-source"><summary>Data source</summary><pre>{JSON.stringify({ context_sha256: info.context_sha256, snapshot_id: info.snapshot_id, result_id: info.result_id, history_binding: info.history_binding }, null, 2)}</pre></details>
    </main>
  )
}
