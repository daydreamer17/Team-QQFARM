import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { api, ApiClientError } from '../api/client'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'

function errorMessage(error: unknown) {
  return error instanceof ApiClientError ? error.message : '调查记录读取失败。'
}

export function InvestigationPage() {
  const { taskId = '' } = useParams()
  const task = useQuery({ queryKey: ['tasks', taskId], queryFn: () => api.getTask(taskId), enabled: Boolean(taskId) })
  const investigations = useQuery({ queryKey: ['tasks', taskId, 'investigations'], queryFn: () => api.listInvestigations(taskId), enabled: Boolean(taskId) })
  if (task.isPending || investigations.isPending) return <section className="card loading-panel">正在读取调查记录…</section>
  if (task.isError || investigations.isError) return <section className="card error-panel">{errorMessage(task.error ?? investigations.error)}</section>
  const data = task.data
  return (
    <div className="page-stack investigation-page">
      <TaskWorkspaceHeader taskId={data.task_id} scenarioId={data.scenario_id} title={data.requirement.manufacturer_part_number} subtitle={`${investigations.data.length} 个调查 Case · 只读记录`} status={data.status} revision={data.task_revision} resultId={data.current_result_id} quoteCount={data.quotes.length} summaryComplete={data.summary_completed} progress={data.progress} reviewBlocked={Boolean(data.current_issue)} policyReviewBlocked={data.current_issue?.issue_type === 'POLICY_EVIDENCE_REVIEW'} active="investigations" />
      <section className="review-workspace-lead"><div><p className="eyebrow">BOUNDED INVESTIGATIONS</p><h2>调查记录</h2><p>展示后端保存的计划、工具观察和停止原因；历史输入对应的 Case 会标记为 STALE。</p></div><span>{investigations.data.filter((item) => item.is_current).length} 个当前 Case</span></section>
      {investigations.data.length === 0 && <section className="card audit-empty">当前任务没有调查记录。Agent 关闭或没有需要调查的事实时这是正常状态。</section>}
      <div className="investigation-list">
        {investigations.data.map((item) => (
          <article className="card investigation-card" key={item.case_id}>
            <header><div><strong>{item.kind} · {item.quote_id ?? 'Policy'}</strong><code>{item.case_id}</code></div><span className={`status-pill ${item.is_current ? 'status-ready' : 'status-muted'}`}>{item.status}</span></header>
            <p>{item.goal}</p>
            <dl className="detail-grid"><div><dt>影响状态</dt><dd>{item.impact_status}</dd></div><div><dt>停止原因</dt><dd>{item.stop_reason ?? '—'}</dd></div><div><dt>模型</dt><dd>{item.model_id ?? '未调用'}</dd></div><div><dt>模型调用</dt><dd>{item.model_calls}</dd></div></dl>
            {item.unknown_fields.length > 0 && <p><strong>未知字段：</strong>{item.unknown_fields.join('、')}</p>}
            {item.plan.length > 0 && <ol>{item.plan.map((step) => <li key={step}>{step}</li>)}</ol>}
            <div className="investigation-observations">
              {item.observations.map((observation) => <div key={observation.sequence}><strong>#{observation.sequence} {observation.result.tool_name}</strong><span>{observation.result.status} · {observation.latency_ms.toFixed(0)} ms</span><p>{observation.reason}</p>{observation.result.error_code && <code>{observation.result.error_code}</code>}</div>)}
            </div>
            {item.clarification.length > 0 && <details><summary>需要人工核对的澄清卡（{item.clarification.length}）</summary><pre>{JSON.stringify(item.clarification, null, 2)}</pre></details>}
          </article>
        ))}
      </div>
    </div>
  )
}
