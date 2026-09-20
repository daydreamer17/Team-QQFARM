import { useMutation } from '@tanstack/react-query'
import { type FormEvent, useMemo, useState } from 'react'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import type {
  QuoteDraftResponse,
  QuoteField,
  QuoteFieldSchemaDefinition,
  QuoteFieldSchemaResponse,
} from '../api/types'
import {
  buildQuoteReviewActions,
  initializeQuoteReviewValues,
  issuesByField,
  quoteValueAsText,
  requiredQuoteFields,
  type QuoteReviewValidationIssue,
  validateQuoteReview,
} from '../lib/quoteReview'
import { reviewFindingAction, reviewFindingActionMessages } from '../lib/reviewMessages'

const draftStatusLabels: Record<string, string> = {
  UPLOADED: '已上传',
  PROCESSING: '正在解析',
  REVIEW_REQUIRED: '待人工确认',
  READY_TO_SUBMIT: '后端复核通过',
  SUBMITTED: '已正式提交',
  FAILED: '处理失败',
  STALE: '已过期',
  DISCARDED: '已废弃',
}

const criticalityLabels: Record<string, string> = {
  ALWAYS: '关键字段',
  CONDITIONAL_APPLICABLE: '当前必填',
  CONDITIONAL_NOT_APPLICABLE: '当前不适用',
  NON_CRITICAL: '可选字段',
  SYSTEM_AUDIT: '系统字段',
}

const reviewStateLabels: Record<string, string> = {
  UNREVIEWED: '待人工确认',
  CONFIRMED: '人工已确认',
  CORRECTED: '人工已修改',
  MISSING_CONFIRMED: '人工确认缺失',
  CONFLICT_CONFIRMED: '人工确认冲突',
}

const optionLabels: Record<string, string> = {
  NEW: '全新',
  REFURBISHED: '翻新',
  USED: '二手',
  KNOWN_AMOUNT: '另有明确金额',
  FREE: '免费',
  INCLUDED: '已包含在报价中',
  NOT_APPLICABLE: '不适用',
  UNKNOWN: '未知（不能满足必填）',
  CALENDAR_DAYS: '自然日',
  BUSINESS_DAYS: '工作日',
  ARRIVAL: '到货',
  SHIPMENT: '发货',
  ORDER_DATE: '下单日',
  PAYMENT_RECEIPT: '收到付款',
  EXCLUDED: '未包含',
  piece: '颗（piece）',
  tray: '盘（tray）',
}

interface QuoteDraftReviewWorkspaceProps {
  draft: QuoteDraftResponse
  schema: QuoteFieldSchemaResponse
  taskRevision: number
  onChanged: () => Promise<void>
  onPreview: () => void
}

function displayValue(value: unknown): string {
  const text = quoteValueAsText(value)
  return text || '未提供'
}

function findingMessage(finding: QuoteDraftResponse['review_findings'][number]): string {
  const code = finding.codes.find((item) => reviewFindingActionMessages[item])
  return code ? reviewFindingAction(code, finding.message) : finding.message
}

function apiErrorMessage(error: unknown): string {
  if (!(error instanceof ApiClientError)) return '操作失败，请稍后重试。'
  if (error.status === 409) {
    const serverVersion = error.details.actual ?? error.details.actual_draft_revision ?? error.details.actual_field_version
    const suffix = typeof serverVersion === 'number' || typeof serverVersion === 'string'
      ? `服务器当前版本：${serverVersion}。`
      : ''
    return `草稿或字段版本已经变化。${suffix}当前输入仍保留在页面中，请核对最新版本后再提交。`
  }
  if (error.code === 'quote_review_schema_conflict' || error.code === 'quote_draft_review_stale') return '字段规则已经更新，请刷新页面并按最新规则重新确认。'
  if (error.code === 'quote_draft_not_reviewable') return '当前草稿已不能继续修改，请刷新页面查看最新状态。'
  if (error.status === 422) return error.message || '部分字段未通过后端复核，请按下方具体提示修改。'
  return error.message
}

