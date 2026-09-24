import { useQuery } from '@tanstack/react-query'
import { Link, Navigate, useLocation, useParams } from 'react-router-dom'
import { api, ApiClientError } from '../api/client'
import { RunPanel } from '../components/RunPanel'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'

function errorMessage(error: unknown) {
  return error instanceof ApiClientError ? error.message : '任务读取失败。'
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

  if (task.isPending) return <section className="card loading-panel">正在读取决策工作区…</section>
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
        subtitle={`${data.requirement.required_quantity} ${data.requirement.quantity_unit} · ${data.quotes.length} 份正式报价`}
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

      <section className="decision-section-lead">
        <div>
          <h2>决策比较</h2>
          {expectedRevision !== null && !data.current_result_id && (
            <p>正在生成第 {expectedRevision} 版决策结果，完成后将自动打开。</p>
          )}
          {expectedGraphRunId !== null && (data.status === 'QUEUED' || data.status === 'RUNNING') && (
            <p>正在按当前代码重新分析；旧结果仍保留为历史记录，新结果完成后将自动打开。</p>
          )}
        </div>
      </section>

      {complianceBlocked ? <section className="card decision-empty-state"><h2>请先处理制度检查</h2><p>核对材料并确认当前版本后，即可进入决策比较。</p><Link className="button button-submit" to={`/tasks/${taskId}/compliance`}>前往制度检查</Link></section> : data.quotes.length === 0 ? (
        <section className="card decision-empty-state">
          <div><h2>暂无可比较的报价</h2></div>
          <p>请先完成报价审核并正式提交。</p>
          <Link className="button button-submit" to={`/tasks/${taskId}/quotes/new`}>前往报价审核</Link>
        </section>
      ) : (
        <>
          {quoteBlocked && (
            <div className="run-notice decision-blocker-link">
              报价字段或证据仍有阻塞项。
              <Link to={batchReviewBlocked ? `/tasks/${taskId}/review` : `/tasks/${taskId}/quotes/new`}>
                {batchReviewBlocked ? '进入待处理事项' : '返回报价与证据处理'}
              </Link>
            </div>
          )}
          {policyBlocked && (
            <div className="run-notice decision-blocker-link">
              制度检索需要人工复核。
              <Link to={`/tasks/${taskId}/compliance`}>进入合规页面处理</Link>
            </div>
          )}
          <RunPanel task={data} onRefresh={() => void task.refetch()} />
        </>
      )}
    </div>
  )
}
