import { useQuery } from '@tanstack/react-query'
import { Link, Navigate, useLocation, useParams } from 'react-router-dom'
import { api, ApiClientError } from '../api/client'
import { RunPanel } from '../components/RunPanel'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'

function errorMessage(error: unknown) {
  return error instanceof ApiClientError ? error.message : 'Unable to load the task.'
}

export function DecisionPage() {
  const { taskId = '' } = useParams()
  const location = useLocation()
  const expectedRevision = typeof location.state === 'object'
    && location.state !== null
    && 'expectedRevision' in location.state
    && typeof location.state.expectedRevision === 'number'
    ? location.state.expectedRevision
    : null
  const expectedGraphRunId = typeof location.state === 'object'
    && location.state !== null
    && 'expectedGraphRunId' in location.state
    && typeof location.state.expectedGraphRunId === 'string'
    ? location.state.expectedGraphRunId
    : null
  const previousResultId = typeof location.state === 'object'
    && location.state !== null
    && 'previousResultId' in location.state
    && typeof location.state.previousResultId === 'string'
    ? location.state.previousResultId
    : null
  const task = useQuery({
    queryKey: ['tasks', taskId],
    queryFn: () => api.getTask(taskId),
    enabled: Boolean(taskId),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      const waitingForAppliedRevision = expectedRevision !== null && (
        !query.state.data
        || query.state.data.task_revision < expectedRevision
        || !query.state.data.current_result_id
      )
      const waitingForRerun = expectedGraphRunId !== null
        && query.state.data?.current_graph_run_id === expectedGraphRunId
        && (status === 'QUEUED' || status === 'RUNNING')
      return waitingForAppliedRevision || waitingForRerun || status === 'QUEUED' || status === 'RUNNING'
        ? 1_500
        : false
    },
  })

  if (task.isPending) return <section className="card loading-panel">Loading decision workspace…</section>
  if (task.isError) return <section className="card error-panel" role="alert">{errorMessage(task.error)}</section>
  const expectedRerunReady = expectedGraphRunId !== null
    && task.data.current_graph_run_id === expectedGraphRunId
    && task.data.status === 'COMPLETED'
    && Boolean(task.data.current_result_id)
    && task.data.current_result_id !== previousResultId
  const normalResultReady = expectedGraphRunId === null
    && Boolean(task.data.current_result_id)
    && (expectedRevision === null || task.data.task_revision >= expectedRevision)
  const complianceBlocked = task.data.workflow_contract_version === 'compliance/2.0' && !task.data.progress.compliance?.can_compare
  if (!complianceBlocked && (expectedRerunReady || normalResultReady)) {
    return <Navigate to={`/tasks/${taskId}/results/${task.data.current_result_id}`} replace />
  }

  const data = task.data
  const policyBlocked = data.current_issue?.issue_type === 'POLICY_EVIDENCE_REVIEW'
  const batchReviewBlocked = data.current_issue?.issue_type === 'BATCH_FIELD_REVIEW'
  const quoteBlocked = Boolean(data.current_issue) && !policyBlocked
    || data.current_job?.error_code === 'review_required'

  return (
    <div className="page-stack decision-page">
      <TaskWorkspaceHeader
        taskId={data.task_id}
        scenarioId={data.scenario_id}
        title={data.task_name}
        subtitle={`${data.requirement.required_quantity} ${data.requirement.quantity_unit} · ${data.quotes.length} submitted quotations`}
        status={data.status}
        revision={data.task_revision}
        resultId={data.current_result_id}
        quoteCount={data.quotes.length}
        summaryComplete={data.summary_completed}
        progress={data.progress}
        reviewBlocked={quoteBlocked}
        policyReviewBlocked={policyBlocked}
        active="decision"
      />

      <section className="card workspace-page-lead decision-section-lead">
        <div className="workspace-page-lead-copy">
          <h2>Decision Comparison</h2>
          {expectedRevision !== null && !data.current_result_id && (
            <p>Generating decision results for Revision {expectedRevision}. The result will open automatically when ready.</p>
          )}
          {expectedGraphRunId !== null && (data.status === 'QUEUED' || data.status === 'RUNNING') && (
            <p>Reanalysing with the current code. The previous result remains in history, and the new result will open automatically.</p>
          )}
        </div>
      </section>

      {complianceBlocked ? <section className="card decision-empty-state"><h2>Complete the compliance review first</h2><p>Review the evidence and confirm the current revision before comparing decisions.</p><Link className="button button-submit" to={`/tasks/${taskId}/compliance`}>Go to compliance review</Link></section> : data.quotes.length === 0 ? (
        <section className="card decision-empty-state">
          <div><h2>No comparable quotations</h2></div>
          <p>Review and submit a quotation first.</p>
          <Link className="button button-submit" to={`/tasks/${taskId}/quotes/new`}>Go to quotation review</Link>
        </section>
      ) : (
        <>
          {quoteBlocked && (
            <div className="run-notice decision-blocker-link">
              Quotation fields or evidence still contain blocking issues.
              <Link to={batchReviewBlocked ? `/tasks/${taskId}/review` : `/tasks/${taskId}/quotes/new`}>
                {batchReviewBlocked ? 'Go to action items' : 'Return to quotations and evidence'}
              </Link>
            </div>
          )}
          {policyBlocked && (
            <div className="run-notice decision-blocker-link">
              Policy retrieval requires human review.
              <Link to={`/tasks/${taskId}/compliance`}>Open compliance review</Link>
            </div>
          )}
          <RunPanel task={data} onRefresh={() => void task.refetch()} />
        </>
      )}
    </div>
  )
}
