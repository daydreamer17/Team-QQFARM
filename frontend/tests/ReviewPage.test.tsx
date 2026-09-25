import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { api, ApiClientError } from '../src/api/client'
import type { ReviewOverviewResponse, TaskDetail } from '../src/api/types'
import { ReviewPage } from '../src/pages/ReviewPage'
import { TaskWorkspaceHeader } from '../src/components/TaskWorkspaceHeader'
import { makeQuoteFieldSchema } from './quoteReviewFixtures'

function makeTask(): TaskDetail {
  return {
    task_id: 'task-1',
    task_name: 'PART-1 · 2026-09-24',
    task_revision: 3,
    status: 'NEEDS_INPUT',
    scenario_id: null,
    current_graph_run_id: 'run-1',
    current_snapshot_id: null,
    current_result_id: null,
    summary_completed: false,
    progress: {
      requirement_completed: true,
      quote_review_completed: false,
      decision_completed: false,
      summary_completed: false,
    },
    policy_binding: null,
    decision_profile: {
      decision_profile_id: null,
      task_revision: 3,
      profile_version: 1,
      preferences: { ranking_mode: null, excluded_supplier_ids: [], cost_tolerance_amount: null },
      source_scenario_id: null,
    },
    current_issue: null,
    current_job: null,
    quotes: [{
      quote_id: 'quote-1',
      quote_version: 1,
      supplier_id: 'SUP-001',
      document_id: 'doc-1',
      document_version: 1,
      original_filename: 'supplier.pdf',
    }],
    requirement: {
      manufacturer: 'Maker',
      manufacturer_part_number: 'PART-1',
      package: 'QFN-32',
      revision: null,
      condition: 'NEW',
      allow_substitutes: false,
      base_unit: 'piece',
      required_quantity: 1000,
      quantity_unit: 'piece',
      budget_amount: '8000.00',
      currency: 'SGD',
      includes_shipping: true,
      tax_mode: 'EXCLUDED',
      other_fees_required: false,
      planned_order_date: '2026-09-14',
      delivery_deadline: '2026-09-19',
      delivery_location: 'SG',
      ranking_preference: 'LOWEST_CONFIRMED_TOTAL_COST',
      secondary_preference: null,
    },
  }
}

