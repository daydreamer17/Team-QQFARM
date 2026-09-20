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
  type QuoteReviewValidationIssue,
  validateQuoteReview,
} from '../lib/quoteReview'
import { reviewFindingAction, reviewFindingActionMessages } from '../lib/reviewMessages'

const draftStatusLabels: Record<string, string> = {
  UPLOADED: '已上传',
  PROCESSING: '正在解析',
  REVIEW_REQUIRED: '待处理',
  READY_TO_SUBMIT: '审核完成',
  SUBMITTED: '已正式提交',
  FAILED: '处理失败',
  STALE: '已过期',
  DISCARDED: '已废弃',
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
  if (!(error instanceof ApiClientError)) return error instanceof Error ? error.message : '操作失败，请稍后重试。'
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
    <div className="quote-field-evidence">
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
  )
}

function QuoteFieldEditor({
  definition,
  field,
  value,
  errors,
  findings,
  onChange,
}: {
  definition: QuoteFieldSchemaDefinition
  field: QuoteField
  value: string
  errors: string[]
  findings: QuoteDraftResponse['review_findings']
  onChange: (value: string) => void
}) {
  const unresolved = findings.filter((finding) => (
    !finding.resolved &&
    finding.decision !== 'PASS' &&
    !finding.accepted_for_calculation
  ))
  const hasValue = value.trim().length > 0
  const hasConflict = field.validation_status === 'CONFLICT'
  const needsAttention = errors.length > 0 || hasConflict
  return (
    <article
      className={`quote-review-field${needsAttention ? ' has-error' : ''}`}
      id={`quote-field-${definition.field_name}`}
    >
      <header>
        <div>
          <strong>{definition.label}</strong>
        </div>
        {needsAttention && (
          <div className="quote-field-badges">
            <span className="badge-review-state state-needs-attention">
            {hasConflict ? '字段冲突' : errors.length > 0 ? (hasValue ? '格式错误' : '请补充') : hasValue ? '已自动填写' : '允许留空'}
            </span>
          </div>
        )}
      </header>

      {unresolved.length > 0 && (
        <div className="quote-field-note">
          {unresolved.map((finding) => <p key={finding.finding_id}>{findingMessage(finding)}</p>)}
        </div>
      )}

      <label className="field quote-review-input">
        {definition.allowed_values && definition.allowed_values.length > 0 ? (
          <select
            aria-label={definition.label}
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
            aria-label={definition.label}
            aria-invalid={errors.length > 0}
            type={fieldInputType(definition)}
            inputMode={fieldInputMode(definition)}
            value={value}
            onChange={(event) => onChange(event.target.value)}
          />
        )}
        {errors.map((message) => <small className="field-error-text" key={message}>{message}</small>)}
      </label>

      <details className="quote-field-rules">
        <summary>查看原文与依据</summary>
        <p>原文：{displayValue(field.raw_value)}{field.unit ? ` ${field.unit}` : ''}</p>
        <p>字段：{definition.field_name} · 状态：{field.validation_status}</p>
        <EvidenceList field={field} />
        <p>{definition.normalization_rule}</p>
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
  const [priorityFieldNames] = useState(() => {
    const initialValues = initializeQuoteReviewValues(draft, schema)
    const result = new Set<string>()
    for (const current of validateQuoteReview(draft, schema, initialValues)) {
      current.fieldNames.forEach((fieldName) => result.add(fieldName))
    }
    for (const field of draft.fields) {
      if (field.validation_status === 'CONFLICT') result.add(field.field_name)
    }
    return result
  })
  const [validationIssues, setValidationIssues] = useState<ReturnType<typeof validateQuoteReview>>([])
  const [reviewKey, setReviewKey] = useState<string | null>(null)
  const [submitKey, setSubmitKey] = useState<string | null>(null)
  const [discardKey, setDiscardKey] = useState<string | null>(null)
  const [isDirty, setIsDirty] = useState(false)

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
  const liveValidationIssues = useMemo(
    () => validateQuoteReview(draft, schema, values),
    [draft, schema, values],
  )
  const conflictIssues = useMemo<QuoteReviewValidationIssue[]>(() => {
    const labels = new Map(schema.fields.map((definition) => [definition.field_name, definition.label]))
    return draft.fields
      .filter((field) => field.validation_status === 'CONFLICT')
      .map((field) => ({
        code: 'FIELD_VALUE_CONFLICT',
        fieldNames: [field.field_name],
        groupId: schema.fields.find((definition) => definition.field_name === field.field_name)?.group_id ?? 'field',
        message: `“${labels.get(field.field_name) ?? field.field_name}”存在冲突，请核对后选择正确内容。`,
      }))
  }, [draft.fields, schema.fields])
  const activeValidationIssues = useMemo(() => {
    const seen = new Set<string>()
    return [...liveValidationIssues, ...conflictIssues, ...validationIssues].filter((current) => {
      const key = `${current.code}:${current.fieldNames.join(',')}:${current.message}`
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
  }, [conflictIssues, liveValidationIssues, validationIssues])
  const allFieldIssues = useMemo(
    () => issuesByField(activeValidationIssues),
    [activeValidationIssues],
  )
  const currentProblemFieldNames = useMemo(() => {
    const result = new Set<string>()
    for (const current of activeValidationIssues) current.fieldNames.forEach((fieldName) => result.add(fieldName))
    return result
  }, [activeValidationIssues])
  const attentionFields = useMemo(
    () => schema.fields.filter((definition) => priorityFieldNames.has(definition.field_name)),
    [priorityFieldNames, schema.fields],
  )
  const readyFields = useMemo(
    () => schema.fields.filter((definition) => !priorityFieldNames.has(definition.field_name)),
    [priorityFieldNames, schema.fields],
  )
  const autoFilledCount = schema.fields.filter((definition) => (
    !currentProblemFieldNames.has(definition.field_name) && (values[definition.field_name] ?? '').trim()
  )).length
  const optionalEmptyCount = schema.fields.filter((definition) => (
    !currentProblemFieldNames.has(definition.field_name) && !(values[definition.field_name] ?? '').trim()
  )).length
  const readyFieldsContainProblem = readyFields.some((definition) => currentProblemFieldNames.has(definition.field_name))
  const canSubmitWithoutReview = draft.human_review_complete === true && draft.submission_ready === true && !isDirty

  const finalize = useMutation({
    mutationFn: async (submission: {
      reviewKey: string
      submitKey: string
      actions: ReturnType<typeof buildQuoteReviewActions>
      submitOnly: boolean
    }) => {
      let reviewedDraftRevision = draft.draft_revision
      if (!submission.submitOnly) {
        const reviewedDraft = await api.reviewQuoteDraft(
          draft.task_id,
          draft.quote_draft_id,
          draft.draft_revision,
          schema.schema_version,
          submission.actions,
          submission.reviewKey,
        )
        if (!reviewedDraft.human_review_complete || !reviewedDraft.submission_ready) {
          throw new Error('系统复核未通过，请根据页面提示修改后重试。')
        }
        reviewedDraftRevision = reviewedDraft.draft_revision
      }
      return api.submitQuoteDraft(
        draft.task_id,
        draft.quote_draft_id,
        taskRevision,
        reviewedDraftRevision,
        submission.submitKey,
      )
    },
    onSuccess: onChanged,
    onError: (error) => {
      const issues = backendValidationIssues(error)
      if (issues.length > 0) setValidationIssues(issues)
    },
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
    setSubmitKey(null)
    setIsDirty(true)
    finalize.reset()
  }

  function confirmAndSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const issues = validateQuoteReview(draft, schema, values)
    setValidationIssues(issues)
    if (issues.length > 0) {
      const firstField = issues.find((item) => item.fieldNames.length > 0)?.fieldNames[0]
      if (firstField) document.getElementById(`quote-field-${firstField}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      return
    }
    try {
      const actions = canSubmitWithoutReview ? [] : buildQuoteReviewActions(draft, schema, values)
      const nextReviewKey = reviewKey ?? createIdempotencyKey()
      const nextSubmitKey = submitKey ?? createIdempotencyKey()
      setReviewKey(nextReviewKey)
      setSubmitKey(nextSubmitKey)
      finalize.mutate({
        reviewKey: nextReviewKey,
        submitKey: nextSubmitKey,
        actions,
        submitOnly: canSubmitWithoutReview,
      })
    } catch (error) {
      setValidationIssues([{
        code: 'REVIEW_ACTION_BUILD_FAILED',
        fieldNames: [],
        groupId: 'schema',
        message: error instanceof Error ? error.message : '无法生成字段确认请求，请刷新页面重试。',
      }])
    }
  }

  const backendMessages = backendIssueMessages(finalize.error ?? discard.error)
  const mutationError = finalize.error ?? discard.error
  const schemaChanged = Boolean(draft.schema_version && draft.schema_version !== schema.schema_version)
  const legacyReview = Boolean(
    draft.review_envelope_schema_version &&
    draft.review_envelope_schema_version !== 'review-envelope/1.1.0',
  )

  function renderField(definition: QuoteFieldSchemaDefinition) {
    const field = fieldsByName.get(definition.field_name)
    if (!field) {
      return (
        <article className="quote-review-field has-error" key={definition.field_name} id={`quote-field-${definition.field_name}`}>
          <strong>{definition.label}</strong>
          <p className="field-error-text">系统没有生成此字段，请重新解析报价。</p>
        </article>
      )
    }
    return (
      <QuoteFieldEditor
        key={definition.field_name}
        definition={definition}
        field={field}
        value={values[definition.field_name] ?? ''}
        errors={[...new Set((allFieldIssues[definition.field_name] ?? []).map((item) => item.message))]}
        findings={findingsByName.get(definition.field_name) ?? []}
        onChange={(nextValue) => changeValue(definition.field_name, nextValue)}
      />
    )
  }

  return (
    <section className="card draft-review-card quote-full-review">
      <header className="draft-review-header">
        <div>
          <h2>{draft.supplier_id} · {draft.original_filename}</h2>
        </div>
        <div className="quote-draft-header-actions">
          <button className="button button-secondary" type="button" onClick={onPreview}>查看原件</button>
          <span className={`status-pill draft-status-${draft.status.toLowerCase()}`}>{draftStatusLabels[draft.status] ?? draft.status}</span>
        </div>
      </header>

      {draft.status === 'PROCESSING' && (
        <div className="draft-processing"><i className="activity-spinner" /><div><strong>正在解析并执行确定性预检</strong><span>完成后才能进行全字段人工确认。</span></div></div>
      )}
      {draft.status === 'FAILED' && <div className="form-error compact-error"><div><strong>报价处理失败</strong><p>{draft.error_message}</p></div></div>}
      {draft.status === 'STALE' && <div className="form-error compact-error"><div><strong>草稿已过期</strong><p>任务输入在审核期间发生变化，请废弃后重新上传。</p></div></div>}
      {(schemaChanged || legacyReview) && <div className="review-schema-warning"><strong>审核规则已更新</strong><p>请按当前规则重新核对本报价。</p></div>}

      {draft.fields.length > 0 && (
        <form onSubmit={confirmAndSubmit} noValidate>
          <div className="quote-review-overview">
            {currentProblemFieldNames.size > 0 && <div><strong>{currentProblemFieldNames.size}</strong><span>需要处理</span></div>}
            <div><strong>{autoFilledCount}</strong><span>已识别</span></div>
            <div><strong>{optionalEmptyCount}</strong><span>报价未提供</span></div>
          </div>

          {activeValidationIssues.length > 0 && (
            <div className="quote-review-error-summary" role="alert">
              <strong>还有 {activeValidationIssues.length} 个问题需要处理</strong>
              <p>请补充下方标红内容，其余项目不用重新输入。</p>
              <details>
                <summary>查看问题清单</summary>
                <ul>
                  {activeValidationIssues.map((current) => (
                    <li key={`${current.code}:${current.fieldNames.join(',')}`}>
                      {current.fieldNames[0] ? (
                        <button type="button" onClick={() => document.getElementById(`quote-field-${current.fieldNames[0]}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })}>
                          {current.message}
                        </button>
                      ) : current.message}
                    </li>
                  ))}
                </ul>
              </details>
            </div>
          )}

          <div className="quote-review-groups">
            {attentionFields.length > 0 && (
              <section className="quote-review-group quote-review-attention">
                <header><div><span>{attentionFields.length}</span><h3>重点处理</h3></div><p>这些项目最初存在缺失或疑问，请优先核对。</p></header>
                <div className="draft-field-grid">{attentionFields.map(renderField)}</div>
              </section>
            )}
            <details className="quote-review-ready-fields" open={attentionFields.length === 0 || readyFieldsContainProblem}>
              <summary>查看其他内容（{readyFields.length} 项）</summary>
              <div className="quote-review-groups">
                {groups.map(([groupId, group]) => {
                  const visibleFields = group.fields.filter((definition) => !priorityFieldNames.has(definition.field_name))
                  if (visibleFields.length === 0) return null
                  return (
                    <section className="quote-review-group" key={groupId}>
                      <header><div><span>{visibleFields.length}</span><h3>{group.label}</h3></div></header>
                      <div className="draft-field-grid">{visibleFields.map(renderField)}</div>
                    </section>
                  )
                })}
              </div>
            </details>
          </div>

          <div className="quote-review-confirm-bar">
            <button className="button button-submit" type="submit" disabled={finalize.isPending || draft.status === 'PROCESSING' || activeValidationIssues.length > 0}>
              {finalize.isPending ? '正在检查并提交…' : '确认并提交报价'}
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
      </div>
    </section>
  )
}
