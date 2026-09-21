import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { api } from '../src/api/client'
import type { ReviewOverviewResponse, TaskDetail } from '../src/api/types'
import { ReviewPage } from '../src/pages/ReviewPage'
import { makeQuoteFieldSchema } from './quoteReviewFixtures'

function makeTask(): TaskDetail {
  return {
    task_id: 'task-1',
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

    expect(await screen.findByText('已合并 2 条相关规则，填写一次即可。')).toBeInTheDocument()
    expect(screen.getAllByLabelText('运费状态确认值')).toHaveLength(1)
    expect(screen.queryByText('核对后的原始表达')).not.toBeInTheDocument()
    expect(screen.queryByText('标准化值')).not.toBeInTheDocument()
    expect(screen.queryByText('修正理由')).not.toBeInTheDocument()

    await userEvent.selectOptions(screen.getByLabelText('运费状态确认值'), 'FREE')
    await userEvent.click(screen.getByRole('button', { name: '确认并重新计算' }))

    await waitFor(() => expect(correct).toHaveBeenCalled())
    expect(correct.mock.calls[0][2]).toEqual([{
      quoteId: 'quote-1',
      fieldName: 'shipping_fee_status',
      expectedFieldVersion: 2,
      rawValue: '免费',
      normalizedValue: 'FREE',
      unit: null,
      reason: '人工核对报价原文或供应商回复后修正',
    }])
  })

  test('does not submit while any quote review is still being generated', async () => {
    vi.spyOn(api, 'getTask').mockResolvedValue(makeTask())
    vi.spyOn(api, 'getReview').mockResolvedValue({ ...makeReview(), review_pending: true })
    vi.spyOn(api, 'getQuoteFieldSchema').mockResolvedValue(makeQuoteFieldSchema())
    const correct = vi.spyOn(api, 'correctQuoteFields')

    renderPage()

    expect(await screen.findByText('部分报价仍在审核，完成后才可统一提交；页面会自动更新。')).toBeInTheDocument()
    const submit = screen.getByRole('button', { name: '等待报价审核完成' })
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
      question: '请统一确认。',
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

    await userEvent.selectOptions(await screen.findByLabelText('运费状态确认值'), 'FREE')
    expect(screen.getByRole('button', { name: '确认并重新计算' })).toBeEnabled()
    expect(screen.queryByText(/部分报价仍在审核/)).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '确认并重新计算' }))
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

    expect(await screen.findByText('税费方式')).toBeInTheDocument()
    expect(screen.queryByText('税费比较口径')).not.toBeInTheDocument()
    expect(screen.queryByText('报价税费方式不符合采购要求。')).not.toBeInTheDocument()
    expect(screen.queryByText('采购要求：')).not.toBeInTheDocument()
    const details = screen.getByText('查看报价原文').closest('details')!
    await userEvent.click(screen.getByText('查看报价原文'))
    expect(within(details).getByText('Not applicable for this synthetic scenario')).toBeInTheDocument()
    expect(within(details).queryByText('当前报价值')).not.toBeInTheDocument()
    expect(within(details).queryByText('采购要求')).not.toBeInTheDocument()
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

    expect(await screen.findByText(/报价原值已保留/)).toBeInTheDocument()
    expect(screen.queryByLabelText('税费方式确认值')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '确认并重新计算' })).not.toBeInTheDocument()
  })

  test('returns to decision results after review completes without pending fields', async () => {
    const task = makeTask()
    task.status = 'COMPLETED'
    task.current_result_id = 'result-1'
    task.progress.quote_review_completed = true
    task.progress.decision_completed = true
    const review = makeReview()
    review.task_status = 'COMPLETED'
    review.review_pending = true
    review.problems = []
    review.problem_count = 0
    review.blocking_problem_count = 0
    vi.spyOn(api, 'getTask').mockResolvedValue(task)
    vi.spyOn(api, 'getReview').mockResolvedValue(review)
    vi.spyOn(api, 'getQuoteFieldSchema').mockResolvedValue(makeQuoteFieldSchema())

    renderPage()

    expect(await screen.findByText('decision-page')).toBeInTheDocument()
  })
})
