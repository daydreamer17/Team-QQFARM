import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey, documentContentUrl, quoteDraftContentUrl } from '../api/client'
import type { QuoteSupplierIdentification } from '../api/types'
import { FilePreviewDialog, type PreviewFileSource } from '../components/FilePreviewDialog'
import { IssuePanel } from '../components/IssuePanel'
import { QuoteDraftReviewWorkspace } from '../components/QuoteDraftReviewWorkspace'
import { ReviewPanel } from '../components/ReviewPanel'
import { TablePagination } from '../components/TablePagination'
import { useTablePagination } from '../hooks/useTablePagination'
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
    if (error.code === 'duplicate_quote_uploaded') {
      return error.details.quote_active === false
        ? '该文件属于已停用报价，请在下方点击“重新启用”。'
        : '该报价文件已经存在，无需重复上传。'
    }
    if (error.code === 'task_revision_conflict' || error.code === 'quote_draft_revision_conflict') return '版本已变化，请刷新页面后再操作。'
    if (error.code === 'quote_already_active') return '该报价已经启用。'
    if (error.code === 'quote_already_inactive') return '该报价已经停用。'
    if (error.code === 'field_correction_value_invalid') return '填写值不在该字段允许范围内，请按页面选项重新核对。'
    if (error.code === 'draft_correction_scope_invalid') return '待处理字段已经变化，请刷新页面后重新核对。'
    if (error.code === 'quote_draft_not_reviewable') return '当前草稿不在可核对状态，请刷新页面查看最新状态。'
    if (error.code === 'field_correction_invalid') return '字段值、格式或关联关系无效，请按字段提示修正。'
    return error.message
  }
  return '操作失败，请稍后重试。'
}

function uploadFailureNotice(error: unknown, localMessage?: string) {
  if (localMessage) {
    return { title: '文件不符合要求', message: localMessage }
  }
  if (error instanceof ApiClientError) {
    if (error.code === 'duplicate_quote_uploaded') {
      return { title: '无需重复上传', message: errorMessage(error) }
    }
    if (
      error.code.startsWith('csv_') ||
      ['unsupported_media_type', 'unsupported_pdf', 'unsupported_csv', 'empty_file', 'file_empty', 'file_too_large', 'pdf_size_limit_exceeded', 'pdf_page_limit_exceeded', 'blank_pdf', 'pdf_text_quality_insufficient', 'encrypted_pdf_unsupported', 'corrupted_pdf'].includes(error.code)
    ) {
      return {
        title: '文件不符合要求',
        message: error.code === 'csv_header_unregistered'
          ? 'CSV 表头与支持的报价模板不一致。'
          : '请检查文件格式后重新上传。',
      }
    }
  }
  return { title: '上传失败', message: '请重新上传。' }
}

