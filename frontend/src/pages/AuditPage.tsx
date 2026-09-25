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
  if (answer.answer_type === 'CONFIRM_MISSING') return 'ConfirmedMissing'
  if (answer.answer_type === 'RETRY_POLICY_RETRIEVAL') return 'Retrieve again after correction'
  return 'Manual answer recorded'
}

function issueLabel(issue: IssueHistoryItem) {
  const labels: Record<string, string> = {
    CONFIRM_MISSING: 'Confirm missing field',
    SHIPPING_AMOUNT: 'Add shipping fee amount',
    POLICY_EVIDENCE_REVIEW: 'Policy evidence requires review',
  }
  return labels[issue.issue_type] ?? issue.issue_type
}

function retrievalSummary(result: ResultHistoryItem) {
  if (result.policy_retrievals.length === 0) return 'Not run / no record'
  const statuses = result.policy_retrievals.reduce<Record<string, number>>((counts, retrieval) => {
    counts[retrieval.status] = (counts[retrieval.status] ?? 0) + 1
    return counts
  }, {})
  return Object.entries(statuses).map(([status, count]) => `${count} ${policyStatusLabel(status)}`).join(' · ')
}

function issueStatus(value: string) {
  return value === 'RESOLVED' ? 'Resolved' : value === 'OPEN' ? 'Action required' : 'Recorded'
}

