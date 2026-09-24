import { useMutation, useQuery } from '@tanstack/react-query'
import { type FormEvent, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, ApiClientError } from '../api/client'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'
import { dispositionLabel, quoteStatusLabel, reasonText } from '../lib/presentation'

function errorMessage(error: unknown) {
  if (error instanceof ApiClientError) {
    if (error.code === 'selection_review_required') return '请先完成报价审核和决策比较。'
    if (error.code === 'task_revision_conflict' || error.code === 'selection_input_stale') return '采购数据已经更新，请刷新页面后重试。'
    return error.message
  }
  return '暂时无法读取差距分析，请稍后重试。'
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
  if (task.isPending) return <section className="card loading-panel">正在读取任务…</section>
  if (task.isError) return <section className="card error-panel">{errorMessage(task.error)}</section>
  const data = task.data
  const eligibleCount = gaps.data?.gaps.filter((gap) => gap.status === 'FEASIBLE').length ?? 0
  const excludedCount = gaps.data?.gaps.filter((gap) => gap.status === 'INFEASIBLE').length ?? 0
  function supplierName(quoteId: string) {
    return suppliers.get(quoteId) ?? data.quotes.find((quote) => quote.quote_id === quoteId)?.supplier_id ?? '未命名供应商'
  }
  function submitSimulation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const changes = { ...(budget.trim() ? { budget_amount: budget.trim() } : {}), ...(deadline ? { delivery_deadline: deadline } : {}) }
    if (Object.keys(changes).length > 0) simulation.mutate(changes)
  }
  return (
    <div className="page-stack selection-gap-page">
      <TaskWorkspaceHeader taskId={data.task_id} scenarioId={data.scenario_id} title={data.task_name} subtitle="查看每份报价为何入选或未入选" status={data.status} revision={data.task_revision} resultId={data.current_result_id} quoteCount={data.quotes.length} summaryComplete={data.summary_completed} progress={data.progress} reviewBlocked={Boolean(data.current_issue)} policyReviewBlocked={data.current_issue?.issue_type === 'POLICY_EVIDENCE_REVIEW'} active="gaps" />
      <section className="selection-gap-intro">
        <div><p className="eyebrow">选择说明</p><h2>为什么选它，其他报价差在哪里</h2><p>集中展示成本、交期和未满足条件。这里只解释当前结果，不会修改正式采购数据。</p></div>
        <div className="selection-gap-totals"><span><strong>{eligibleCount}</strong> 份符合要求</span><span><strong>{excludedCount}</strong> 份未入选</span></div>
      </section>
      {!data.current_result_id && <section className="card audit-empty">完成决策比较后，这里会解释每份报价的入选与未入选原因。</section>}
      {gaps.isPending && data.current_result_id && <section className="card loading-panel">正在整理报价差距…</section>}
      {(gaps.isError || results.isError) && <section className="card error-panel">{errorMessage(gaps.error ?? results.error)}</section>}
      <section className="selection-gap-list" aria-label="逐供应商差距">
        {gaps.data?.gaps.map((gap) => {
          const draft = gaps.data.clarification_drafts.find((item) => item.quote_id === gap.quote_id)
          const isEligible = gap.status === 'FEASIBLE'
          const isPending = gap.status === 'PENDING'
          return <article className={`selection-gap-card gap-${gap.status.toLowerCase()}`} key={gap.quote_id}>
            <header><div className="gap-supplier"><span className="gap-status-icon">{isEligible ? '✓' : isPending ? '!' : '×'}</span><div><h3>{supplierName(gap.quote_id)}</h3><p>{quoteStatusLabel(gap.status)}</p></div></div><span className={`gap-status-label gap-status-${gap.status.toLowerCase()}`}>{quoteStatusLabel(gap.status)}</span></header>
            <dl className="gap-metrics"><div><dt>确认总成本</dt><dd>{gap.confirmed_total_cost === null ? '尚不能确定' : `${gap.currency} ${gap.confirmed_total_cost}`}</dd></div><div><dt>超过预算</dt><dd>{gap.budget_excess === null ? '—' : `${gap.currency} ${gap.budget_excess}`}</dd></div><div><dt>与更优方案的成本差</dt><dd>{gap.cost_difference_vs_other === null ? '—' : `${gap.currency} ${gap.cost_difference_vs_other}`}</dd></div><div><dt>交付延迟</dt><dd>{gap.delivery_days_late === null || gap.delivery_days_late === 0 ? '按时' : `${gap.delivery_days_late} 天`}</dd></div></dl>
            {isEligible && gap.failed_reasons.length === 0 && gap.pending_reasons.length === 0 && <div className="gap-conclusion gap-conclusion-good">该报价满足当前采购硬性要求，可以进入排序比较。</div>}
            {gap.failed_reasons.length > 0 && <div className="gap-conclusion gap-conclusion-bad"><strong>未入选原因</strong><ul>{gap.failed_reasons.map((item) => <li key={item.code}>{reasonText(item)}</li>)}</ul></div>}
            {gap.pending_reasons.length > 0 && <div className="gap-conclusion gap-conclusion-pending"><strong>仍需确认</strong><ul>{gap.pending_reasons.map((item) => <li key={item.code}>{reasonText(item)}</li>)}</ul></div>}
            {draft && <details className="gap-draft"><summary>查看建议沟通内容</summary><p>{draft.text}</p></details>}
          </article>
        })}
      </section>

      <form className="card simulation-form" onSubmit={submitSimulation}>
        <div><p className="eyebrow">条件试算</p><h2>如果预算或交期改变，推荐会变化吗？</h2><p>试算结果不会修改正式需求，也不会代替制度检查。</p></div>
        <label className="field"><span>假设预算（{data.requirement.currency}）</span><input inputMode="decimal" value={budget} onChange={(event) => { setBudget(event.target.value); simulation.reset() }} placeholder={data.requirement.budget_amount} /></label>
        <label className="field"><span>假设交付截止日期</span><input type="date" value={deadline} onChange={(event) => { setDeadline(event.target.value); simulation.reset() }} /></label>
        <button className="button button-submit" disabled={task.data.status === 'ABANDONED' || simulation.isPending || (!budget.trim() && !deadline)}>{simulation.isPending ? '正在试算…' : '运行试算'}</button>
        {simulation.isError && <div className="form-error">{errorMessage(simulation.error)}</div>}
      </form>
      {simulation.data && <section className="card simulation-result"><span className="status-pill status-pending">仅供试算</span><h2>{dispositionLabel(simulation.data.result.comparison.disposition)}</h2><p>推荐候选：{simulation.data.result.comparison.recommended_quote_ids.map(supplierName).join('、') || '无'}</p><small>该结果不会写入正式推荐，也没有重新执行制度检查。</small></section>}
    </div>
  )
}
