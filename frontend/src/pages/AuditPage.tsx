import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiClientError, documentContentUrl } from '../api/client'
import type { IssueHistoryItem, ResultHistoryItem } from '../api/types'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'
import { FilePreviewDialog, type PreviewFileSource } from '../components/FilePreviewDialog'
import { fieldLabel, policyStatusLabel, taskStatusLabel } from '../lib/presentation'

function displayDate(value: string | null | undefined) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)
}

function displayAnswer(answer: Record<string, unknown> | null) {
  if (!answer) return '—'
  if (answer.answer_type === 'SHIPPING_AMOUNT') {
    return `${String(answer.currency ?? '')} ${String(answer.amount ?? '')}`.trim()
  }
  if (answer.answer_type === 'CONFIRM_MISSING') return '已确认缺失'
  if (answer.answer_type === 'RETRY_POLICY_RETRIEVAL') return '修复后重新检索'
  return '已记录人工回答'
}

function issueLabel(issue: IssueHistoryItem) {
  const labels: Record<string, string> = {
    CONFIRM_MISSING: '确认缺失字段',
    SHIPPING_AMOUNT: '补充运费金额',
    POLICY_EVIDENCE_REVIEW: '制度证据待复核',
  }
  return labels[issue.issue_type] ?? issue.issue_type
}

function retrievalSummary(result: ResultHistoryItem) {
  if (result.policy_retrievals.length === 0) return '未执行 / 无记录'
  const statuses = result.policy_retrievals.reduce<Record<string, number>>((counts, retrieval) => {
    counts[retrieval.status] = (counts[retrieval.status] ?? 0) + 1
    return counts
  }, {})
  return Object.entries(statuses).map(([status, count]) => `${policyStatusLabel(status)} ${count} 项`).join(' · ')
}

function issueStatus(value: string) {
  return value === 'RESOLVED' ? '已解决' : value === 'OPEN' ? '待处理' : '已记录'
}

function changeLabel(value: string) {
  const labels: Record<string, string> = {
    CREATED: '创建采购任务', REQUIREMENT_UPDATED: '修改采购需求', QUOTE_ADDED: '新增报价',
    QUOTE_UPDATED: '更新报价', FIELD_CORRECTED: '人工校正报价', ISSUE_ANSWERED: '完成人工确认',
    RESULT_PUBLISHED: '生成比较结果',
  }
  return labels[value] ?? '更新任务内容'
}

function errorMessage(error: unknown) {
  return error instanceof ApiClientError ? error.message : '审计数据读取失败。'
}

