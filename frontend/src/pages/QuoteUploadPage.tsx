import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
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
  if (!mediaType) throw new Error('Only PDF or CSV quotation documents are supported.')
  if (file.size === 0) throw new Error('Empty files cannot be uploaded.')
  if (file.size > MAX_FILE_BYTES) throw new Error('A file cannot exceed 5 MiB.')
  return file.type === mediaType ? file : new File([file], file.name, { type: mediaType, lastModified: file.lastModified })
}

function formatBytes(bytes: number) {
  return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KiB`
}

function errorMessage(error: unknown) {
  if (error instanceof ApiClientError) {
    if (error.code === 'duplicate_quote_uploaded') {
      return error.details.quote_active === false
        ? 'This file belongs to a deactivated quotation. Select Reactivate below.'
        : 'This quotation document already exists and does not need to be uploaded again.'
    }
    if (error.code === 'task_revision_conflict' || error.code === 'quote_draft_revision_conflict') return 'The revision has changed. Refresh the page before continuing.'
    if (error.code === 'quote_already_active') return 'This quotation is already active.'
    if (error.code === 'quote_already_inactive') return 'This quotation is already deactivated.'
    if (error.code === 'field_correction_value_invalid') return 'The value is outside the allowed range. Review the available options.'
    if (error.code === 'draft_correction_scope_invalid') return 'The fields requiring attention have changed. Refresh and review them again.'
    if (error.code === 'quote_draft_not_reviewable') return 'This draft can no longer be reviewed. Refresh to see its latest status.'
    if (error.code === 'field_correction_invalid') return 'A field value, format or relationship is invalid. Correct it using the field guidance.'
    return error.message
  }
  return 'Operation failed. Try again later.'
}

function uploadFailureNotice(error: unknown, localMessage?: string) {
  if (localMessage) {
    return { title: 'FileFail the check', message: localMessage }
  }
  if (error instanceof ApiClientError) {
    if (error.code === 'duplicate_quote_uploaded') {
      return { title: 'Upload not required', message: errorMessage(error) }
    }
    if (
      error.code.startsWith('csv_') ||
      ['unsupported_media_type', 'unsupported_pdf', 'unsupported_csv', 'empty_file', 'file_empty', 'file_too_large', 'pdf_size_limit_exceeded', 'pdf_page_limit_exceeded', 'blank_pdf', 'pdf_text_quality_insufficient', 'encrypted_pdf_unsupported', 'corrupted_pdf'].includes(error.code)
    ) {
      return {
        title: 'FileFail the check',
        message: error.code === 'csv_header_unregistered'
          ? 'The CSV header does not match a supported quotation template.'
          : 'Check the file format and upload it again.',
      }
    }
  }
  return { title: 'Upload failed', message: 'Upload the file again.' }
}

export function QuoteUploadPage() {
  const { taskId = '' } = useParams()
  const navigate = useNavigate()
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
  const nextStep = useMutation({ mutationFn: async () => {
    if (!task.data) throw new Error('The task has not loaded.')
    if (task.data.progress.compliance?.status === 'NOT_STARTED'
      && !['QUEUED', 'RUNNING', 'PROCESSING'].includes(task.data.status)) {
      await api.startRun(taskId, task.data.task_revision, createIdempotencyKey())
    }
  }, onSuccess: async () => {
    await queryClient.invalidateQueries({ queryKey: ['tasks', taskId] })
    navigate(`/tasks/${taskId}/compliance`)
  } })
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
      if (!task.data) throw new Error('The task has not loaded.')
      return api.createQuoteRevision(taskId, quoteId, task.data.task_revision, createIdempotencyKey())
    },
    onSuccess: refreshAll,
  })
  const deactivate = useMutation({
    mutationFn: ({ quoteId, idempotencyKey }: { quoteId: string; idempotencyKey: string }) => {
      if (!task.data) throw new Error('The task has not loaded.')
      return api.deactivateQuote(taskId, quoteId, task.data.task_revision, idempotencyKey)
    },
    onSuccess: refreshAll,
  })
  const reactivate = useMutation({
    mutationFn: ({ quoteId, idempotencyKey }: { quoteId: string; idempotencyKey: string }) => {
      if (!task.data) throw new Error('The task has not loaded.')
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
      setLocalError(error instanceof Error ? error.message : 'Invalid file.')
    }
  }
  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setLocalError('')
    if (!task.data || !selectedFile || !supplierId.trim()) return setLocalError('Enter a supplier ID and select a quotation document.')
    const submission = { expectedTaskRevision: task.data.task_revision, supplierId: supplierId.trim(), isSynthetic: import.meta.env.DEV, file: selectedFile, idempotencyKey: createIdempotencyKey() }
    upload.mutate(submission)
  }
  function confirmDeactivate(quoteId: string) {
    if (!window.confirm('After deactivation, this quotation will no longer be compared, but historical files will be retained. Continue?')) return
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
    if (submittedQuotes.length === 0) return <div className="card empty-upload-list">No submitted quotations yet.</div>
    return <>
      <div className="audit-table-wrap quote-management-table-wrap">
        <table className="audit-table quote-management-table">
          <thead><tr><th>Status</th><th>Supplier</th><th>Current file</th><th>Version</th><th aria-label="Actions">Actions</th></tr></thead>
          <tbody>
            {submittedQuotePage.pageItems.map((quote) => {
              const currentVersion = quote.versions.find((version) => version.is_current) ?? quote.versions[0]
              const historyVersions = quote.versions.filter((version) => version.document_id !== currentVersion.document_id)
              return <tr key={quote.quote_id}>
                <td><span className={`status-pill ${quote.active ? 'status-ready' : 'status-muted'}`}>{quote.active ? 'Current' : 'Inactive'}</span></td>
                <td><strong>{quote.supplier_id}</strong></td>
                <td>
                  <strong className="quote-table-filename" title={currentVersion.original_filename}>{currentVersion.original_filename}</strong>
                  {historyVersions.length > 0 && <details className="quote-table-history"><summary>Previous files ({historyVersions.length})</summary><div>{historyVersions.map((version) => <p key={version.document_id}><span>Revision {version.quote_version} · {version.original_filename}</span><button className="quote-preview-action" type="button" onClick={() => openVersion(version)}>View</button></p>)}</div></details>}
                </td>
                <td>Revision {currentVersion.quote_version}{quote.versions.length > 1 && <small>{quote.versions.length} revisions total</small>}</td>
                <td>
                  <div className="quote-table-actions">
                    <button className="quote-preview-action" type="button" onClick={() => openVersion(currentVersion)}>View source</button>
                    {!readOnly && (quote.active ? <><button type="button" disabled={Boolean(activeDraft) || revise.isPending} onClick={() => revise.mutate(quote.quote_id)}>Revise</button><button type="button" disabled={Boolean(activeDraft) || deactivate.isPending} onClick={() => confirmDeactivate(quote.quote_id)}>Deactivate</button></> : <button type="button" disabled={Boolean(activeDraft) || reactivate.isPending} onClick={() => reactivate.mutate({ quoteId: quote.quote_id, idempotencyKey: createIdempotencyKey() })}>Reactivate</button>)}
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
      <TaskWorkspaceHeader taskId={task.data.task_id} scenarioId={task.data.scenario_id} title={task.data.task_name} subtitle="Task abandoned; quotations and source documents remain read-only" status={task.data.status} revision={task.data.task_revision} resultId={task.data.current_result_id} quoteCount={task.data.quotes.length} summaryComplete={task.data.summary_completed} progress={task.data.progress} active="quotes" />
      <section className="card workspace-page-lead"><div className="workspace-page-lead-copy"><h2>Quotations and review</h2><p>View submitted quotation documents and review records.</p></div></section>
      <section className="card run-notice"><strong>This task has been abandoned</strong><p>Quotations cannot be uploaded, corrected or submitted. Historical files remain available for preview and download.</p></section>
      <section>{quoteHistory.isPending ? <div className="card empty-upload-list">Loading quotation history…</div> : submittedQuoteTable(true)}</section>
      {preview && <FilePreviewDialog source={preview} onClose={() => setPreview(null)} />}
      {task.data?.progress.quote_review_completed && !activeDraft && <section className="card"><h3>Quotation review complete</h3><button className="button button-submit" disabled={nextStep.isPending} onClick={() => nextStep.mutate()}>{nextStep.isPending ? 'Opening compliance review…' : 'Next: Compliance review'}</button>{nextStep.isError && <p role="alert">{errorMessage(nextStep.error)}</p>}</section>}
    </div>
  }

  return (
    <div className="page-stack quote-review-page">
      {task.data ? <TaskWorkspaceHeader taskId={task.data.task_id} scenarioId={task.data.scenario_id} title={task.data.task_name} subtitle={`${task.data.requirement.required_quantity} ${task.data.requirement.quantity_unit} · ${task.data.quotes.length} submitted quotations`} status={task.data.status} revision={task.data.task_revision} resultId={task.data.current_result_id} quoteCount={task.data.quotes.length} summaryComplete={task.data.summary_completed} progress={task.data.progress} reviewBlocked={Boolean(activeDraft || legacyFieldReview || legacyIssueReview || batchReview)} active="quotes" /> : <section className="card loading-panel">Loading task workspace…</section>}
      <section className="card workspace-page-lead quote-review-lead"><div className="workspace-page-lead-copy"><h2>Quotations and review</h2><p>Upload a quotation document and the system will extract its contents.</p></div></section>
      {!activeDraft && (
        <form className="card upload-form" onSubmit={handleSubmit}>
          <label className="field">
            <span>Supplier ID <small>Can be edited manually</small></span>
            <input required placeholder="Select a file to identify automatically, or enter manually" value={supplierId} onChange={(event) => { supplierIdManuallyEdited.current = true; setSupplierId(event.target.value) }} />
            {isIdentifyingSupplier && <small className="supplier-identification-note">Identifying the supplier ID from the quotation…</small>}
            {!isIdentifyingSupplier && supplierIdentification?.status === 'FOUND' && supplierIdentification.supplier_id === supplierId && <small className="supplier-identification-note is-success">Identified from the file. You may edit it before continuing.</small>}
            {!isIdentifyingSupplier && supplierIdentification?.status === 'FOUND' && supplierIdentification.supplier_id !== supplierId && <small className="supplier-identification-note">The file was identified as {supplierIdentification.supplier_id}; your manually entered value was retained. <button type="button" onClick={() => { supplierIdManuallyEdited.current = false; setSupplierId(supplierIdentification.supplier_id ?? '') }}>Use identified value</button></small>}
            {!isIdentifyingSupplier && supplierIdentification?.status === 'NOT_FOUND' && <small className="supplier-identification-note">No clear supplier ID was found in the file. Enter it manually.</small>}
            {!isIdentifyingSupplier && supplierIdentification?.status === 'AMBIGUOUS' && <small className="supplier-identification-note">Multiple supplier IDs were found. Review the document and enter the correct one manually.</small>}
            {!isIdentifyingSupplier && identificationError && <small className="supplier-identification-note">Automatic identification is temporarily unavailable. You may still enter the supplier ID manually.</small>}
          </label>
          <label className="field">
            <span>Quotation Document</span>
            <input ref={fileInput} required type="file" accept=".pdf,.csv,application/pdf,text/csv" onChange={(event) => handleFile(event.target.files?.[0] ?? null)} />
            <small>PDF and CSV supported, maximum 5 MiB.</small>
          </label>
          {selectedFile && (
            <div className="selected-file">
              <div><strong>{selectedFile.name}</strong><span>{formatBytes(selectedFile.size)}</span></div>
              <button type="button" onClick={() => setPreview({ name: selectedFile.name, mediaType: selectedFile.type, sizeBytes: selectedFile.size, file: selectedFile })}>Preview file</button>
            </div>
          )}
          {currentUploadNotice && <div className="form-error compact-error"><div><strong>{currentUploadNotice.title}</strong><p>{currentUploadNotice.message}</p></div></div>}
          {!currentUploadNotice && latestDraftFailureNotice && latestDraft && <div className="form-error compact-error"><div><strong>{latestDraftFailureNotice.title}</strong><p>{latestDraft.original_filename}: {latestDraftFailureNotice.message}</p></div></div>}
          <button className="button button-submit" type="submit" disabled={upload.isPending || (isIdentifyingSupplier && !supplierId.trim())}>{upload.isPending ? 'Uploading…' : 'Upload and start review'}</button>
        </form>
      )}
      {drafts.isError && <section className="card error-panel">Failed to load drafts: {errorMessage(drafts.error)}</section>}
      {activeDraft && fieldSchema.isPending && <section className="card loading-panel">Loading quotation field rules…</section>}
      {activeDraft && fieldSchema.isError && <section className="card error-panel">Failed to load field rules: {errorMessage(fieldSchema.error)}. To avoid using stale rules, this quotation cannot currently be confirmed or submitted.</section>}
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
      {batchReview && <section className="card run-notice"><strong>Multiple fields require attention.</strong><p>Use the field schema revision returned by the server and submit all changes together on the action-items page.</p><Link className="button button-submit" to={`/tasks/${taskId}/review`}>Go to action items</Link></section>}
      {legacyIssueReview && task.data && <IssuePanel task={task.data} onRefresh={() => void refreshAll()} />}
      <section>
        <div className="section-heading"><div><h2>Submitted quotations ({quoteHistory.data?.items.length ?? 0})</h2></div></div>
        {(revise.isError || deactivate.isError || reactivate.isError) && <div className="form-error compact-error"><div><strong>Operation failed</strong><p>{errorMessage(revise.error ?? deactivate.error ?? reactivate.error)}</p></div></div>}
        {quoteHistory.isPending ? <div className="card empty-upload-list">Loading quotation history…</div>
          : quoteHistory.isError ? <div className="card empty-upload-list">Unable to load quotation history.</div>
            : submittedQuoteTable()}
      </section>
      {preview && <FilePreviewDialog source={preview} onClose={() => setPreview(null)} />}
    </div>
  )
}
