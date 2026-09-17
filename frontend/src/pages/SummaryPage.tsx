import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api, ApiClientError } from '../api/client'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'

function errorMessage(error: unknown) {
  return error instanceof ApiClientError ? error.message : '任务读取失败。'
}

export function SummaryPage() {
  const { taskId = '' } = useParams()
  const task = useQuery({
    queryKey: ['tasks', taskId],
    queryFn: () => api.getTask(taskId),
    enabled: Boolean(taskId),
  })

  if (task.isPending) return <section className="card loading-panel">正在读取 Summary…</section>
  if (task.isError) return <section className="card error-panel" role="alert">{errorMessage(task.error)}</section>
  const data = task.data

  return (
    <div className="page-stack summary-page">
      <TaskWorkspaceHeader
        taskId={data.task_id}
        scenarioId={data.scenario_id}
        title={data.requirement.manufacturer_part_number}
        subtitle={`${data.requirement.required_quantity} ${data.requirement.quantity_unit} · ${data.quotes.length} 份正式报价`}
        status={data.status}
        revision={data.task_revision}
        resultId={data.current_result_id}
        quoteCount={data.quotes.length}
        active="summary"
      />

      <section className="card summary-hero">
        <div>
          <p className="eyebrow">AI SUMMARY</p>
          <h2>{data.current_result_id ? '生成采购决策摘要' : '等待决策比较完成'}</h2>
          <p>
            {data.current_result_id
              ? '未来的 AI Summary 将基于当前冻结结果，整理推荐、成本与交期、关键风险、报价证据、制度引用和版本信息。'
              : '正式报价完成决策分析后，才能基于冻结结果生成可审计的摘要。'}
          </p>
        </div>
        {data.current_result_id && (
          <Link className="button button-secondary" to={`/tasks/${taskId}/results/${data.current_result_id}`}>
            查看当前决策结果
          </Link>
        )}
        <button className="button button-submit" type="button" disabled title="后端 AI 报告接口待接入">
          生成 AI Summary
        </button>
        <small>后端 AI 报告接口待接入；本页不会生成或展示虚构报告。</small>
      </section>

      <section className="summary-scope-grid" aria-label="未来 Summary 内容">
        <article><span>01</span><strong>推荐结论</strong><p>候选范围、推荐依据与决策边界。</p></article>
        <article><span>02</span><strong>成本与交期</strong><p>确认总成本、数量影响和到货时间。</p></article>
        <article><span>03</span><strong>风险与证据</strong><p>报价原文、关键风险与待确认事项。</p></article>
        <article><span>04</span><strong>制度与版本</strong><p>Policy 引用、规则版本与审计信息。</p></article>
      </section>
    </div>
  )
}
