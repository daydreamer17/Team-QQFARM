import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiClientError, documentContentUrl } from '../api/client'
import type { IssueHistoryItem, ResultHistoryItem } from '../api/types'
import { FilePreviewDialog, type PreviewFileSource } from '../components/FilePreviewDialog'
import { TablePagination } from '../components/TablePagination'
import { useTablePagination } from '../hooks/useTablePagination'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'
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
    CREATED: '创建采购任务', TASK_CREATED: '创建采购任务', REQUIREMENT_UPDATED: '修改采购需求',
    QUOTE_ADDED: '新增报价', QUOTE_UPLOADED: '正式提交报价', QUOTE_DRAFT_SUBMITTED: '正式提交报价',
    QUOTE_UPDATED: '更新报价', QUOTE_DEACTIVATED: '停用报价', QUOTE_REACTIVATED: '重新启用报价',
    FIELD_CORRECTED: '人工校正报价', FIELDS_CORRECTED_BATCH: '批量校正报价',
    ISSUE_ANSWERED: '完成人工确认', RESULT_PUBLISHED: '生成比较结果', TASK_ABANDONED: '废弃任务',
  }
  if (value.startsWith('FIELD_CORRECTED:')) return '人工校正报价'
  if (value.startsWith('ISSUE_ANSWERED:')) return '完成人工确认'
  return labels[value] ?? '更新任务内容'
}

