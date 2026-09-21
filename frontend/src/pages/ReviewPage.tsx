import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { Navigate, useParams } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import type {
  FieldCorrectionInput,
  QuoteFieldSchemaDefinition,
  ReviewProblem,
} from '../api/types'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'
import { fieldLabel, reasonText } from '../lib/presentation'
import { reviewFindingAction } from '../lib/reviewMessages'

interface DraftValue {
  value: string
}

interface CorrectionTarget {
  quote_id: string
  field_name: string
  field_version: number
  raw_value: string | null
  normalized_value: unknown
  unit: string | null
  original_filename: string | null
  message: string
  source_refs: Record<string, unknown>[]
}

interface ProblemGroup {
  key: string
  fieldName: string
  problems: ReviewProblem[]
  needsResolution: boolean
}

const optionLabels: Record<string, string> = {
  NEW: '全新',
  REFURBISHED: '翻新',
  USED: '二手',
  KNOWN_AMOUNT: '另有明确金额',
  FREE: '免费',
  INCLUDED: '已包含在报价中',
  NOT_APPLICABLE: '不适用',
  CALENDAR_DAYS: '自然日',
  BUSINESS_DAYS: '工作日',
  ARRIVAL: '到货',
  SHIPMENT: '发运',
  ORDER_DATE: '下单日',
  PAYMENT_RECEIPT: '收到付款',
  EXCLUDED: '未包含',
  piece: '颗',
  tray: '盘',
}

function problemKey(problem: Pick<CorrectionTarget, 'quote_id' | 'field_name'>) {
  return `${problem.quote_id}:${problem.field_name}`
}

function groupProblems(problems: ReviewProblem[]): ProblemGroup[] {
  const groups = new Map<string, ProblemGroup>()
  for (const problem of problems) {
    const key = problemKey(problem)
    const current = groups.get(key)
    if (current) {
      current.problems.push(problem)
      current.needsResolution ||= problem.needs_resolution
    } else {
      groups.set(key, {
        key,
        fieldName: problem.field_name,
        problems: [problem],
        needsResolution: problem.needs_resolution,
      })
    }
  }
  return [...groups.values()]
}

function initialValue(problem: CorrectionTarget, definition?: QuoteFieldSchemaDefinition): string {
  const value = problem.normalized_value
  if (value === null || value === undefined) return ''
  if (
    (problem.field_name === 'shipping_fee_status' || problem.field_name === 'other_fees_status')
    && value === 'UNKNOWN'
  ) return ''
  if (definition?.value_type.toLowerCase().includes('date')) return String(value).slice(0, 10)
  return String(value)
}

function errorMessage(error: unknown) {
  if (error instanceof ApiClientError) {
    if (error.code === 'field_correction_batch_invalid' || error.code === 'task_revision_conflict') {
      return '审核数据已经更新，页面已重新同步。请核对后再次提交。'
    }
    if (error.code === 'extraction_batch_stale') {
      return '报价解析结果已经更新，页面已同步最新数据。请核对后再次提交。'
    }
    if (error.code === 'extraction_batch_missing') {
      return '当前报价还没有可用的解析结果，请先完成报价解析。'
    }
    return error.message
  }
  return '集中审核数据读取失败。'
}

function isStaleCorrectionError(error: unknown) {
  if (!(error instanceof ApiClientError)) return false
  if (error.code === 'task_revision_conflict' || error.code === 'extraction_batch_stale') return true
  if (error.code !== 'field_correction_batch_invalid') return false
  const errors = error.details.errors
  return Array.isArray(errors) && errors.some((item) => (
    item && typeof item === 'object' && 'code' in item && item.code === 'field_version_conflict'
  ))
}

function typedValue(value: string, definition: QuoteFieldSchemaDefinition | undefined, original: unknown) {
  const kind = definition?.value_type.toLowerCase() ?? ''
  if (kind.includes('boolean') || typeof original === 'boolean') return value === 'true'
  if (kind.includes('integer') || typeof original === 'number') return Number.parseInt(value, 10)
  return value
}

function displayOption(value: string, fieldName?: string) {
  if (fieldName === 'tax_mode' && value === 'INCLUDED') return '已含税'
  if (fieldName === 'tax_mode' && value === 'EXCLUDED') return '不含税'
  return optionLabels[value] ?? value
}

function userFieldLabel(fieldName: string, fallback?: string) {
  const label = fieldLabel(fieldName)
  return label === '相关信息' ? (fallback ?? label) : label
}

