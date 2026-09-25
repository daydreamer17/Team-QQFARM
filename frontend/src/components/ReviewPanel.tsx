import { useMutation, useQueries, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useMemo, useState } from 'react'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import type {
  FieldCorrectionInput,
  QuoteField,
  QuoteFieldsResponse,
  ReviewFinding,
  TaskDetail,
  TaskQuote,
} from '../api/types'
import { fieldLabel } from '../lib/presentation'
import { canConfirmReviewField, fieldCorrectionErrorMessage } from '../lib/reviewPanel'
import {
  reviewFindingAction,
  reviewFindingActionMessages,
  reviewFindingCodeLabel,
} from '../lib/reviewMessages'

interface ReviewPanelProps {
  task: TaskDetail
  onRefresh: () => void
}

interface EditorState {
  quote: TaskQuote
  finding: ReviewFinding
  field: QuoteField
  rawValue: string
  normalizedValue: string
  unit: string
  reason: string
  amountField?: QuoteField
  amount?: string
  currency?: string
}

const feeStatusFields = new Set([
  'shipping_fee_status',
  'other_fees_status',
])

const feeAmountFields: Record<string, string> = {
  shipping_fee_status: 'shipping_fee_amount',
  other_fees_status: 'other_fees_amount',
}

const resolvingFeeStatuses = [
  ['FREE', 'Free'],
  ['INCLUDED', 'Included in quotation'],
  ['NOT_APPLICABLE', 'Explicitly not applicable / no such fee'],
  ['KNOWN_AMOUNT', 'A separate fee amount is available'],
] as const

const fieldLabels: Record<string, string> = {
  unit_price: 'Unit Price',
  moq_quantity: 'Minimum order quantity (MOQ)',
  shipping_fee_status: 'Shipping Fee Status',
  shipping_fee_amount: 'Shipping Fee Amount',
  other_fees_status: 'Other Fees Status',
  other_fees_amount: 'Other Fees Amount',
  tax_mode: 'Tax Treatment',
  manufacturer_part_number: 'Manufacturer part number',
  manufacturer_revision: 'Item revision',
  package: 'Package',
  condition: 'Item condition',
  currency: 'Currency',
  packaging_type: 'Packaging type',
  units_per_pack: 'Units per Pack',
  order_multiple_units: 'Order Multiple',
  lead_time_days: 'Lead Time (Days)',
  day_basis: 'Day Basis',
  delivery_semantics: 'Delivery semantics',
  start_event: 'Lead-time Start Event',
  start_date: 'Lead-time start date',
  delivery_date: 'Explicit delivery date',
  payment_terms: 'Payment terms',
  quote_date: 'Quotation Date',
  valid_until: 'Valid Until',
}

function findingDisplayMessage(finding: ReviewFinding) {
  const firstKnownCode = finding.codes.find((code) => reviewFindingActionMessages[code])
  return firstKnownCode ? reviewFindingAction(firstKnownCode, finding.message) : finding.message
}

function blockingFindingSummary(
  details: Record<string, unknown>,
  quotes: TaskQuote[],
) {
  const groups = details.blocking_findings
  if (!groups || typeof groups !== 'object' || Array.isArray(groups)) return ''
  const filenames = new Map(
    quotes.map((quote) => [quote.quote_id, quote.original_filename]),
  )

  return Object.entries(groups)
    .flatMap(([quoteId, group]) =>
      (Array.isArray(group) ? group : []).map((item) => ({ quoteId, item })),
    )
    .map(({ quoteId, item }) => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) return null
      const finding = item as Record<string, unknown>
      const fieldName =
        typeof finding.field_name === 'string' ? finding.field_name : 'Unknown Fields'
      const codes = Array.isArray(finding.codes)
        ? finding.codes.filter((code): code is string => typeof code === 'string')
        : []
      const filename = filenames.get(quoteId) ?? quoteId
      const fieldLabel = fieldLabels[fieldName] ?? fieldName
      const reason = codes.length > 0
        ? codes.map(reviewFindingCodeLabel).join(', ')
        : 'Still did not pass review'
      return `${filename} — “${fieldLabel}”: ${reason}`
    })
    .filter((item): item is string => item !== null)
    .join('; ')
}

