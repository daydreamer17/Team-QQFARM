import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { useLocation, useParams } from 'react-router-dom'
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
  NEW: 'New',
  REFURBISHED: 'Refurbished',
  USED: 'Used',
  KNOWN_AMOUNT: 'Known amount',
  FREE: 'Free',
  INCLUDED: 'Included in quotation',
  NOT_APPLICABLE: 'Not Applicable',
  UNKNOWN: 'Unknown; follow-up required',
  CALENDAR_DAYS: 'Calendar Days',
  BUSINESS_DAYS: 'Business Days',
  ARRIVAL: 'Arrival',
  SHIPMENT: 'Shipment',
  ORDER_DATE: 'Order date',
  PAYMENT_RECEIPT: 'Payment Receipt',
  EXCLUDED: 'Excluded',
  piece: 'pieces',
  tray: 'trays',
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
  if (definition?.value_type.toLowerCase().includes('date')) return String(value).slice(0, 10)
  return String(value)
}

function errorMessage(error: unknown) {
  if (error instanceof ApiClientError) {
    if (error.code === 'task_revision_conflict') {
      return 'Review data has changed and the page has been synchronised. Review it before submitting again.'
    }
    if (error.code === 'field_correction_batch_invalid') {
      const errors = Array.isArray(error.details.errors) ? error.details.errors : []
      const stale = errors.some((item) => (
        item && typeof item === 'object' && 'code' in item && item.code === 'field_version_conflict'
      ))
      return stale
        ? 'The field schema revision has changed and the page has been synchronised. Review the latest values before submitting again.'
        : 'Some confirmed values did not pass save validation. Check the format shown on each card; if submission still fails, refresh and try again.'
    }
    if (error.code === 'field_correction_invalid') {
      return 'This confirmed value cannot be saved. Check its format on the card, or select “Not yet known” to keep the item open.'
    }
    if (error.code === 'extraction_batch_stale') {
      return 'The quotation extraction result has changed and the page now shows the latest data. Review it before submitting again.'
    }
    if (error.code === 'extraction_batch_missing') {
      return 'The current quotation has no usable extraction result. Complete quotation extraction first.'
    }
    if (error.status === 500) {
      return `The server failed while saving action items, so this attempt was not submitted. ${error.requestId ? `Request ID: ${error.requestId}. ` : ''}Refresh and verify the current revision. If it still fails, provide the request ID with the API logs.`
    }
    return error.message
  }
  return 'Failed to load action items.'
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
  if (fieldName === 'tax_mode' && value === 'INCLUDED') return 'Tax included'
  if (fieldName === 'tax_mode' && value === 'EXCLUDED') return 'Tax excluded'
  return optionLabels[value] ?? value
}

