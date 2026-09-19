import { useQuery } from '@tanstack/react-query'
import { Link, Navigate, useParams } from 'react-router-dom'
import { api, ApiClientError } from '../api/client'
import { RunPanel } from '../components/RunPanel'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'

function errorMessage(error: unknown) {
  return error instanceof ApiClientError ? error.message : '任务读取失败。'
}

export function DecisionPage() {
  const { taskId = '' } = useParams()
  const task = useQuery({
    queryKey: ['tasks', taskId],
    queryFn: () => api.getTask(taskId),
    enabled: Boolean(taskId),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === 'QUEUED' || status === 'RUNNING' ? 1_500 : false
    },
  })

  if (task.isPending) return <section className="card loading-panel">正在读取决策工作区…</section>
  if (task.isError) return <section className="card error-panel" role="alert">{errorMessage(task.error)}</section>
  if (task.data.current_result_id) {
    return <Navigate to={`/tasks/${taskId}/results/${task.data.current_result_id}`} replace />
  }

  const data = task.data
  const policyBlocked = data.current_issue?.issue_type === 'POLICY_EVIDENCE_REVIEW'
  const quoteBlocked = Boolean(data.current_issue) && !policyBlocked
    || data.current_job?.error_code === 'review_required'

  return (
    <div className="page-stack decision-page">
      <TaskWorkspaceHeader
        taskId={data.task_id}
        scenarioId={data.scenario_id}
        title={data.requirement.manufacturer_part_number}
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
          <p className="eyebrow">DECISION ANALYSIS</p>
          <h2>决策比较</h2>
          <p>仅使用已正式提交的报价，由后端完成金额、可行性、排序和制度检索。</p>
        </div>
      </section>

      {data.quotes.length === 0 ? (
        <section className="card decision-empty-state">
          <div><span>01</span><h2>先完成报价审核</h2></div>
          <p>草稿报价不会进入决策计算。请上传报价、处理全部阻塞字段并正式提交。</p>
          <Link className="button button-submit" to={`/tasks/${taskId}/quotes/new`}>进入报价与审核</Link>
        </section>
      ) : (
        <>
          {quoteBlocked && (
            <div className="run-notice decision-blocker-link">
              报价字段或证据仍有阻塞项。
              <Link to={`/tasks/${taskId}/quotes/new`}>返回报价与证据处理</Link>
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