function errorMessage(error: unknown, quotes: TaskQuote[] = []) {
  if (
    error instanceof ApiClientError &&
    error.code === 'field_correction_batch_invalid'
  ) {
    return fieldCorrectionErrorMessage(error, quotes)
      ?? `Submission failed: ${blockingFindingSummary(error.details, quotes) || 'The corrections did not pass validation.'}`
  }
  return error instanceof ApiClientError ? error.message : 'Field correction failed.'
}

function editableValue(value: unknown) {
  if (value === null || value === undefined) return ''
  return String(value)
}

function displayFieldValue(value: unknown) {
  if (value === null || value === undefined || value === '') return 'Not provided'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function preserveValueType(value: string, original: unknown) {
  if (typeof original === 'boolean') return value.trim().toLowerCase() === 'true'
  if (typeof original === 'number') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : value
  }
  return value
}

function actionableFindings(response: QuoteFieldsResponse) {
  return response.review_findings.filter(
    (finding) =>
      !finding.resolved &&
      (finding.decision === 'REVIEW_REQUIRED' ||
        finding.decision === 'REJECTED' ||
        finding.decision === 'WARNING'),
  )
}

function groupFindingsByField(findings: ReviewFinding[]) {
  const groups = new Map<string, ReviewFinding[]>()
  findings.forEach((finding) => {
    groups.set(finding.field_name, [...(groups.get(finding.field_name) ?? []), finding])
  })
  return Array.from(groups.values())
}

function isDeferredShippingFinding(
  finding: ReviewFinding,
  response: QuoteFieldsResponse,
) {
  if (finding.field_name !== 'shipping_fee_status') return false
  const field = response.fields.find((item) => item.field_name === finding.field_name)
  return field?.normalized_value === null ||
    field?.normalized_value === undefined ||
    field?.normalized_value === 'UNKNOWN'
}