export function AuditPage() {
  const { taskId = '' } = useParams()
  const [preview, setPreview] = useState<PreviewFileSource | null>(null)
  const task = useQuery({
    queryKey: ['tasks', taskId],
    queryFn: () => api.getTask(taskId),
    enabled: Boolean(taskId),
  })
  const quotes = useQuery({
    queryKey: ['tasks', taskId, 'quotes'],
    queryFn: () => api.listQuotes(taskId),
    enabled: Boolean(taskId),
  })
  const issues = useQuery({
    queryKey: ['tasks', taskId, 'issues'],
    queryFn: () => api.listIssues(taskId),
    enabled: Boolean(taskId),
  })
  const results = useQuery({
    queryKey: ['tasks', taskId, 'results'],
    queryFn: () => api.listResults(taskId),
    enabled: Boolean(taskId),
  })
  const audit = useQuery({
    queryKey: ['tasks', taskId, 'audit-events'],
    queryFn: () => api.getTaskAudit(taskId),
    enabled: Boolean(taskId),
  })

  if (task.isPending) return <section className="card loading-panel">正在读取任务审计信息…</section>
  if (task.isError) return <section className="card error-panel" role="alert">{errorMessage(task.error)}</section>

  const data = task.data
  const childError = quotes.error ?? issues.error ?? results.error

  return (
    <div className="page-stack audit-page">
      <TaskWorkspaceHeader
        taskId={data.task_id}
        scenarioId={data.scenario_id}
        title={data.requirement.manufacturer_part_number}
        subtitle={`${data.requirement.required_quantity} ${data.requirement.quantity_unit} · ${data.quotes.length} 份报价`}
        status={data.status}
        revision={data.task_revision}
        resultId={data.current_result_id}
        quoteCount={data.quotes.length}
        summaryComplete={data.summary_completed}
        progress={data.progress}
        reviewBlocked={Boolean(data.current_issue)}
        policyReviewBlocked={data.current_issue?.issue_type === 'POLICY_EVIDENCE_REVIEW'}
        active="audit"
      />

      <section className="review-workspace-lead">
        <div>
          <p className="eyebrow">版本记录</p>
          <h2>任务版本与处理记录</h2>
          <p>查看报价文件、人工确认和分析结果的历史变化。</p>
        </div>
        <span className="status-pill status-ready">当前第 {data.task_revision} 版</span>
      </section>

      <section className="audit-overview">
        <article><span>当前任务版本</span><strong>第 {data.task_revision} 版</strong><small>{taskStatusLabel(data.status)}</small></article>
        <article><span>当前分析结果</span><strong>{data.current_result_id ? '已生成' : '尚未生成'}</strong><small>{data.current_result_id ? '可在“决策结果”查看' : '等待完成分析'}</small></article>
        <article><span>制度绑定</span><strong>{data.policy_binding?.policy_set_version ?? '未绑定'}</strong><small>{data.policy_binding ? '已保存制度版本' : '未执行制度检索'}</small></article>
      </section>

      {childError && <section className="card error-panel" role="alert">部分审计数据读取失败：{errorMessage(childError)}</section>}

      <section className="audit-section">
        <div className="section-heading">
          <div><p className="eyebrow">报价版本</p><h2>报价与文件版本</h2></div>
          <span>{quotes.data?.items.length ?? 0} 个报价</span>
        </div>
        {quotes.isPending && <div className="card loading-panel">正在读取报价版本…</div>}
        {quotes.data?.items.length === 0 && <div className="card audit-empty">暂无报价版本。</div>}
        <div className="audit-list">
          {quotes.data?.items.map((quote) => (
            <article className="card audit-record" key={quote.quote_id}>
              <header>
                <div><strong>{quote.supplier_id}</strong><span>{quote.versions.length} 个文件版本</span></div>
                <span className={`status-pill ${quote.active ? 'status-ready' : 'status-muted'}`}>{quote.active ? `当前 v${quote.current_version}` : '非当前报价'}</span>
              </header>
              <div className="audit-version-list">
                {quote.versions.map((version) => (
                  <div className={version.is_current ? 'audit-version audit-version-current' : 'audit-version'} key={`${quote.quote_id}-${version.quote_version}`}>
                    <strong>第 {version.quote_version} 版报价</strong>
                    <span>{version.original_filename}</span>
                    <span>{displayDate(version.created_at)}</span>
                    <small>{version.media_type}</small>
                    {version.is_current && <b>当前版本</b>}
                    <button className="button button-secondary" type="button" onClick={() => setPreview({ name: version.original_filename, mediaType: version.media_type, sizeBytes: version.size_bytes, remoteUrl: documentContentUrl(taskId, version.document_id), downloadUrl: documentContentUrl(taskId, version.document_id, 'attachment') })}>预览 / 下载</button>
                  </div>
                ))}
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="audit-section">
        <div className="section-heading">
          <div><p className="eyebrow">人工确认</p><h2>问题处理历史</h2></div>
          <span>{issues.data?.length ?? 0} 条记录</span>
        </div>
        {issues.isPending && <div className="card loading-panel">正在读取问题历史…</div>}
        {issues.data?.length === 0 && <div className="card audit-empty">暂无人工问题。</div>}
        <div className="audit-table-wrap">
          {(issues.data?.length ?? 0) > 0 && (
            <table className="audit-table">
              <thead><tr><th>问题</th><th>状态</th><th>任务版本</th><th>回答</th><th>处理信息</th></tr></thead>
              <tbody>
                {issues.data?.map((issue) => (
                  <tr key={issue.issue_id}>
                    <td><strong>{issueLabel(issue)}</strong><span>{issue.question}</span><small>{issue.field_name ? fieldLabel(issue.field_name) : '制度依据'}</small></td>
                    <td><span className={`status-pill ${issue.status === 'RESOLVED' ? 'status-ready' : 'status-pending'}`}>{issueStatus(issue.status)}</span></td>
                    <td>创建 {issue.created_revision}<br />解决 {issue.resolved_revision ?? '—'}</td>
                    <td>{displayAnswer(issue.answer)}</td>
                    <td>{issue.answered_at ? '已人工处理' : '尚未处理'}<br /><small>{displayDate(issue.answered_at)}</small></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>

      <section className="audit-section">
        <div className="section-heading">
          <div><p className="eyebrow">分析结果</p><h2>结果历史</h2></div>
          <span>{results.data?.length ?? 0} 个结果</span>
        </div>
        {results.isPending && <div className="card loading-panel">正在读取结果历史…</div>}
        {results.data?.length === 0 && <div className="card audit-empty">暂无比较结果。</div>}
        <div className="audit-result-list">
          {results.data?.map((result) => (
            <article className={`card audit-result ${result.is_current ? 'audit-result-current' : ''}`} key={result.result_id}>
              <div>
                <span className="audit-result-revision">采购任务第 {result.task_revision} 版</span>
                <strong>{result.is_current ? '当前分析结果' : '历史分析结果'}</strong>
                <small>{result.result.supplier_results.length} 份报价参与比较</small>
              </div>
              <dl>
                <div><dt>规则版本</dt><dd>{result.result.rule_version}</dd></div>
                <div><dt>评估时间</dt><dd>{displayDate(result.result.evaluated_at)}</dd></div>
                <div><dt>制度检索</dt><dd>{retrievalSummary(result)}</dd></div>
                <div><dt>结果状态</dt><dd>{result.is_current ? '当前结果' : '历史结果'}</dd></div>
              </dl>
              <Link className="button button-secondary" to={`/tasks/${taskId}/results/${result.result_id}`}>
                {result.is_current ? '查看当前结果' : '查看历史结果'}
              </Link>
            </article>
          ))}
        </div>
      </section>

      <section className="audit-section">
        <div className="section-heading"><div><p className="eyebrow">任务变更</p><h2>任务变更与文件访问</h2></div><span>{audit.data?.revisions.length ?? 0} 个版本</span></div>
        <div className="audit-list">{audit.data?.revisions.map((revision) => <article className="card audit-record" key={revision.revision}><header><strong>第 {revision.revision} 版 · {changeLabel(revision.change_type)}</strong><span>{displayDate(revision.created_at)}</span></header><p>系统已保存该版本的变更内容，可用于追溯。</p></article>)}</div>
        {(audit.data?.document_accesses.length ?? 0) > 0 && <details><summary>文件访问记录（{audit.data?.document_accesses.length}）</summary><div className="audit-list">{audit.data?.document_accesses.map((event) => <article className="card audit-record" key={event.access_event_id}><strong>访问报价文件</strong><span>{displayDate(event.created_at)}</span></article>)}</div></details>}
      </section>
      {preview && <FilePreviewDialog source={preview} onClose={() => setPreview(null)} />}
    </div>
  )
}
