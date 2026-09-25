import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'

export function TaskPage() {
  const { taskId = '' } = useParams()
  const task = useQuery({
    queryKey: ['tasks', taskId],
    queryFn: () => api.getTask(taskId),
    enabled: Boolean(taskId),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === 'QUEUED' || status === 'RUNNING' ? 2_000 : false
    },
  })
  const [showAbandon, setShowAbandon] = useState(false)
  const [abandonReason, setAbandonReason] = useState('')
  const abandon = useMutation({
    mutationFn: () => api.abandonTask(taskId, task.data!.task_revision, abandonReason.trim(), createIdempotencyKey()),
    onSuccess: async () => {
      setShowAbandon(false)
      setAbandonReason('')
      await task.refetch()
    },
  })

  if (task.isPending) {
    return <section className="card loading-panel">Loading tasks…</section>
  }

  if (task.isError) {
    const message = task.error instanceof ApiClientError
      ? task.error.message
      : 'Unable to load the task.'
    return (
      <section className="card error-panel" role="alert">
        <p className="eyebrow">TASK ERROR</p>
        <h1>Unable to load task</h1>
        <p>{message}</p>
        <div className="inline-actions">
          <button className="button button-secondary" type="button" onClick={() => void task.refetch()}>
            Reload
          </button>
          <Link className="button button-secondary" to="/">Back to workspace</Link>
        </div>
      </section>
    )
  }

  const requirement = task.data.requirement
  const reviewBlocked =
    task.data.status === 'NEEDS_INPUT' ||
    (task.data.status === 'FAILED' &&
      task.data.current_job?.error_code === 'review_required') ||
    Boolean(task.data.current_job?.correction_batch_incomplete)

  return (
    <div className="page-stack">
      <TaskWorkspaceHeader
        taskId={task.data.task_id}
        scenarioId={task.data.scenario_id}
        title={task.data.task_name}
        subtitle={`${requirement.required_quantity} ${requirement.quantity_unit} · ${requirement.currency} · Delivery by ${requirement.delivery_deadline}`}
        status={task.data.status}
        revision={task.data.task_revision}
        resultId={task.data.current_result_id}
        quoteCount={task.data.quotes.length}
        summaryComplete={task.data.summary_completed}
        progress={task.data.progress}
        reviewBlocked={reviewBlocked}
        policyReviewBlocked={task.data.current_issue?.issue_type === 'POLICY_EVIDENCE_REVIEW'}
        active="overview"
      />

      <section className="card workspace-page-lead">
        <div className="workspace-page-lead-copy">
          <h2>Requirements</h2>
          <p>View the confirmed procurement scope and decision settings for the current revision.</p>
        </div>
      </section>

      <section>
        <dl className="detail-grid">
          <div><dt>Manufacturer</dt><dd>{requirement.manufacturer}</dd></div>
          <div><dt>Manufacturer part number</dt><dd>{requirement.manufacturer_part_number}</dd></div>
          <div><dt>Package / revision</dt><dd>{requirement.package} / {requirement.revision}</dd></div>
          <div><dt>Item condition</dt><dd>{requirement.condition}</dd></div>
          <div><dt>Allow Substitutes</dt><dd>{requirement.allow_substitutes ? 'Yes' : 'No'}</dd></div>
          <div><dt>Required Quantity</dt><dd>{requirement.required_quantity} {requirement.quantity_unit}</dd></div>
          <div><dt>Budget</dt><dd>{requirement.currency} {requirement.budget_amount}</dd></div>
          <div><dt>Budget includes shipping</dt><dd>{requirement.includes_shipping ? 'Yes' : 'No'}</dd></div>
          <div><dt>Cost Comparison Basis</dt><dd>{requirement.tax_mode}</dd></div>
          <div><dt>Other Fee Requirement</dt><dd>{requirement.other_fees_required ? 'Required' : 'Not required'}</dd></div>
          <div><dt>Planned Order Date</dt><dd>{requirement.planned_order_date ?? '—'}</dd></div>
          <div><dt>Delivery Deadline</dt><dd>{requirement.delivery_deadline}</dd></div>
          <div><dt>Delivery Location</dt><dd>{requirement.delivery_location}</dd></div>
          <div><dt>Primary ranking criterion</dt><dd>{requirement.ranking_preference}</dd></div>
          <div><dt>Secondary ranking criterion</dt><dd>{requirement.secondary_preference ?? '—'}</dd></div>
          <div><dt>Policy binding</dt><dd>{task.data.policy_binding ? `${task.data.policy_binding.policy_set_version} / ${task.data.policy_binding.policy_index_version}` : 'Not bound'}</dd></div>
          <div><dt>Policy scope</dt><dd>{task.data.policy_binding ? `${task.data.policy_binding.category} · ${task.data.policy_binding.region}` : 'Policy retrieval not run'}</dd></div>
        </dl>
        <div className="requirement-actions">
          <div className="inline-actions">
            {task.data.status !== 'ABANDONED' && <Link className="button button-secondary" to={`/tasks/${taskId}/edit`}>Edit</Link>}
            {task.data.status !== 'ABANDONED' && <button className="button button-danger" type="button" onClick={() => setShowAbandon(true)}>Abandon</button>}
            {task.data.status === 'ABANDONED' && <><span className="status-pill status-muted">Abandoned</span><small>Historical records are read-only.</small></>}
          </div>
            {showAbandon && <div className="card abandon-task-panel"><strong>Confirm task abandonment</strong><p>This action cannot be reversed, but quotations, source documents, results and audit records will be retained.</p><label className="field"><span>Reason for abandonment</span><textarea value={abandonReason} onChange={(event) => setAbandonReason(event.target.value)} minLength={3} maxLength={1000} /></label><div className="inline-actions"><button className="button button-secondary" type="button" onClick={() => setShowAbandon(false)}>Cancel</button><button className="button button-danger" type="button" disabled={abandonReason.trim().length < 3 || abandon.isPending} onClick={() => { if (window.confirm('Permanently set this task to read-only abandoned status?')) abandon.mutate() }}>{abandon.isPending ? 'Abandoning…' : 'Abandon'}</button></div>{abandon.isError && <div className="form-error">{abandon.error instanceof ApiClientError ? abandon.error.message : 'Unable to abandon task.'}</div>}</div>}
        </div>
      </section>
    </div>
  )
}
