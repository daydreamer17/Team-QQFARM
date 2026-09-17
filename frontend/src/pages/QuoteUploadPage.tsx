import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import type { QuoteDraftCorrectionInput, QuoteDraftResponse } from '../api/types'
import { FilePreviewDialog, type PreviewFileSource } from '../components/FilePreviewDialog'
import { IssuePanel } from '../components/IssuePanel'
import { ReviewPanel } from '../components/ReviewPanel'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'

const MAX_FILE_BYTES = 5 * 1024 * 1024
const ACTIVE_STATUSES = new Set(['UPLOADED', 'PROCESSING', 'REVIEW_REQUIRED', 'READY_TO_SUBMIT'])
const numberFields = new Set(['moq_quantity', 'units_per_pack', 'order_multiple_units', 'lead_time_days'])
const fieldLabels: Record<string, string> = {
  manufacturer: '制造商', manufacturer_part_number: '制造商料号', package: '封装', revision: '版本',
  condition: '物料状态', unit_price: '单价', currency: '币种', moq_quantity: '最低订购量',
  shipping_fee_status: '运费状态', shipping_fee_amount: '运费金额', other_fees_status: '其他费用状态',
  lead_time_days: '交期天数', delivery_date: '交付日期', payment_terms: '付款条件', valid_until: '报价有效期',
}

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