function quotedTexts(sourceRefs: Record<string, unknown>[], rawValue: string | null) {
  const texts = sourceRefs
    .map((source) => source.quoted_text)
    .filter((value): value is string => typeof value === 'string' && Boolean(value.trim()))
  if (texts.length === 0 && rawValue?.trim()) texts.push(rawValue.trim())
  return [...new Set(texts)]
}

function reviewProblemMessage(problem: ReviewProblem) {
  const code = problem.codes[0] ?? ''
  return reviewFindingAction(code, reasonText({ code, message: problem.message }))
}

function BusinessValueInput({
  definition,
  label,
  value,
  currency,
  onChange,
}: {
  definition?: QuoteFieldSchemaDefinition
  label: string
  value: string
  currency: string | null
  onChange: (value: string) => void
}) {
  const allowedValues = definition?.allowed_values?.filter((option) => option !== 'UNKNOWN') ?? []
  const kind = definition?.value_type.toLowerCase() ?? ''
  const isAmount = definition?.unit_kind === 'currency' || definition?.field_name.endsWith('_amount')
  const inputType = kind.includes('date') ? 'date' : 'text'
  const inputMode = kind.includes('decimal') ? 'decimal' : kind.includes('integer') ? 'numeric' : 'text'

  if (allowedValues.length > 0) {
    return (
      <select aria-label={`${label}确认值`} value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">请选择</option>
        {allowedValues.map((option) => (
          <option key={option} value={option}>{displayOption(option, definition?.field_name)}</option>
        ))}
      </select>
    )
  }

  if (kind.includes('boolean')) {
    return (
      <select aria-label={`${label}确认值`} value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">请选择</option>
        <option value="true">是</option>
        <option value="false">否</option>
      </select>
    )
  }

  return (
    <div className="review-business-value-control">
      <input
        aria-label={`${label}确认值`}
        type={inputType}
        inputMode={inputMode}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
      {isAmount && currency && <span>{currency}</span>}
    </div>
  )
}

