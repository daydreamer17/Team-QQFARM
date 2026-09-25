import { useMutation, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import type { IssueAnswer, TaskDetail } from '../api/types'
import { controlLabel, fieldLabel, issueQuestionText, policyStatusLabel } from '../lib/presentation'

interface IssuePanelProps {
  task: TaskDetail
  onRefresh: () => void
}

function errorMessage(error: unknown) {
  return error instanceof ApiClientError ? error.message : 'The response was not saved.'
}

function issueTypeLabel(issueType: string) {
  const labels: Record<string, string> = {
    POLICY_EVIDENCE_REVIEW: 'Policy evidence retrieval requires attention',
    CONFIRM_MISSING: 'Confirm information missing from the source quotation',
    SHIPPING_AMOUNT: 'Enter confirmed shipping fee',
    BATCH_FIELD_REVIEW: 'Review quotation fields together',
    PAYMENT_INFORMATION: 'Enter payment-term start information',
  }
  return labels[issueType] ?? 'Human confirmation required'
}

export function IssuePanel({ task, onRefresh }: IssuePanelProps) {
  const queryClient = useQueryClient()
  const issue = task.current_issue
  const currency = issue?.answer_schema.currency ?? task.requirement.currency
  const [amount, setAmount] = useState('')
  const [paymentNote, setPaymentNote] = useState('')
  const [paymentSourceType, setPaymentSourceType] = useState<'SUPPLIER_CONFIRMATION' | 'DOCUMENT_CLARIFICATION' | 'USER_INPUT'>('SUPPLIER_CONFIRMATION')

  const answer = useMutation({
    mutationFn: (payload: IssueAnswer) => {
      if (!issue) throw new Error('Current issue is missing.')
      return api.answerIssue(
        task.task_id,
        issue.issue_id,
        task.task_revision,
        payload,
        createIdempotencyKey(),
      )
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['tasks', task.task_id] })
      onRefresh()
    },
  })

  if (!issue) return null

  function submitAmount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!/^\d+(?:\.\d{1,4})?$/.test(amount.trim())) return
    answer.mutate({
      answer_type: 'SHIPPING_AMOUNT',
      amount: amount.trim(),
      currency,
    })
  }

  function submitPaymentInformation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!paymentNote.trim()) return
    answer.mutate({
      answer_type: 'PAYMENT_INFORMATION',
      payment_start_event: 'INVOICE_DATE',
      note: paymentNote.trim(),
      source_type: paymentSourceType,
      source_refs: [],
    })
  }

  return (
    <section className="card issue-panel">
      <div>
        <p className="eyebrow">INPUT REQUIRED</p>
        <h2>Human confirmation required</h2>
        <p>{issueQuestionText(issue.question, issue.issue_type)}</p>
      </div>

      <dl className="job-summary">
        <div><dt>Supplier</dt><dd>{task.quotes.find((quote) => quote.quote_id === issue.quote_id)?.supplier_id ?? '—'}</dd></div>
        <div><dt>Action Required</dt><dd>{issue.field_name ? fieldLabel(issue.field_name) : 'Policy Evidence'}</dd></div>
        <div><dt>Action item</dt><dd>{issueTypeLabel(issue.issue_type)}</dd></div>
      </dl>

      {issue.issue_type === 'POLICY_EVIDENCE_REVIEW' && (
        <div className="issue-action policy-issue-action">
          <p>
            Retry only after a network, model-service or temporary-index fault has been resolved. If the policy content or index version changed, create a new task and bind the new policy version.
          </p>
          <div className="policy-issue-statuses">
            {Object.entries(issue.answer_schema.retrieval_statuses ?? {}).map(([controlCode, status]) => (
              <div key={controlCode}>
                <strong>{controlLabel(controlCode)}</strong>
                <span className={`policy-retrieval-status policy-status-${String(status).toLowerCase().replace('_', '-')}`}>
                  {policyStatusLabel(String(status))}
                </span>
              </div>
            ))}
          </div>
          <button
            className="button button-submit"
            type="button"
            disabled={answer.isPending}
            onClick={() => answer.mutate({ answer_type: 'RETRY_POLICY_RETRIEVAL' })}
          >
            {answer.isPending ? 'Resuming analysis…' : 'Retrieve again after repair'}
          </button>
          <small>This task remains bound to its original policy and index versions.</small>
        </div>
      )}

      {issue.issue_type === 'CONFIRM_MISSING' && (
        <div className="issue-action">
          <p>
            This confirms only that the document provides no calculable shipping amount. Unknown shipping is never treated as zero.
          </p>
          <button
            className="button button-submit"
            type="button"
            disabled={answer.isPending}
            onClick={() => answer.mutate({ answer_type: 'CONFIRM_MISSING' })}
          >
            {answer.isPending ? 'Saving…' : 'Confirm missing calculable shipping amount'}
          </button>
        </div>
      )}

      {issue.issue_type === 'SHIPPING_AMOUNT' && (
        <form className="issue-action" onSubmit={submitAmount}>
          <p>
            Enter only an amount confirmed by the supplier. Do not enter zero or guess when no amount was provided.
          </p>
          <label>
            Shipping fee amount ({currency})
            <input
              required
              inputMode="decimal"
              pattern="\d+(?:\.\d{1,4})?"
              placeholder="For example, 125.00"
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
            />
          </label>
          <button className="button button-submit" type="submit" disabled={answer.isPending}>
            {answer.isPending ? 'Saving…' : 'Save amount and continue'}
          </button>
        </form>
      )}

      {issue.issue_type === 'PAYMENT_INFORMATION' && (
        <form className="issue-action" onSubmit={submitPaymentInformation}>
          <p>Enter start information from the quotation or supplier confirmation. This response is audited separately and does not rewrite the original payment terms.</p>
          <label><span>Payment-term start event</span><select value="INVOICE_DATE" disabled><option value="INVOICE_DATE">Invoice date</option></select></label>
          <label><span>Information source</span><select value={paymentSourceType} onChange={(event) => setPaymentSourceType(event.target.value as typeof paymentSourceType)}><option value="SUPPLIER_CONFIRMATION">Supplier confirmation</option><option value="DOCUMENT_CLARIFICATION">Document clarification</option><option value="USER_INPUT">Buyer input</option></select></label>
          <label><span>Note</span><textarea required maxLength={1000} value={paymentNote} onChange={(event) => setPaymentNote(event.target.value)} placeholder="For example: supplier email confirms Net 45 from invoice date" /></label>
          <button className="button button-submit" type="submit" disabled={answer.isPending || !paymentNote.trim()}>{answer.isPending ? 'Saving…' : 'Save and continue analysis'}</button>
        </form>
      )}

      {answer.isError && <p className="form-error" role="alert">{errorMessage(answer.error)}</p>}
    </section>
  )
}
