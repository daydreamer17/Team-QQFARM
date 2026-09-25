import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api, ApiClientError } from '../api/client'
import type { InvestigationObservation } from '../api/types'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'
import { fieldLabel, impactStatusLabel } from '../lib/presentation'

function errorMessage(error: unknown) {
  return error instanceof ApiClientError ? error.message : 'Failed to load investigation log.'
}

const toolLabels: Record<string, string> = {
  get_task_context: 'Read current task information',
  analyze_decision_impact: 'Review decision impact',
  get_comparison_result: 'Read supplier comparison result',
  get_cost_breakdown: 'Read cost breakdown',
  locate_quote_source: 'Locate source quotation',
  get_confirmed_quote_records: 'Find historical manual confirmations',
  request_clarification: 'Request user input',
  retrieve_policy: 'Retrieve applicable policy',
  analyze_selection_gap: 'Analyse selection gap',
  draft_clarification: 'Draft supplier clarification',
  simulate_requirement_change: 'Simulate requirement change',
  get_policy_retrieval_status: 'Diagnose policy retrieval status',
  retry_policy_retrieval: 'Retry policy retrieval',
  read_decision_overview: 'Review current recommendation',
  compare_alternatives: 'Compare alternatives',
  inspect_quote_evidence: 'Review source quotation',
  inspect_supplier_history: 'Review supplier history',
  inspect_policy_evidence: 'Review policy evidence',
  compile_decision_brief: 'Compile investigation findings',
}

const stopReasonLabels: Record<string, string> = {
  EVIDENCE_CONFIRMED: 'Evidence confirmed',
  REQUEST_COMPLETED: 'Investigation completed',
  NO_DECISION_IMPACT: 'No impact on the current decision',
  SOURCES_EXHAUSTED: 'Available sources exhausted; more information required',
  CONFLICT_UNRESOLVED: 'Evidence conflict awaiting manual resolution',
  BUDGET_EXHAUSTED: 'Investigation limit reached',
  INPUT_CHANGED: 'Task data changed; this record is stale',
  EVIDENCE_INSUFFICIENT: 'Insufficient evidence',
  MODEL_UNAVAILABLE: 'Agent temporarily unavailable',
}

function observationSummary(observation: InvestigationObservation) {
  const data = observation.result.data
  if (observation.result.tool_name === 'read_decision_overview') {
    return `Reviewed ${Array.isArray(data.suppliers) ? data.suppliers.length : 0} suppliers and the current ranking basis.`
  }
  if (observation.result.tool_name === 'compare_alternatives') {
    return `Compared cost, delivery, and blocking differences for ${Array.isArray(data.gaps) ? data.gaps.length : 0} suppliers.`
  }
  if (observation.result.tool_name === 'inspect_quote_evidence') {
    const focus = { COST: 'cost', DELIVERY: 'delivery', TERMS: 'commercial terms', ALL: 'key' }[String(data.focus)] ?? 'key'
    return `Reviewed ${Array.isArray(data.fields) ? data.fields.length : 0} ${focus} quotation evidence items for ${String(data.supplier_name || data.quote_id || 'this supplier')}.`
  }
  if (observation.result.tool_name === 'inspect_supplier_history') {
    return `Reviewed historical performance and data availability for ${String(data.supplier_name || data.quote_id || 'this supplier')}.`
  }
  if (observation.result.tool_name === 'inspect_policy_evidence') return 'Reviewed the policy retrieval and compliance status frozen with this result.'
  if (observation.result.tool_name === 'compile_decision_brief') {
    const pending = Array.isArray(data.unresolved_items) ? data.unresolved_items.length : 0
    const risks = Array.isArray(data.verified_risks) ? data.verified_risks.length : 0
    if (pending > 0) return `Compiled the findings, retaining ${risks} verified risks and ${pending} follow-up items.`
    return risks > 0 ? `Compiled the findings and retained ${risks} verified risks; there are no current follow-up items.` : 'Compiled the facts and conclusions from this investigation.'
  }
  if (observation.result.tool_name === 'draft_clarification' && typeof data.text === 'string') {
    return data.text
  }
  if (observation.result.tool_name === 'analyze_selection_gap') {
    const details = [
      typeof data.cost_difference_vs_other === 'string' ? `Cost difference ${data.cost_difference_vs_other}` : null,
      typeof data.delivery_days_late === 'number' ? `Delivery difference ${data.delivery_days_late} days` : null,
    ].filter(Boolean)
    return details.join('; ') || 'Cost, delivery, and blocking items have been analysed.'
  }
  if (observation.result.tool_name === 'simulate_requirement_change') {
    const comparison = data.comparison as Record<string, unknown> | undefined
    const rows = Array.isArray(comparison?.supplier_results) ? comparison.supplier_results as Record<string, unknown>[] : []
    const ids = Array.isArray(comparison?.recommended_quote_ids) ? comparison.recommended_quote_ids : []
    const recommended = rows.filter((row) => ids.includes(row.quote_id)).map((row) => String(row.supplier_name))
    const changes = data.changes as Record<string, unknown> | undefined
    const conditions = [changes?.budget_amount ? `Budget ${changes.budget_amount}` : null,
      changes?.delivery_deadline ? `Delivery ${changes.delivery_deadline}` : null].filter(Boolean).join(', ')
    return `Assumptions: ${conditions || 'Authorised conditions'}; simulated recommendation: ${recommended.join(', ') || 'None'}. Official procurement requirements and recommendation are unchanged.`
  }
  if (observation.result.sources.length > 0) {
    return `Found ${observation.result.sources.length} traceable sources.`
  }
  if (observation.result.status === 'NOT_FOUND') return 'No usable records were found within the current scope.'
  if (observation.result.status === 'NEEDS_INPUT') return 'Additional information is required from a user or administrator.'
  if (observation.result.status === 'DENIED') return 'This call is outside the authorised scope of this investigation.'
  return observation.reason || 'Check result saved.'
}