export function QuoteUploadPage() {
  const { taskId = '' } = useParams()
  const queryClient = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const supplierIdManuallyEdited = useRef(false)
  const identificationSequence = useRef(0)
  const [supplierId, setSupplierId] = useState('')
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [supplierIdentification, setSupplierIdentification] = useState<QuoteSupplierIdentification | null>(null)
  const [isIdentifyingSupplier, setIsIdentifyingSupplier] = useState(false)
  const [identificationError, setIdentificationError] = useState(false)
  const [preview, setPreview] = useState<PreviewFileSource | null>(null)
  const [localError, setLocalError] = useState('')
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
  const upload = useMutation({ mutationFn: (submission: UploadSubmission) => api.uploadQuoteDraft(taskId, submission, submission.idempotencyKey), onSuccess: async () => { setSupplierId(''); supplierIdManuallyEdited.current = false; setSupplierIdentification(null); setSelectedFile(null); if (fileInput.current) fileInput.current.value = ''; await refreshAll() } })
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
  const reactivate = useMutation({
    mutationFn: ({ quoteId, idempotencyKey }: { quoteId: string; idempotencyKey: string }) => {
      if (!task.data) throw new Error('任务尚未加载。')
      return api.reactivateQuote(taskId, quoteId, task.data.task_revision, idempotencyKey)
    },
    onSuccess: async () => {
      upload.reset()
      setSelectedFile(null)
      if (fileInput.current) fileInput.current.value = ''
      await refreshAll()
    },
  })

  function handleFile(file: File | null) {
    setLocalError(''); upload.reset()
    const sequence = ++identificationSequence.current
    if (!supplierIdManuallyEdited.current) setSupplierId('')
    setSupplierIdentification(null)
    setIdentificationError(false)
    setIsIdentifyingSupplier(false)
    if (!file) return setSelectedFile(null)
    try {
      const prepared = prepareFile(file)
      setSelectedFile(prepared)
      setIsIdentifyingSupplier(true)
      void api.identifyQuoteSupplier(taskId, prepared).then((result) => {
        if (sequence !== identificationSequence.current) return
        setSupplierIdentification(result)
        if (result.status === 'FOUND' && result.supplier_id) {
          setSupplierId((current) => {
            if (!supplierIdManuallyEdited.current || !current.trim()) {
              supplierIdManuallyEdited.current = false
              return result.supplier_id!
            }
            return current
          })
        }
      }).catch(() => {
        if (sequence === identificationSequence.current) setIdentificationError(true)
      }).finally(() => {
        if (sequence === identificationSequence.current) setIsIdentifyingSupplier(false)
      })
    } catch (error) {
      setSelectedFile(null)
      setLocalError(error instanceof Error ? error.message : '文件无效。')
    }
  }
  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setLocalError('')
    if (!task.data || !selectedFile || !supplierId.trim()) return setLocalError('请填写供应商编号并选择报价文件。')
    const submission = { expectedTaskRevision: task.data.task_revision, supplierId: supplierId.trim(), isSynthetic: import.meta.env.DEV, file: selectedFile, idempotencyKey: createIdempotencyKey() }
    upload.mutate(submission)
  }
  function confirmDeactivate(quoteId: string) {
    if (!window.confirm('停用后，该报价不再参与比较，但历史文件会保留。确定停用？')) return
    deactivate.mutate({ quoteId, idempotencyKey: createIdempotencyKey() })
  }
  const legacyFieldReview = task.data && task.data.status === 'FAILED' && task.data.current_job?.error_code === 'review_required'
  const batchReview = task.data?.current_issue?.issue_type === 'BATCH_FIELD_REVIEW'
  const legacyIssueReview = task.data?.current_issue && !['POLICY_EVIDENCE_REVIEW', 'BATCH_FIELD_REVIEW'].includes(task.data.current_issue.issue_type)
  const currentUploadNotice = localError || upload.isError
    ? uploadFailureNotice(upload.error, localError || undefined)
    : null
  const latestDraft = drafts.data?.items[0]
  const latestDraftFailureNotice = latestDraft?.status === 'FAILED'
    ? uploadFailureNotice(new ApiClientError(400, latestDraft.error_code ?? 'quote_draft_failed', latestDraft.error_message ?? ''))
    : null
  const submittedQuotes = quoteHistory.data?.items ?? []
  const submittedQuotePage = useTablePagination(submittedQuotes)

  function openVersion(version: (typeof submittedQuotes)[number]['versions'][number]) {
    setPreview({
      name: version.original_filename,
      mediaType: version.media_type,
      sizeBytes: version.size_bytes,
      remoteUrl: documentContentUrl(taskId, version.document_id),
      downloadUrl: documentContentUrl(taskId, version.document_id, 'attachment'),
    })
  }

  function submittedQuoteTable(readOnly = false) {
    if (submittedQuotes.length === 0) return <div className="card empty-upload-list">尚无已提交报价。</div>
    return <>
      <div className="audit-table-wrap quote-management-table-wrap">
        <table className="audit-table quote-management-table">
          <thead><tr><th>状态</th><th>供应商</th><th>当前文件</th><th>版本</th><th aria-label="操作">操作</th></tr></thead>
          <tbody>
            {submittedQuotePage.pageItems.map((quote) => {
              const currentVersion = quote.versions.find((version) => version.is_current) ?? quote.versions[0]
              const historyVersions = quote.versions.filter((version) => version.document_id !== currentVersion.document_id)
              return <tr key={quote.quote_id}>
                <td><span className={`status-pill ${quote.active ? 'status-ready' : 'status-muted'}`}>{quote.active ? '当前有效' : '已停用'}</span></td>
                <td><strong>{quote.supplier_id}</strong></td>
                <td>
                  <strong className="quote-table-filename" title={currentVersion.original_filename}>{currentVersion.original_filename}</strong>
                  {historyVersions.length > 0 && <details className="quote-table-history"><summary>历史文件（{historyVersions.length}）</summary><div>{historyVersions.map((version) => <p key={version.document_id}><span>第 {version.quote_version} 版 · {version.original_filename}</span><button className="quote-preview-action" type="button" onClick={() => openVersion(version)}>查看</button></p>)}</div></details>}
                </td>
                <td>第 {currentVersion.quote_version} 版{quote.versions.length > 1 && <small>共 {quote.versions.length} 版</small>}</td>
                <td>
                  <div className="quote-table-actions">
                    <button className="quote-preview-action" type="button" onClick={() => openVersion(currentVersion)}>查看原件</button>
                    {!readOnly && (quote.active ? <><button type="button" disabled={Boolean(activeDraft) || revise.isPending} onClick={() => revise.mutate(quote.quote_id)}>修改</button><button type="button" disabled={Boolean(activeDraft) || deactivate.isPending} onClick={() => confirmDeactivate(quote.quote_id)}>停用</button></> : <button type="button" disabled={Boolean(activeDraft) || reactivate.isPending} onClick={() => reactivate.mutate({ quoteId: quote.quote_id, idempotencyKey: createIdempotencyKey() })}>重新启用</button>)}
                  </div>
                </td>
              </tr>
            })}
          </tbody>
        </table>
      </div>
      <TablePagination page={submittedQuotePage.page} pageSize={submittedQuotePage.pageSize} pageCount={submittedQuotePage.pageCount} total={submittedQuotes.length} onPageChange={submittedQuotePage.setPage} />
    </>
  }

  if (task.data?.status === 'ABANDONED') {
    return <div className="page-stack quote-review-page">
      <TaskWorkspaceHeader taskId={task.data.task_id} scenarioId={task.data.scenario_id} title={task.data.task_name} subtitle="任务已废弃；报价与原件保持只读" status={task.data.status} revision={task.data.task_revision} resultId={task.data.current_result_id} quoteCount={task.data.quotes.length} summaryComplete={task.data.summary_completed} progress={task.data.progress} active="quotes" />
      <section className="card run-notice"><strong>该任务已软废弃</strong><p>不能上传、修正或提交报价；历史文件仍可预览和下载。</p></section>
      <section>{quoteHistory.isPending ? <div className="card empty-upload-list">正在加载报价历史…</div> : submittedQuoteTable(true)}</section>
      {preview && <FilePreviewDialog source={preview} onClose={() => setPreview(null)} />}
    </div>
  }

  return (
    <div className="page-stack quote-review-page">
      {task.data ? <TaskWorkspaceHeader taskId={task.data.task_id} scenarioId={task.data.scenario_id} title={task.data.task_name} subtitle={`${task.data.requirement.required_quantity} ${task.data.requirement.quantity_unit} · ${task.data.quotes.length} 份正式报价`} status={task.data.status} revision={task.data.task_revision} resultId={task.data.current_result_id} quoteCount={task.data.quotes.length} summaryComplete={task.data.summary_completed} progress={task.data.progress} reviewBlocked={Boolean(activeDraft || legacyFieldReview || legacyIssueReview || batchReview)} active="quotes" /> : <section className="card loading-panel">正在读取任务工作台…</section>}
      <section className="quote-review-lead"><div><h2>报价与审核</h2><p>上传报价文件，系统将自动提取内容。</p></div></section>
      {!activeDraft && (
        <form className="card upload-form" onSubmit={handleSubmit}>
          <label className="field">
            <span>供应商编号 <small>可手工修改</small></span>
            <input required placeholder="选择文件后自动识别，或手工填写" value={supplierId} onChange={(event) => { supplierIdManuallyEdited.current = true; setSupplierId(event.target.value) }} />
            {isIdentifyingSupplier && <small className="supplier-identification-note">正在从报价文件识别供应商编号…</small>}
            {!isIdentifyingSupplier && supplierIdentification?.status === 'FOUND' && supplierIdentification.supplier_id === supplierId && <small className="supplier-identification-note is-success">已从文件识别并填入，可继续修改。</small>}
            {!isIdentifyingSupplier && supplierIdentification?.status === 'FOUND' && supplierIdentification.supplier_id !== supplierId && <small className="supplier-identification-note">文件识别到 {supplierIdentification.supplier_id}，已保留手工填写值。 <button type="button" onClick={() => { supplierIdManuallyEdited.current = false; setSupplierId(supplierIdentification.supplier_id ?? '') }}>使用识别结果</button></small>}
            {!isIdentifyingSupplier && supplierIdentification?.status === 'NOT_FOUND' && <small className="supplier-identification-note">文件中没有明确的供应商编号，请手工填写。</small>}
            {!isIdentifyingSupplier && supplierIdentification?.status === 'AMBIGUOUS' && <small className="supplier-identification-note">文件中存在多个供应商编号，请核对后手工填写。</small>}
            {!isIdentifyingSupplier && identificationError && <small className="supplier-identification-note">自动识别暂不可用，仍可手工填写。</small>}
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
          {currentUploadNotice && <div className="form-error compact-error"><div><strong>{currentUploadNotice.title}</strong><p>{currentUploadNotice.message}</p></div></div>}
          {!currentUploadNotice && latestDraftFailureNotice && latestDraft && <div className="form-error compact-error"><div><strong>{latestDraftFailureNotice.title}</strong><p>{latestDraft.original_filename}：{latestDraftFailureNotice.message}</p></div></div>}
          <button className="button button-submit" type="submit" disabled={upload.isPending || (isIdentifyingSupplier && !supplierId.trim())}>{upload.isPending ? '正在上传…' : '上传并开始审核'}</button>
        </form>
      )}
      {drafts.isError && <section className="card error-panel">草稿读取失败：{errorMessage(drafts.error)}</section>}
      {activeDraft && fieldSchema.isPending && <section className="card loading-panel">正在加载报价字段规则…</section>}
      {activeDraft && fieldSchema.isError && <section className="card error-panel">字段规则加载失败：{errorMessage(fieldSchema.error)}。为避免使用过期规则，当前不能确认或提交报价。</section>}
      {activeDraft && task.data && fieldSchema.data && <QuoteDraftReviewWorkspace
        key={`${activeDraft.quote_draft_id}:${activeDraft.draft_revision}:${activeDraft.updated_at}:${fieldSchema.data.schema_version}`}
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
        {(revise.isError || deactivate.isError || reactivate.isError) && <div className="form-error compact-error"><div><strong>操作失败</strong><p>{errorMessage(revise.error ?? deactivate.error ?? reactivate.error)}</p></div></div>}
        {quoteHistory.isPending ? <div className="card empty-upload-list">正在加载报价历史…</div>
          : quoteHistory.isError ? <div className="card empty-upload-list">报价历史加载失败。</div>
            : submittedQuoteTable()}
      </section>
      {preview && <FilePreviewDialog source={preview} onClose={() => setPreview(null)} />}
    </div>
  )
}
