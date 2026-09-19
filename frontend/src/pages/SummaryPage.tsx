import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'
import { cleanSummaryText, summaryStatusLabel } from '../lib/presentation'

function errorMessage(error: unknown) {
  return error instanceof ApiClientError ? error.message : '采购总结读取失败。'
}

export function SummaryPage() {
  const { taskId = '' } = useParams()
  const queryClient = useQueryClient()
  const task = useQuery({ queryKey: ['tasks', taskId], queryFn: () => api.getTask(taskId), enabled: Boolean(taskId) })
  const summaries = useQuery({
    queryKey: ['tasks', taskId, 'summaries'],
    queryFn: () => api.listSummaries(taskId),
    enabled: Boolean(taskId),
    refetchInterval: (query) => query.state.data?.items.some((item) => ['PENDING', 'RUNNING'].includes(item.status)) ? 1_500 : false,
  })
  const result = useQuery({
    queryKey: ['tasks', taskId, 'results', task.data?.current_result_id],
    queryFn: () => api.getResult(taskId, task.data!.current_result_id!),
    enabled: Boolean(taskId && task.data?.current_result_id),
  })
  const refresh = async () => queryClient.invalidateQueries({ queryKey: ['tasks', taskId, 'summaries'] })
  const generate = useMutation({
    mutationFn: () => api.createSummary(taskId, task.data!.task_revision, task.data!.current_result_id!, createIdempotencyKey()),
    onSuccess: refresh,
  })
  const retry = useMutation({
    mutationFn: (summaryId: string) => api.retrySummary(taskId, summaryId, task.data!.task_revision, createIdempotencyKey()),
    onSuccess: refresh,
  })

  if (task.isPending || summaries.isPending) return <section className="card loading-panel">正在读取采购总结…</section>
  if (task.isError || summaries.isError) return <section className="card error-panel" role="alert">{errorMessage(task.error ?? summaries.error)}</section>
  const data = task.data
  const current = summaries.data.items.find((item) => item.is_current) ?? summaries.data.items[0]
  const mayGenerate = Boolean(data.current_result_id && data.status !== 'ABANDONED' && (!current || !current.is_current))
  const supplierNames = new Map(result.data?.result.supplier_results.map((item) => [item.quote_id, item.supplier_name]) ?? [])
  const narrativeText = (value: string) => cleanSummaryText(value, supplierNames)

  return <div className="page-stack summary-page">
    <TaskWorkspaceHeader taskId={data.task_id} scenarioId={data.scenario_id} title={data.requirement.manufacturer_part_number} subtitle={`${data.requirement.required_quantity} ${data.requirement.quantity_unit} · ${data.quotes.length} 份正式报价`} status={data.status} revision={data.task_revision} resultId={data.current_result_id} quoteCount={data.quotes.length} summaryComplete={data.summary_completed} progress={data.progress} active="summary" />

    <section className="card summary-hero">
      <div><p className="eyebrow">采购总结</p><h2>{current?.narrative?.title ? narrativeText(current.narrative.title) : (data.current_result_id ? '生成采购决策摘要' : '等待决策比较完成')}</h2><p>{current?.narrative?.overview ? narrativeText(current.narrative.overview) : '用简洁中文汇总推荐结论、各报价差异和制度依据，不重新计算或批准采购。'}</p></div>
      {mayGenerate && <button className="button button-submit" type="button" disabled={generate.isPending} onClick={() => generate.mutate()}>{generate.isPending ? '正在创建…' : '生成采购总结'}</button>}
      {current && <span className={`status-pill ${current.status === 'SUCCEEDED' ? 'status-ready' : current.status === 'FAILED' ? 'status-offline' : 'status-pending'}`}>{summaryStatusLabel(current.status)} · 第 {current.task_revision} 版</span>}
      {current?.status === 'FAILED' && current.is_current && data.status !== 'ABANDONED' && <button className="button button-secondary" type="button" disabled={retry.isPending || current.calls_used >= current.max_calls} onClick={() => retry.mutate(current.summary_id)}>重试生成</button>}
      {(generate.isError || retry.isError) && <div className="form-error">{errorMessage(generate.error ?? retry.error)}</div>}
    </section>

    {current?.status === 'FAILED' && <section className="card error-panel"><strong>总结生成失败</strong><p>{current.error_message}</p><small>已尝试 {current.calls_used} 次</small></section>}

    {current && <section className="summary-meta-strip" aria-label="总结依据">
      <div><span>对应采购版本</span><strong>第 {current.task_revision} 版</strong></div>
      <div><span>纳入报价</span><strong>{data.quotes.length} 份</strong></div>
      <div><span>制度检查</span><strong>{data.policy_binding ? '已绑定制度' : '未绑定制度'}</strong></div>
      <div><span>生成时间</span><strong>{new Date(current.updated_at).toLocaleString('zh-CN')}</strong></div>
    </section>}

    {current?.narrative && <section className="summary-scope-grid">{current.narrative.sections.map((section, index) => <article key={section.heading + index}><span>{String(index + 1).padStart(2, '0')}</span><strong>{narrativeText(section.heading)}</strong><p>{narrativeText(section.text)}</p><small>依据当前报价、比较结果与制度记录</small></article>)}</section>}
    {current?.narrative && <section className="card summary-boundary"><strong>使用边界</strong><p>{narrativeText(current.narrative.disclaimer)}</p></section>}

    {summaries.data.items.length > 1 && <section><div className="section-heading"><h2>历史采购总结</h2><span>{summaries.data.items.length}</span></div><div className="summary-history-list">{summaries.data.items.map((item) => <article className="card summary-history-record" key={item.summary_id}><strong>{summaryStatusLabel(item.status)}</strong><span>采购任务第 {item.task_revision} 版</span><small>{new Date(item.created_at).toLocaleString('zh-CN')}</small></article>)}</div></section>}
  </div>
}