export function ReviewPage() {
  const { taskId = '' } = useParams()
  const queryClient = useQueryClient()
  const [drafts, setDrafts] = useState<Record<string, DraftValue>>({})
  const task = useQuery({
    queryKey: ['tasks', taskId],
    queryFn: () => api.getTask(taskId),
    enabled: Boolean(taskId),
  })
  const review = useQuery({
    queryKey: ['tasks', taskId, 'review'],
    queryFn: () => api.getReview(taskId),
    enabled: Boolean(taskId),
    refetchInterval: (query) => (
      query.state.data?.review_pending
      && task.data?.current_issue?.issue_type !== 'BATCH_FIELD_REVIEW'
        ? 1500
        : false
    ),
  })
  const schema = useQuery({
    queryKey: ['quote-field-schema'],
    queryFn: () => api.getQuoteFieldSchema(),
  })
  const definitionByName = useMemo(
    () => new Map(schema.data?.fields.map((definition) => [definition.field_name, definition]) ?? []),
    [schema.data],
  )
  const groupedProblems = useMemo(
    () => groupProblems(review.data?.problems ?? []),
    [review.data],
  )
  const actionable = useMemo(() => {
    const unique = new Map<string, CorrectionTarget>()
    const reportedKeys = new Set(review.data?.problems.map(problemKey) ?? [])
    const correctableKeys = new Set(
      review.data?.problems
        .filter((problem) => problem.resolution === 'FIELD_CORRECTION')
        .map(problemKey) ?? [],
    )
    for (const problem of review.data?.problems ?? []) {
      if (problem.needs_resolution && problem.resolution === 'FIELD_CORRECTION' && problem.field_version) {
        const key = problemKey(problem)
        const current = unique.get(key)
        const quote = review.data?.quotes.find((item) => item.quote_id === problem.quote_id)
        const field = quote?.fields.find((item) => item.field_name === problem.field_name)
        unique.set(key, {
          quote_id: problem.quote_id,
          field_name: problem.field_name,
          field_version: problem.field_version,
          raw_value: problem.raw_value,
          normalized_value: problem.normalized_value,
          unit: problem.unit,
          original_filename: problem.original_filename,
          message: current?.message ?? problem.message,
          source_refs: current?.source_refs.length ? current.source_refs : field?.source_refs ?? [],
        })
      }
    }
    for (const card of task.data?.current_issue?.answer_schema.cards ?? []) {
      if (card.resolution === 'FIELD_CORRECTION' && card.expected_field_version) {
        const key = problemKey(card)
        if (reportedKeys.has(key) && !correctableKeys.has(key)) continue
        const current = unique.get(key)
        if (current) {
          if (current.source_refs.length === 0 && card.source_refs.length > 0) {
            unique.set(key, { ...current, source_refs: card.source_refs })
          }
        } else {
          unique.set(key, {
            quote_id: card.quote_id,
            field_name: card.field_name,
            field_version: card.expected_field_version,
            raw_value: null,
            normalized_value: card.current_value,
            unit: null,
            original_filename: card.original_filename,
            message: card.question,
            source_refs: card.source_refs,
          })
        }
      }
    }
    return [...unique.values()]
  }, [review.data, task.data])

  const correction = useMutation({
    mutationFn: (items: FieldCorrectionInput[]) =>
      api.correctQuoteFields(taskId, review.data!.task_revision, items, createIdempotencyKey()),
    onSuccess: async () => {
      setDrafts({})
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['tasks', taskId] }),
        queryClient.invalidateQueries({ queryKey: ['tasks', taskId, 'review'] }),
      ])
    },
    onError: async (error) => {
      if (!isStaleCorrectionError(error)) return
      await Promise.all([
        queryClient.refetchQueries({ queryKey: ['tasks', taskId] }),
        queryClient.refetchQueries({ queryKey: ['tasks', taskId, 'review'] }),
      ])
    },
  })

  if (task.isPending || review.isPending || schema.isPending) {
    return <section className="card loading-panel">正在读取集中审核数据…</section>
  }
  if (task.isError || review.isError || schema.isError) {
    return <section className="card error-panel" role="alert">{errorMessage(task.error ?? review.error ?? schema.error)}</section>
  }

  const data = task.data
  const report = review.data
  const targetByKey = new Map(actionable.map((target) => [problemKey(target), target]))
  const groupsByKey = new Map(groupedProblems.map((group) => [group.key, group]))
  const manualBlockers = groupedProblems.filter((group) => group.needsResolution && !targetByKey.has(group.key))
  const recordOnly = groupedProblems.filter((group) => !group.needsResolution)
  const pendingCount = actionable.length + manualBlockers.length
  const versionsAligned = data.task_revision === report.task_revision
  const batchIssueReady = (
    data.current_issue?.issue_type === 'BATCH_FIELD_REVIEW'
    && data.current_issue.status === 'OPEN'
    && data.current_issue.answer_schema.expected_task_revision === data.task_revision
    && versionsAligned
  )
  const waitingForReview = report.review_pending && !batchIssueReady
  const reviewIsStable = versionsAligned && !waitingForReview
  const complete = actionable.length > 0 && actionable.every((problem) => {
    const definition = definitionByName.get(problem.field_name)
    const draft = drafts[problemKey(problem)] ?? { value: initialValue(problem, definition) }
    return Boolean(draft.value.trim())
  })

  if (
    data.status === 'COMPLETED'
    && data.current_result_id
    && pendingCount === 0
  ) {
    return <Navigate to={`/tasks/${encodeURIComponent(taskId)}/decision`} replace />
  }

  function quoteCurrency(target: CorrectionTarget): string | null {
    const quote = report.quotes.find((item) => item.quote_id === target.quote_id)
    const currency = quote?.fields.find((field) => field.field_name === 'currency')?.normalized_value
    return typeof currency === 'string' && currency ? currency : data.requirement.currency || null
  }

  function submitAll() {
    if (!complete || !reviewIsStable) return
    correction.mutate(actionable.map((problem) => {
      const definition = definitionByName.get(problem.field_name)
      const draft = drafts[problemKey(problem)] ?? { value: initialValue(problem, definition) }
      const value = draft.value.trim()
      const currency = quoteCurrency(problem)
      const isCurrency = definition?.unit_kind === 'currency' || problem.field_name.endsWith('_amount')
      return {
        quoteId: problem.quote_id,
        fieldName: problem.field_name,
        expectedFieldVersion: problem.field_version,
        rawValue: definition?.allowed_values?.includes(value) ? displayOption(value, problem.field_name) : value,
        normalizedValue: typedValue(value, definition, problem.normalized_value),
        unit: problem.unit || (isCurrency ? currency : null),
        reason: '人工核对报价原文或供应商回复后修正',
      }
    }))
  }

  return (
    <div className="page-stack review-overview-page">
      <TaskWorkspaceHeader
        taskId={data.task_id}
        scenarioId={data.scenario_id}
        title={data.requirement.manufacturer_part_number}
        subtitle={`${data.requirement.required_quantity} ${data.requirement.quantity_unit} · ${pendingCount} 个字段待处理`}
        status={data.status}
        revision={data.task_revision}
        resultId={data.current_result_id}
        quoteCount={data.quotes.length}
        summaryComplete={data.summary_completed}
        progress={data.progress}
        reviewBlocked={pendingCount > 0 || report.review_pending}
        active="review"
      />

      <section className="review-workspace-lead">
        <div>
          <h2>集中审核</h2>
          <p>只需补充会影响当前决策的字段，提交后系统统一重新审核与计算。</p>
        </div>
        {pendingCount > 0 && <span className="status-pill status-pending">{pendingCount} 项待处理</span>}
      </section>

      <section className="audit-overview">
        <article><span>有效报价</span><strong>{report.quotes.length}</strong></article>
        <article><span>待处理字段</span><strong>{pendingCount}</strong></article>
        <article><span>仅保留记录</span><strong>{recordOnly.length}</strong></article>
      </section>

      {waitingForReview && <div className="run-notice">部分报价仍在审核，完成后才可统一提交；页面会自动更新。</div>}
      {!report.review_pending && data.task_revision !== report.task_revision && (
        <div className="run-notice">正在同步最新审核数据，请稍候。</div>
      )}
      {!report.review_pending && pendingCount === 0 && (
        <section className="card audit-empty">当前没有需要补充的字段。</section>
      )}

      {actionable.length > 0 && (
        <div className="review-problem-grid">
          {actionable.map((target) => {
            const key = problemKey(target)
            const group = groupsByKey.get(key)
            const definition = definitionByName.get(target.field_name)
            const label = userFieldLabel(target.field_name, definition?.label)
            const draft = drafts[key] ?? { value: initialValue(target, definition) }
            const evidenceTexts = quotedTexts(target.source_refs, target.raw_value)
            return (
              <article className="card review-problem-card review-action-card" key={key}>
                <header>
                  <div><strong>{target.original_filename ?? '报价文件'}</strong><span>{label}</span></div>
                  <span className="status-pill status-pending">待确认</span>
                </header>
                {group && group.problems.length > 1 && (
                  <small className="review-merged-note">已合并 {group.problems.length} 条相关规则，填写一次即可。</small>
                )}
                <label className="field review-business-value">
                  <span>确认值</span>
                  <BusinessValueInput
                    definition={definition}
                    label={label}
                    value={draft.value}
                    currency={quoteCurrency(target)}
                    onChange={(value) => setDrafts((current) => ({ ...current, [key]: { value } }))}
                  />
                </label>
                {evidenceTexts.length > 0 && (
                  <details className="review-rule-details">
                    <summary>查看报价原文</summary>
                    {evidenceTexts.map((text) => <blockquote key={text}>{text}</blockquote>)}
                  </details>
                )}
              </article>
            )
          })}
        </div>
      )}

      {manualBlockers.length > 0 && (
        <section className="card review-manual-blockers">
          <h3>需要补充信息</h3>
          {manualBlockers.map((group) => {
            const problem = group.problems[0]
            const message = problem.resolution === 'ADDITIONAL_INFORMATION_REQUIRED'
              ? '报价原值已保留；需要补充换算或评估信息，不能通过改写报价值解决。'
              : '当前字段无法在线修正，请重新上传报价或联系管理员。'
            return (
              <p key={group.key}>{problem.original_filename ?? '报价文件'} · {userFieldLabel(group.fieldName)}：{message}</p>
            )
          })}
        </section>
      )}

      {recordOnly.length > 0 && (
        <details className="card review-records">
          <summary>查看不影响当前推荐的记录（{recordOnly.length}）</summary>
          <div>
            {recordOnly.map((group) => (
              <p key={group.key}>
                <strong>{group.problems[0].original_filename ?? '报价文件'} · {userFieldLabel(group.fieldName)}</strong>
                <span>{reviewProblemMessage(group.problems[0])}</span>
              </p>
            ))}
          </div>
        </details>
      )}

      {actionable.length > 0 && data.status !== 'ABANDONED' && (
        <button className="button button-submit" type="button" disabled={!complete || !reviewIsStable || correction.isPending} onClick={submitAll}>
          {correction.isPending
            ? '正在提交…'
            : waitingForReview
              ? '等待报价审核完成'
              : !versionsAligned
                ? '正在同步最新数据…'
                : '确认并重新计算'}
        </button>
      )}
      {correction.isError && <div className="form-error" role="alert">{errorMessage(correction.error)}</div>}
    </div>
  )
}
