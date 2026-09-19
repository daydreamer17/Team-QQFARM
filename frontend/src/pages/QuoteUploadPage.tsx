import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey, documentContentUrl } from '../api/client'
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
  other_fees_amount: '其他费用金额', tax_mode: '税模式', price_basis_unit: '计价单位',
  packaging_type: '包装类型', units_per_pack: '每包装数量', order_multiple_units: '订购倍数', moq_unit: 'MOQ 单位',
  lead_time_days: '交期天数', day_basis: '交期日历口径', delivery_semantics: '交付语义', start_event: '交期起算事件',
  delivery_date: '交付日期', payment_terms: '付款条件', valid_until: '报价有效期',
}

type CorrectionOption = { value: string; label: string }

const correctionOptions: Record<string, CorrectionOption[]> = {
  shipping_fee_status: [
    { value: 'FREE', label: '免费（FREE）' },
    { value: 'INCLUDED', label: '已包含在报价中（INCLUDED）' },
    { value: 'NOT_APPLICABLE', label: '明确无此费用（NOT_APPLICABLE）' },
    { value: 'KNOWN_AMOUNT', label: '另有明确金额（KNOWN_AMOUNT）' },
  ],
  other_fees_status: [
    { value: 'FREE', label: '免费（FREE）' },
    { value: 'INCLUDED', label: '已包含在报价中（INCLUDED）' },
    { value: 'NOT_APPLICABLE', label: '明确无其他费用（NOT_APPLICABLE）' },
    { value: 'KNOWN_AMOUNT', label: '另有明确金额（KNOWN_AMOUNT）' },
  ],
  tax_mode: [
    { value: 'NOT_APPLICABLE', label: '明确不适用税费（NOT_APPLICABLE）' },
    { value: 'INCLUDED', label: '报价已含税（INCLUDED）' },
    { value: 'EXCLUDED', label: '报价未含税（EXCLUDED）' },
  ],
  day_basis: [
    { value: 'CALENDAR_DAYS', label: '自然日（CALENDAR_DAYS）' },
    { value: 'BUSINESS_DAYS', label: '工作日（BUSINESS_DAYS）' },
  ],
  delivery_semantics: [
    { value: 'ARRIVAL', label: '承诺到货（ARRIVAL）' },
    { value: 'SHIPMENT', label: '仅承诺发货（SHIPMENT）' },
  ],
  start_event: [
    { value: 'ORDER_DATE', label: '从下单日计算（ORDER_DATE）' },
    { value: 'PAYMENT_RECEIPT', label: '从收到付款计算（PAYMENT_RECEIPT）' },
  ],
  condition: [
    { value: 'NEW', label: '全新（NEW）' },
    { value: 'REFURBISHED', label: '翻新（REFURBISHED）' },
    { value: 'USED', label: '二手（USED）' },
  ],
  price_basis_unit: [{ value: 'piece', label: '按颗（piece）' }],
  moq_unit: [
    { value: 'piece', label: '颗（piece）' },
    { value: 'tray', label: '盘（tray）' },
  ],
}

const findingActionMessages: Record<string, string> = {
  CRITICAL_FIELD_MISSING: '报价中没有明确提供该信息，请依据原报价或向供应商确认后选择。',
  CRITICAL_FIELD_CONFLICT: '报价内容存在冲突，请核对原文件后填写最终确认值。',
  FEE_STATUS_UNKNOWN: '请确认费用是免费、已包含、明确无此费用，还是另有金额。',
  NORMALIZED_ENUM_INVALID: '当前值不是系统支持的标准值，请从下拉选项中选择。',
  NORMALIZED_TYPE_INVALID: '当前值格式不正确，请按字段要求重新填写。',
  CORRECTION_EVENT_INVALID: '校对记录与当前草稿版本不一致，请刷新页面后重新提交。',
  CORRECTION_AUDIT_MISSING: '系统未找到该字段的校对记录；刷新页面后重新提交即可。',
}

