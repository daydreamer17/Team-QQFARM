import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey, documentContentUrl, quoteDraftContentUrl } from '../api/client'
import { FilePreviewDialog, type PreviewFileSource } from '../components/FilePreviewDialog'
import { IssuePanel } from '../components/IssuePanel'
import { QuoteDraftReviewWorkspace } from '../components/QuoteDraftReviewWorkspace'
import { ReviewPanel } from '../components/ReviewPanel'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'

const MAX_FILE_BYTES = 5 * 1024 * 1024
const ACTIVE_STATUSES = new Set(['UPLOADED', 'PROCESSING', 'REVIEW_REQUIRED', 'READY_TO_SUBMIT'])

interface UploadSubmission {
  expectedTaskRevision: number
  supplierId: string
  isSynthetic: boolean
  file: File
  idempotencyKey: string
}

function prepareFile(file: File) {
  const name = file.name.toLowerCase()
  const mediaType = name.endsWith('.pdf') ? 'application/pdf' : name.endsWith('.csv') ? 'text/csv' : null
  if (!mediaType) throw new Error('仅支持 PDF 或 CSV 报价文件。')
  if (file.size === 0) throw new Error('不能上传空文件。')
  if (file.size > MAX_FILE_BYTES) throw new Error('单个文件不能超过 5 MiB。')
  return file.type === mediaType ? file : new File([file], file.name, { type: mediaType, lastModified: file.lastModified })
}