function userFieldLabel(fieldName: string, fallback?: string) {
  const label = fieldLabel(fieldName)
  return label === 'Related information' ? (fallback ?? label) : label
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

function valueFormatError(value: string, definition?: QuoteFieldSchemaDefinition): string | null {
  const normalized = value.trim()
  if (!normalized) return 'Enter a confirmed value, or select “Not yet known”.'
  if (definition?.allowed_values?.length && !definition.allowed_values.includes(normalized)) {
    return 'Select one of the standard values provided by the system.'
  }
  const kind = definition?.value_type.toLowerCase() ?? ''
  if (kind.includes('integer') && !/^\d+$/.test(normalized)) {
    return 'Enter a whole number without units or text.'
  }
  if (kind.includes('decimal') && !/^\d+(?:\.\d+)?$/.test(normalized)) {
    return 'Enter a decimal number without a currency symbol or text.'
  }
  if (kind.includes('date')) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(normalized)) return 'Use the YYYY-MM-DD date format.'
    const parsed = new Date(`${normalized}T00:00:00Z`)
    if (Number.isNaN(parsed.valueOf()) || parsed.toISOString().slice(0, 10) !== normalized) {
      return 'Enter a valid date, for example 2026-10-18.'
    }
  }
  return null
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
  const allowedValues = definition?.allowed_values ?? []
  const kind = definition?.value_type.toLowerCase() ?? ''
  const isAmount = definition?.unit_kind === 'currency' || definition?.field_name.endsWith('_amount')
  const inputMode = kind.includes('decimal') ? 'decimal' : kind.includes('integer') ? 'numeric' : 'text'

  if (allowedValues.length > 0) {
    return (
      <select aria-label={`${label} value`} value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">Select an option</option>
        {allowedValues.map((option) => (
          <option key={option} value={option}>{displayOption(option, definition?.field_name)}</option>
        ))}
      </select>
    )
  }

  if (kind.includes('boolean')) {
    return (
      <select aria-label={`${label} value`} value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">Select an option</option>
        <option value="true">Yes</option>
        <option value="false">No</option>
      </select>
    )
  }

  return (
    <div className="review-business-value-control">
      <input
        aria-label={`${label} value`}
        type="text"
        placeholder={kind.includes('date') ? 'YYYY-MM-DD' : undefined}
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
  const location = useLocation()
  const queryClient = useQueryClient()
  const [drafts, setDrafts] = useState<Record<string, DraftValue>>({})
  const [adopted, setAdopted] = useState<Set<string>>(new Set())
  const [deferred, setDeferred] = useState<Set<string>>(new Set())
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
  const reviewRevision = review.data?.task_revision
  const localStateKey = (key: string) => `${reviewRevision ?? 'pending'}:${key}`
  const definitionByName = useMemo(
    () => new Map(schema.data?.fields.map((definition) => [definition.field_name, definition]) ?? []),
    [schema.data],
  )
  const excludedQuoteIds = useMemo(() => {
    const excluded = new Set(task.data?.decision_profile?.preferences.excluded_supplier_ids ?? [])
    return new Set(review.data?.quotes.filter((quote) => excluded.has(quote.supplier_id)).map((quote) => quote.quote_id) ?? [])
  }, [task.data, review.data])
  // Excluded quotes do not block today's recommendation, but unresolved review
  // findings must remain correctable before the quote is included again.
  const reviewProblems = useMemo(() => (review.data?.problems ?? []).map((problem) => ({
    ...problem,
    needs_resolution: problem.needs_resolution || (excludedQuoteIds.has(problem.quote_id)
      && (problem.severity === 'BLOCKING' || problem.decision === 'REJECTED')),
  })), [review.data, excludedQuoteIds])
  const groupedProblems = useMemo(() => groupProblems(reviewProblems), [reviewProblems])
  useEffect(() => {
    if (location.hash === '#excluded-review') {
      document.getElementById('excluded-review')?.scrollIntoView?.({ block: 'start' })
    }
  }, [location.hash, groupedProblems])
  const actionable = useMemo(() => {
    const unique = new Map<string, CorrectionTarget>()
    const reportedKeys = new Set(review.data?.problems.map(problemKey) ?? [])
    const correctableKeys = new Set(
      review.data?.problems
        .filter((problem) => problem.resolution === 'FIELD_CORRECTION')
        .map(problemKey) ?? [],
    )
    for (const problem of reviewProblems) {
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
    // A fee can become known only together with its amount. Offer the paired
    // field even when the original review reported the status alone.
    for (const target of [...unique.values()]) {
      const amountName = target.field_name === 'shipping_fee_status' ? 'shipping_fee_amount'
        : target.field_name === 'other_fees_status' ? 'other_fees_amount' : null
      if (!amountName) continue
      const status = drafts[`${reviewRevision ?? 'pending'}:${problemKey(target)}`]?.value ?? target.normalized_value
      if (status !== 'KNOWN_AMOUNT') continue
      const key = `${target.quote_id}:${amountName}`
      if (unique.has(key)) continue
      const amount = review.data?.quotes.find((q) => q.quote_id === target.quote_id)?.fields.find((f) => f.field_name === amountName)
      if (!amount) continue
      unique.set(key, { ...target, field_name: amountName, field_version: amount.field_version,
        raw_value: amount.raw_value, normalized_value: amount.normalized_value, unit: amount.unit,
        source_refs: amount.source_refs ?? [], message: 'Enter the confirmed amount; save the fee status and amount together.' })
    }
    return [...unique.values()]
  }, [review.data, task.data, reviewProblems, drafts, reviewRevision])

  const correction = useMutation({
    mutationFn: (items: FieldCorrectionInput[]) =>
      api.correctQuoteFields(taskId, review.data!.task_revision, items, createIdempotencyKey()),
    onSuccess: async () => {
      setDrafts({})
      setAdopted(new Set())
      setDeferred(new Set())
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
    return <section className="card loading-panel">Loading action items…</section>
  }
  if (task.isError || review.isError || schema.isError) {
    return <section className="card error-panel" role="alert">{errorMessage(task.error ?? review.error ?? schema.error)}</section>
  }

  const data = task.data
  const report = review.data
  const targetByKey = new Map(actionable.map((target) => [problemKey(target), target]))
  const groupsByKey = new Map(groupedProblems.map((group) => [group.key, group]))
  const pendingLimitations = groupedProblems.filter((group) => (
    group.problems.some((problem) => problem.resolution === 'ADDITIONAL_INFORMATION_REQUIRED')
  ))
  const pendingLimitationKeys = new Set(pendingLimitations.map((group) => group.key))
  const manualBlockers = groupedProblems.filter((group) => (
    group.needsResolution && !targetByKey.has(group.key) && !pendingLimitationKeys.has(group.key)
  ))
  const recordOnly = groupedProblems.filter((group) => (
    !group.needsResolution && !pendingLimitationKeys.has(group.key)
  ))
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
    const key = problemKey(problem)
    const stateKey = localStateKey(key)
    const draft = drafts[stateKey] ?? { value: initialValue(problem, definition) }
    return !valueFormatError(draft.value, definition)
      && !deferred.has(stateKey)
      && (adopted.has(stateKey) || draft.value !== initialValue(problem, definition))
  })

  function quoteCurrency(target: CorrectionTarget): string | null {
    const quote = report.quotes.find((item) => item.quote_id === target.quote_id)
    const currency = quote?.fields.find((field) => field.field_name === 'currency')?.normalized_value
    return typeof currency === 'string' && currency ? currency : data.requirement.currency || null
  }

  function submitAll() {
    if (!complete || !reviewIsStable) return
    correction.mutate(actionable.map((problem) => {
      const definition = definitionByName.get(problem.field_name)
      const draft = drafts[localStateKey(problemKey(problem))] ?? { value: initialValue(problem, definition) }
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
        reason: 'Corrected after manually reviewing the source quotation or supplier response',
      }
    }))
  }

  return (
    <div className="page-stack review-overview-page">
      <TaskWorkspaceHeader
        taskId={data.task_id}
        scenarioId={data.scenario_id}
        title={data.task_name}
        subtitle={`${data.requirement.required_quantity} ${data.requirement.quantity_unit} · ${pendingCount} pending`}
        status={data.status}
        revision={data.task_revision}
        resultId={data.current_result_id}
        quoteCount={data.quotes.length}
        summaryComplete={data.summary_completed}
        progress={data.progress}
        reviewBlocked={pendingCount > 0 || report.review_pending}
        active="review"
      />

      <section className="card workspace-page-lead" aria-labelledby="review-overview-title">
        <div className="workspace-page-lead-copy">
          <h2 id="review-overview-title">Actions</h2>
          <p>Review decision fields before recalculation.</p>
        </div>
        <dl className="review-overview-stats" aria-label="Review statistics">
          <div><dt>Quotations</dt><dd>{report.quotes.length}</dd></div>
          <div className={pendingCount > 0 ? 'review-stat-pending' : 'review-stat-clear'}><dt>Pending</dt><dd>{pendingCount}</dd></div>
          {pendingLimitations.length > 0 && <div className="review-stat-limitation"><dt>Limitations</dt><dd>{pendingLimitations.length}</dd></div>}
          <div><dt>Recorded</dt><dd>{recordOnly.length}</dd></div>
        </dl>
      </section>

      {waitingForReview && <div className="run-notice">Some quotations are still under review. Complete them before submitting the batch; this page will update automatically.</div>}
      {!report.review_pending && data.task_revision !== report.task_revision && (
        <div className="run-notice">Synchronising the latest review data…</div>
      )}
      {!report.review_pending && pendingCount === 0 && pendingLimitations.length === 0 && (
        <section className="card audit-empty">No pending fields.</section>
      )}

      {[false, true].map((excluded) => {
        const targets = actionable.filter((target) => excludedQuoteIds.has(target.quote_id) === excluded)
        if (!targets.length) return null
        return <section className="review-overview-group" key={String(excluded)} id={excluded ? 'excluded-review' : 'current-review'}>
          <h3>{excluded ? 'Excluded' : 'Current'}</h3>
          {excluded && <p className="muted">The following issues do not block the current recommendation. Saving the review will not automatically re-include the supplier; request inclusion again in the decision assistant after completing the review.</p>}
        <div className="review-problem-grid">
          {targets.map((target) => {
            const key = problemKey(target)
            const stateKey = localStateKey(key)
            const group = groupsByKey.get(key)
            const definition = definitionByName.get(target.field_name)
            const label = userFieldLabel(target.field_name, definition?.label)
            const draft = drafts[stateKey] ?? { value: initialValue(target, definition) }
            const evidenceTexts = quotedTexts(target.source_refs, target.raw_value)
            const changed = draft.value !== initialValue(target, definition)
            const isAdopted = adopted.has(stateKey)
            const isDeferred = deferred.has(stateKey)
            const formatError = isDeferred ? null : valueFormatError(draft.value, definition)
            const canAdopt = !formatError && draft.value.trim() !== 'UNKNOWN'
            const problemMessages = [...new Set((group?.problems ?? []).map(reviewProblemMessage))]
            return (
              <article className="card review-problem-card review-action-card" key={key}>
                <header>
                  <div><strong>{target.original_filename ?? 'Quotation Document'}</strong><span>{label}</span></div>
                  <span className={`status-pill ${isDeferred ? 'status-pending' : changed || isAdopted ? 'status-ready' : 'status-pending'}`}>
                    {isDeferred ? 'Pending' : changed ? 'Revised' : isAdopted ? 'Accepted' : 'Review'}
                  </span>
                </header>
                {group && group.problems.length > 1 && (
                  <small className="review-merged-note">{group.problems.length} related rules have been combined; enter the value once.</small>
                )}
                {problemMessages.length > 0 && (
                  <div className="quote-field-note">
                    {problemMessages.map((message) => <p key={message}>{message}</p>)}
                    <p>Next: review the source, accept the current value, edit the field or leave it pending.</p>
                  </div>
                )}
                <label className="field review-business-value">
                  <span>Value</span>
                  <BusinessValueInput
                    definition={definition}
                    label={label}
                    value={draft.value}
                    currency={quoteCurrency(target)}
                    onChange={(value) => {
                      setDrafts((current) => ({ ...current, [stateKey]: { value } }))
                      setAdopted((current) => { const next = new Set(current); next.delete(stateKey); return next })
                      setDeferred((current) => { const next = new Set(current); next.delete(stateKey); return next })
                    }}
                  />
                  {formatError && <small className="field-error-text">{formatError}</small>}
                </label>
                <div className="review-card-actions">
                  {!changed && !isAdopted && !isDeferred && canAdopt && (
                    <button className="button button-secondary" type="button" onClick={() => setAdopted((current) => new Set([...current, stateKey]))}>
                      Confirm
                    </button>
                  )}
                  {!isDeferred && (
                    <button className="button button-secondary" type="button" onClick={() => {
                      setDeferred((current) => new Set([...current, stateKey]))
                      setAdopted((current) => { const next = new Set(current); next.delete(stateKey); return next })
                    }}>Defer</button>
                  )}
                  {isDeferred && <button className="button button-secondary" type="button" onClick={() => setDeferred((current) => { const next = new Set(current); next.delete(stateKey); return next })}>Continue</button>}
                </div>
                {evidenceTexts.length > 0 && (
                  <details className="review-rule-details">
                    <summary>Source</summary>
                    {evidenceTexts.map((text) => <blockquote key={text}>{text}</blockquote>)}
                  </details>
                )}
              </article>
            )
          })}
        </div>
        </section>
      })}

      {manualBlockers.length > 0 && (
        <section className="card review-manual-blockers">
          <h3>Missing information</h3>
          {manualBlockers.map((group) => {
            const problem = group.problems[0]
            const message = problem.resolution === 'ADDITIONAL_INFORMATION_REQUIRED'
              ? 'The original quotation value is retained. Additional conversion or evaluation information is required; changing the quotation value cannot resolve this item.'
              : 'This field cannot be corrected online. Upload the quotation again or contact an administrator.'
            return (
              <p key={group.key}>{problem.original_filename ?? 'Quotation Document'} · {userFieldLabel(group.fieldName)}: {message}</p>
            )
          })}
        </section>
      )}

      {pendingLimitations.length > 0 && (
        <section className="card review-pending-limitations">
          <h3>Calculation pending</h3>
          {pendingLimitations.map((group) => {
            const problem = group.problems[0]
            const isBusinessDays = problem.codes.includes('DAY_BASIS_UNSUPPORTED')
            return (
              <div key={group.key}>
                <strong>{problem.original_filename ?? 'Quotation Document'} · {userFieldLabel(group.fieldName)}</strong>
                <p>{isBusinessDays
                  ? '“Business days” from the source quotation has been retained and will not be converted to calendar days. The quotation does not need revision. Because no business calendar is configured, this supplier remains PENDING. Configure a business calendar or obtain an explicit arrival date to resolve it.'
                  : 'The original quotation value has been retained and should not be changed merely to continue the analysis. The required conversion or evaluation capability is unavailable, so this supplier remains PENDING.'}</p>
              </div>
            )
          })}
        </section>
      )}

      {recordOnly.length > 0 && (
        <details className="card review-records">
          <summary>Records ({recordOnly.length})</summary>
          <div>
            {recordOnly.map((group) => (
              <p key={group.key}>
                <strong>{group.problems[0].original_filename ?? 'Quotation Document'} · {userFieldLabel(group.fieldName)}</strong>
                <span>{reviewProblemMessage(group.problems[0])}</span>
              </p>
            ))}
          </div>
        </details>
      )}

      {actionable.length > 0 && data.status !== 'ABANDONED' && (
        <button className="button button-submit" type="button" disabled={!complete || !reviewIsStable || correction.isPending} onClick={submitAll}>
          {correction.isPending
            ? 'Submitting…'
            : waitingForReview
              ? 'Waiting…'
              : !versionsAligned
                ? 'Synchronising…'
                : 'Save & recalculate'}
        </button>
      )}
      {actionable.length > 0 && !complete && (
        <div className="run-notice">Confirm the current value or edit each field. Items left pending will not trigger a new calculation.</div>
      )}
      {correction.isError && <div className="form-error" role="alert">{errorMessage(correction.error)}</div>}
    </div>
  )
}
