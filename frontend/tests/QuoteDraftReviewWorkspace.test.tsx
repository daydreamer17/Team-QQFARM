import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactElement } from 'react'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { api, ApiClientError } from '../src/api/client'
import { QuoteDraftReviewWorkspace } from '../src/components/QuoteDraftReviewWorkspace'
import { makeQuoteDraft, makeQuoteFieldSchema } from './quoteReviewFixtures'

function renderWorkspace(element: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={queryClient}>{element}</QueryClientProvider>)
}

describe('QuoteDraftReviewWorkspace', () => {
  beforeEach(() => vi.restoreAllMocks())

  test('renders all 30 business fields as editable while formal submission stays gated', () => {
    const schema = makeQuoteFieldSchema()
    const draft = makeQuoteDraft()

    renderWorkspace(
      <QuoteDraftReviewWorkspace
        draft={draft}
        schema={schema}
        taskRevision={1}
        onChanged={vi.fn()}
        onPreview={vi.fn()}
      />,
    )

    const editors = document.querySelectorAll('.quote-review-field input, .quote-review-field select')
    expect(editors).toHaveLength(30)
    editors.forEach((editor) => expect(editor).toBeEnabled())
    expect(within(document.querySelector('#quote-field-manufacturer')!).getByRole('textbox')).toHaveValue('QQ Demo Components')
    expect(screen.getByRole('button', { name: '正式提交报价' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '确认并复核 30 个字段' })).toBeEnabled()
  })

  test('enables formal submit only after the backend marks full human review ready', () => {
    const schema = makeQuoteFieldSchema()
    const draft = makeQuoteDraft({}, {
      status: 'READY_TO_SUBMIT',
      human_review_complete: true,
      submission_ready: true,
      calculation_ready: true,
      submission_blocking_fields: [],
      unconfirmed_fields: [],
      review_progress: { total: 30, reviewed: 30, confirmed: 30, corrected: 0, missing_confirmed: 0 },
    })

    renderWorkspace(
      <QuoteDraftReviewWorkspace
        draft={draft}
        schema={schema}
        taskRevision={1}
        onChanged={vi.fn()}
        onPreview={vi.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: '正式提交报价' })).toBeEnabled()
  })

  test('re-evaluates fee applicability and audits a changed status plus cleared amount', async () => {
    const user = userEvent.setup()
    const schema = makeQuoteFieldSchema()
    const draft = makeQuoteDraft(
      { shipping_fee_status: 'KNOWN_AMOUNT', shipping_fee_amount: '20.00' },
    )
    const statusField = draft.fields.find((field) => field.field_name === 'shipping_fee_status')!
    const amountField = draft.fields.find((field) => field.field_name === 'shipping_fee_amount')!
    statusField.criticality = 'ALWAYS'
    statusField.required_for_submission = true
    amountField.criticality = 'CONDITIONAL_APPLICABLE'
    amountField.applicable = true
    amountField.required_for_submission = true
    const reviewSpy = vi.spyOn(api, 'reviewQuoteDraft').mockResolvedValue({
      ...draft,
      status: 'READY_TO_SUBMIT',
      human_review_complete: true,
      submission_ready: true,
    })

    renderWorkspace(
      <QuoteDraftReviewWorkspace
        draft={draft}
        schema={schema}
        taskRevision={1}
        onChanged={vi.fn().mockResolvedValue(undefined)}
        onPreview={vi.fn()}
      />,
    )

    await user.selectOptions(
      within(document.querySelector('#quote-field-shipping_fee_status')!).getByRole('combobox'),
      'FREE',
    )
    const amountInput = within(document.querySelector('#quote-field-shipping_fee_amount')!).getByRole('textbox')
    await user.clear(amountInput)
    await user.click(screen.getByRole('button', { name: '确认并复核 30 个字段' }))

    await waitFor(() => expect(reviewSpy).toHaveBeenCalledOnce())
    const actions = reviewSpy.mock.calls[0][4]
    expect(actions).toHaveLength(30)
    expect(actions.find((action) => action.fieldName === 'shipping_fee_status')).toMatchObject({
      action: 'SET_VALUE',
      normalizedValue: 'FREE',
    })
    expect(actions.find((action) => action.fieldName === 'shipping_fee_amount')).toMatchObject({
      action: 'MARK_MISSING',
    })
    expect(screen.queryByText(/费用免费或不适用时/)).not.toBeInTheDocument()
  })

  test('keeps UNKNOWN visible but blocks backend review with a specific reason', async () => {
    const user = userEvent.setup()
    const schema = makeQuoteFieldSchema()
    const draft = makeQuoteDraft({ shipping_fee_status: 'UNKNOWN', shipping_fee_amount: null })
    const reviewSpy = vi.spyOn(api, 'reviewQuoteDraft')

    renderWorkspace(
      <QuoteDraftReviewWorkspace
        draft={draft}
        schema={schema}
        taskRevision={1}
        onChanged={vi.fn()}
        onPreview={vi.fn()}
      />,
    )

    expect(within(document.querySelector('#quote-field-shipping_fee_status')!).getByRole('combobox')).toHaveValue('UNKNOWN')
    expect(within(document.querySelector('#quote-field-shipping_fee_amount')!).getByRole('textbox')).toHaveValue('')
    await user.click(screen.getByRole('button', { name: '确认并复核 30 个字段' }))

    expect(reviewSpy).not.toHaveBeenCalled()
    expect(screen.getAllByText(/不能保持“未知”/).length).toBeGreaterThan(0)
  })

  test('preserves human edits when the server reports a revision conflict', async () => {
    const user = userEvent.setup()
    const schema = makeQuoteFieldSchema()
    const draft = makeQuoteDraft()
    vi.spyOn(api, 'reviewQuoteDraft').mockRejectedValue(new ApiClientError(
      409,
      'quote_draft_revision_conflict',
      'Quote draft revision has changed.',
      { actual_draft_revision: 3 },
    ))

    renderWorkspace(
      <QuoteDraftReviewWorkspace
        draft={draft}
        schema={schema}
        taskRevision={1}
        onChanged={vi.fn()}
        onPreview={vi.fn()}
      />,
    )

    const manufacturerInput = within(document.querySelector('#quote-field-manufacturer')!).getByRole('textbox')
    await user.clear(manufacturerInput)
    await user.type(manufacturerInput, '人工确认制造商')
    await user.click(screen.getByRole('button', { name: '确认并复核 30 个字段' }))

    expect(await screen.findByText(/服务器当前版本：3/)).toBeInTheDocument()
    expect(manufacturerInput).toHaveValue('人工确认制造商')
  })

  test('does not let users bypass a server-applicable delivery group by clearing all four fields', async () => {
    const user = userEvent.setup()
    const schema = makeQuoteFieldSchema()
    const draft = makeQuoteDraft()
    const deliveryFields = ['lead_time_days', 'day_basis', 'delivery_semantics', 'start_event']
    for (const name of deliveryFields) {
      const field = draft.fields.find((item) => item.field_name === name)!
      field.required_for_submission = true
      field.applicable = true
      field.criticality = 'CONDITIONAL_APPLICABLE'
    }
    const reviewSpy = vi.spyOn(api, 'reviewQuoteDraft')

    renderWorkspace(
      <QuoteDraftReviewWorkspace
        draft={draft}
        schema={schema}
        taskRevision={1}
        onChanged={vi.fn()}
        onPreview={vi.fn()}
      />,
    )

    for (const name of deliveryFields) {
      const editor = within(document.querySelector(`#quote-field-${name}`)!).getByRole(
        schema.fields.find((field) => field.field_name === name)?.allowed_values ? 'combobox' : 'textbox',
      )
      if (editor instanceof HTMLSelectElement) await user.selectOptions(editor, '')
      else await user.clear(editor)
    }
    await user.click(screen.getByRole('button', { name: '确认并复核 30 个字段' }))

    expect(reviewSpy).not.toHaveBeenCalled()
    expect(screen.getAllByText(/本报价当前必须确认/).length).toBeGreaterThanOrEqual(4)
  })
})
