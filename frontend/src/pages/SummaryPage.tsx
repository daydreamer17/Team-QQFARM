import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'

function errorMessage(error: unknown) {
  return error instanceof ApiClientError ? error.message : 'Summary 数据读取失败。'
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
  const refresh = async () => queryClient.invalidateQueries({ queryKey: ['tasks', taskId, 'summaries'] })
  const generate = useMutation({
    mutationFn: () => api.createSummary(taskId, task.data!.task_revision, task.data!.current_result_id!, createIdempotencyKey()),
    onSuccess: refresh,
  })
  const retry = useMutation({
    mutationFn: (summaryId: string) => api.retrySummary(taskId, summaryId, task.data!.task_revision, createIdempotencyKey()),
    onSuccess: refresh,
  })

  if (task.isPending || summaries.isPending) return <section className="card loading-panel">正在读取 Summary…</section>
  if (task.isError || summaries.isError) return <section className="card error-panel" role="alert">{errorMessage(task.error ?? summaries.error)}</section>
  const data = task.data
  const current = summaries.data.items.find((item) => item.is_current) ?? summaries.data.items[0]
  const mayGenerate = Boolean(data.current_result_id && data.status !== 'ABANDONED' && (!current || !current.is_current))

  return <div className="page-stack summary-page">
    <TaskWorkspaceHeader taskId={data.task_id} scenarioId={data.scenario_id} title={data.requirement.manufacturer_part_number} subtitle={`${data.requirement.required_quantity} ${data.requirement.quantity_unit} · ${data.quotes.length} 份正式报价`} status={data.status} revision={data.task_revision} resultId={data.current_result_id} quoteCount={data.quotes.length} summaryComplete={data.summary_completed} progress={data.progress} active="summary" />

    <section className="card summary-hero">
      <div><p className="eyebrow">AI SUMMARY</p><h2>{current?.narrative?.title ?? (data.current_result_id ? '生成采购决策摘要' : '等待决策比较完成')}</h2><p>{current?.narrative?.overview ?? '摘要严格绑定当前 Task Revision 和冻结结果；模型只负责中文表述，不重新计算或批准采购。'}</p></div>
      {data.current_result_id && <Link className="button button-secondary" to={`/tasks/${taskId}/results/${data.current_result_id}`}>查看当前决策结果</Link>}
      {mayGenerate && <button className="button button-submit" type="button" disabled={generate.isPending} onClick={() => generate.mutate()}>{generate.isPending ? '正在创建…' : '生成 AI Summary'}</button>}
      {current && <span className={`status-pill ${current.status === 'SUCCEEDED' ? 'status-ready' : current.status === 'FAILED' ? 'status-offline' : 'status-pending'}`}>{current.status} · Rev {current.task_revision}</span>}
      {current?.status === 'FAILED' && current.is_current && data.status !== 'ABANDONED' && <button className="button button-secondary" type="button" disabled={retry.isPending || current.calls_used >= current.max_calls} onClick={() => retry.mutate(current.summary_id)}>重试生成</button>}
      {(generate.isError || retry.isError) && <div className="form-error">{errorMessage(generate.error ?? retry.error)}</div>}
    </section>

    {current?.status === 'FAILED' && <section className="card error-panel"><strong>{current.error_code}</strong><p>{current.error_message}</p><small>模型调用 {current.calls_used}/{current.max_calls}</small></section>}
    {current?.narrative && <section className="summary-scope-grid">{current.narrative.sections.map((section) => <article key={section.heading}><strong>{section.heading}</strong><p>{section.text}</p><small>{section.reference_ids.join(' · ') || '仅使用报告级冻结事实'}</small></article>)}</section>}
    {current?.narrative && <section className="card run-notice"><strong>边界声明</strong><p>{current.narrative.disclaimer}</p></section>}

    {current && <details className="card"><summary>查看确定性事实与版本绑定</summary><pre>{JSON.stringify(current.facts, null, 2)}</pre><small>{current.input_sha256} · {current.model_id ?? '未配置模型'} · {current.prompt_version}</small></details>}
    {summaries.data.items.length > 1 && <section><div className="section-heading"><h2>历史 Summary</h2><span>{summaries.data.items.length}</span></div><div className="audit-list">{summaries.data.items.map((item) => <article className="card audit-record" key={item.summary_id}><strong>{item.status} · Rev {item.task_revision}</strong><span>{item.summary_id}</span><small>{new Date(item.created_at).toLocaleString('zh-CN')}</small></article>)}</div></section>}
  </div>
}