export function InvestigationPage() {
  const { taskId = '' } = useParams()
  const task = useQuery({ queryKey: ['tasks', taskId], queryFn: () => api.getTask(taskId), enabled: Boolean(taskId) })
  const investigations = useQuery({ queryKey: ['tasks', taskId, 'investigations'], queryFn: () => api.listInvestigations(taskId), enabled: Boolean(taskId) })
  if (task.isPending || investigations.isPending) return <section className="card loading-panel">Loading investigation log…</section>
  if (task.isError || investigations.isError) return <section className="card error-panel">{errorMessage(task.error ?? investigations.error)}</section>
  const data = task.data
  return (
    <div className="page-stack investigation-page">
      <TaskWorkspaceHeader taskId={data.task_id} scenarioId={data.scenario_id} title={data.task_name} subtitle={`${investigations.data.length} read-only investigation records`} status={data.status} revision={data.task_revision} resultId={data.current_result_id} quoteCount={data.quotes.length} summaryComplete={data.summary_completed} progress={data.progress} reviewBlocked={Boolean(data.current_issue)} policyReviewBlocked={data.current_issue?.issue_type === 'POLICY_EVIDENCE_REVIEW'} active="investigations" />
      <section className="review-workspace-lead"><div><p className="eyebrow">Supporting investigation</p><h2>Investigation steps</h2><p>This page retains read-only investigations created to resolve unknown information. Start new investigations from decision results, and use the decision assistant for budget or delivery simulations.</p></div><span>{investigations.data.filter((item) => item.is_current).length} current records · {investigations.data.length} total</span></section>
      {data.current_result_id && <Link className="button button-secondary" to={`/tasks/${taskId}/decision`}>Back to decision results</Link>}
      {investigations.data.length === 0 && <section className="card audit-empty">This task has no items requiring further investigation. This is a normal state.</section>}
      <div className="investigation-list">
        {investigations.data.map((item) => (
          <article className="card investigation-card" key={item.case_id}>
            <header><div><strong>{item.kind === 'DECISION' ? 'Decision investigation' : item.quote_id ? (data.quotes.find((quote) => quote.quote_id === item.quote_id)?.supplier_id ?? 'Quotation investigation') : 'Policy investigation'}</strong><span>{item.kind === 'QUOTE' ? 'Quotation information review' : item.kind === 'DECISION' ? 'Supplier comparison review' : 'Policy evidence review'}</span></div><span className={`status-pill ${item.is_current ? 'status-ready' : 'status-muted'}`}>{item.is_current ? 'Current' : 'Historical'}</span></header>
            <p>{item.goal}</p>
            <dl className="detail-grid"><div><dt>Decision impact</dt><dd>{impactStatusLabel(item.impact_status)}</dd></div><div><dt>Investigation result</dt><dd>{item.stop_reason ? (stopReasonLabels[item.stop_reason] ?? item.stop_reason) : 'Check recorded'}</dd></div><div><dt>Model calls</dt><dd>{item.model_calls > 0 ? `${item.model_calls}` : 'Not called'}</dd></div></dl>
            {item.unknown_fields.length > 0 && <p><strong>Information requiring confirmation: </strong>{item.unknown_fields.map(fieldLabel).join(', ')}</p>}
            {item.plan.length > 0 && <ol>{item.plan.map((step) => <li key={step}>{step}</li>)}</ol>}
            <div className="investigation-observations">
              {item.observations.map((observation) => <div key={observation.sequence}><strong>{observation.sequence}. {toolLabels[observation.result.tool_name] ?? observation.result.tool_name}</strong><span>{observation.result.status === 'OK' ? 'Completed' : observation.result.status}</span><p>{observationSummary(observation)}</p></div>)}
            </div>
            {item.clarification.length > 0 && <div className="run-notice">{item.clarification.length} items require manual confirmation. Process them together under Action Items.</div>}
          </article>
        ))}
      </div>
    </div>
  )
}
