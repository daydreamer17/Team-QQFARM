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

  test('renders prefilled fields with one combined submit action', () => {
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
    expect(screen.queryByRole('button', { name: '正式提交报价' })).not.toBeInTheDocument()
    expect(screen.getByText('已识别')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '确认并提交报价' })).toBeEnabled()
  })

  test('refreshes empty processing fields when extraction finishes', async () => {
    const schema = makeQuoteFieldSchema()
    const completedDraft = makeQuoteDraft({}, {
      status: 'REVIEW_REQUIRED',
      updated_at: '2026-09-20T00:00:10Z',
    })
    const processingDraft = {
      ...completedDraft,
      status: 'PROCESSING',
      updated_at: '2026-09-20T00:00:00Z',
      fields: completedDraft.fields.map((field) => ({
        ...field,
        raw_value: null,
        normalized_value: null,
        validation_status: 'MISSING' as const,
      })),
    }
    const props = {
      schema,
      taskRevision: 1,
      onChanged: vi.fn(),
      onPreview: vi.fn(),
    }
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    const view = render(
      <QueryClientProvider client={queryClient}>
        <QuoteDraftReviewWorkspace key={processingDraft.updated_at} draft={processingDraft} {...props} />
      </QueryClientProvider>,
    )

    expect(within(document.querySelector('#quote-field-manufacturer')!).getByRole('textbox')).toHaveValue('')

    view.rerender(
      <QueryClientProvider client={queryClient}>
        <QuoteDraftReviewWorkspace key={completedDraft.updated_at} draft={completedDraft} {...props} />
      </QueryClientProvider>,
    )

    await waitFor(() => expect(
      within(document.querySelector('#quote-field-manufacturer')!).getByRole('textbox'),
    ).toHaveValue('QQ Demo Components'))
    expect(screen.getByText('28')).toBeInTheDocument()
  })

  test('does not mark a passed field red or show its technical pass message', () => {
    const schema = makeQuoteFieldSchema()
    const draft = makeQuoteDraft()
    draft.review_findings = [
      {
        finding_id: 'finding-pass',
        field_name: 'payment_terms',
        criticality: 'NON_CRITICAL',
        applicable: false,
        decision: 'PASS',
        severity: 'INFO',
        review_reason: null,
        codes: ['FIELD_ACCEPTED'],
        message: 'Candidate passed deterministic field review.',
        source_ids: [],
        accepted_for_calculation: false,
        resolved: false,
      },
      {
        finding_id: 'finding-missing-pass',
        field_name: 'supplier_country',
        criticality: 'NON_CRITICAL',
        applicable: false,
        decision: 'PASS',
        severity: 'INFO',
        review_reason: null,
        codes: ['NON_BLOCKING_MISSING'],
        message: 'Field is legitimately missing and is not currently critical.',
        source_ids: [],
        accepted_for_calculation: false,
        resolved: false,
      },
    ]

    renderWorkspace(
      <QuoteDraftReviewWorkspace
        draft={draft}
        schema={schema}
        taskRevision={1}
        onChanged={vi.fn()}
        onPreview={vi.fn()}
      />,
    )

    expect(document.querySelector('#quote-field-payment_terms')).not.toHaveClass('has-error')
    expect(screen.queryByText('Candidate passed deterministic field review.')).not.toBeInTheDocument()
    expect(screen.queryByText('Field is legitimately missing and is not currently critical.')).not.toBeInTheDocument()
    expect(screen.queryByText('金额币种随“报价币种”字段统一确认。')).not.toBeInTheDocument()
    expect(screen.queryByText('已自动填写')).not.toBeInTheDocument()
    expect(screen.queryByText('系统已填写，可直接确认，也可以修改。')).not.toBeInTheDocument()
  })

  test('marks an actual field conflict red', () => {
    const schema = makeQuoteFieldSchema()
    const draft = makeQuoteDraft()
    const field = draft.fields.find((current) => current.field_name === 'manufacturer')!
    field.validation_status = 'CONFLICT'

    renderWorkspace(
      <QuoteDraftReviewWorkspace
        draft={draft}
        schema={schema}
        taskRevision={1}
        onChanged={vi.fn()}
        onPreview={vi.fn()}
      />,
    )

    expect(document.querySelector('#quote-field-manufacturer')).toHaveClass('has-error')
    expect(screen.getByText('待人工核对')).toBeInTheDocument()
  })

  test('submits an already reviewed draft without repeating the review call', async () => {
    const user = userEvent.setup()
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

    const reviewSpy = vi.spyOn(api, 'reviewQuoteDraft')
    const submitSpy = vi.spyOn(api, 'submitQuoteDraft').mockResolvedValue({} as never)

    renderWorkspace(
      <QuoteDraftReviewWorkspace
        draft={draft}
        schema={schema}
        taskRevision={1}
        onChanged={vi.fn()}
        onPreview={vi.fn()}
      />,
    )

    await user.click(screen.getByRole('button', { name: '确认并提交报价' }))

    await waitFor(() => expect(submitSpy).toHaveBeenCalledOnce())
    expect(reviewSpy).not.toHaveBeenCalled()
  })

  test('explains that an unchanged replacement does not need a new version', async () => {
    const user = userEvent.setup()
    const schema = makeQuoteFieldSchema()
    const draft = makeQuoteDraft({}, {
      status: 'READY_TO_SUBMIT',
      replacement_quote_id: 'quote-current',
      human_review_complete: true,
      submission_ready: true,
      calculation_ready: true,
      submission_blocking_fields: [],
      unconfirmed_fields: [],
      review_progress: { total: 30, reviewed: 30, confirmed: 30, corrected: 0, missing_confirmed: 0 },
    })
    vi.spyOn(api, 'submitQuoteDraft').mockRejectedValue(new ApiClientError(
      409,
      'quote_revision_unchanged',
      'No quote changes were detected; a new version is not required.',
      {},
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

    await user.click(screen.getByRole('button', { name: '确认并提交报价' }))

    expect(await screen.findByText('无需更新')).toBeInTheDocument()
    expect(screen.getByText('报价内容没有变化，已保留当前版本。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '返回报价列表' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByText(/草稿或字段版本已经变化/)).not.toBeInTheDocument()
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
      draft_revision: 3,
      status: 'READY_TO_SUBMIT',
      human_review_complete: true,
      submission_ready: true,
    })
    const submitSpy = vi.spyOn(api, 'submitQuoteDraft').mockResolvedValue({} as never)

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
    await user.click(screen.getByRole('button', { name: '确认并提交报价' }))

    await waitFor(() => expect(reviewSpy).toHaveBeenCalledOnce())
    await waitFor(() => expect(submitSpy).toHaveBeenCalledOnce())
    expect(submitSpy.mock.calls[0][3]).toBe(3)
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

  test('keeps UNKNOWN selectable and allows saving review progress', async () => {
    const user = userEvent.setup()
    const schema = makeQuoteFieldSchema()
    const draft = makeQuoteDraft({ shipping_fee_status: 'UNKNOWN', shipping_fee_amount: null })
    const reviewSpy = vi.spyOn(api, 'reviewQuoteDraft').mockResolvedValue({ ...draft, draft_revision: 2, human_review_complete: true, submission_ready: false })
    const submitSpy = vi.spyOn(api, 'submitQuoteDraft')

    renderWorkspace(
      <QuoteDraftReviewWorkspace
        draft={draft}
        schema={schema}
        taskRevision={1}
        onChanged={vi.fn()}
        onPreview={vi.fn()}
      />,
    )

    const shippingStatus = within(document.querySelector('#quote-field-shipping_fee_status')!).getByRole('combobox')
    expect(shippingStatus).toHaveValue('UNKNOWN')
    expect(within(shippingStatus).getByRole('option', { name: '请选择费用状态' })).toBeDisabled()
    expect(within(shippingStatus).getByRole('option', { name: /UNKNOWN/ })).toBeEnabled()
    expect(within(document.querySelector('#quote-field-shipping_fee_amount')!).getByRole('textbox')).toHaveValue('')
    await user.click(screen.getByRole('button', { name: '保存并确认审核' }))
    await waitFor(() => expect(reviewSpy).toHaveBeenCalledOnce())
    expect(submitSpy).not.toHaveBeenCalled()
  })

  test('saves an invalid amount and another edit without trying to submit', async () => {
    const user = userEvent.setup()
    const draft = makeQuoteDraft()
    const onChanged = vi.fn()
    const reviewSpy = vi.spyOn(api, 'reviewQuoteDraft').mockResolvedValue({ ...draft, draft_revision: 2, submission_ready: false })
    const submitSpy = vi.spyOn(api, 'submitQuoteDraft')
    renderWorkspace(<QuoteDraftReviewWorkspace draft={draft} schema={makeQuoteFieldSchema()} taskRevision={1} onChanged={onChanged} onPreview={vi.fn()} />)
    const price = within(document.querySelector('#quote-field-unit_price')!).getByRole('textbox')
    await user.clear(price)
    await user.type(price, 'abc')
    const manufacturer = within(document.querySelector('#quote-field-manufacturer')!).getByRole('textbox')
    await user.clear(manufacturer)
    await user.type(manufacturer, 'Manually corrected maker')
    expect(screen.getByRole('button', { name: '确认并提交报价' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '保存并确认审核' }))
    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce())
    const actions = reviewSpy.mock.calls[0][4]
    expect(actions.find((action) => action.fieldName === 'unit_price')).toMatchObject({ action: 'SET_VALUE', normalizedValue: 'abc' })
    expect(actions.find((action) => action.fieldName === 'manufacturer')).toMatchObject({ action: 'SET_VALUE', normalizedValue: 'Manually corrected maker' })
    expect(submitSpy).not.toHaveBeenCalled()
  })

  test('shows resolved automatic doubts only in collapsed audit details', () => {
    const draft = makeQuoteDraft()
    draft.fields.find((field) => field.field_name === 'unit_price')!.review_state = 'CONFIRMED'
    draft.review_findings = [{ finding_id: 'resolved-price', field_name: 'unit_price',
      criticality: 'ALWAYS', applicable: true, decision: 'REVIEW_REQUIRED', severity: 'BLOCKING',
      review_reason: 'EVIDENCE_ERROR', codes: ['NORMALIZED_PRICE_NOT_IN_EVIDENCE'], message: 'old automatic doubt',
      source_ids: [], accepted_for_calculation: true, resolved: true }]
    renderWorkspace(<QuoteDraftReviewWorkspace draft={draft} schema={makeQuoteFieldSchema()} taskRevision={1} onChanged={vi.fn()} onPreview={vi.fn()} />)
    const card = document.querySelector('#quote-field-unit_price')!
    expect(card).not.toHaveClass('has-error')
    expect(within(card).getByText('人工已确认。')).toBeInTheDocument()
    expect(within(card).getByText(/已人工处理：/).closest('details')).not.toHaveAttribute('open')
    expect(within(card).queryByRole('button', { name: '已核对，采用此值' })).not.toBeInTheDocument()
  })

  test('explicitly adopts an unchanged conflicting value and saves its correction', async () => {
    const user = userEvent.setup()
    const draft = makeQuoteDraft()
    draft.fields.find((field) => field.field_name === 'unit_price')!.validation_status = 'CONFLICT'
    const reviewSpy = vi.spyOn(api, 'reviewQuoteDraft').mockResolvedValue({ ...draft, draft_revision: 2 })
    renderWorkspace(<QuoteDraftReviewWorkspace draft={draft} schema={makeQuoteFieldSchema()} taskRevision={1} onChanged={vi.fn()} onPreview={vi.fn()} />)
    const card = document.querySelector('#quote-field-unit_price')!
    const originalValue = within(card).getByRole('textbox').getAttribute('value')
    await user.click(within(card).getByRole('button', { name: '已核对，采用此值' }))
    expect(card).not.toHaveClass('has-error')
    expect(within(card).getByText('已核对当前值，待保存确认。')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '保存并确认审核' }))
    await waitFor(() => expect(reviewSpy).toHaveBeenCalledOnce())
    expect(reviewSpy.mock.calls[0][4].find((action) => action.fieldName === 'unit_price')).toMatchObject({ action: 'SET_VALUE', normalizedValue: originalValue })
  })

  test('keeps related fields expanded while the user finishes typing a valid value', async () => {
    const user = userEvent.setup()
    const schema = makeQuoteFieldSchema()
    const draft = makeQuoteDraft({ shipping_fee_status: 'UNKNOWN', shipping_fee_amount: null })

    renderWorkspace(
      <QuoteDraftReviewWorkspace
        draft={draft}
        schema={schema}
        taskRevision={1}
        onChanged={vi.fn()}
        onPreview={vi.fn()}
      />,
    )

    const otherFields = document.querySelector('.quote-review-ready-fields') as HTMLDetailsElement
    expect(otherFields.open).toBe(true)

    await user.selectOptions(
      within(document.querySelector('#quote-field-shipping_fee_status')!).getByRole('combobox'),
      'KNOWN_AMOUNT',
    )
    expect(otherFields.open).toBe(true)

    const amount = within(document.querySelector('#quote-field-shipping_fee_amount')!).getByRole('textbox')
    await user.type(amount, '2')
    expect(amount).toHaveValue('2')
    expect(otherFields.open).toBe(true)

    await user.type(amount, '00')
    expect(amount).toHaveValue('200')
    expect(otherFields.open).toBe(true)
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
    await user.click(screen.getByRole('button', { name: '确认并提交报价' }))

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
    await user.click(screen.getByRole('button', { name: '确认并提交报价' }))

    expect(reviewSpy).not.toHaveBeenCalled()
    expect(screen.getAllByText(/系统未识别到/).length).toBeGreaterThanOrEqual(4)
  })
})