function changeDetail(details: Record<string, unknown>) {
  const supplier = typeof details.supplier_id === 'string' ? details.supplier_id : ''
  const filename = typeof details.original_filename === 'string' ? details.original_filename : ''
  if (supplier && filename) return `${supplier} · ${filename}`
  if (filename) return filename
  if (supplier) return supplier
  return ''
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
  const quoteRows = (quotes.data?.items ?? []).flatMap((quote) => quote.versions.map((version) => ({ quote, version })))
  const quotePage = useTablePagination(quoteRows)
  const issueRows = issues.data ?? []
  const issuePage = useTablePagination(issueRows)
  const resultRows = results.data ?? []
  const resultPage = useTablePagination(resultRows)
  const revisionRows = audit.data?.revisions ?? []
  const revisionPage = useTablePagination(revisionRows)
  const accessRows = audit.data?.document_accesses ?? []
  const accessPage = useTablePagination(accessRows)

  if (task.isPending) return <section className="card loading-panel">正在读取任务审计信息…</section>
  if (task.isError) return <section className="card error-panel" role="alert">{errorMessage(task.error)}</section>

  const data = task.data
  const childError = quotes.error ?? issues.error ?? results.error

  return (
    <div className="page-stack audit-page">
      <TaskWorkspaceHeader
        taskId={data.task_id}
        scenarioId={data.scenario_id}
        title={data.task_name}
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

      <section className="card audit-summary-bar workspace-page-lead">
        <div className="audit-summary-copy workspace-page-lead-copy">
          <h2>版本记录</h2>
          <p>提交报价、修改需求或完成人工处理时生成版本；草稿和运行分析不增加版本。</p>
        </div>
        <dl className="audit-summary-status" aria-label="当前版本状态">
          <div><dt>当前任务</dt><dd>第 {data.task_revision} 版 · {taskStatusLabel(data.status)}</dd></div>
          <div><dt>分析结果</dt><dd>{data.current_result_id ? '已生成' : '尚未生成'}</dd></div>
          <div><dt>制度</dt><dd>{data.policy_binding?.policy_set_version ?? '未绑定'}</dd></div>
        </dl>
      </section>

      {childError && <section className="card error-panel" role="alert">部分审计数据读取失败：{errorMessage(childError)}</section>}

      <section className="audit-section">
        <div className="section-heading">
          <div><h2>报价文件</h2></div>
          {(quotes.data?.items.length ?? 0) > 0 && <span>{quotes.data?.items.length} 个供应商 · {quoteRows.length} 个文件</span>}
        </div>
        {quotes.isPending && <div className="card loading-panel">正在读取报价版本…</div>}
        {quotes.data?.items.length === 0 && <div className="card audit-empty">暂无报价版本。</div>}
        {quoteRows.length > 0 && <><div className="audit-table-wrap"><table className="audit-table audit-quote-table"><thead><tr><th>供应商</th><th>状态</th><th>版本</th><th>文件</th><th>提交时间</th><th aria-label="操作">操作</th></tr></thead><tbody>{quotePage.pageItems.map(({ quote, version }) => <tr className={version.is_current ? 'audit-current-row' : ''} key={`${quote.quote_id}-${version.quote_version}`}><td><strong>{quote.supplier_id}</strong></td><td><span className={`status-pill ${version.is_current && quote.active ? 'status-ready' : 'status-muted'}`}>{version.is_current ? (quote.active ? '当前报价' : '停用前版本') : '历史版本'}</span></td><td>第 {version.quote_version} 版</td><td><span title={version.original_filename}>{version.original_filename}</span></td><td>{displayDate(version.created_at)}</td><td><button className="quote-preview-action" type="button" onClick={() => setPreview({ name: version.original_filename, mediaType: version.media_type, sizeBytes: version.size_bytes, remoteUrl: documentContentUrl(taskId, version.document_id), downloadUrl: documentContentUrl(taskId, version.document_id, 'attachment') })}>预览 / 下载</button></td></tr>)}</tbody></table></div><TablePagination page={quotePage.page} pageSize={quotePage.pageSize} pageCount={quotePage.pageCount} total={quoteRows.length} onPageChange={quotePage.setPage} /></>}
      </section>

      <section className="audit-section">
        <div className="section-heading">
          <div><h2>问题处理历史</h2></div>
          {(issues.data?.length ?? 0) > 0 && <span>{issues.data?.length} 条记录</span>}
        </div>
        {issues.isPending && <div className="card loading-panel">正在读取问题历史…</div>}
        {issues.data?.length === 0 && <div className="card audit-empty">无人工处理记录。</div>}
        <div className="audit-table-wrap">
          {(issues.data?.length ?? 0) > 0 && (
            <table className="audit-table">
              <thead><tr><th>问题</th><th>状态</th><th>任务版本</th><th>回答</th><th>处理信息</th></tr></thead>
              <tbody>
                {issuePage.pageItems.map((issue) => (
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
        <TablePagination page={issuePage.page} pageSize={issuePage.pageSize} pageCount={issuePage.pageCount} total={issueRows.length} onPageChange={issuePage.setPage} />
      </section>

      <section className="audit-section">
        <div className="section-heading">
          <div><h2>结果历史</h2></div>
          {(results.data?.length ?? 0) > 0 && <span>{results.data?.length} 个结果</span>}
        </div>
        {results.isPending && <div className="card loading-panel">正在读取结果历史…</div>}
        {results.data?.length === 0 && <div className="card audit-empty">暂无比较结果。</div>}
        {resultRows.length > 0 && <><div className="audit-table-wrap"><table className="audit-table audit-result-table"><thead><tr><th>任务版本</th><th>结果状态</th><th>报价数</th><th>规则版本</th><th>制度检索</th><th>评估时间</th><th aria-label="操作">操作</th></tr></thead><tbody>{resultPage.pageItems.map((result) => <tr className={result.is_current ? 'audit-current-row' : ''} key={result.result_id}><td><strong>第 {result.task_revision} 版</strong></td><td><span className={`status-pill ${result.is_current ? 'status-ready' : 'status-muted'}`}>{result.is_current ? '当前结果' : '历史结果'}</span></td><td>{result.result.supplier_results.length}</td><td>{result.result.rule_version}</td><td>{retrievalSummary(result)}</td><td>{displayDate(result.result.evaluated_at)}</td><td><Link className="table-open-action" to={`/tasks/${taskId}/results/${result.result_id}`}>查看</Link></td></tr>)}</tbody></table></div><TablePagination page={resultPage.page} pageSize={resultPage.pageSize} pageCount={resultPage.pageCount} total={resultRows.length} onPageChange={resultPage.setPage} /></>}
      </section>

      <section className="audit-section">
        <div className="section-heading"><div><h2>任务变更</h2></div>{(audit.data?.revisions.length ?? 0) > 0 && <span>{audit.data?.revisions.length} 个版本</span>}</div>
        {revisionRows.length === 0 && !audit.isPending && <div className="card audit-empty">暂无任务变更记录。</div>}
        {revisionRows.length > 0 && <><div className="audit-table-wrap"><table className="audit-table audit-revision-table"><thead><tr><th>版本</th><th>变更类型</th><th>相关信息</th><th>操作人</th><th>时间</th></tr></thead><tbody>{revisionPage.pageItems.map((revision) => <tr key={revision.revision}><td><strong>第 {revision.revision} 版</strong></td><td>{changeLabel(revision.change_type)}</td><td>{changeDetail(revision.details) || '—'}</td><td>{revision.actor_id}</td><td>{displayDate(revision.created_at)}</td></tr>)}</tbody></table></div><TablePagination page={revisionPage.page} pageSize={revisionPage.pageSize} pageCount={revisionPage.pageCount} total={revisionRows.length} onPageChange={revisionPage.setPage} /></>}
        {accessRows.length > 0 && <details className="audit-access-details"><summary>文件访问记录（{accessRows.length}）</summary><div className="audit-table-wrap"><table className="audit-table audit-access-table"><thead><tr><th>操作</th><th>文件</th><th>操作人</th><th>时间</th></tr></thead><tbody>{accessPage.pageItems.map((event) => <tr key={event.access_event_id}><td>{event.action === 'DOWNLOAD' ? '下载' : '预览'}</td><td>{event.document_id}</td><td>{event.actor_id}</td><td>{displayDate(event.created_at)}</td></tr>)}</tbody></table></div><TablePagination page={accessPage.page} pageSize={accessPage.pageSize} pageCount={accessPage.pageCount} total={accessRows.length} onPageChange={accessPage.setPage} /></details>}
      </section>
      {preview && <FilePreviewDialog source={preview} onClose={() => setPreview(null)} />}
    </div>
  )
}