function makeReview(): ReviewOverviewResponse {
  const common = {
    quote_id: 'quote-1',
    quote_version: 1,
    field_version: 2,
    raw_value: 'unknown',
    normalized_value: 'UNKNOWN',
    unit: null,
    document_id: 'doc-1',
    original_filename: 'supplier.pdf',
    needs_resolution: true,
    resolution: 'FIELD_CORRECTION',
    field_name: 'shipping_fee_status',
    criticality: 'CRITICAL',
    applicable: true,
    decision: 'REVIEW_REQUIRED',
    severity: 'BLOCKING',
    review_reason: 'MISSING',
    source_ids: [],
    accepted_for_calculation: false,
    resolved: false,
  }
  return {
    task_id: 'task-1',
    task_revision: 3,
    graph_run_id: 'run-1',
    task_status: 'NEEDS_INPUT',
    review_pending: false,
    quotes: [{
      quote_id: 'quote-1',
      supplier_id: 'SUP-001',
      quote_version: 1,
      document_id: 'doc-1',
      original_filename: 'supplier.pdf',
      batch_artifact_id: 'batch-1',
      review_artifact_id: 'review-1',
      review_status: 'REVIEW_REQUIRED',
      review_pending: false,
      fields: [{
        field_name: 'currency',
        field_version: 1,
        raw_value: 'SGD',
        normalized_value: 'SGD',
        unit: null,
        validation_status: 'EXTRACTED',
        origin: 'DOCUMENT',
        source_refs: [],
        producer: 'test',
        adapter_version: null,
        prompt_version: null,
      }],
      evidence_sources: [],
    }],
    problems: [
      { ...common, finding_id: 'finding-1', codes: ['FEE_STATUS_UNKNOWN'], message: 'Status unknown.' },
      { ...common, finding_id: 'finding-2', codes: ['FEE_AMOUNT_UNKNOWN'], message: 'Amount unknown.' },
    ],
    blocking_problem_count: 2,
    problem_count: 2,
  }
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/tasks/task-1/review']}>
        <Routes>
          <Route path="/tasks/:taskId/review" element={<ReviewPage />} />
          <Route path="/tasks/:taskId/decision" element={<div>decision-page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('ReviewPage', () => {
  beforeEach(() => vi.restoreAllMocks())

  test('collects a previously missing freight amount with KNOWN_AMOUNT in one submission', async () => {
    const report = makeReview()
    report.quotes[0].fields.push({ ...report.quotes[0].fields[0], field_name: 'shipping_fee_amount',
      raw_value: null, normalized_value: null, unit: 'SGD', validation_status: 'MISSING' })
    vi.spyOn(api, 'getTask').mockResolvedValue(makeTask())
    vi.spyOn(api, 'getReview').mockResolvedValue(report)
    vi.spyOn(api, 'getQuoteFieldSchema').mockResolvedValue(makeQuoteFieldSchema())
    const correct = vi.spyOn(api, 'correctQuoteFields').mockResolvedValue({ task_id: 'task-1', task_revision: 4,
      graph_run_id: 'run-2', job_id: 'job-1', job_type: 'REVIEW', job_status: 'PENDING', correction_count: 2 })
    renderPage()
    await userEvent.selectOptions(await screen.findByLabelText('Shipping Fee Status value'), 'KNOWN_AMOUNT')
    expect(screen.getByRole('button', { name: 'Save & recalculate' })).toBeDisabled()
    await userEvent.type(screen.getByLabelText('Shipping Fee Amount value'), '320')
    await userEvent.click(screen.getByRole('button', { name: 'Save & recalculate' }))
    await waitFor(() => expect(correct).toHaveBeenCalledOnce())
    expect(correct.mock.calls[0][2]).toEqual(expect.arrayContaining([
      expect.objectContaining({ fieldName: 'shipping_fee_status', normalizedValue: 'KNOWN_AMOUNT' }),
      expect.objectContaining({ fieldName: 'shipping_fee_amount', normalizedValue: '320', unit: 'SGD' }),
    ]))
  })

  test('places review statistics next to the title without a duplicate pending badge', async () => {
    vi.spyOn(api, 'getTask').mockResolvedValue(makeTask())
    vi.spyOn(api, 'getReview').mockResolvedValue(makeReview())
    vi.spyOn(api, 'getQuoteFieldSchema').mockResolvedValue(makeQuoteFieldSchema())
    renderPage()
    const heading = await screen.findByRole('heading', { name: 'Actions' })
    const row = heading.closest('section')!
    expect(row).toHaveClass('workspace-page-lead')
    expect(screen.getByRole('link', { name: 'Requirements' })).toHaveAttribute('href', '/tasks/task-1')
    expect(screen.queryByRole('link', { name: 'Overview' })).not.toBeInTheDocument()
    const stats = within(row).getByLabelText('Review statistics')
    expect(within(stats).getByText('Quotations')).toBeInTheDocument()
    expect(within(stats).getByText('Pending')).toBeInTheDocument()
    expect(within(stats).getByText('Recorded')).toBeInTheDocument()
    expect(screen.queryByText(/项待处理$/)).not.toBeInTheDocument()
  })

  test('shows unsupported business days as a pending system limitation, not an editable field', async () => {
    const report = makeReview()
    report.problems = [{
      ...report.problems[0],
      field_name: 'day_basis',
      raw_value: 'BUSINESS_DAYS',
      normalized_value: 'BUSINESS_DAYS',
      codes: ['DAY_BASIS_UNSUPPORTED'],
      message: 'MVP cannot infer arrival from non-calendar-day lead time.',
      needs_resolution: false,
      resolution: 'ADDITIONAL_INFORMATION_REQUIRED',
    }]
    report.blocking_problem_count = 0
    report.problem_count = 1
    vi.spyOn(api, 'getTask').mockResolvedValue(makeTask())
    vi.spyOn(api, 'getReview').mockResolvedValue(report)
    vi.spyOn(api, 'getQuoteFieldSchema').mockResolvedValue(makeQuoteFieldSchema())

    renderPage()

    expect(await screen.findByText('Calculation pending')).toBeInTheDocument()
    expect(screen.getByText(/“Business days” from the source quotation has been retained and will not be converted to calendar days/)).toBeInTheDocument()
    const stats = screen.getByLabelText('Review statistics')
    expect(within(stats).getByText('Limitations')).toBeInTheDocument()
    expect(within(stats).getByText('Pending')).toBeInTheDocument()
    expect(within(stats).getAllByText('0')).toHaveLength(2)
    expect(screen.queryByLabelText('交期计算方式 value')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save & recalculate' })).not.toBeInTheDocument()
  })

  test('completed decision pages keep the review navigation without a blocking issue', () => {
    const task = makeTask()
    render(<MemoryRouter><TaskWorkspaceHeader taskId="task-1" scenarioId={null}
      title="Task" subtitle="测试" status="COMPLETED" revision={3} resultId="result-1"
      quoteCount={1} summaryComplete={false} progress={task.progress}
      reviewBlocked={false} active="decision" /></MemoryRouter>)
    expect(screen.getByRole('link', { name: 'Actions' })).toHaveAttribute('href', '/tasks/task-1/review')
  })

  test('merges duplicate findings and derives technical correction fields from one business value', async () => {
    vi.spyOn(api, 'getTask').mockResolvedValue(makeTask())
    vi.spyOn(api, 'getReview').mockResolvedValue(makeReview())
    vi.spyOn(api, 'getQuoteFieldSchema').mockResolvedValue(makeQuoteFieldSchema())
    const correct = vi.spyOn(api, 'correctQuoteFields').mockResolvedValue({
      task_id: 'task-1',
      task_revision: 4,
      graph_run_id: 'run-2',
      job_id: 'job-1',
      job_type: 'REVIEW',
      job_status: 'PENDING',
      correction_count: 1,
    })

    renderPage()

    expect(await screen.findByText('2 related rules have been combined; enter the value once.')).toBeInTheDocument()
    expect(screen.getAllByLabelText('Shipping Fee Status value')).toHaveLength(1)
    expect(screen.queryByText('核对后的Source wording')).not.toBeInTheDocument()
    expect(screen.queryByText('Normalised Value')).not.toBeInTheDocument()
    expect(screen.queryByText('修正理由')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Confirm' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save & recalculate' })).toBeDisabled()

    await userEvent.selectOptions(screen.getByLabelText('Shipping Fee Status value'), 'FREE')
    await userEvent.click(screen.getByRole('button', { name: 'Save & recalculate' }))

    await waitFor(() => expect(correct).toHaveBeenCalled())
    expect(correct.mock.calls[0][2]).toEqual([{
      quoteId: 'quote-1',
      fieldName: 'shipping_fee_status',
      expectedFieldVersion: 2,
      rawValue: 'Free',
      normalizedValue: 'FREE',
      unit: null,
      reason: 'Corrected after manually reviewing the source quotation or supplier response',
    }])
  })

  test('does not submit while any quote review is still being generated', async () => {
    vi.spyOn(api, 'getTask').mockResolvedValue(makeTask())
    vi.spyOn(api, 'getReview').mockResolvedValue({ ...makeReview(), review_pending: true })
    vi.spyOn(api, 'getQuoteFieldSchema').mockResolvedValue(makeQuoteFieldSchema())
    const correct = vi.spyOn(api, 'correctQuoteFields')

    renderPage()

    expect(await screen.findByText('Some quotations are still under review. Complete them before submitting the batch; this page will update automatically.')).toBeInTheDocument()
    const submit = screen.getByRole('button', { name: 'Waiting…' })
    expect(submit).toBeDisabled()
    await userEvent.click(submit)
    expect(correct).not.toHaveBeenCalled()
  })

  test('allows the open batch review issue even when another quote lacks a current review artifact', async () => {
    const task = makeTask()
    task.current_issue = {
      issue_id: 'issue-1',
      task_id: 'task-1',
      graph_run_id: 'run-1',
      quote_id: null,
      field_name: 'batch_review:3',
      issue_type: 'BATCH_FIELD_REVIEW',
      status: 'OPEN',
      question: '请统一Confirm。',
      answer_schema: {
        answer_type: 'BATCH_FIELD_CORRECTIONS',
        expected_task_revision: 3,
        cards: [],
      },
      created_revision: 3,
    }
    vi.spyOn(api, 'getTask').mockResolvedValue(task)
    vi.spyOn(api, 'getReview').mockResolvedValue({ ...makeReview(), review_pending: true })
    vi.spyOn(api, 'getQuoteFieldSchema').mockResolvedValue(makeQuoteFieldSchema())
    const correct = vi.spyOn(api, 'correctQuoteFields').mockResolvedValue({
      task_id: 'task-1',
      task_revision: 4,
      graph_run_id: 'run-2',
      job_id: 'job-1',
      job_type: 'REVIEW',
      job_status: 'PENDING',
      correction_count: 1,
    })

    renderPage()

    await userEvent.selectOptions(await screen.findByLabelText('Shipping Fee Status value'), 'FREE')
    expect(screen.getByRole('button', { name: 'Save & recalculate' })).toBeEnabled()
    expect(screen.queryByText(/部分报价仍在审核/)).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Save & recalculate' }))
    await waitFor(() => expect(correct).toHaveBeenCalledOnce())
  })

  test('uses a plain field label and keeps only the quote text in the evidence disclosure', async () => {
    const review = makeReview()
    review.problems = [{
      ...review.problems[0],
      finding_id: 'tax-finding',
      field_name: 'tax_mode',
      raw_value: 'Not applicable for this synthetic scenario',
      normalized_value: 'NOT_APPLICABLE',
      codes: ['TAX_CONVERSION_REQUIRED'],
      message: 'Quote tax treatment does not match the requirement.',
    }]
    review.problem_count = 1
    review.blocking_problem_count = 1
    review.quotes[0].fields.push({
      field_name: 'tax_mode',
      field_version: 2,
      raw_value: 'Not applicable for this synthetic scenario',
      normalized_value: 'NOT_APPLICABLE',
      unit: null,
      validation_status: 'EXTRACTED',
      origin: 'DOCUMENT',
      source_refs: [{ source_id: 'source-1', quoted_text: 'Not applicable for this synthetic scenario' }],
      producer: 'test',
      adapter_version: null,
      prompt_version: null,
    })
    vi.spyOn(api, 'getTask').mockResolvedValue(makeTask())
    vi.spyOn(api, 'getReview').mockResolvedValue(review)
    vi.spyOn(api, 'getQuoteFieldSchema').mockResolvedValue(makeQuoteFieldSchema())

    renderPage()

    expect(await screen.findByText('Tax Treatment')).toBeInTheDocument()
    expect(screen.queryByText('Cost Comparison Basis')).not.toBeInTheDocument()
    expect(screen.queryByText('QuotationTax TreatmentDoes Not Meet Requirements。')).not.toBeInTheDocument()
    expect(screen.queryByText('采购要求：')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save & recalculate' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    expect(screen.getByRole('button', { name: 'Save & recalculate' })).toBeEnabled()
    const details = screen.getByText('Source').closest('details')!
    await userEvent.click(screen.getByText('Source'))
    expect(within(details).getByText('Not applicable for this synthetic scenario')).toBeInTheDocument()
    expect(within(details).queryByText('Current Quotation值')).not.toBeInTheDocument()
    expect(within(details).queryByText('采购要求')).not.toBeInTheDocument()
  })

  test('keeps a deferred field pending and explains an unexpected save failure with a request id', async () => {
    vi.spyOn(api, 'getTask').mockResolvedValue(makeTask())
    vi.spyOn(api, 'getReview').mockResolvedValue(makeReview())
    vi.spyOn(api, 'getQuoteFieldSchema').mockResolvedValue(makeQuoteFieldSchema())
    const correct = vi.spyOn(api, 'correctQuoteFields').mockRejectedValue(new ApiClientError(
      500,
      'internal_server_error',
      'An unexpected server error occurred.',
      {},
      'request-review-500',
    ))

    renderPage()

    await screen.findByLabelText('Shipping Fee Status value')
    await userEvent.click(screen.getByRole('button', { name: 'Defer' }))
    expect(screen.getAllByText('Pending').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: 'Save & recalculate' })).toBeDisabled()

    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
    await userEvent.selectOptions(screen.getByLabelText('Shipping Fee Status value'), 'FREE')
    await userEvent.click(screen.getByRole('button', { name: 'Save & recalculate' }))

    await waitFor(() => expect(correct).toHaveBeenCalledOnce())
    expect(await screen.findByRole('alert')).toHaveTextContent('Request ID: request-review-500')
    expect(screen.getByRole('alert')).toHaveTextContent('this attempt was not submitted')
  })

  test('does not ask users to rewrite a valid quote fact for a comparison limitation', async () => {
    const review = makeReview()
    review.problems = [{
      ...review.problems[0],
      finding_id: 'tax-conversion',
      field_name: 'tax_mode',
      raw_value: 'Tax included',
      normalized_value: 'INCLUDED',
      codes: ['TAX_CONVERSION_REQUIRED'],
      message: 'Conversion data is required.',
      resolution: 'ADDITIONAL_INFORMATION_REQUIRED',
    }]
    review.problem_count = 1
    review.blocking_problem_count = 1
    vi.spyOn(api, 'getTask').mockResolvedValue(makeTask())
    vi.spyOn(api, 'getReview').mockResolvedValue(review)
    vi.spyOn(api, 'getQuoteFieldSchema').mockResolvedValue(makeQuoteFieldSchema())

    renderPage()

    expect(await screen.findByText(/The original quotation value has been retained/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Tax Treatment value')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save & recalculate' })).not.toBeInTheDocument()
  })

  test('keeps the review page accessible after completion with no pending fields', async () => {
    const task = makeTask()
    task.status = 'COMPLETED'
    task.current_result_id = 'result-1'
    task.progress.quote_review_completed = true
    task.progress.decision_completed = true
    const review = makeReview()
    review.task_status = 'COMPLETED'
    review.review_pending = false
    review.problems = []
    review.problem_count = 0
    review.blocking_problem_count = 0
    vi.spyOn(api, 'getTask').mockResolvedValue(task)
    vi.spyOn(api, 'getReview').mockResolvedValue(review)
    vi.spyOn(api, 'getQuoteFieldSchema').mockResolvedValue(makeQuoteFieldSchema())

    renderPage()

    expect(await screen.findByText('No pending fields.')).toBeInTheDocument()
    expect(screen.queryByText('decision-page')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Actions' })).toHaveAttribute('href', '/tasks/task-1/review')
  })

  test('exposes excluded supplier findings for manual correction without changing exclusions', async () => {
    const task = makeTask()
    task.status = 'COMPLETED'
    task.current_result_id = 'result-1'
    task.decision_profile.preferences.excluded_supplier_ids = ['SUP-001']
    const review = makeReview()
    review.problems = review.problems.map((problem) => ({ ...problem, needs_resolution: false }))
    review.blocking_problem_count = 0
    vi.spyOn(api, 'getTask').mockResolvedValue(task)
    vi.spyOn(api, 'getReview').mockResolvedValue(review)
    vi.spyOn(api, 'getQuoteFieldSchema').mockResolvedValue(makeQuoteFieldSchema())
    const correct = vi.spyOn(api, 'correctQuoteFields').mockResolvedValue({
      task_id: 'task-1', task_revision: 4, graph_run_id: 'run-2', job_id: 'job-1',
      job_status: 'PENDING', corrected_fields: [],
    })
    renderPage()
    expect(await screen.findByText('Excluded')).toBeInTheDocument()
    expect(screen.queryByText('No pending fields.')).not.toBeInTheDocument()
    const user = userEvent.setup()
    await user.selectOptions(screen.getByRole('combobox'), 'FREE')
    await user.click(screen.getByRole('button', { name: 'Save & recalculate' }))
    await waitFor(() => expect(correct).toHaveBeenCalled())
    expect(task.decision_profile.preferences.excluded_supplier_ids).toEqual(['SUP-001'])
  })
})
