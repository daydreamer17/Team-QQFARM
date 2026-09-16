import { useMutation, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import type { TaskDetail } from '../api/types'

interface IssuePanelProps {
  task: TaskDetail
  onRefresh: () => void
}

function errorMessage(error: unknown) {
  return error instanceof ApiClientError ? error.message : '问题回答未保存。'
}

export function IssuePanel({ task, onRefresh }: IssuePanelProps) {
  const queryClient = useQueryClient()
  const issue = task.current_issue
  const currency = issue?.answer_schema.currency ?? task.requirement.currency
  const [amount, setAmount] = useState('')

  const answer = useMutation({
    mutationFn: (
      payload:
        | { answer_type: 'CONFIRM_MISSING' }
        | { answer_type: 'SHIPPING_AMOUNT'; amount: string; currency: string },
    ) => {
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

  return (
    <section className="card issue-panel">
      <div>
        <p className="eyebrow">INPUT REQUIRED</p>
        <h2>需要人工确认</h2>
        <p>{issue.question}</p>
      </div>

      <dl className="job-summary">
        <div><dt>供应商</dt><dd>{task.quotes.find((quote) => quote.quote_id === issue.quote_id)?.supplier_id ?? '—'}</dd></div>
        <div><dt>字段</dt><dd>{issue.field_name ?? '—'}</dd></div>
        <div><dt>问题类型</dt><dd>{issue.issue_type}</dd></div>
      </dl>

      {issue.issue_type === 'CONFIRM_MISSING' && (
        <div className="issue-action">
          <p>
            这里只确认“文档没有给出可用于计算的运费金额”，不会把未知运费当作 0。
          </p>
          <button
            className="button button-submit"
            type="button"
            disabled={answer.isPending}
            onClick={() => answer.mutate({ answer_type: 'CONFIRM_MISSING' })}
          >
            {answer.isPending ? '正在保存…' : '确认缺少可计算的运费金额'}
          </button>
        </div>
      )}

      {issue.issue_type === 'SHIPPING_AMOUNT' && (
        <form className="issue-action" onSubmit={submitAmount}>
          <p>
            请输入从供应商补充确认得到的金额。没有得到金额时不要填 0，也不要猜测。
          </p>
          <label>
            运费金额（{currency}）
            <input
              required
              inputMode="decimal"
              pattern="\d+(?:\.\d{1,4})?"
              placeholder="例如 125.00"
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
            />
          </label>
          <button className="button button-submit" type="submit" disabled={answer.isPending}>
            {answer.isPending ? '正在保存…' : '保存金额并继续'}
          </button>
        </form>
      )}

      {answer.isError && <p className="form-error" role="alert">{errorMessage(answer.error)}</p>}
    </section>
  )
}
