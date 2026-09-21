import { useMutation, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import type { IssueAnswer, TaskDetail } from '../api/types'
import { controlLabel, fieldLabel, policyStatusLabel } from '../lib/presentation'

interface IssuePanelProps {
  task: TaskDetail
  onRefresh: () => void
}

function errorMessage(error: unknown) {
  return error instanceof ApiClientError ? error.message : '问题回答未保存。'
}

function issueTypeLabel(issueType: string) {
  const labels: Record<string, string> = {
    POLICY_EVIDENCE_REVIEW: '制度证据需要重新检索',
    CONFIRM_MISSING: '确认原报价未提供信息',
    SHIPPING_AMOUNT: '补充已确认的运费',
    BATCH_FIELD_REVIEW: '集中核对报价字段',
    PAYMENT_INFORMATION: '补充付款账期起算信息',
  }
  return labels[issueType] ?? '需要人工确认'
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
        <h2>需要人工确认</h2>
        <p>{issue.question}</p>
      </div>

      <dl className="job-summary">
        <div><dt>供应商</dt><dd>{task.quotes.find((quote) => quote.quote_id === issue.quote_id)?.supplier_id ?? '—'}</dd></div>
        <div><dt>需要确认</dt><dd>{issue.field_name ? fieldLabel(issue.field_name) : '制度依据'}</dd></div>
        <div><dt>处理事项</dt><dd>{issueTypeLabel(issue.issue_type)}</dd></div>
      </dl>

      {issue.issue_type === 'POLICY_EVIDENCE_REVIEW' && (
        <div className="issue-action policy-issue-action">
          <p>
            仅在网络、模型服务或临时索引故障已经修复时重试。若制度内容或索引版本发生变化，需要新建任务并重新绑定制度版本。
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
            {answer.isPending ? '正在恢复分析…' : '修复后重新检索'}
          </button>
          <small>当前任务仍固定使用原制度版本与索引版本。</small>
        </div>
      )}

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

      {issue.issue_type === 'PAYMENT_INFORMATION' && (
        <form className="issue-action" onSubmit={submitPaymentInformation}>
          <p>请填写从报价文件或供应商补充确认得到的起算信息。该回答会单独审计，不会改写原始付款条款。</p>
          <label><span>账期起算事件</span><select value="INVOICE_DATE" disabled><option value="INVOICE_DATE">发票日期</option></select></label>
          <label><span>信息来源</span><select value={paymentSourceType} onChange={(event) => setPaymentSourceType(event.target.value as typeof paymentSourceType)}><option value="SUPPLIER_CONFIRMATION">供应商确认</option><option value="DOCUMENT_CLARIFICATION">文件补充说明</option><option value="USER_INPUT">采购人员补充</option></select></label>
          <label><span>补充说明</span><textarea required maxLength={1000} value={paymentNote} onChange={(event) => setPaymentNote(event.target.value)} placeholder="例如：供应商邮件确认 Net 45 从发票日期起算" /></label>
          <button className="button button-submit" type="submit" disabled={answer.isPending || !paymentNote.trim()}>{answer.isPending ? '正在保存…' : '保存并继续分析'}</button>
        </form>
      )}

      {answer.isError && <p className="form-error" role="alert">{errorMessage(answer.error)}</p>}
    </section>
  )
}