function formatBytes(bytes: number) {
  return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KiB`
}

function errorMessage(error: unknown) {
  if (error instanceof ApiClientError) {
    if (error.code === 'task_revision_conflict' || error.code === 'quote_draft_revision_conflict') return '版本已变化，请刷新页面后再操作。'
    if (error.code === 'field_correction_value_invalid') return '填写值不在该字段允许范围内，请按页面选项重新核对。'
    if (error.code === 'draft_correction_scope_invalid') return '待处理字段已经变化，请刷新页面后重新核对。'
    if (error.code === 'quote_draft_not_reviewable') return '当前草稿不在可核对状态，请刷新页面查看最新状态。'
    if (error.code === 'field_correction_invalid') return '字段值、格式或关联关系无效，请按字段提示修正。'
    return error.message
  }
  return '操作失败，请稍后重试。'
}

export function QuoteUploadPage() {
  const { taskId = '' } = useParams()
  const queryClient = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const [supplierId, setSupplierId] = useState('')
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<PreviewFileSource | null>(null)
  const [localError, setLocalError] = useState('')
  const [lastSubmission, setLastSubmission] = useState<UploadSubmission | null>(null)
  const task = useQuery({ queryKey: ['tasks', taskId], queryFn: () => api.getTask(taskId), enabled: Boolean(taskId) })
  const quoteHistory = useQuery({ queryKey: ['tasks', taskId, 'quotes'], queryFn: () => api.listQuotes(taskId), enabled: Boolean(taskId) })
  const drafts = useQuery({ queryKey: ['tasks', taskId, 'quote-drafts'], queryFn: () => api.listQuoteDrafts(taskId), enabled: Boolean(taskId), refetchInterval: (query) => query.state.data?.items.some((item) => item.status === 'PROCESSING') ? 1_500 : false })
  const activeDraftSummary = drafts.data?.items.find((item) => ACTIVE_STATUSES.has(item.status))
  const fieldSchema = useQuery({
    queryKey: ['quote-field-schema'],
    queryFn: () => api.getQuoteFieldSchema(),
    enabled: Boolean(activeDraftSummary),
    staleTime: 5 * 60_000,
  })
  const activeDraftDetail = useQuery({
    queryKey: ['tasks', taskId, 'quote-drafts', activeDraftSummary?.quote_draft_id],
    queryFn: () => api.getQuoteDraft(taskId, activeDraftSummary!.quote_draft_id),
    enabled: Boolean(taskId && activeDraftSummary),
    refetchInterval: (query) => query.state.data?.status === 'PROCESSING' ? 1_500 : false,
  })
  const activeDraft = activeDraftDetail.data ?? activeDraftSummary
  const refreshAll = async () => { await Promise.all([queryClient.invalidateQueries({ queryKey: ['tasks', taskId] }), queryClient.invalidateQueries({ queryKey: ['tasks', taskId, 'quotes'] }), queryClient.invalidateQueries({ queryKey: ['tasks', taskId, 'quote-drafts'] })]) }
  const upload = useMutation({ mutationFn: (submission: UploadSubmission) => api.uploadQuoteDraft(taskId, submission, submission.idempotencyKey), onSuccess: async () => { setSupplierId(''); setSelectedFile(null); setLastSubmission(null); if (fileInput.current) fileInput.current.value = ''; await refreshAll() } })
  const revise = useMutation({
    mutationFn: (quoteId: string) => {
      if (!task.data) throw new Error('任务尚未加载。')
      return api.createQuoteRevision(taskId, quoteId, task.data.task_revision, createIdempotencyKey())
    },
    onSuccess: refreshAll,
  })
  const deactivate = useMutation({
    mutationFn: ({ quoteId, idempotencyKey }: { quoteId: string; idempotencyKey: string }) => {
      if (!task.data) throw new Error('任务尚未加载。')
      return api.deactivateQuote(taskId, quoteId, task.data.task_revision, idempotencyKey)
    },
    onSuccess: refreshAll,
  })

  function handleFile(file: File | null) {
    setLocalError(''); upload.reset()
    if (!file) return setSelectedFile(null)
    try { setSelectedFile(prepareFile(file)) } catch (error) { setSelectedFile(null); setLocalError(error instanceof Error ? error.message : '文件无效。') }
  }
  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setLocalError('')
    if (!task.data || !selectedFile || !supplierId.trim()) return setLocalError('请填写供应商编号并选择报价文件。')
    const submission = { expectedTaskRevision: task.data.task_revision, supplierId: supplierId.trim(), isSynthetic: import.meta.env.DEV, file: selectedFile, idempotencyKey: createIdempotencyKey() }
    setLastSubmission(submission); upload.mutate(submission)
  }
  function confirmDeactivate(quoteId: string) {
    if (!window.confirm('停用后，该报价不再参与比较，但历史文件会保留。确定停用？')) return
    deactivate.mutate({ quoteId, idempotencyKey: createIdempotencyKey() })
  }
  const legacyFieldReview = task.data && task.data.status === 'FAILED' && task.data.current_job?.error_code === 'review_required'
  const batchReview = task.data?.current_issue?.issue_type === 'BATCH_FIELD_REVIEW'
  const legacyIssueReview = task.data?.current_issue && !['POLICY_EVIDENCE_REVIEW', 'BATCH_FIELD_REVIEW'].includes(task.data.current_issue.issue_type)

  if (task.data?.status === 'ABANDONED') {
    return <div className="page-stack quote-review-page">
      <TaskWorkspaceHeader taskId={task.data.task_id} scenarioId={task.data.scenario_id} title={task.data.requirement.manufacturer_part_number} subtitle="任务已废弃；报价与原件保持只读" status={task.data.status} revision={task.data.task_revision} resultId={task.data.current_result_id} quoteCount={task.data.quotes.length} summaryComplete={task.data.summary_completed} progress={task.data.progress} active="quotes" />
      <section className="card run-notice"><strong>该任务已软废弃</strong><p>不能上传、修正或提交报价；历史文件仍可预览和下载。</p></section>
      <div className="uploaded-list">{quoteHistory.data?.items.flatMap((quote) => quote.versions.map((version) => <article className="card uploaded-quote" key={version.document_id}><div><strong>{quote.supplier_id} · 第 {version.quote_version} 版</strong><p>{version.original_filename}</p></div><button className="button button-secondary" type="button" onClick={() => setPreview({ name: version.original_filename, mediaType: version.media_type, sizeBytes: version.size_bytes, remoteUrl: documentContentUrl(taskId, version.document_id), downloadUrl: documentContentUrl(taskId, version.document_id, 'attachment') })}>查看原件</button></article>))}</div>
      {preview && <FilePreviewDialog source={preview} onClose={() => setPreview(null)} />}
    </div>
  }

  return (
    <div className="page-stack quote-review-page">
      {task.data ? <TaskWorkspaceHeader taskId={task.data.task_id} scenarioId={task.data.scenario_id} title={task.data.requirement.manufacturer_part_number} subtitle={`${task.data.requirement.required_quantity} ${task.data.requirement.quantity_unit} · ${task.data.quotes.length} 份正式报价`} status={task.data.status} revision={task.data.task_revision} resultId={task.data.current_result_id} quoteCount={task.data.quotes.length} summaryComplete={task.data.summary_completed} progress={task.data.progress} reviewBlocked={Boolean(activeDraft || legacyFieldReview || legacyIssueReview || batchReview)} active="quotes" /> : <section className="card loading-panel">正在读取任务工作台…</section>}
      <section className="quote-review-lead"><div><h2>报价与审核</h2><p>上传报价文件，系统将自动提取内容。</p></div></section>
      {!activeDraft && (
        <form className="card upload-form" onSubmit={handleSubmit}>
          <label className="field">
            <span>供应商编号</span>
            <input required placeholder="例如 SUP-001" value={supplierId} onChange={(event) => setSupplierId(event.target.value)} />
          </label>
          <label className="field">
            <span>报价文件</span>
            <input ref={fileInput} required type="file" accept=".pdf,.csv,application/pdf,text/csv" onChange={(event) => handleFile(event.target.files?.[0] ?? null)} />
            <small>支持 PDF/CSV，最大 5 MiB。</small>
          </label>
          {selectedFile && (
            <div className="selected-file">
              <div><strong>{selectedFile.name}</strong><span>{formatBytes(selectedFile.size)}</span></div>
              <button type="button" onClick={() => setPreview({ name: selectedFile.name, mediaType: selectedFile.type, sizeBytes: selectedFile.size, file: selectedFile })}>预览文件</button>
            </div>
          )}
          {(localError || upload.isError) && <div className="form-error compact-error"><div><strong>上传失败</strong><p>{localError || errorMessage(upload.error)}</p></div>{lastSubmission && <button className="button button-secondary" type="button" onClick={() => upload.mutate(lastSubmission)}>重试</button>}</div>}
          <button className="button button-submit" type="submit" disabled={upload.isPending}>{upload.isPending ? '正在上传…' : '上传并开始审核'}</button>
        </form>
      )}
      {drafts.isError && <section className="card error-panel">草稿读取失败：{errorMessage(drafts.error)}</section>}
      {activeDraft && fieldSchema.isPending && <section className="card loading-panel">正在加载报价字段规则…</section>}
      {activeDraft && fieldSchema.isError && <section className="card error-panel">字段规则加载失败：{errorMessage(fieldSchema.error)}。为避免使用过期规则，当前不能确认或提交报价。</section>}
      {activeDraft && task.data && fieldSchema.data && <QuoteDraftReviewWorkspace
        key={`${activeDraft.quote_draft_id}:${activeDraft.draft_revision}:${fieldSchema.data.schema_version}`}
        draft={activeDraft}
        schema={fieldSchema.data}
        taskRevision={task.data.task_revision}
        onChanged={refreshAll}
        onPreview={() => setPreview({
          name: activeDraft.original_filename,
          mediaType: activeDraft.media_type,
          sizeBytes: activeDraft.size_bytes,
          remoteUrl: quoteDraftContentUrl(taskId, activeDraft.quote_draft_id),
          downloadUrl: quoteDraftContentUrl(taskId, activeDraft.quote_draft_id, 'attachment'),
        })}
      />}
      {legacyFieldReview && task.data && <ReviewPanel task={task.data} onRefresh={() => void refreshAll()} />}
      {batchReview && <section className="card run-notice"><strong>本轮需要集中审核多个字段。</strong><p>请在集中审核页按后端返回的字段版本统一提交。</p><Link className="button button-submit" to={`/tasks/${taskId}/review`}>进入集中审核</Link></section>}
      {legacyIssueReview && task.data && <IssuePanel task={task.data} onRefresh={() => void refreshAll()} />}
      <section>
        <div className="section-heading"><div><h2>已提交报价（{quoteHistory.data?.items.length ?? 0}）</h2></div></div>
        {(revise.isError || deactivate.isError) && <div className="form-error compact-error"><div><strong>操作失败</strong><p>{errorMessage(revise.error ?? deactivate.error)}</p></div></div>}
        {quoteHistory.isPending ? <div className="card empty-upload-list">正在加载报价历史…</div>
          : quoteHistory.isError ? <div className="card empty-upload-list">报价历史加载失败。</div>
            : quoteHistory.data.items.length === 0 ? <div className="card empty-upload-list">尚无已提交报价。</div>
              : <div className="uploaded-list">{quoteHistory.data.items.map((quote) => <article className="card uploaded-quote submitted-quote" key={quote.quote_id}>
                <div className="submitted-quote-heading"><div><span className={`status-pill ${quote.active ? 'status-ready' : 'status-muted'}`}>{quote.active ? '当前有效' : '已停用'}</span><h3>{quote.supplier_id}</h3></div>{quote.active && <div className="submitted-quote-actions"><button type="button" disabled={Boolean(activeDraft) || revise.isPending} onClick={() => revise.mutate(quote.quote_id)}>修改报价</button><button type="button" disabled={Boolean(activeDraft) || deactivate.isPending} onClick={() => confirmDeactivate(quote.quote_id)}>停用报价</button></div>}</div>
                <div className="quote-version-list">{quote.versions.map((version) => <div className="quote-version-row" key={version.document_id}><div><strong>{version.original_filename}</strong>{quote.versions.length > 1 && <span>第 {version.quote_version} 版</span>}</div><div>{quote.versions.length > 1 && <span>{version.is_current ? '当前版本' : '历史版本'}</span>}<button className="quote-preview-action" type="button" onClick={() => setPreview({ name: version.original_filename, mediaType: version.media_type, sizeBytes: version.size_bytes, remoteUrl: documentContentUrl(taskId, version.document_id), downloadUrl: documentContentUrl(taskId, version.document_id, 'attachment') })}>查看原件</button></div></div>)}</div>
              </article>)}</div>}
      </section>
      {preview && <FilePreviewDialog source={preview} onClose={() => setPreview(null)} />}
    </div>
  )
}
