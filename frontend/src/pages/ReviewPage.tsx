import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import type { FieldCorrectionInput } from '../api/types'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'
import { fieldLabel } from '../lib/presentation'

interface DraftValue {
  rawValue: string
  normalizedValue: string
  unit: string
  reason: string
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
}

function problemKey(problem: Pick<CorrectionTarget, 'quote_id' | 'field_name'>) {
  return `${problem.quote_id}:${problem.field_name}`
}

function initialDraft(problem: CorrectionTarget): DraftValue {
  return {
    rawValue: problem.raw_value ?? '',
    normalizedValue: problem.normalized_value === null || problem.normalized_value === undefined
      ? ''
      : String(problem.normalized_value),
    unit: problem.unit ?? '',
    reason: '人工核对原始报价后修正',
  }
}

function errorMessage(error: unknown) {
  if (error instanceof ApiClientError) {
    if (error.code === 'field_correction_batch_invalid' || error.code === 'task_revision_conflict') {
      return '字段或任务版本已经变化，请刷新后重新核对。'
    }
    return error.message
  }
  return '集中审核数据读取失败。'
}

function typedValue(value: string, original: unknown) {
  if (typeof original === 'boolean') return value.trim().toLowerCase() === 'true'
  if (typeof original === 'number') {
    const parsed = Number(value)
    return Number.isInteger(parsed) ? parsed : value
  }
  return value
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
  })
  const actionable = useMemo(() => {
    const unique = new Map<string, CorrectionTarget>()
    for (const problem of review.data?.problems ?? []) {
      if (problem.needs_resolution && problem.resolution === 'FIELD_CORRECTION' && problem.field_version) {
        unique.set(problemKey(problem), {
          quote_id: problem.quote_id,
          field_name: problem.field_name,
          field_version: problem.field_version,
          raw_value: problem.raw_value,
          normalized_value: problem.normalized_value,
          unit: problem.unit,
          original_filename: problem.original_filename,
          message: problem.message,
        })
      }
    }
    for (const card of task.data?.current_issue?.answer_schema.cards ?? []) {
      if (card.resolution === 'FIELD_CORRECTION' && card.expected_field_version) {
        const key = problemKey(card)
        if (!unique.has(key)) {
          unique.set(key, {
            quote_id: card.quote_id,
            field_name: card.field_name,
            field_version: card.expected_field_version,
            raw_value: null,
            normalized_value: card.current_value,
            unit: null,
            original_filename: card.original_filename,
            message: card.question,
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
  })

  if (task.isPending || review.isPending) return <section className="card loading-panel">正在读取集中审核数据…</section>
  if (task.isError || review.isError) return <section className="card error-panel" role="alert">{errorMessage(task.error ?? review.error)}</section>

  const data = task.data
  const report = review.data
  const complete = actionable.length > 0 && actionable.every((problem) => {
    const draft = drafts[problemKey(problem)] ?? initialDraft(problem)
    return Boolean(draft?.rawValue.trim() && draft.normalizedValue.trim() && draft.reason.trim())
  })

  function submitAll() {
    if (!complete) return
    correction.mutate(actionable.map((problem) => {
      const draft = drafts[problemKey(problem)] ?? initialDraft(problem)
      return {
        quoteId: problem.quote_id,
        fieldName: problem.field_name,
        expectedFieldVersion: problem.field_version,
        rawValue: draft.rawValue.trim(),
        normalizedValue: typedValue(draft.normalizedValue, problem.normalized_value),
        unit: draft.unit.trim() || null,
        reason: draft.reason.trim(),
      }
    }))
  }

  const reportedKeys = new Set(report.problems.map(problemKey))
  const clarificationOnly = actionable.filter((target) => !reportedKeys.has(problemKey(target)))

  return (
    <div className="page-stack review-overview-page">
      <TaskWorkspaceHeader taskId={data.task_id} scenarioId={data.scenario_id} title={data.requirement.manufacturer_part_number} subtitle={`${data.requirement.required_quantity} ${data.requirement.quantity_unit} · ${report.problem_count} 个审核问题`} status={data.status} revision={data.task_revision} resultId={data.current_result_id} quoteCount={data.quotes.length} summaryComplete={data.summary_completed} progress={data.progress} reviewBlocked={report.blocking_problem_count > 0 || report.review_pending} active="review" />

      <section className="review-workspace-lead">
        <div><p className="eyebrow">CONSOLIDATED REVIEW</p><h2>集中审核</h2><p>问题、字段版本和是否必须解决均来自后端；提交后会创建新的任务版本和 Job。</p></div>
        <span className={`status-pill ${report.blocking_problem_count ? 'status-pending' : 'status-ready'}`}>{report.blocking_problem_count} 个必须解决</span>
      </section>

      {data.current_issue?.issue_type === 'BATCH_FIELD_REVIEW' && (
        <section className="card run-notice"><strong>{data.current_issue.question}</strong><p>请一次提交本轮所有必须解决的字段。调查澄清卡共 {data.current_issue.answer_schema.cards?.length ?? 0} 条。</p></section>
      )}

      <section className="audit-overview">
        <article><span>报价范围</span><strong>{report.quotes.length}</strong><small>当前有效报价</small></article>
        <article><span>全部问题</span><strong>{report.problem_count}</strong><small>含非阻塞问题</small></article>
        <article><span>必须解决</span><strong>{report.blocking_problem_count}</strong><small>后端 needs_resolution</small></article>
      </section>

      {report.review_pending && <div className="run-notice">当前版本仍有报价等待审核，列表会在审核产物生成后补全。</div>}
      {report.problems.length === 0 && <section className="card audit-empty">当前版本没有未解决审核问题。</section>}

      <div className="review-problem-grid">
        {report.problems.map((problem) => {
          const key = problemKey(problem)
          const target = actionable.find((item) => problemKey(item) === key)
          const draft = target ? drafts[key] ?? initialDraft(target) : null
          const editable = Boolean(target)
          return (
            <article className="card review-problem-card" key={problem.finding_id}>
              <header><div><strong>{problem.original_filename ?? '报价文件'}</strong><span>{fieldLabel(problem.field_name)}</span></div><span className={`status-pill ${problem.needs_resolution ? 'status-pending' : 'status-muted'}`}>{problem.needs_resolution ? '必须解决' : '保留记录'}</span></header>
              <p>{problem.message}</p>
              <small>{problem.codes.join('、')}</small>
              {editable && draft && (
                <div className="review-correction-fields">
                  <label className="field"><span>核对后的原始表达</span><input value={draft.rawValue} onChange={(event) => setDrafts((current) => ({ ...current, [key]: { ...draft, rawValue: event.target.value } }))} /></label>
                  <label className="field"><span>标准化值</span><input value={draft.normalizedValue} onChange={(event) => setDrafts((current) => ({ ...current, [key]: { ...draft, normalizedValue: event.target.value } }))} /></label>
                  <label className="field"><span>单位</span><input value={draft.unit} onChange={(event) => setDrafts((current) => ({ ...current, [key]: { ...draft, unit: event.target.value } }))} /></label>
                  <label className="field"><span>修正理由</span><input value={draft.reason} onChange={(event) => setDrafts((current) => ({ ...current, [key]: { ...draft, reason: event.target.value } }))} /></label>
                </div>
              )}
            </article>
          )
        })}
        {clarificationOnly.map((target) => {
          const key = problemKey(target)
          const draft = drafts[key] ?? initialDraft(target)
          return <article className="card review-problem-card" key={`clarification:${key}`}><header><div><strong>{target.original_filename ?? '报价文件'}</strong><span>{fieldLabel(target.field_name)}</span></div><span className="status-pill status-pending">调查待确认</span></header><p>{target.message}</p><div className="review-correction-fields"><label className="field"><span>核对后的原始表达</span><input value={draft.rawValue} onChange={(event) => setDrafts((current) => ({ ...current, [key]: { ...draft, rawValue: event.target.value } }))} /></label><label className="field"><span>标准化值</span><input value={draft.normalizedValue} onChange={(event) => setDrafts((current) => ({ ...current, [key]: { ...draft, normalizedValue: event.target.value } }))} /></label><label className="field"><span>单位</span><input value={draft.unit} onChange={(event) => setDrafts((current) => ({ ...current, [key]: { ...draft, unit: event.target.value } }))} /></label><label className="field"><span>修正理由</span><input value={draft.reason} onChange={(event) => setDrafts((current) => ({ ...current, [key]: { ...draft, reason: event.target.value } }))} /></label></div></article>
        })}
      </div>

      {actionable.length > 0 && data.status !== 'ABANDONED' && <button className="button button-submit" type="button" disabled={!complete || correction.isPending} onClick={submitAll}>{correction.isPending ? '正在提交…' : `统一提交 ${actionable.length} 个字段`}</button>}
      {correction.isError && <div className="form-error" role="alert">{errorMessage(correction.error)}</div>}
    </div>
  )
}