function backendValidationIssues(error: unknown): QuoteReviewValidationIssue[] {
  if (!(error instanceof ApiClientError)) return []
  const details = error.details
  const collections = [details.errors, details.issues, details.violations]
  const issues: QuoteReviewValidationIssue[] = []
  for (const collection of collections) {
    if (!Array.isArray(collection)) continue
    for (const entry of collection) {
      if (!entry || typeof entry !== 'object') continue
      const message = 'message' in entry && typeof entry.message === 'string' ? entry.message : null
      const fields = 'field_names' in entry && Array.isArray(entry.field_names)
        ? entry.field_names.filter((item: unknown): item is string => typeof item === 'string')
        : []
      const code = 'code' in entry && typeof entry.code === 'string' ? entry.code : 'BACKEND_REVIEW_FAILED'
      const groupId = 'group_id' in entry && typeof entry.group_id === 'string' ? entry.group_id : 'backend'
      if (message) issues.push({ code, fieldNames: fields, groupId, message })
    }
  }
  if (issues.length === 0 && typeof details.message === 'string') {
    issues.push({ code: error.code, fieldNames: [], groupId: 'backend', message: details.message })
  }
  return issues
}

function backendIssueMessages(error: unknown): string[] {
  return [...new Set(backendValidationIssues(error).map((current) => (
    current.fieldNames.length > 0
      ? `${current.fieldNames.join('、')}：${current.message}`
      : current.message
  )))]
}

function fieldInputType(definition: QuoteFieldSchemaDefinition): 'date' | 'text' {
  return definition.value_type.toLowerCase().includes('date') ? 'date' : 'text'
}

function fieldInputMode(definition: QuoteFieldSchemaDefinition): 'decimal' | 'numeric' | 'text' {
  const kind = definition.value_type.toLowerCase()
  if (kind.includes('decimal')) return 'decimal'
  if (kind.includes('integer')) return 'numeric'
  return 'text'
}

function EvidenceList({ field }: { field: QuoteField }) {
  if (field.evidence.length === 0) {
    return <p className="quote-field-no-evidence">没有文档证据；如需补值，请确认信息来源后填写。</p>
  }
  return (
    <details className="quote-field-evidence">
      <summary>查看文档证据（{field.evidence.length}）</summary>
      <div>
        {field.evidence.map((evidence, index) => (
          <blockquote key={`${evidence.source_id ?? 'source'}:${index}`}>
            <p>{evidence.quoted_text || '该来源没有可显示的文本片段。'}</p>
            <footer>
              {evidence.page_number ? `第 ${evidence.page_number} 页` : evidence.row_number ? `第 ${evidence.row_number} 行` : '文档来源'}
              {evidence.column_name ? ` · ${evidence.column_name}` : ''}
              {evidence.source_id ? ` · ${evidence.source_id}` : ''}
            </footer>
          </blockquote>
        ))}
      </div>
    </details>
  )
}

