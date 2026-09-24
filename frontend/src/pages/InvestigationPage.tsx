import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { api, ApiClientError } from '../api/client'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'
import { fieldLabel, impactStatusLabel } from '../lib/presentation'

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
      <TaskWorkspaceHeader taskId={data.task_id} scenarioId={data.scenario_id} title={data.task_name} subtitle={`${investigations.data.length} 条只读调查记录`} status={data.status} revision={data.task_revision} resultId={data.current_result_id} quoteCount={data.quotes.length} summaryComplete={data.summary_completed} progress={data.progress} reviewBlocked={Boolean(data.current_issue)} policyReviewBlocked={data.current_issue?.issue_type === 'POLICY_EVIDENCE_REVIEW'} active="investigations" />
      <section className="review-workspace-lead"><div><p className="eyebrow">辅助调查</p><h2>系统调查记录</h2><p>仅用于说明系统为解决未知信息做过哪些检查；无需日常逐项操作。</p></div><span>{investigations.data.filter((item) => item.is_current).length} 条当前记录</span></section>
      {investigations.data.length === 0 && <section className="card audit-empty">当前任务没有需要额外调查的信息，这是正常状态。</section>}
      <div className="investigation-list">
        {investigations.data.map((item) => (
          <article className="card investigation-card" key={item.case_id}>
            <header><div><strong>{item.quote_id ? (data.quotes.find((quote) => quote.quote_id === item.quote_id)?.supplier_id ?? '报价调查') : '制度调查'}</strong><span>{item.kind === 'QUOTE' ? '报价信息核查' : '制度依据核查'}</span></div><span className={`status-pill ${item.is_current ? 'status-ready' : 'status-muted'}`}>{item.is_current ? '当前记录' : '历史记录'}</span></header>
            <p>{item.goal}</p>
            <dl className="detail-grid"><div><dt>对决策的影响</dt><dd>{impactStatusLabel(item.impact_status)}</dd></div><div><dt>检查状态</dt><dd>{item.stop_reason ? '本轮检查已停止' : '检查已记录'}</dd></div><div><dt>自动分析</dt><dd>{item.model_calls > 0 ? `已分析 ${item.model_calls} 次` : '未调用'}</dd></div></dl>
            {item.unknown_fields.length > 0 && <p><strong>待确认信息：</strong>{item.unknown_fields.map(fieldLabel).join('、')}</p>}
            {item.plan.length > 0 && <ol>{item.plan.map((step) => <li key={step}>{step}</li>)}</ol>}
            <div className="investigation-observations">
              {item.observations.map((observation) => <div key={observation.sequence}><strong>检查步骤 {observation.sequence}</strong><span>已保存检查结果</span><p>{observation.reason}</p></div>)}
            </div>
            {item.clarification.length > 0 && <div className="run-notice">有 {item.clarification.length} 项信息需要人工确认，请前往“待处理事项”统一处理。</div>}
          </article>
        ))}
      </div>
    </div>
  )
}