function changeLabel(value: string) {
  const labels: Record<string, string> = {
    CREATED: 'Procurement task created', TASK_CREATED: 'Procurement task created', REQUIREMENT_UPDATED: 'Edit Procurement Requirements',
    QUOTE_ADDED: 'Add quotation', QUOTE_UPLOADED: 'Submit quotation', QUOTE_DRAFT_SUBMITTED: 'Submit quotation',
    QUOTE_UPDATED: 'Update quotation', QUOTE_DEACTIVATED: 'Deactivate quotation', QUOTE_REACTIVATED: 'Reactivate quotation',
    FIELD_CORRECTED: 'Manually correct quotation', FIELDS_CORRECTED_BATCH: 'Batch-correct quotation',
    ISSUE_ANSWERED: 'Complete manual confirmation', RESULT_PUBLISHED: 'Generate comparison result', TASK_ABANDONED: 'Abandon task',
  }
  if (value.startsWith('FIELD_CORRECTED:')) return 'Manually correct quotation'
  if (value.startsWith('ISSUE_ANSWERED:')) return 'Complete manual confirmation'
  return labels[value] ?? 'Task updated'
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
  return error instanceof ApiClientError ? error.message : 'Failed to load audit data.'
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

  if (task.isPending) return <section className="card loading-panel">Loading task audit information…</section>
  if (task.isError) return <section className="card error-panel" role="alert">{errorMessage(task.error)}</section>

  const data = task.data
  const childError = quotes.error ?? issues.error ?? results.error

  return (
    <div className="page-stack audit-page">
      <TaskWorkspaceHeader
        taskId={data.task_id}
        scenarioId={data.scenario_id}
        title={data.task_name}
        subtitle={`${data.requirement.required_quantity} ${data.requirement.quantity_unit} · ${data.quotes.length} quotations`}
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
          <h2>Version History</h2>
          <p>A revision is created when a quotation is submitted, requirements are changed, or manual processing is completed. Drafts and analysis runs do not create revisions.</p>
        </div>
        <dl className="audit-summary-status" aria-label="Current version status">
          <div><dt>Current task</dt><dd>Revision {data.task_revision} · {taskStatusLabel(data.status)}</dd></div>
          <div><dt>Analysis result</dt><dd>{data.current_result_id ? 'Generated' : 'Not generated'}</dd></div>
          <div><dt>Policy</dt><dd>{data.policy_binding?.policy_set_version ?? 'Not bound'}</dd></div>
        </dl>
      </section>

      {childError && <section className="card error-panel" role="alert">Some audit data could not be loaded: {errorMessage(childError)}</section>}

      <section className="audit-section">
        <div className="section-heading">
          <div><h2>Quotation Document</h2></div>
          {(quotes.data?.items.length ?? 0) > 0 && <span>{quotes.data?.items.length} suppliers · {quoteRows.length} files</span>}
        </div>
        {quotes.isPending && <div className="card loading-panel">Loading quotation revisions…</div>}
        {quotes.data?.items.length === 0 && <div className="card audit-empty">No quotation revisions.</div>}
        {quoteRows.length > 0 && <><div className="audit-table-wrap"><table className="audit-table audit-quote-table"><thead><tr><th>Supplier</th><th>Status</th><th>Revision</th><th>File</th><th>Submitted</th><th aria-label="Actions">Actions</th></tr></thead><tbody>{quotePage.pageItems.map(({ quote, version }) => <tr className={version.is_current ? 'audit-current-row' : ''} key={`${quote.quote_id}-${version.quote_version}`}><td><strong>{quote.supplier_id}</strong></td><td><span className={`status-pill ${version.is_current && quote.active ? 'status-ready' : 'status-muted'}`}>{version.is_current ? (quote.active ? 'Current quotation' : 'Revision before deactivation') : 'Historical revision'}</span></td><td>Revision {version.quote_version}</td><td><span title={version.original_filename}>{version.original_filename}</span></td><td>{displayDate(version.created_at)}</td><td><button className="quote-preview-action" type="button" onClick={() => setPreview({ name: version.original_filename, mediaType: version.media_type, sizeBytes: version.size_bytes, remoteUrl: documentContentUrl(taskId, version.document_id), downloadUrl: documentContentUrl(taskId, version.document_id, 'attachment') })}>Preview / download</button></td></tr>)}</tbody></table></div><TablePagination page={quotePage.page} pageSize={quotePage.pageSize} pageCount={quotePage.pageCount} total={quoteRows.length} onPageChange={quotePage.setPage} /></>}
      </section>

      <section className="audit-section">
        <div className="section-heading">
          <div><h2>Action-item history</h2></div>
          {(issues.data?.length ?? 0) > 0 && <span>{issues.data?.length} records</span>}
        </div>
        {issues.isPending && <div className="card loading-panel">Loading action-item history…</div>}
        {issues.data?.length === 0 && <div className="card audit-empty">No manual processing records.</div>}
        <div className="audit-table-wrap">
          {(issues.data?.length ?? 0) > 0 && (
            <table className="audit-table">
              <thead><tr><th>Issue</th><th>Status</th><th>Task revision</th><th>Answer</th><th>Processing details</th></tr></thead>
              <tbody>
                {issuePage.pageItems.map((issue) => (
                  <tr key={issue.issue_id}>
                    <td><strong>{issueLabel(issue)}</strong><span>{issue.question}</span><small>{issue.field_name ? fieldLabel(issue.field_name) : 'Policy Evidence'}</small></td>
                    <td><span className={`status-pill ${issue.status === 'RESOLVED' ? 'status-ready' : 'status-pending'}`}>{issueStatus(issue.status)}</span></td>
                    <td>Created {issue.created_revision}<br />Resolved {issue.resolved_revision ?? '—'}</td>
                    <td>{displayAnswer(issue.answer)}</td>
                    <td>{issue.answered_at ? 'Manually processed' : 'Not processed'}<br /><small>{displayDate(issue.answered_at)}</small></td>
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
          <div><h2>Result history</h2></div>
          {(results.data?.length ?? 0) > 0 && <span>{results.data?.length} results</span>}
        </div>
        {results.isPending && <div className="card loading-panel">Loading result history…</div>}
        {results.data?.length === 0 && <div className="card audit-empty">No comparison results.</div>}
        {resultRows.length > 0 && <><div className="audit-table-wrap"><table className="audit-table audit-result-table"><thead><tr><th>Task revision</th><th>Result status</th><th>Quotations</th><th>Rule revision</th><th>Policy retrieval</th><th>Evaluated</th><th aria-label="Actions">Actions</th></tr></thead><tbody>{resultPage.pageItems.map((result) => <tr className={result.is_current ? 'audit-current-row' : ''} key={result.result_id}><td><strong>Revision {result.task_revision}</strong></td><td><span className={`status-pill ${result.is_current ? 'status-ready' : 'status-muted'}`}>{result.is_current ? 'Current result' : 'Historical result'}</span></td><td>{result.result.supplier_results.length}</td><td>{result.result.rule_version}</td><td>{retrievalSummary(result)}</td><td>{displayDate(result.result.evaluated_at)}</td><td><Link className="table-open-action" to={`/tasks/${taskId}/results/${result.result_id}`}>View</Link></td></tr>)}</tbody></table></div><TablePagination page={resultPage.page} pageSize={resultPage.pageSize} pageCount={resultPage.pageCount} total={resultRows.length} onPageChange={resultPage.setPage} /></>}
      </section>

      <section className="audit-section">
        <div className="section-heading"><div><h2>Task revisions</h2></div>{(audit.data?.revisions.length ?? 0) > 0 && <span>{audit.data?.revisions.length} revisions</span>}</div>
        {revisionRows.length === 0 && !audit.isPending && <div className="card audit-empty">No task change records.</div>}
        {revisionRows.length > 0 && <><div className="audit-table-wrap"><table className="audit-table audit-revision-table"><thead><tr><th>Revision</th><th>Change type</th><th>Related information</th><th>Actor</th><th>Time</th></tr></thead><tbody>{revisionPage.pageItems.map((revision) => <tr key={revision.revision}><td><strong>Revision {revision.revision}</strong></td><td>{changeLabel(revision.change_type)}</td><td>{changeDetail(revision.details) || '—'}</td><td>{revision.actor_id}</td><td>{displayDate(revision.created_at)}</td></tr>)}</tbody></table></div><TablePagination page={revisionPage.page} pageSize={revisionPage.pageSize} pageCount={revisionPage.pageCount} total={revisionRows.length} onPageChange={revisionPage.setPage} /></>}
        {accessRows.length > 0 && <details className="audit-access-details"><summary>File access records ({accessRows.length})</summary><div className="audit-table-wrap"><table className="audit-table audit-access-table"><thead><tr><th>Action</th><th>File</th><th>Actor</th><th>Time</th></tr></thead><tbody>{accessPage.pageItems.map((event) => <tr key={event.access_event_id}><td>{event.action === 'DOWNLOAD' ? 'Download' : 'Preview'}</td><td>{event.document_id}</td><td>{event.actor_id}</td><td>{displayDate(event.created_at)}</td></tr>)}</tbody></table></div><TablePagination page={accessPage.page} pageSize={accessPage.pageSize} pageCount={accessPage.pageCount} total={accessRows.length} onPageChange={accessPage.setPage} /></details>}
      </section>
      {preview && <FilePreviewDialog source={preview} onClose={() => setPreview(null)} />}
    </div>
  )
}