function displayValue(value: unknown) {
  if (value === null || value === undefined || value === '') return '未提供'
  if (typeof value === 'boolean') return value ? '是' : '否'
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

function errorMessage(error: unknown) {
  if (error instanceof ApiClientError) {
    if (error.code === 'task_revision_conflict' || error.code === 'quote_draft_revision_conflict') return '版本已变化，请刷新页面后再操作。'
    return error.message
  }
  return '操作失败，请稍后重试。'
}

function normalizedValue(fieldName: string, value: string, original: unknown) {
  if (typeof original === 'boolean') return value.toLowerCase() === 'true'
  if (typeof original === 'number' || numberFields.has(fieldName)) {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : value
  }
  return value
}

function DraftReview({ draft, taskRevision, onChanged }: { draft: QuoteDraftResponse; taskRevision: number; onChanged: () => Promise<void> }) {
  const blockers = useMemo(() => draft.review_findings.filter((finding) => finding.severity === 'BLOCKING' && !finding.resolved), [draft.review_findings])
  const blockingNames = useMemo(() => [...new Set(blockers.map((finding) => finding.field_name))], [blockers])
  const [values, setValues] = useState<Record<string, string>>(() => {
    const next: Record<string, string> = {}
    for (const name of blockingNames) {
      const field = draft.fields.find((item) => item.field_name === name)
      next[name] = field?.normalized_value === null || field?.normalized_value === undefined ? '' : String(field.normalized_value)
    }
    return next
  })
  const [lastCorrection, setLastCorrection] = useState<{ items: QuoteDraftCorrectionInput[]; key: string } | null>(null)
  const [submitKey, setSubmitKey] = useState<string | null>(null)
  const [discardKey, setDiscardKey] = useState<string | null>(null)

  const correction = useMutation({
    mutationFn: (submission: { items: QuoteDraftCorrectionInput[]; key: string }) => api.correctQuoteDraft(draft.task_id, draft.quote_draft_id, draft.draft_revision, submission.items, submission.key),
    onSuccess: onChanged,
  })
  const submit = useMutation({
    mutationFn: (key: string) => api.submitQuoteDraft(draft.task_id, draft.quote_draft_id, taskRevision, draft.draft_revision, key),
    onSuccess: onChanged,
  })
  const discard = useMutation({
    mutationFn: (key: string) => api.discardQuoteDraft(draft.task_id, draft.quote_draft_id, draft.draft_revision, key),
    onSuccess: onChanged,
  })

  function submitCorrections(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const items = blockingNames.flatMap((fieldName) => {
      const field = draft.fields.find((item) => item.field_name === fieldName)
      const value = values[fieldName]?.trim()
      if (!field || !value) return []
      return [{ fieldName, rawValue: value, normalizedValue: normalizedValue(fieldName, value, field.normalized_value), unit: field.unit, reason: '用户在正式提交前核对并修正阻塞字段。' }]
    })
    const submission = lastCorrection ?? { items, key: createIdempotencyKey() }
    setLastCorrection(submission)
    correction.mutate(submission)
  }

  const mutationError = correction.error ?? submit.error ?? discard.error
  return (
    <section className="card draft-review-card">
      <header className="draft-review-header">
        <div><p className="eyebrow">QUOTE DRAFT REVIEW</p><h2>{draft.supplier_id} · {draft.original_filename}</h2><p>草稿不会进入正式报价历史，也不会推进 Task Revision。</p></div>
        <span className={`status-pill draft-status-${draft.status.toLowerCase()}`}>{draft.status}</span>
      </header>
      {draft.status === 'PROCESSING' && <div className="draft-processing"><i className="activity-spinner" /><div><strong>正在解析并审核报价</strong><span>Worker 会自动处理，页面正在刷新状态。</span></div></div>}
      {draft.status === 'FAILED' && <div className="form-error compact-error"><div><strong>草稿处理失败：{draft.error_code}</strong><p>{draft.error_message}</p></div></div>}
      {draft.status === 'STALE' && <div className="form-error compact-error"><div><strong>草稿已过期</strong><p>任务输入在审核期间发生变化，请废弃后重新上传。</p></div></div>}
      {draft.fields.length > 0 && (
        <form onSubmit={submitCorrections}>
          <div className="draft-field-grid">
            {draft.fields.map((field) => {
              const findings = blockers.filter((finding) => finding.field_name === field.field_name)
              const blocked = findings.length > 0
              return (
                <article className={`draft-field${blocked ? ' draft-field-blocked' : ''}`} key={field.field_name}>
                  <div className="draft-field-title"><strong>{fieldLabels[field.field_name] ?? field.field_name}</strong><code>{field.field_name}</code></div>
                  <div className="draft-current-value"><span>当前值</span><strong>{displayValue(field.normalized_value)}{field.unit ? ` ${field.unit}` : ''}</strong><small>原文：{displayValue(field.raw_value)}</small></div>
                  {blocked ? <><div className="draft-finding-reasons">{findings.map((finding) => <p key={finding.finding_id}>{finding.message}</p>)}</div><label className="field"><span>核对后的值</span><input required value={values[field.field_name] ?? ''} onChange={(event) => { setValues((current) => ({ ...current, [field.field_name]: event.target.value })); setLastCorrection(null); correction.reset() }} /></label></> : <span className="draft-verified">已通过自动审核 · 只读</span>}
                </article>
              )
            })}
          </div>
          {blockingNames.length > 0 && <button className="button button-submit" type="submit" disabled={correction.isPending || blockingNames.some((name) => !values[name]?.trim())}>{correction.isPending ? '正在重新审核…' : `提交 ${blockingNames.length} 个阻塞字段`}</button>}
        </form>
      )}
      {mutationError && <div className="form-error compact-error"><strong>操作未完成</strong><p>{errorMessage(mutationError)}</p></div>}
      <div className="draft-actions"><button className="button button-secondary" type="button" onClick={() => { const key = discardKey ?? createIdempotencyKey(); setDiscardKey(key); discard.mutate(key) }} disabled={discard.isPending || draft.status === 'SUBMITTED'}>废弃草稿</button><button className="button button-submit" type="button" onClick={() => { const key = submitKey ?? createIdempotencyKey(); setSubmitKey(key); submit.mutate(key) }} disabled={draft.status !== 'READY_TO_SUBMIT' || submit.isPending}>{submit.isPending ? '正在正式提交…' : '正式提交报价'}</button></div>
    </section>
  )
}

export function QuoteUploadPage() {
  const { taskId = '' } = useParams()
  const queryClient = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const [supplierId, setSupplierId] = useState('')
  const [isSynthetic, setIsSynthetic] = useState(true)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<PreviewFileSource | null>(null)
  const [localError, setLocalError] = useState('')
  const [lastSubmission, setLastSubmission] = useState<UploadSubmission | null>(null)
  const task = useQuery({ queryKey: ['tasks', taskId], queryFn: () => api.getTask(taskId), enabled: Boolean(taskId) })
  const quoteHistory = useQuery({ queryKey: ['tasks', taskId, 'quotes'], queryFn: () => api.listQuotes(taskId), enabled: Boolean(taskId) })
  const drafts = useQuery({ queryKey: ['tasks', taskId, 'quote-drafts'], queryFn: () => api.listQuoteDrafts(taskId), enabled: Boolean(taskId), refetchInterval: (query) => query.state.data?.items.some((item) => item.status === 'PROCESSING') ? 1_500 : false })
  const activeDraft = drafts.data?.items.find((item) => ACTIVE_STATUSES.has(item.status))
  const refreshAll = async () => { await Promise.all([queryClient.invalidateQueries({ queryKey: ['tasks', taskId] }), queryClient.invalidateQueries({ queryKey: ['tasks', taskId, 'quotes'] }), queryClient.invalidateQueries({ queryKey: ['tasks', taskId, 'quote-drafts'] })]) }
  const upload = useMutation({ mutationFn: (submission: UploadSubmission) => api.uploadQuoteDraft(taskId, submission, submission.idempotencyKey), onSuccess: async () => { setSupplierId(''); setSelectedFile(null); setLastSubmission(null); if (fileInput.current) fileInput.current.value = ''; await refreshAll() } })

  function handleFile(file: File | null) {
    setLocalError(''); upload.reset()
    if (!file) return setSelectedFile(null)
    try { setSelectedFile(prepareFile(file)) } catch (error) { setSelectedFile(null); setLocalError(error instanceof Error ? error.message : '文件无效。') }
  }
  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setLocalError('')
    if (!task.data || !selectedFile || !supplierId.trim()) return setLocalError('请填写供应商编号并选择报价文件。')
    const submission = { expectedTaskRevision: task.data.task_revision, supplierId: supplierId.trim(), isSynthetic, file: selectedFile, idempotencyKey: createIdempotencyKey() }
    setLastSubmission(submission); upload.mutate(submission)
  }
  const legacyFieldReview = task.data && task.data.status === 'FAILED' && task.data.current_job?.error_code === 'review_required'
  const legacyIssueReview = task.data?.current_issue && task.data.current_issue.issue_type !== 'POLICY_EVIDENCE_REVIEW'

  return (
    <div className="page-stack quote-review-page">
      {task.data ? <TaskWorkspaceHeader taskId={task.data.task_id} scenarioId={task.data.scenario_id} title={task.data.requirement.manufacturer_part_number} subtitle={`${task.data.requirement.required_quantity} ${task.data.requirement.quantity_unit} · ${task.data.quotes.length} 份正式报价`} status={task.data.status} revision={task.data.task_revision} resultId={task.data.current_result_id} quoteCount={task.data.quotes.length} reviewBlocked={Boolean(activeDraft || legacyFieldReview || legacyIssueReview)} active="quotes" /> : <section className="card loading-panel">正在读取任务工作台…</section>}
      <section className="quote-review-lead"><div><p className="eyebrow">QUOTE & REVIEW</p><h2>报价与审核</h2><p>先解析草稿并修正阻塞字段；审核通过后再正式写入任务。</p></div><span>{activeDraft ? '1 个活动草稿' : '可上传新草稿'}</span></section>
      {!activeDraft && <section className="upload-layout"><form className="card upload-form" onSubmit={handleSubmit}><div><p className="eyebrow">NEW QUOTE DRAFT</p><h2>上传报价草稿</h2><p className="helper-text">草稿解析不会推进 Task Revision。</p></div><label className="field"><span>供应商编号</span><input required placeholder="例如 SUP-001" value={supplierId} onChange={(event) => setSupplierId(event.target.value)} /></label><label className="field"><span>报价文件</span><input ref={fileInput} required type="file" accept=".pdf,.csv,application/pdf,text/csv" onChange={(event) => handleFile(event.target.files?.[0] ?? null)} /><small>仅限 PDF/CSV，非空且不超过 5 MiB。</small></label>{selectedFile && <div className="selected-file"><div><strong>{selectedFile.name}</strong><span>{formatBytes(selectedFile.size)} · {selectedFile.type}</span></div><button type="button" onClick={() => setPreview({ name: selectedFile.name, mediaType: selectedFile.type, sizeBytes: selectedFile.size, file: selectedFile })}>预览文件</button></div>}<label className="field checkbox-field"><input type="checkbox" checked={isSynthetic} onChange={(event) => setIsSynthetic(event.target.checked)} /><span>这是合成测试数据</span></label>{(localError || upload.isError) && <div className="form-error compact-error"><div><strong>草稿上传未完成</strong><p>{localError || errorMessage(upload.error)}</p></div>{lastSubmission && <button className="button button-secondary" type="button" onClick={() => upload.mutate(lastSubmission)}>重试相同请求</button>}</div>}<button className="button button-submit" type="submit" disabled={upload.isPending}>{upload.isPending ? '正在上传…' : '上传并开始审核'}</button></form><aside className="card upload-guidance"><p className="eyebrow">SUBMISSION GATE</p><h2>正式提交门禁</h2><ol><li>Worker 解析全部字段与证据。</li><li>已验证字段保持只读。</li><li>仅阻塞字段需要人工修正。</li><li>全部通过后才能正式提交。</li></ol></aside></section>}
      {drafts.isError && <section className="card error-panel">草稿读取失败：{errorMessage(drafts.error)}</section>}
      {activeDraft && task.data && <DraftReview key={`${activeDraft.quote_draft_id}:${activeDraft.draft_revision}`} draft={activeDraft} taskRevision={task.data.task_revision} onChanged={refreshAll} />}
      {legacyFieldReview && task.data && <ReviewPanel task={task.data} onRefresh={() => void refreshAll()} />}
      {legacyIssueReview && task.data && <IssuePanel task={task.data} onRefresh={() => void refreshAll()} />}
      <section><div className="section-heading"><div><p className="eyebrow">FORMAL QUOTES</p><h2>已正式提交的报价</h2></div><span>{quoteHistory.data?.items.length ?? 0} 个报价</span></div>{quoteHistory.isPending ? <div className="card empty-upload-list">正在加载报价历史…</div> : quoteHistory.isError ? <div className="card empty-upload-list">报价历史加载失败。</div> : quoteHistory.data.items.length === 0 ? <div className="card empty-upload-list">尚无正式报价；活动草稿不会显示在这里。</div> : <div className="uploaded-list">{quoteHistory.data.items.map((quote) => <article className="card uploaded-quote" key={quote.quote_id}><div><span className={`status-pill ${quote.active ? 'status-ready' : 'status-muted'}`}>{quote.active ? '当前有效' : '已停用'}</span><h3>{quote.supplier_id}</h3><p>{quote.versions.length} 个文件版本 · 当前 V{quote.current_version}</p></div><dl><div><dt>当前版本</dt><dd>V{quote.current_version}</dd></div><div><dt>Quote ID</dt><dd>{quote.quote_id}</dd></div></dl><div className="quote-version-list">{quote.versions.map((version) => <div className="quote-version-row" key={version.document_id}><div><strong>V{version.quote_version} · {version.original_filename}</strong><span>{formatBytes(version.size_bytes)} · {version.media_type}</span></div><div><span>{version.is_current ? '当前使用' : '历史版本'}</span><code>{version.document_sha256.slice(0, 12)}…</code><button className="quote-preview-action" type="button" onClick={() => setPreview({ name: version.original_filename, mediaType: version.media_type, sizeBytes: version.size_bytes, description: '后端尚未提供带权限控制的文件内容流。' })}>预览</button></div></div>)}</div></article>)}</div>}</section>
      {preview && <FilePreviewDialog source={preview} onClose={() => setPreview(null)} />}
    </div>
  )
}