export function ReviewPanel({ task, onRefresh }: ReviewPanelProps) {
  const queryClient = useQueryClient()
  const quotes = useMemo(() => task.quotes ?? [], [task.quotes])
  const [editor, setEditor] = useState<EditorState | null>(null)
  const [drafts, setDrafts] = useState<Record<string, EditorState>>({})
  const fieldQueries = useQueries({
    queries: quotes.map((quote) => ({
      queryKey: ['tasks', task.task_id, 'quotes', quote.quote_id, 'fields'],
      queryFn: () => api.getQuoteFields(task.task_id, quote.quote_id),
    })),
  })

  const reviewRows = useMemo(
    () =>
      quotes.map((quote, index) => ({
        quote,
        query: fieldQueries[index],
        findings: fieldQueries[index]?.data
          ? actionableFindings(fieldQueries[index].data)
          : [],
      })),
    [fieldQueries, quotes],
  )
  const usableDrafts = useMemo(() => Object.fromEntries(Object.entries(drafts).filter(([key, state]) => {
    const row = reviewRows.find(({ quote }) => quote.quote_id === state.quote.quote_id)
    const latest = row?.query.data?.fields.find((field) => field.field_name === state.field.field_name)
    return Boolean(
      latest && latest.field_version === state.field.field_version &&
      !(feeStatusFields.has(latest.field_name) && state.normalizedValue === 'UNKNOWN') &&
      key === state.quote.quote_id + ':' + state.field.field_name,
    )
  })), [drafts, reviewRows])

  const correction = useMutation({
    mutationFn: (states: EditorState[]) =>
      api.correctQuoteFields(
        task.task_id,
        task.task_revision,
        states.map<FieldCorrectionInput>((state) => ({
          quoteId: state.quote.quote_id,
          fieldName: state.field.field_name,
          expectedFieldVersion: state.field.field_version,
          rawValue: state.rawValue.trim(),
          normalizedValue: preserveValueType(
            state.normalizedValue.trim(),
            state.field.normalized_value,
          ),
          unit: state.unit.trim() || null,
          reason: state.reason.trim(),
        })),
        createIdempotencyKey(),
      ),
    onSuccess: async () => {
      setEditor(null)
      setDrafts({})
      await queryClient.invalidateQueries({ queryKey: ['tasks', task.task_id] })
      onRefresh()
    },
  })

  const blockingKeys = useMemo(
    () =>
      Array.from(
        new Set(
          reviewRows.flatMap(({ quote, query, findings }) =>
            findings
              .filter(
                (finding) =>
                  finding.severity === 'BLOCKING' &&
                  !(query.data && isDeferredShippingFinding(finding, query.data)),
              )
              .map((finding) => quote.quote_id + ':' + finding.field_name),
          ),
        ),
      ),
    [reviewRows],
  )
  const stagedBlockingCount = blockingKeys.filter((key) => usableDrafts[key]).length
  const allBlockingStaged =
    blockingKeys.length === 0 || stagedBlockingCount === blockingKeys.length

  function openEditor(
    quote: TaskQuote,
    finding: ReviewFinding,
    response: QuoteFieldsResponse,
  ) {
    const field = response.fields.find((item) => item.field_name === finding.field_name)
    if (!field) return
    const key = quote.quote_id + ':' + field.field_name
    const unresolvedUnknownFee =
      feeStatusFields.has(field.field_name) && field.normalized_value === 'UNKNOWN'
    const amountFieldName = feeAmountFields[field.field_name]
    const amountField = amountFieldName
      ? response.fields.find((item) => item.field_name === amountFieldName)
      : undefined
    setEditor(usableDrafts[key] ?? {
      quote,
      finding,
      field,
      rawValue: unresolvedUnknownFee ? '' : editableValue(field.raw_value),
      normalizedValue: unresolvedUnknownFee
        ? ''
        : editableValue(field.normalized_value),
      unit: field.unit ?? '',
      reason: 'Corrected after human review of the source quotation',
      amountField,
      amount: editableValue(amountField?.normalized_value),
      currency: String(response.fields.find((item) => item.field_name === 'currency')?.normalized_value ?? task.requirement.currency),
    })
    correction.reset()
  }

  function submitCorrection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!editor) return
    const key = editor.quote.quote_id + ':' + editor.field.field_name
    setDrafts((current) => {
      const next = { ...current, [key]: editor }
      if (editor.amountField && editor.normalizedValue === 'KNOWN_AMOUNT') {
        next[editor.quote.quote_id + ':' + editor.amountField.field_name] = {
          quote: editor.quote, finding: editor.finding, field: editor.amountField,
          rawValue: `${editor.currency} ${editor.amount}`,
          normalizedValue: editor.amount?.trim() ?? '', unit: editor.currency ?? '',
          reason: editor.reason,
        }
      }
      return next
    })
    setEditor(null)
  }

  function confirmCurrentValue(
    quote: TaskQuote,
    finding: ReviewFinding,
    response: QuoteFieldsResponse,
  ) {
    const field = response.fields.find((item) => item.field_name === finding.field_name)
    if (
      !field || field.raw_value === null || field.raw_value === undefined ||
      field.normalized_value === null || field.normalized_value === undefined
    ) return
    const key = quote.quote_id + ':' + field.field_name
    const amountName = feeAmountFields[field.field_name]
    const amount = amountName
      ? response.fields.find((item) => item.field_name === amountName)
      : undefined
    setDrafts((current) => ({
      ...current,
      [key]: {
        quote,
        finding,
        field,
        rawValue: editableValue(field.raw_value),
        normalizedValue: editableValue(field.normalized_value),
        unit: field.unit ?? '',
        reason: 'Human review confirmed the extracted value against the source quotation',
        amountField: amount,
        amount: editableValue(amount?.normalized_value),
      },
    }))
    setEditor(null)
  }

  function submitAllCorrections() {
    const states = Object.values(usableDrafts)
    if (states.length > 0 && allBlockingStaged) correction.mutate(states)
  }

  return (
    <section className="card review-panel">
      <div className="section-heading compact-heading">
        <div>
          <p className="eyebrow">MANUAL REVIEW</p>
          <h2>Fields requiring review and reasons</h2>
        </div>
        <span>Correct each item using the source document or confirmed information. Do not guess missing values.</span>
      </div>

      <div className="review-quote-list">
        {reviewRows.map(({ quote, query, findings }) => (
          <article className="review-quote" key={quote.quote_id}>
            <header>
              <div>
                <strong>{quote.supplier_id}</strong>
                <span>{quote.original_filename}</span>
              </div>
              <span className="status-pill">
                {query.data?.review_status ?? (query.isPending ? 'Loading' : 'Not reviewed')}
              </span>
            </header>

            {query.isError && <p className="form-error">{errorMessage(query.error)}</p>}
            {query.data && findings.length === 0 && (
              <p className="review-empty">The saved review has no unresolved findings.</p>
            )}
            {query.data && findings.length > 0 && (
              <ul className="review-finding-list">
                {groupFindingsByField(findings).map((findingGroup) => {
                  const finding = findingGroup[0]
                  const key = quote.quote_id + ':' + finding.field_name
                  const field = query.data.fields.find(
                    (item) => item.field_name === finding.field_name,
                  )
                  const staged = usableDrafts[key]
                  const isEditing = editor?.quote.quote_id === quote.quote_id &&
                    editor.field.field_name === finding.field_name
                  const canConfirm = canConfirmReviewField(field)
                  const severity = findingGroup.some((item) => item.severity === 'BLOCKING')
                    ? 'BLOCKING'
                    : findingGroup.some((item) => item.severity === 'WARNING')
                      ? 'WARNING'
                      : finding.severity
                  const reasonCodes = Array.from(new Set(findingGroup.flatMap((item) => item.codes)))
                  const messages = Array.from(new Set(findingGroup.map(findingDisplayMessage)))

                  return (
                    <li className={isEditing ? 'review-finding review-finding-editing' : 'review-finding'} key={finding.field_name}>
                      <div className="review-finding-title">
                        <strong>{fieldLabels[finding.field_name] ?? fieldLabel(finding.field_name)}</strong>
                        <span className={'review-severity review-severity-' + severity.toLowerCase()}>
                          {severity === 'BLOCKING' ? 'Must resolve' : severity === 'WARNING' ? 'Review recommended' : 'Information'}
                        </span>
                        {staged && <span className="review-staged">Staged</span>}
                      </div>

                      <div className="review-current-values" aria-label="Current field values">
                        <div>
                          <span>Current source value</span>
                          <strong>{displayFieldValue(field?.raw_value)}</strong>
                        </div>
                        <div>
                          <span>Current normalised value</span>
                          <strong>
                            {displayFieldValue(field?.normalized_value)}
                            {field?.unit ? ` ${field.unit}` : ''}
                          </strong>
                        </div>
                      </div>

                      <div className="review-finding-reason">
                        <span>Reason for review</span>
                        <strong>{reasonCodes.map(reviewFindingCodeLabel).join(' / ')}</strong>
                        {messages.map((message) => <p key={message}>{message}</p>)}
                      </div>

                      {staged && (
                        <div className="review-staged-value">
                          Staged as: {displayFieldValue(staged.normalizedValue)}{staged.unit ? ` ${staged.unit}` : ''}
                        </div>
                      )}

                      <div className="review-finding-actions">
                        {canConfirm && (
                        <button
                          className='button button-secondary button-small'
                          type='button'
                          onClick={() => confirmCurrentValue(quote, finding, query.data)}
                        >
                          Confirm current value
                        </button>
                        )}
                        <button
                          className="button button-secondary button-small"
                          type="button"
                          disabled={!field}
                          onClick={() => openEditor(quote, finding, query.data)}
                        >
                          {staged ? 'Edit staged value' : 'Review and correct field'}
                        </button>
                      </div>

                      {isEditing && editor && (
                        <form className="review-editor review-editor-inline" onSubmit={submitCorrection}>
                          <div className="section-heading compact-heading">
                            <div>
                              <p className="eyebrow">FIELD CORRECTION</p>
                              <h3>Review: {fieldLabels[editor.field.field_name] ?? editor.field.field_name}</h3>
                            </div>
                            <button className="button button-secondary button-small" type="button" onClick={() => setEditor(null)}>
                              Cancel
                            </button>
                          </div>
                          <p className="review-editor-warning">
                            Changes are only staged here. Enter values from the original quotation or supplier confirmation; do not guess.
                          </p>
                          <div className="review-editor-grid">
                            <label>
                              Source text or manual confirmation basis
                              <input
                                required
                                placeholder={
                                  feeStatusFields.has(editor.field.field_name)
                                    ? 'For example: supplier confirms free shipping'
                                    : undefined
                                }
                                value={editor.rawValue}
                                onChange={(event) => setEditor({ ...editor, rawValue: event.target.value })}
                              />
                            </label>
                            <label>
                              Normalised value
                              {feeStatusFields.has(editor.field.field_name) ? (
                                <>
                                  <select
                                    required
                                    value={editor.normalizedValue}
                                    onChange={(event) => setEditor({ ...editor, normalizedValue: event.target.value })}
                                  >
                                    <option value="">Select the confirmed actual status</option>
                                    {resolvingFeeStatuses.map(([value, label]) => (
                                        <option key={value} value={value}>{value} — {label}</option>
                                      ))}
                                  </select>
                                  <small>
                                    When a specific amount has been confirmed, select KNOWN_AMOUNT and enter the amount; save both together.
                                  </small>
                                </>
                              ) : (
                                <input
                                  required
                                  value={editor.normalizedValue}
                                  onChange={(event) => setEditor({ ...editor, normalizedValue: event.target.value })}
                                />
                              )}
                            </label>
                            {feeStatusFields.has(editor.field.field_name) && editor.normalizedValue === 'KNOWN_AMOUNT' && (
                              <label>
                                Confirmed fee amount ({editor.currency})
                                <input required inputMode="decimal" pattern="\d+(?:\.\d{1,4})?"
                                  value={editor.amount ?? ''}
                                  onChange={(event) => setEditor({ ...editor, amount: event.target.value })} />
                              </label>
                            )}
                            <label>
                              Unit (leave blank if not applicable)
                              <input value={editor.unit} onChange={(event) => setEditor({ ...editor, unit: event.target.value })} />
                            </label>
                            <label>
                              Reason for correction
                              <input
                                required
                                minLength={3}
                                value={editor.reason}
                                onChange={(event) => setEditor({ ...editor, reason: event.target.value })}
                              />
                            </label>
                          </div>
                          <button className="button button-submit" type="submit">Add to submission list</button>
                        </form>
                      )}
                    </li>
                  )
                })}
              </ul>
            )}
          </article>
        ))}
      </div>

      <div className="review-batch-actions">
        <div>
          <strong>Batch corrections</strong>
          <p>
            {stagedBlockingCount} of {blockingKeys.length} blocking fields staged.
            Completing all fields creates only one revision and one job.
          </p>
        </div>
        <button
          className="button button-submit"
          type="button"
          disabled={
            correction.isPending ||
            Object.keys(usableDrafts).length === 0 ||
            !allBlockingStaged
          }
          onClick={submitAllCorrections}
        >
          {correction.isPending ? 'Submitting all corrections…' : 'Submit all corrections and continue'}
        </button>
      </div>
      {correction.isError && (
        <p className="form-error" role="alert">{errorMessage(correction.error, quotes)}</p>
      )}
    </section>
  )
}
