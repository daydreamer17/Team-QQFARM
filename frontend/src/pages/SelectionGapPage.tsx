import { useMutation, useQuery } from '@tanstack/react-query'
import { type FormEvent, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, ApiClientError } from '../api/client'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'
import { dispositionLabel, quoteStatusLabel, reasonText } from '../lib/presentation'

function errorMessage(error: unknown) {
  if (error instanceof ApiClientError) {
    if (error.code === 'selection_review_required') return 'Complete quotation review and decision comparison first.'
    if (error.code === 'task_revision_conflict' || error.code === 'selection_input_stale') return 'The procurement data has changed. Refresh the page and try again.'
    return error.message
  }
  return 'Selection-gap analysis is temporarily unavailable. Try again later.'
}

export function SelectionGapPage() {
  const { taskId = '' } = useParams()
  const [budget, setBudget] = useState('')
  const [deadline, setDeadline] = useState('')
  const task = useQuery({ queryKey: ['tasks', taskId], queryFn: () => api.getTask(taskId), enabled: Boolean(taskId) })
  const results = useQuery({
    queryKey: ['tasks', taskId, 'results'],
    queryFn: () => api.listResults(taskId),
    enabled: Boolean(taskId && task.data?.current_result_id),
  })
  const gaps = useQuery({
    queryKey: ['tasks', taskId, 'selection-gaps', task.data?.task_revision],
    queryFn: () => api.getSelectionGaps(taskId, task.data!.task_revision),
    enabled: Boolean(taskId && task.data?.current_result_id),
    retry: false,
  })
  const simulation = useMutation({
    mutationFn: (changes: { budget_amount?: string; delivery_deadline?: string }) => api.simulateRequirement(taskId, task.data!.task_revision, changes),
  })
  const currentResult = results.data?.find((item) => item.is_current) ?? results.data?.[0]
  const suppliers = useMemo(
    () => new Map(currentResult?.result.supplier_results.map((item) => [item.quote_id, item.supplier_name]) ?? []),
    [currentResult],
  )
  if (task.isPending) return <section className="card loading-panel">Loading tasks…</section>
  if (task.isError) return <section className="card error-panel">{errorMessage(task.error)}</section>
  const data = task.data
  const eligibleCount = gaps.data?.gaps.filter((gap) => gap.status === 'FEASIBLE').length ?? 0
  const excludedCount = gaps.data?.gaps.filter((gap) => gap.status === 'INFEASIBLE').length ?? 0
  function supplierName(quoteId: string) {
    return suppliers.get(quoteId) ?? data.quotes.find((quote) => quote.quote_id === quoteId)?.supplier_id ?? 'Unnamed supplier'
  }
  function submitSimulation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const changes = { ...(budget.trim() ? { budget_amount: budget.trim() } : {}), ...(deadline ? { delivery_deadline: deadline } : {}) }
    if (Object.keys(changes).length > 0) simulation.mutate(changes)
  }
  return (
    <div className="page-stack selection-gap-page">
      <TaskWorkspaceHeader taskId={data.task_id} scenarioId={data.scenario_id} title={data.task_name} subtitle="See why each quotation was selected or not selected" status={data.status} revision={data.task_revision} resultId={data.current_result_id} quoteCount={data.quotes.length} summaryComplete={data.summary_completed} progress={data.progress} reviewBlocked={Boolean(data.current_issue)} policyReviewBlocked={data.current_issue?.issue_type === 'POLICY_EVIDENCE_REVIEW'} active="gaps" />
      <section className="selection-gap-intro">
        <div><p className="eyebrow">Selection rationale</p><h2>Why this supplier was selected and how the others differ</h2><p>This view brings together cost, delivery and unmet conditions. It explains the current result without changing formal procurement data.</p></div>
        <div className="selection-gap-totals"><span><strong>{eligibleCount}</strong> meet requirements</span><span><strong>{excludedCount}</strong> not selected</span></div>
      </section>
      {!data.current_result_id && <section className="card audit-empty">Complete the decision comparison to see why each quotation was selected or not selected.</section>}
      {gaps.isPending && data.current_result_id && <section className="card loading-panel">Preparing quotation differences…</section>}
      {(gaps.isError || results.isError) && <section className="card error-panel">{errorMessage(gaps.error ?? results.error)}</section>}
      <section className="selection-gap-list" aria-label="Supplier-by-supplier gaps">
        {gaps.data?.gaps.map((gap) => {
          const draft = gaps.data.clarification_drafts.find((item) => item.quote_id === gap.quote_id)
          const isEligible = gap.status === 'FEASIBLE'
          const isPending = gap.status === 'PENDING'
          return <article className={`selection-gap-card gap-${gap.status.toLowerCase()}`} key={gap.quote_id}>
            <header><div className="gap-supplier"><span className="gap-status-icon">{isEligible ? '✓' : isPending ? '!' : '×'}</span><div><h3>{supplierName(gap.quote_id)}</h3><p>{quoteStatusLabel(gap.status)}</p></div></div><span className={`gap-status-label gap-status-${gap.status.toLowerCase()}`}>{quoteStatusLabel(gap.status)}</span></header>
            <dl className="gap-metrics"><div><dt>Confirmed total cost</dt><dd>{gap.confirmed_total_cost === null ? 'Undetermined' : `${gap.currency} ${gap.confirmed_total_cost}`}</dd></div><div><dt>Over budget</dt><dd>{gap.budget_excess === null ? '—' : `${gap.currency} ${gap.budget_excess}`}</dd></div><div><dt>Cost difference from the better option</dt><dd>{gap.cost_difference_vs_other === null ? '—' : `${gap.currency} ${gap.cost_difference_vs_other}`}</dd></div><div><dt>Delivery delay</dt><dd>{gap.delivery_days_late === null || gap.delivery_days_late === 0 ? 'On time' : `${gap.delivery_days_late} days`}</dd></div></dl>
            {isEligible && gap.failed_reasons.length === 0 && gap.pending_reasons.length === 0 && <div className="gap-conclusion gap-conclusion-good">This quotation meets the current mandatory procurement requirements and can proceed to ranking.</div>}
            {gap.failed_reasons.length > 0 && <div className="gap-conclusion gap-conclusion-bad"><strong>Reason Not Selected</strong><ul>{gap.failed_reasons.map((item) => <li key={item.code}>{reasonText(item)}</li>)}</ul></div>}
            {gap.pending_reasons.length > 0 && <div className="gap-conclusion gap-conclusion-pending"><strong>Confirmation required</strong><ul>{gap.pending_reasons.map((item) => <li key={item.code}>{reasonText(item)}</li>)}</ul></div>}
            {draft && <details className="gap-draft"><summary>View suggested communication</summary><p>{draft.text}</p></details>}
          </article>
        })}
      </section>

      <form className="card simulation-form" onSubmit={submitSimulation}>
        <div><p className="eyebrow">What-if analysis</p><h2>Would the recommendation change if the budget or delivery deadline changed?</h2><p>Simulation results do not change the formal requirements or replace the compliance review.</p></div>
        <label className="field"><span>Assumed budget ({data.requirement.currency})</span><input inputMode="decimal" value={budget} onChange={(event) => { setBudget(event.target.value); simulation.reset() }} placeholder={data.requirement.budget_amount} /></label>
        <label className="field"><span>Assumed delivery deadline</span><input type="date" value={deadline} onChange={(event) => { setDeadline(event.target.value); simulation.reset() }} /></label>
        <button className="button button-submit" disabled={task.data.status === 'ABANDONED' || simulation.isPending || (!budget.trim() && !deadline)}>{simulation.isPending ? 'Simulating…' : 'Run simulation'}</button>
        {simulation.isError && <div className="form-error">{errorMessage(simulation.error)}</div>}
      </form>
      {simulation.data && <section className="card simulation-result"><span className="status-pill status-pending">Simulation only</span><h2>{dispositionLabel(simulation.data.result.comparison.disposition)}</h2><p>Recommended candidates: {simulation.data.result.comparison.recommended_quote_ids.map(supplierName).join(', ') || 'None'}</p><small>This result will not be written to the formal recommendation, and the compliance review has not been rerun.</small></section>}
    </div>
  )
}
