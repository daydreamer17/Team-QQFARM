import { useMutation, useQuery } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, ApiClientError } from '../api/client'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'

function errorMessage(error: unknown) {
  if (error instanceof ApiClientError) {
    if (error.code === 'selection_review_required') return '需要先完成当前版本的解析、审核和初步比较。'
    if (error.code === 'task_revision_conflict' || error.code === 'selection_input_stale') return '任务输入已经变化，请刷新后重试。'
    return error.message
  }
  return '入选差距分析失败。'
}

export function SelectionGapPage() {
  const { taskId = '' } = useParams()
  const [budget, setBudget] = useState('')
  const [deadline, setDeadline] = useState('')
  const task = useQuery({ queryKey: ['tasks', taskId], queryFn: () => api.getTask(taskId), enabled: Boolean(taskId) })
  const gaps = useQuery({
    queryKey: ['tasks', taskId, 'selection-gaps', task.data?.task_revision],
    queryFn: () => api.getSelectionGaps(taskId, task.data!.task_revision),
    enabled: Boolean(taskId && task.data?.current_result_id),
    retry: false,
  })
  const simulation = useMutation({
    mutationFn: (changes: { budget_amount?: string; delivery_deadline?: string }) => api.simulateRequirement(taskId, task.data!.task_revision, changes),
  })
  if (task.isPending) return <section className="card loading-panel">正在读取任务…</section>
  if (task.isError) return <section className="card error-panel">{errorMessage(task.error)}</section>
  const data = task.data
  function submitSimulation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const changes = { ...(budget.trim() ? { budget_amount: budget.trim() } : {}), ...(deadline ? { delivery_deadline: deadline } : {}) }
    if (Object.keys(changes).length > 0) simulation.mutate(changes)
  }
  return (
    <div className="page-stack selection-gap-page">
      <TaskWorkspaceHeader taskId={data.task_id} scenarioId={data.scenario_id} title={data.requirement.manufacturer_part_number} subtitle="只读差距分析与明确授权的假设试算" status={data.status} revision={data.task_revision} resultId={data.current_result_id} quoteCount={data.quotes.length} summaryComplete={data.summary_completed} progress={data.progress} reviewBlocked={Boolean(data.current_issue)} policyReviewBlocked={data.current_issue?.issue_type === 'POLICY_EVIDENCE_REVIEW'} active="gaps" />
      <section className="review-workspace-lead"><div><p className="eyebrow">SELECTION GAP</p><h2>入选差距</h2><p>数值和状态由后端确定性规则产生；沟通草稿未发送，也不代表供应商承诺。</p></div></section>
      {!data.current_result_id && <section className="card audit-empty">生成当前比较结果后才能分析入选差距。</section>}
      {gaps.isPending && data.current_result_id && <section className="card loading-panel">正在计算当前版本差距…</section>}
      {gaps.isError && <section className="card error-panel">{errorMessage(gaps.error)}</section>}
      <div className="selection-gap-grid">
        {gaps.data?.gaps.map((gap) => {
          const draft = gaps.data.clarification_drafts.find((item) => item.quote_id === gap.quote_id)
          return <article className="card selection-gap-card" key={gap.quote_id}><header><div><strong>{gap.quote_id}</strong><span>Quote v{gap.quote_version}</span></div><span className="status-pill">{gap.status}</span></header><dl className="detail-grid"><div><dt>确认总成本</dt><dd>{gap.confirmed_total_cost ?? '—'} {gap.currency}</dd></div><div><dt>超过预算</dt><dd>{gap.budget_excess ?? '—'}</dd></div><div><dt>追平其他方案需降低</dt><dd>{gap.total_cost_reduction_to_tie_other ?? '—'}</dd></div><div><dt>交付晚</dt><dd>{gap.delivery_days_late === null ? '—' : `${gap.delivery_days_late} 天`}</dd></div></dl>{gap.failed_reasons.length > 0 && <p><strong>不符合：</strong>{gap.failed_reasons.map((item) => item.message).join('；')}</p>}{gap.pending_reasons.length > 0 && <p><strong>待确认：</strong>{gap.pending_reasons.map((item) => item.message).join('；')}</p>}{draft && <details><summary>查看供应商沟通草稿</summary><pre>{draft.text}</pre></details>}</article>
        })}
      </div>

      <form className="card simulation-form" onSubmit={submitSimulation}>
        <div><p className="eyebrow">AUTHORIZED HYPOTHETICAL</p><h2>需求条件试算</h2><p>此处只试算预算和交付截止日期，不修改正式需求，不执行制度审批。</p></div>
        <label className="field"><span>假设预算（{data.requirement.currency}）</span><input inputMode="decimal" value={budget} onChange={(event) => { setBudget(event.target.value); simulation.reset() }} placeholder={data.requirement.budget_amount} /></label>
        <label className="field"><span>假设交付截止日期</span><input type="date" value={deadline} onChange={(event) => { setDeadline(event.target.value); simulation.reset() }} /></label>
        <button className="button button-submit" disabled={task.data.status === 'ABANDONED' || simulation.isPending || (!budget.trim() && !deadline)}>{simulation.isPending ? '正在试算…' : '明确授权并运行假设试算'}</button>
        {simulation.isError && <div className="form-error">{errorMessage(simulation.error)}</div>}
      </form>
      {simulation.data && <section className="card simulation-result"><span className="status-pill status-pending">HYPOTHETICAL</span><h2>{simulation.data.result.comparison.disposition}</h2><p>推荐候选：{simulation.data.result.comparison.recommended_quote_ids.join('、') || '无'}</p><ul>{simulation.data.result.assumptions.map((item) => <li key={item}>{item}</li>)}</ul><small>正式推荐允许：否 · 制度评估已执行：否</small></section>}
    </div>
  )
}