function QuoteFieldEditor({
  definition,
  field,
  value,
  errors,
  findings,
  required,
  onChange,
}: {
  definition: QuoteFieldSchemaDefinition
  field: QuoteField
  value: string
  errors: string[]
  findings: QuoteDraftResponse['review_findings']
  required: boolean
  onChange: (value: string) => void
}) {
  const reviewState = field.review_state ?? 'UNREVIEWED'
  const criticalityLabel = field.criticality?.startsWith('CONDITIONAL_')
    ? required ? '当前必填' : '当前不适用'
    : criticalityLabels[field.criticality ?? ''] ?? definition.required_level
  const unresolved = findings.filter((finding) => !finding.resolved)
  return (
    <article
      className={`quote-review-field${errors.length > 0 ? ' has-error' : ''}`}
      id={`quote-field-${definition.field_name}`}
    >
      <header>
        <div>
          <strong>{definition.label}</strong>
          <code>{definition.field_name}</code>
        </div>
        <div className="quote-field-badges">
          <span className={required ? 'badge-required' : 'badge-optional'}>
            {criticalityLabel}
          </span>
          <span className={`badge-review-state state-${reviewState.toLowerCase()}`}>
            {reviewStateLabels[reviewState] ?? '待人工确认'}
          </span>
        </div>
      </header>

      <div className="quote-field-extraction">
        <span>LLM / 解析候选值</span>
        <strong>{displayValue(field.normalized_value)}{field.unit ? ` ${field.unit}` : ''}</strong>
        <small>原文值：{displayValue(field.raw_value)} · {field.validation_status}</small>
      </div>

      {unresolved.length === 0 ? (
        <p className="quote-precheck-pass">确定性预检未发现阻塞；仍需人工核对。</p>
      ) : (
        <div className="draft-finding-reasons">
          {unresolved.map((finding) => <p key={finding.finding_id}>{findingMessage(finding)}</p>)}
        </div>
      )}

      <label className="field quote-review-input">
        <span>人工确认值{required ? '（必填）' : '（可确认缺失）'}</span>
        {definition.allowed_values && definition.allowed_values.length > 0 ? (
          <select
            aria-invalid={errors.length > 0}
            value={value}
            onChange={(event) => onChange(event.target.value)}
          >
            <option value="">未提供 / 当前不适用</option>
            {definition.allowed_values.map((option) => (
              <option key={option} value={option}>{optionLabels[option] ? `${optionLabels[option]}（${option}）` : option}</option>
            ))}
          </select>
        ) : (
          <input
            aria-invalid={errors.length > 0}
            type={fieldInputType(definition)}
            inputMode={fieldInputMode(definition)}
            value={value}
            onChange={(event) => onChange(event.target.value)}
          />
        )}
        {definition.unit_kind === 'currency' && <small>金额币种随“报价币种”字段统一确认。</small>}
        {errors.map((message) => <small className="field-error-text" key={message}>{message}</small>)}
      </label>

      <EvidenceList field={field} />
      <details className="quote-field-rules">
        <summary>字段规则</summary>
        <p>{definition.normalization_rule}</p>
        <p>{definition.validation_boundary}</p>
      </details>
    </article>
  )
}

