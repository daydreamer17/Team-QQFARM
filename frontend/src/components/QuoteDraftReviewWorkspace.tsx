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
  UPLOADED: 'Uploaded',
  PROCESSING: 'Processing',
  REVIEW_REQUIRED: 'Action required',
  READY_TO_SUBMIT: 'Review complete',
  SUBMITTED: 'Submitted',
  FAILED: 'Failed',
  STALE: 'Expired',
  DISCARDED: 'Abandoned',
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
  piece: 'Piece',
  tray: 'Tray',
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
  return text || 'Not provided'
}

function findingMessage(finding: QuoteDraftResponse['review_findings'][number]): string {
  const code = finding.codes.find((item) => reviewFindingActionMessages[item])
  return code ? reviewFindingAction(code, finding.message) : finding.message
}

function apiErrorMessage(error: unknown): string {
  if (!(error instanceof ApiClientError)) return error instanceof Error ? error.message : 'Operation failed. Try again later.'
  if (error.code === 'quote_revision_unchanged') return 'No changes were detected, so a new revision is not required. Update a field before submitting again.'
  if (error.status === 409) {
    const serverVersion = error.details.actual ?? error.details.actual_draft_revision ?? error.details.actual_field_version
    const suffix = typeof serverVersion === 'number' || typeof serverVersion === 'string'
      ? `Current server revision: ${serverVersion}. `
      : ''
    return `The draft or field schema revision has changed. ${suffix}Your current input remains on the page; review the latest revision before submitting.`
  }
  if (error.code === 'quote_review_schema_conflict' || error.code === 'quote_draft_review_stale') return 'Field rules have changed. Refresh the page and review the latest rules before confirming.'
  if (error.code === 'quote_draft_not_reviewable') return 'This draft can no longer be revised. Refresh the page to view its latest status.'
  if (error.status === 422) return error.message || 'Some fields did not pass server validation. Review the details below.'
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
      const nextAction = 'next_action' in entry && typeof entry.next_action === 'string' ? entry.next_action : ''
      if (message) issues.push({ code, fieldNames: fields, groupId, message: `${message}${nextAction ? ` ${nextAction}` : ''}` })
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
      ? `${current.fieldNames.join(', ')}: ${current.message}`
      : current.message
  )))]
}

function fieldInputMode(definition: QuoteFieldSchemaDefinition): 'decimal' | 'numeric' | 'text' {
  const kind = definition.value_type.toLowerCase()
  if (kind.includes('decimal')) return 'decimal'
  if (kind.includes('integer')) return 'numeric'
  return 'text'
}