const draftStatusLabels: Record<string, string> = {
  UPLOADED: '已上传',
  PROCESSING: '正在解析',
  REVIEW_REQUIRED: '需要核对',
  READY_TO_SUBMIT: '可以提交',
  SUBMITTED: '已正式提交',
  FAILED: '处理失败',
  STALE: '已过期',
  DISCARDED: '已废弃',
}

function findingDisplayMessage(finding: QuoteDraftResponse['review_findings'][number]) {
  const code = finding.codes.find((item) => findingActionMessages[item])
  return code ? findingActionMessages[code] : finding.message
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
      const current = field?.normalized_value === null || field?.normalized_value === undefined ? '' : String(field.normalized_value)
      const options = correctionOptions[name]
      next[name] = options && !options.some((option) => option.value === current) ? '' : current
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
        <div><p className="eyebrow">报价草稿审核</p><h2>{draft.supplier_id} · {draft.original_filename}</h2><p>草稿不会进入正式报价历史，也不会更新任务版本。</p></div>
        <span className={`status-pill draft-status-${draft.status.toLowerCase()}`}>{draftStatusLabels[draft.status] ?? '处理中'}</span>
      </header>
      {draft.status === 'PROCESSING' && <div className="draft-processing"><i className="activity-spinner" /><div><strong>正在解析并审核报价</strong><span>后台服务会自动处理，页面正在刷新状态。</span></div></div>}
      {draft.status === 'FAILED' && <div className="form-error compact-error"><div><strong>报价处理失败</strong><p>{draft.error_message}</p></div></div>}
      {draft.status === 'STALE' && <div className="form-error compact-error"><div><strong>草稿已过期</strong><p>任务输入在审核期间发生变化，请废弃后重新上传。</p></div></div>}
      {draft.fields.length > 0 && (
        <form onSubmit={submitCorrections}>
          <div className="draft-field-grid">
            {draft.fields.map((field) => {
              const findings = blockers.filter((finding) => finding.field_name === field.field_name)
              const blocked = findings.length > 0
              return (
                <article className={`draft-field${blocked ? ' draft-field-blocked' : ''}`} key={field.field_name}>
                  <div className="draft-field-title"><strong>{fieldLabels[field.field_name] ?? '相关信息'}</strong></div>
                  <div className="draft-current-value"><span>当前值</span><strong>{displayValue(field.normalized_value)}{field.unit ? ` ${field.unit}` : ''}</strong><small>原文：{displayValue(field.raw_value)}</small></div>
                  {blocked ? <><div className="draft-finding-reasons">{findings.map((finding) => <p key={finding.finding_id}>{findingDisplayMessage(finding)}</p>)}</div><label className="field"><span>核对后的值</span>{correctionOptions[field.field_name] ? <select required value={values[field.field_name] ?? ''} onChange={(event) => { setValues((current) => ({ ...current, [field.field_name]: event.target.value })); setLastCorrection(null); correction.reset() }}><option value="">请选择已确认的实际情况</option>{correctionOptions[field.field_name].map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select> : <input required value={values[field.field_name] ?? ''} onChange={(event) => { setValues((current) => ({ ...current, [field.field_name]: event.target.value })); setLastCorrection(null); correction.reset() }} />}{field.field_name === 'delivery_semantics' && <small>“NO”不是交付语义；请选择承诺的是到货还是发货。</small>}{field.field_name === 'tax_mode' && <small>“N/A”不是标准值；只有报价明确说明不适用税费时才选“不适用”。</small>}</label></> : <span className="draft-verified">已通过自动审核 · 只读</span>}
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
  const activeDraftSummary = drafts.data?.items.find((item) => ACTIVE_STATUSES.has(item.status))
  const activeDraftDetail = useQuery({
    queryKey: ['tasks', taskId, 'quote-drafts', activeDraftSummary?.quote_draft_id],
    queryFn: () => api.getQuoteDraft(taskId, activeDraftSummary!.quote_draft_id),
    enabled: Boolean(taskId && activeDraftSummary),
    refetchInterval: (query) => query.state.data?.status === 'PROCESSING' ? 1_500 : false,
  })
  const activeDraft = activeDraftDetail.data ?? activeDraftSummary
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
  const batchReview = task.data?.current_issue?.issue_type === 'BATCH_FIELD_REVIEW'
  const legacyIssueReview = task.data?.current_issue && !['POLICY_EVIDENCE_REVIEW', 'BATCH_FIELD_REVIEW'].includes(task.data.current_issue.issue_type)

  if (task.data?.status === 'ABANDONED') {
    return <div className="page-stack quote-review-page">
      <TaskWorkspaceHeader taskId={task.data.task_id} scenarioId={task.data.scenario_id} title={task.data.requirement.manufacturer_part_number} subtitle="任务已废弃；报价与原件保持只读" status={task.data.status} revision={task.data.task_revision} resultId={task.data.current_result_id} quoteCount={task.data.quotes.length} summaryComplete={task.data.summary_completed} progress={task.data.progress} active="quotes" />
      <section className="card run-notice"><strong>该任务已软废弃</strong><p>不能上传、修正或提交报价；历史文件仍可预览和下载。</p></section>
      <div className="uploaded-list">{quoteHistory.data?.items.flatMap((quote) => quote.versions.map((version) => <article className="card uploaded-quote" key={version.document_id}><div><strong>{quote.supplier_id} · 第 {version.quote_version} 版</strong><p>{version.original_filename}</p></div><button className="button button-secondary" type="button" onClick={() => setPreview({ name: version.original_filename, mediaType: version.media_type, sizeBytes: version.size_bytes, remoteUrl: documentContentUrl(taskId, version.document_id), downloadUrl: documentContentUrl(taskId, version.document_id, 'attachment') })}>预览 / 下载</button></article>))}</div>
      {preview && <FilePreviewDialog source={preview} onClose={() => setPreview(null)} />}
    </div>
  }

  return (
    <div className="page-stack quote-review-page">
      {task.data ? <TaskWorkspaceHeader taskId={task.data.task_id} scenarioId={task.data.scenario_id} title={task.data.requirement.manufacturer_part_number} subtitle={`${task.data.requirement.required_quantity} ${task.data.requirement.quantity_unit} · ${task.data.quotes.length} 份正式报价`} status={task.data.status} revision={task.data.task_revision} resultId={task.data.current_result_id} quoteCount={task.data.quotes.length} summaryComplete={task.data.summary_completed} progress={task.data.progress} reviewBlocked={Boolean(activeDraft || legacyFieldReview || legacyIssueReview || batchReview)} active="quotes" /> : <section className="card loading-panel">正在读取任务工作台…</section>}
      <section className="quote-review-lead"><div><p className="eyebrow">QUOTE & REVIEW</p><h2>报价与审核</h2><p>先解析草稿并修正阻塞字段；审核通过后再正式写入任务。</p></div><span>{activeDraft ? '1 个活动草稿' : '可上传新草稿'}</span></section>
      {!activeDraft && <section className="upload-layout"><form className="card upload-form" onSubmit={handleSubmit}><div><p className="eyebrow">上传报价</p><h2>上传报价草稿</h2><p className="helper-text">草稿解析不会更新正式任务版本。</p></div><label className="field"><span>供应商编号</span><input required placeholder="例如 SUP-001" value={supplierId} onChange={(event) => setSupplierId(event.target.value)} /></label><label className="field"><span>报价文件</span><input ref={fileInput} required type="file" accept=".pdf,.csv,application/pdf,text/csv" onChange={(event) => handleFile(event.target.files?.[0] ?? null)} /><small>仅限 PDF/CSV，非空且不超过 5 MiB。</small></label>{selectedFile && <div className="selected-file"><div><strong>{selectedFile.name}</strong><span>{formatBytes(selectedFile.size)} · {selectedFile.type}</span></div><button type="button" onClick={() => setPreview({ name: selectedFile.name, mediaType: selectedFile.type, sizeBytes: selectedFile.size, file: selectedFile })}>预览文件</button></div>}<label className="field checkbox-field"><input type="checkbox" checked={isSynthetic} onChange={(event) => setIsSynthetic(event.target.checked)} /><span>这是合成测试数据</span></label>{(localError || upload.isError) && <div className="form-error compact-error"><div><strong>草稿上传未完成</strong><p>{localError || errorMessage(upload.error)}</p></div>{lastSubmission && <button className="button button-secondary" type="button" onClick={() => upload.mutate(lastSubmission)}>重试相同请求</button>}</div>}<button className="button button-submit" type="submit" disabled={upload.isPending}>{upload.isPending ? '正在上传…' : '上传并开始审核'}</button></form><aside className="card upload-guidance"><p className="eyebrow">提交前检查</p><h2>正式提交条件</h2><ol><li>后台解析全部字段与证据。</li><li>已验证字段保持只读。</li><li>仅阻塞字段需要人工修正。</li><li>全部通过后才能正式提交。</li></ol></aside></section>}
      {drafts.isError && <section className="card error-panel">草稿读取失败：{errorMessage(drafts.error)}</section>}
      {activeDraft && task.data && <DraftReview key={`${activeDraft.quote_draft_id}:${activeDraft.draft_revision}`} draft={activeDraft} taskRevision={task.data.task_revision} onChanged={refreshAll} />}
      {legacyFieldReview && task.data && <ReviewPanel task={task.data} onRefresh={() => void refreshAll()} />}
      {batchReview && <section className="card run-notice"><strong>本轮需要集中审核多个字段。</strong><p>请在集中审核页按后端返回的字段版本统一提交。</p><Link className="button button-submit" to={`/tasks/${taskId}/review`}>进入集中审核</Link></section>}
      {legacyIssueReview && task.data && <IssuePanel task={task.data} onRefresh={() => void refreshAll()} />}
      <section>
        <div className="section-heading"><div><p className="eyebrow">报价记录</p><h2>已正式提交的报价</h2></div><span>{quoteHistory.data?.items.length ?? 0} 个报价</span></div>
        {quoteHistory.isPending ? <div className="card empty-upload-list">正在加载报价历史…</div>
          : quoteHistory.isError ? <div className="card empty-upload-list">报价历史加载失败。</div>
            : quoteHistory.data.items.length === 0 ? <div className="card empty-upload-list">尚无正式报价；活动草稿不会显示在这里。</div>
              : <div className="uploaded-list">{quoteHistory.data.items.map((quote) => <article className="card uploaded-quote" key={quote.quote_id}>
                <div><span className={`status-pill ${quote.active ? 'status-ready' : 'status-muted'}`}>{quote.active ? '当前有效' : '已停用'}</span><h3>{quote.supplier_id}</h3><p>{quote.versions.length} 个文件版本 · 当前第 {quote.current_version} 版</p></div>
                <dl><div><dt>当前版本</dt><dd>第 {quote.current_version} 版</dd></div></dl>
                <div className="quote-version-list">{quote.versions.map((version) => <div className="quote-version-row" key={version.document_id}><div><strong>第 {version.quote_version} 版 · {version.original_filename}</strong><span>{formatBytes(version.size_bytes)} · {version.media_type}</span></div><div><span>{version.is_current ? '当前使用' : '历史版本'}</span><button className="quote-preview-action" type="button" onClick={() => setPreview({ name: version.original_filename, mediaType: version.media_type, sizeBytes: version.size_bytes, remoteUrl: documentContentUrl(taskId, version.document_id), downloadUrl: documentContentUrl(taskId, version.document_id, 'attachment') })}>预览 / 下载</button></div></div>)}</div>
              </article>)}</div>}
      </section>
      {preview && <FilePreviewDialog source={preview} onClose={() => setPreview(null)} />}
    </div>
  )
}