export function QuoteDraftReviewWorkspace({
  draft,
  schema,
  taskRevision,
  onChanged,
  onPreview,
}: QuoteDraftReviewWorkspaceProps) {
  const [values, setValues] = useState(() => initializeQuoteReviewValues(draft, schema))
  const [validationIssues, setValidationIssues] = useState<ReturnType<typeof validateQuoteReview>>([])
  const [reviewKey, setReviewKey] = useState<string | null>(null)
  const [submitKey, setSubmitKey] = useState<string | null>(null)
  const [discardKey, setDiscardKey] = useState<string | null>(null)

  const fieldsByName = useMemo(
    () => new Map(draft.fields.map((field) => [field.field_name, field])),
    [draft.fields],
  )
  const findingsByName = useMemo(() => {
    const result = new Map<string, QuoteDraftResponse['review_findings']>()
    for (const finding of draft.review_findings) {
      const current = result.get(finding.field_name) ?? []
      current.push(finding)
      result.set(finding.field_name, current)
    }
    return result
  }, [draft.review_findings])
  const groups = useMemo(() => {
    const result = new Map<string, { label: string; fields: QuoteFieldSchemaDefinition[] }>()
    for (const definition of schema.fields) {
      const current = result.get(definition.group_id) ?? { label: definition.group_label, fields: [] }
      current.fields.push(definition)
      result.set(definition.group_id, current)
    }
    return [...result.entries()]
  }, [schema.fields])
  const fieldIssues = useMemo(() => issuesByField(validationIssues), [validationIssues])
  const requiredFields = useMemo(
    () => requiredQuoteFields(draft, schema, values),
    [draft, schema, values],
  )

  const review = useMutation({
    mutationFn: (submission: { key: string; actions: ReturnType<typeof buildQuoteReviewActions> }) =>
      api.reviewQuoteDraft(
        draft.task_id,
        draft.quote_draft_id,
        draft.draft_revision,
        schema.schema_version,
        submission.actions,
        submission.key,
      ),
    onSuccess: onChanged,
    onError: (error) => {
      const issues = backendValidationIssues(error)
      if (issues.length > 0) setValidationIssues(issues)
    },
  })
  const submit = useMutation({
    mutationFn: (key: string) => api.submitQuoteDraft(
      draft.task_id,
      draft.quote_draft_id,
      taskRevision,
      draft.draft_revision,
      key,
    ),
    onSuccess: onChanged,
  })
  const discard = useMutation({
    mutationFn: (key: string) => api.discardQuoteDraft(
      draft.task_id,
      draft.quote_draft_id,
      draft.draft_revision,
      key,
    ),
    onSuccess: onChanged,
  })

  function changeValue(fieldName: string, nextValue: string) {
    setValues((current) => ({ ...current, [fieldName]: nextValue }))
    setValidationIssues((current) => current.filter((item) => !item.fieldNames.includes(fieldName)))
    setReviewKey(null)
    review.reset()
  }

  function confirmAllFields(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const issues = validateQuoteReview(draft, schema, values)
    setValidationIssues(issues)
    if (issues.length > 0) {
      const firstField = issues.find((item) => item.fieldNames.length > 0)?.fieldNames[0]
      if (firstField) document.getElementById(`quote-field-${firstField}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      return
    }
    try {
      const actions = buildQuoteReviewActions(draft, schema, values)
      const key = reviewKey ?? createIdempotencyKey()
      setReviewKey(key)
      review.mutate({ key, actions })
    } catch (error) {
      setValidationIssues([{
        code: 'REVIEW_ACTION_BUILD_FAILED',
        fieldNames: [],
        groupId: 'schema',
        message: error instanceof Error ? error.message : '无法生成字段确认请求，请刷新页面重试。',
      }])
    }
  }

  const backendMessages = backendIssueMessages(review.error ?? submit.error ?? discard.error)
  const mutationError = review.error ?? submit.error ?? discard.error
  const canSubmit = draft.human_review_complete === true && draft.submission_ready === true
  const progress = draft.review_progress
  const reviewed = progress?.reviewed ?? (draft.human_review_complete ? schema.fields.length : 0)
  const schemaChanged = Boolean(draft.schema_version && draft.schema_version !== schema.schema_version)
  const legacyReview = Boolean(
    draft.review_envelope_schema_version &&
    draft.review_envelope_schema_version !== 'review-envelope/1.1.0',
  )

  return (
    <section className="card draft-review-card quote-full-review">
      <header className="draft-review-header">
        <div>
          <p className="eyebrow">报价草稿人工复核</p>
          <h2>{draft.supplier_id} · {draft.original_filename}</h2>
          <p>自动提取和预检仅提供辅助；正式提交前请对照原件确认全部 {schema.fields.length} 个字段。</p>
        </div>
        <div className="quote-draft-header-actions">
          <button className="button button-secondary" type="button" onClick={onPreview}>预览 / 下载原件</button>
          <span className={`status-pill draft-status-${draft.status.toLowerCase()}`}>{draftStatusLabels[draft.status] ?? draft.status}</span>
        </div>
      </header>

      {draft.status === 'PROCESSING' && (
        <div className="draft-processing"><i className="activity-spinner" /><div><strong>正在解析并执行确定性预检</strong><span>完成后才能进行全字段人工确认。</span></div></div>
      )}
      {draft.status === 'FAILED' && <div className="form-error compact-error"><div><strong>报价处理失败</strong><p>{draft.error_message}</p></div></div>}
      {draft.status === 'STALE' && <div className="form-error compact-error"><div><strong>草稿已过期</strong><p>任务输入在审核期间发生变化，请废弃后重新上传。</p></div></div>}
      {(schemaChanged || legacyReview) && <div className="review-schema-warning"><strong>审核规则已更新</strong><p>该草稿需要按当前 {schema.schema_version} 规则重新确认全部字段。</p></div>}

      {draft.fields.length > 0 && (
        <form onSubmit={confirmAllFields} noValidate>
          <div className="quote-review-progress">
            <div>
              <span>人工确认进度</span>
              <strong>{reviewed} / {progress?.total ?? schema.fields.length}</strong>
            </div>
            <progress max={progress?.total ?? schema.fields.length} value={reviewed} />
            <p>
              {draft.human_review_complete
                ? '全部字段已有人工审核记录；如再次修改，需要重新确认整份报价。'
                : '点击页面底部按钮后，未修改值、修正值及确认缺失都会逐字段写入审计。'}
            </p>
          </div>

          {validationIssues.length > 0 && (
            <div className="quote-review-error-summary" role="alert">
              <strong>暂不能提交后端复核：请处理以下 {validationIssues.length} 项</strong>
              <ul>
                {validationIssues.map((current) => (
                  <li key={`${current.code}:${current.fieldNames.join(',')}`}>
                    {current.fieldNames[0] ? (
                      <button type="button" onClick={() => document.getElementById(`quote-field-${current.fieldNames[0]}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })}>
                        {current.message}
                      </button>
                    ) : current.message}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="quote-review-groups">
            {groups.map(([groupId, group]) => (
              <section className="quote-review-group" key={groupId}>
                <header>
                  <div><span>{group.fields.length}</span><h3>{group.label}</h3></div>
                  <p>请核对候选值、原文和证据后确认或修改。</p>
                </header>
                <div className="draft-field-grid">
                  {group.fields.map((definition) => {
                    const field = fieldsByName.get(definition.field_name)
                    if (!field) {
                      return (
                        <article className="quote-review-field has-error" key={definition.field_name}>
                          <strong>{definition.label}</strong>
                          <p className="field-error-text">草稿缺少此字段，无法提交。</p>
                        </article>
                      )
                    }
                    return (
                      <QuoteFieldEditor
                        key={definition.field_name}
                        definition={definition}
                        field={field}
                        value={values[definition.field_name] ?? ''}
                        errors={(fieldIssues[definition.field_name] ?? []).map((item) => item.message)}
                        findings={findingsByName.get(definition.field_name) ?? []}
                        required={requiredFields.has(definition.field_name)}
                        onChange={(nextValue) => changeValue(definition.field_name, nextValue)}
                      />
                    )
                  })}
                </div>
              </section>
            ))}
          </div>

          <div className="quote-review-confirm-bar">
            <div>
              <strong>确认全部字段并提交后端复核</strong>
              <p>此操作不会正式写入报价；后端权威复核通过后，才会启用“正式提交报价”。</p>
            </div>
            <button className="button button-submit" type="submit" disabled={review.isPending || draft.status === 'PROCESSING'}>
              {review.isPending ? '正在执行后端复核…' : `确认并复核 ${schema.fields.length} 个字段`}
            </button>
          </div>
        </form>
      )}

      {mutationError && (
        <div className="form-error compact-error" role="alert">
          <div>
            <strong>操作未完成</strong>
            <p>{apiErrorMessage(mutationError)}</p>
            {backendMessages.length > 0 && <ul>{backendMessages.map((message) => <li key={message}>{message}</li>)}</ul>}
          </div>
        </div>
      )}

      <div className="draft-actions">
        <button
          className="button button-secondary"
          type="button"
          onClick={() => {
            const key = discardKey ?? createIdempotencyKey()
            setDiscardKey(key)
            discard.mutate(key)
          }}
          disabled={discard.isPending || draft.status === 'SUBMITTED'}
        >
          废弃草稿
        </button>
        <div className="quote-submit-gate">
          {!canSubmit && <small>需先完成人工逐字段确认并通过后端权威复核。</small>}
          <button
            className="button button-submit"
            type="button"
            onClick={() => {
              const key = submitKey ?? createIdempotencyKey()
              setSubmitKey(key)
              submit.mutate(key)
            }}
            disabled={!canSubmit || submit.isPending}
          >
            {submit.isPending ? '正在正式提交…' : '正式提交报价'}
          </button>
        </div>
      </div>
    </section>
  )
}