function EvidenceList({ field }: { field: QuoteField }) {
  if (field.evidence.length === 0) {
    return <p className="quote-field-no-evidence">No document evidence is available. Confirm the information source before entering a value.</p>
  }
  return (
    <div className="quote-field-evidence">
      {field.evidence.map((evidence, index) => (
        <blockquote key={`${evidence.source_id ?? 'source'}:${index}`}>
          <p>{evidence.quoted_text || 'This source has no displayable text excerpt.'}</p>
          <footer>
            {evidence.page_number ? `Page ${evidence.page_number}` : evidence.row_number ? `Row ${evidence.row_number}` : 'Document source'}
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
  adopted,
  onAdopt,
  disabled,
  canAdopt,
  onChange,
}: {
  definition: QuoteFieldSchemaDefinition
  field: QuoteField
  value: string
  errors: string[]
  findings: QuoteDraftResponse['review_findings']
  adopted: boolean
  onAdopt: () => void
  disabled: boolean
  canAdopt: boolean
  onChange: (value: string) => void
}) {
  const unresolved = findings.filter((finding) => (
    !finding.resolved &&
    finding.decision !== 'PASS' &&
    !finding.accepted_for_calculation
  ))
  const hasValue = value.trim().length > 0
  const changed = value !== quoteValueAsText(field.normalized_value)
  const handled = adopted || changed
  const confirmed = field.review_state === 'CONFIRMED' || field.review_state === 'CORRECTED'
  const hasConflict = field.validation_status === 'CONFLICT' && !handled
  const needsAttention = errors.length > 0 || hasConflict
  const hasInterpretationDoubt = hasConflict || canAdopt
  const requiresResolvedFeeStatus = definition.field_name === 'shipping_fee_status' || definition.field_name === 'other_fees_status'
  const selectableOptions = definition.allowed_values ?? []
  const displayedValue = value
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
            {hasInterpretationDoubt && !handled ? 'Manual review required' : errors.length > 0 ? (hasValue ? 'Resolve before submission' : 'Missing; draft can still be saved') : 'Review required'}
            </span>
          </div>
        )}
      </header>

      {handled && <p className="quote-field-note">{adopted ? 'Current value reviewed; save to confirm.' : 'Revised; save to confirm.'}</p>}
      {!handled && confirmed && <p className="quote-field-note">Manually confirmed{errors.length > 0 ? '; the data issues below still need attention.' : '.'}</p>}
      {!handled && unresolved.length > 0 && (
        <div className="quote-field-note">
          {unresolved.map((finding) => <p key={finding.finding_id}>{findingMessage(finding)}</p>)}
        </div>
      )}

      {hasValue && !handled && hasInterpretationDoubt && (
        <button type="button" className="button button-secondary" disabled={disabled} onClick={onAdopt}>Confirm current value</button>
      )}
      {hasValue && (hasConflict || unresolved.length > 0) && (
        <button type="button" className="button button-secondary" disabled={disabled} onClick={() => onChange(requiresResolvedFeeStatus ? 'UNKNOWN' : '')}>Mark as unknown for now</button>
      )}

      <label className="field quote-review-input">
        {definition.allowed_values && definition.allowed_values.length > 0 ? (
          <select
            disabled={disabled}
            aria-label={definition.label}
            aria-invalid={errors.length > 0}
            value={displayedValue}
            onChange={(event) => onChange(event.target.value)}
          >
            <option value="" disabled={requiresResolvedFeeStatus}>
              {requiresResolvedFeeStatus ? 'Select a fee status' : 'Not provided / not currently applicable'}
            </option>
            {selectableOptions.map((option) => (
              <option key={option} value={option}>{optionLabels[option] ? `${optionLabels[option]} (${option})` : option}</option>
            ))}
          </select>
        ) : (
          <input
            disabled={disabled}
            aria-label={definition.label}
            aria-invalid={errors.length > 0}
            type="text"
            placeholder={definition.value_type.toLowerCase().includes('date') ? 'YYYY-MM-DD' : undefined}
            inputMode={fieldInputMode(definition)}
            value={value}
            onChange={(event) => onChange(event.target.value)}
          />
        )}
        {errors.map((message) => <small className="field-error-text" key={message}>{message}</small>)}
      </label>

      <details className="quote-field-rules">
        <summary>View source and evidence</summary>
        <p>Source value: {displayValue(field.raw_value)}{field.unit ? ` ${field.unit}` : ''}</p>
        <p>Field: {definition.field_name} · Status: {field.validation_status}</p>
        <EvidenceList field={field} />
        {Boolean(field.review_evidence?.length) && <>
          <p>Other source clues (may include historical prices; verify the applicable version and do not treat them as support for the current value)</p>
          <EvidenceList field={{ ...field, evidence: field.review_evidence! }} />
        </>}
        <p>{definition.normalization_rule}</p>
        {findings.filter((finding) => finding.decision !== 'PASS').map((finding) => (
          <p key={finding.finding_id}>{finding.resolved ? 'Manually resolved: ' : 'Initial pre-check: '}{findingMessage(finding)}</p>
        ))}
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
    for (const issue of draft.review_errors ?? []) {
      if (issue.code !== 'FULL_FIELD_REVIEW_REQUIRED') issue.field_names.forEach((name) => result.add(name))
    }
    return result
  })
  const [otherFieldsOpen, setOtherFieldsOpen] = useState(() => priorityFieldNames.size === 0)
  const [validationIssues, setValidationIssues] = useState<ReturnType<typeof validateQuoteReview>>(() => (
    (draft.review_errors ?? []).filter((issue) => issue.code !== 'FULL_FIELD_REVIEW_REQUIRED').map((issue) => ({
      code: issue.code, fieldNames: issue.field_names, groupId: issue.group_id ?? 'field',
      message: `${issue.message}${issue.next_action ? ` ${issue.next_action}` : ''}`,
    }))
  ))
  const [reviewKey, setReviewKey] = useState<string | null>(null)
  const [submitKey, setSubmitKey] = useState<string | null>(null)
  const [discardKey, setDiscardKey] = useState<string | null>(null)
  const [isDirty, setIsDirty] = useState(false)
  const [adoptedFields, setAdoptedFields] = useState<Set<string>>(new Set())

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
      .filter((field) => field.validation_status === 'CONFLICT' && !adoptedFields.has(field.field_name)
        && (values[field.field_name] ?? '') === quoteValueAsText(field.normalized_value))
      .map((field) => ({
        code: 'FIELD_VALUE_CONFLICT',
        fieldNames: [field.field_name],
        groupId: schema.fields.find((definition) => definition.field_name === field.field_name)?.group_id ?? 'field',
        message: `“${labels.get(field.field_name) ?? field.field_name}” contains conflicting values. Review them and select the correct one.`,
      }))
  }, [draft.fields, schema.fields, values, adoptedFields])
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
  const canSubmitWithoutReview = draft.human_review_complete === true && draft.submission_ready === true && !isDirty

  const finalize = useMutation({
    mutationFn: async (submission: {
      reviewKey: string
      submitKey: string
      actions: ReturnType<typeof buildQuoteReviewActions>
      submitOnly: boolean
      saveOnly?: boolean
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
        if (submission.saveOnly || !reviewedDraft.submission_ready) return reviewedDraft
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
    setAdoptedFields((current) => {
      const next = new Set(current)
      next.delete(fieldName)
      return next
    })
    const nextValues = { ...values, [fieldName]: nextValue }
    setValues(nextValues)
    const nextIssues = validateQuoteReview(draft, schema, nextValues)
    if (nextIssues.some((issue) => issue.fieldNames.some((name) => !priorityFieldNames.has(name)))) {
      setOtherFieldsOpen(true)
    }
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
      const actions = canSubmitWithoutReview ? [] : buildQuoteReviewActions(draft, schema, values, adoptedFields)
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
        message: error instanceof Error ? error.message : 'Unable to create the field-confirmation request. Refresh the page and try again.',
      }])
    }
  }

  const backendMessages = backendIssueMessages(finalize.error ?? discard.error)
  const mutationError = finalize.error ?? discard.error
  const unchangedRevision = mutationError instanceof ApiClientError && mutationError.code === 'quote_revision_unchanged'
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
          <p className="field-error-text">The system did not generate this field. Reparse the quotation.</p>
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
        adopted={adoptedFields.has(definition.field_name)}
        disabled={finalize.isPending}
        canAdopt={(draft.review_errors ?? []).some((issue) => issue.field_names.includes(definition.field_name) && issue.actions?.includes('CONFIRM_VALUE'))}
        onAdopt={() => {
          setAdoptedFields((current) => new Set([...current, definition.field_name]))
          setValidationIssues((current) => current.filter((item) => !item.fieldNames.includes(definition.field_name)))
          setIsDirty(true)
          setReviewKey(null)
          finalize.reset()
        }}
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
          <button className="button button-secondary" type="button" onClick={onPreview}>View source</button>
          <span className={`status-pill draft-status-${draft.status.toLowerCase()}`}>{draftStatusLabels[draft.status] ?? draft.status}</span>
        </div>
      </header>

      {draft.status === 'PROCESSING' && (
        <div className="draft-processing"><i className="activity-spinner" /><div><strong>Parsing and running deterministic pre-checks</strong><span>Complete these items before confirming all fields.</span></div></div>
      )}
      {draft.status === 'FAILED' && <div className="form-error compact-error"><div><strong>Quotation processing failed</strong><p>{draft.error_message}</p></div></div>}
      {draft.status === 'STALE' && <div className="form-error compact-error"><div><strong>Draft is stale</strong><p>The task inputs changed during review. Discard this draft and upload the quotation again.</p></div></div>}
      {(schemaChanged || legacyReview) && <div className="review-schema-warning"><strong>Review rules have changed</strong><p>Review this quotation again using the current rules.</p></div>}

      {draft.fields.length > 0 && (
        <form onSubmit={confirmAndSubmit} noValidate>
          <div className="quote-review-overview">
            {currentProblemFieldNames.size > 0 && <div><strong>{currentProblemFieldNames.size}</strong><span>Action required</span></div>}
            <div><strong>{autoFilledCount}</strong><span>Identified</span></div>
            <div><strong>{optionalEmptyCount}</strong><span>Not provided in quotation</span></div>
          </div>

          {activeValidationIssues.length > 0 && (
            <div className="quote-review-error-summary" role="alert">
              <strong>{activeValidationIssues.length} issues still need attention</strong>
              <p>You may save first. Confirm or correct uncertain extractions; invalid data must be resolved before formal submission.</p>
              <details>
                <summary>View issue list</summary>
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
                <header><div><span>{attentionFields.length}</span><h3>Priority items</h3></div><p>These items were initially missing or uncertain. Review them first.</p></header>
                <div className="draft-field-grid">{attentionFields.map(renderField)}</div>
              </section>
            )}
            <details
              className="quote-review-ready-fields"
              open={otherFieldsOpen}
              onToggle={(event) => setOtherFieldsOpen(event.currentTarget.open)}
            >
              <summary>View other fields ({readyFields.length})</summary>
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
            <button className="button button-secondary" type="button" disabled={finalize.isPending || draft.status === 'PROCESSING'} onClick={() => {
              try {
                const key = reviewKey ?? createIdempotencyKey()
                setReviewKey(key)
                finalize.mutate({ reviewKey: key, submitKey: createIdempotencyKey(),
                  actions: buildQuoteReviewActions(draft, schema, values, adoptedFields), submitOnly: false, saveOnly: true })
              } catch (error) {
                setValidationIssues([{ code: 'REVIEW_ACTION_BUILD_FAILED', fieldNames: [], groupId: 'schema', message: error instanceof Error ? error.message : 'Refresh the field schema revision and try again.' }])
              }
            }}>Save and confirm review</button>
            <button className="button button-submit" type="submit" disabled={finalize.isPending || draft.status === 'PROCESSING' || activeValidationIssues.length > 0}>
              {finalize.isPending ? 'Checking and submitting…' : 'Confirm and submit quotation'}
            </button>
          </div>
          {draft.human_review_complete && !isDirty && <p role="status">Manual review saved. {draft.submission_ready ? 'The quotation can now be submitted.' : 'Some information is still missing or needs correction. Resolve it before submission; unknown values are never treated as zero.'}</p>}
        </form>
      )}

      {unchangedRevision && (
        <div className="quote-no-change-notice" role="status">
          <strong>No update required</strong>
          <p>The quotation content has not changed. The current version has been retained.</p>
        </div>
      )}

      {mutationError && !unchangedRevision && (
        <div className="form-error compact-error" role="alert">
          <div>
            <strong>Operation not completed</strong>
            <p>{apiErrorMessage(mutationError)}</p>
            {backendMessages.length > 0 && <ul>{backendMessages.map((message) => <li key={message}>{message}</li>)}</ul>}
            {mutationError instanceof ApiClientError && mutationError.status === 409 && (
              <button type="button" className="button button-secondary" onClick={onChanged}>Load latest draft and replace this form</button>
            )}
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
          {unchangedRevision ? 'Back to quotation list' : draft.replacement_quote_id ? 'Cancel revision' : 'Discard draft'}
        </button>
      </div>
    </section>
  )
}
