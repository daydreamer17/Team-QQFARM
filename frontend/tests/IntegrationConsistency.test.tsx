import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { api, ApiClientError } from '../src/api/client'
import type { ComparisonResultResponse, PolicyImportSummary, ProcurementRequirement, TaskDetail } from '../src/api/types'
import { PolicyImportPage } from '../src/pages/PolicyImportPage'
import type { PolicyImportResponse } from '../src/api/types'
import { CompliancePage } from '../src/pages/CompliancePage'
import { DecisionPage } from '../src/pages/DecisionPage'
import { EditTaskPage } from '../src/pages/EditTaskPage'
import { backendFieldErrors } from '../src/lib/apiErrors'
import { ResultPage } from '../src/pages/ResultPage'
import { ResourcePage } from '../src/pages/ResourcePage'
import { SummaryPage } from '../src/pages/SummaryPage'
import { NewTaskPage } from '../src/pages/NewTaskPage'

const currentRequirement: ProcurementRequirement = {
  manufacturer: 'Current Maker',
  manufacturer_part_number: 'CURRENT-PART',
  package: 'QFN-32',
  revision: 'R2',
  condition: 'NEW',
  allow_substitutes: false,
  base_unit: 'piece',
  required_quantity: 2000,
  quantity_unit: 'piece',
  budget_amount: '9000.00',
  currency: 'SGD',
  includes_shipping: true,
  tax_mode: 'NOT_APPLICABLE',
  other_fees_required: true,
  planned_order_date: '2026-09-14',
  delivery_deadline: '2026-09-20',
  delivery_location: 'Singapore',
  ranking_preference: 'LOWEST_CONFIRMED_TOTAL_COST',
  secondary_preference: null,
}

const frozenRequirement: ProcurementRequirement = {
  ...currentRequirement,
  manufacturer: 'Frozen Maker',
  manufacturer_part_number: 'FROZEN-PART',
  required_quantity: 1000,
  budget_amount: '8000.00',
  delivery_deadline: '2026-09-19',
}

function makeTask(overrides: Partial<TaskDetail> = {}): TaskDetail {
  return {
    task_id: 'task-1',
    task_name: 'FROZEN-PART',
    task_revision: 6,
    status: 'COMPLETED',
    scenario_id: 'SCENARIO-1',
    current_graph_run_id: 'run-current',
    current_snapshot_id: 'snapshot-current',
    current_result_id: 'result-current',
    summary_completed: false,
    progress: {
      requirement_completed: true,
      quote_review_completed: true,
      decision_completed: true,
      summary_completed: false,
    },
    policy_binding: null,
    decision_profile: {
      decision_profile_id: null,
      task_revision: 6,
      profile_version: 1,
      preferences: {
        ranking_mode: 'FASTEST_CONFIRMED_DELIVERY',
        excluded_supplier_ids: [],
        cost_tolerance_amount: null,
      },
      source_scenario_id: null,
    },
    current_issue: null,
    current_job: null,
    quotes: [{
      quote_id: 'quote-1',
      quote_version: 1,
      supplier_id: 'SUP-1',
      document_id: 'document-1',
      document_version: 1,
      original_filename: 'quote.pdf',
    }],
    requirement: currentRequirement,
    ...overrides,
  }
}

function makeHistoricalResult(): ComparisonResultResponse {
  return {
    result_id: 'result-old',
    snapshot_id: 'snapshot-old',
    task_revision: 5,
    graph_run_id: 'run-old',
    is_current: false,
    input_snapshot: {
      requirement: frozenRequirement,
      decision_profile: {
        decision_profile_id: null,
        task_revision: 5,
        profile_version: 1,
        preferences: {
          ranking_mode: 'LOWEST_CONFIRMED_TOTAL_COST',
          excluded_supplier_ids: [],
          cost_tolerance_amount: null,
        },
        source_scenario_id: null,
      },
      policy_set_version: null,
      policy_index_version: null,
      policy_category: null,
      policy_region: null,
    },
    result: {
      disposition: 'DRAFT',
      evaluated_at: '2026-09-12T00:00:00Z',
      rule_version: 'rules/1',
      ranked_quote_ids: [],
      supplier_results: [{
        status: 'PENDING',
        quote_id: 'quote-1',
        quote_version: 1,
        supplier_name: 'Supplier One',
        goods_cost: '6800.00',
        known_cost_subtotal: '6800.00',
        total_cost: null,
        actual_quantity: 1000,
        estimated_arrival_date: '2026-09-17',
        failed_reasons: [],
        pending_reasons: [{ code: 'SHIPPING_UNKNOWN', fields: ['shipping_fee_amount'], message: '运费待确认。' }],
      }],
      pending_quote_ids: ['quote-1'],
      comparison_reasons: [],
      recommended_quote_ids: [],
      blocking_pending_quote_ids: ['quote-1'],
      final_recommendation_allowed: false,
    },
    decision_impact: null,
    policy_retrievals: [{
      retrieval_id: 'RET-old',
      status: 'OK',
      policy_set_version: '2026.09.1',
      policy_index_version: 'index-old',
      embedding_model: 'fixed-embedding',
      rerank_model: 'fixed-rerank',
      filters: {},
      covered_control_codes: ['APPROVED_SUPPLIER'],
      missing_control_codes: [],
      citations: [{
        citation_id: 'CIT-old',
        retrieval_id: 'RET-old',
        policy_set_version: '2026.09.1',
        policy_id: 'POL-001',
        document_id: 'DOC-001',
        document_version: '1.0',
        clause_id: 'approved-1',
        section: 'Approved suppliers',
        text: 'A current supplier registry record is required before approval.',
        content_sha256: 'a'.repeat(64),
        control_code: 'APPROVED_SUPPLIER',
        bm25_rank: 1,
        bm25_score: 1,
        vector_rank: 1,
        vector_score: 1,
        fusion_rank: 1,
        fusion_score: 1,
        rerank_rank: 1,
        rerank_score: 1,
      }],
      candidates: [],
      latency_ms: { total: 1 },
      attempts: { embedding: 1, rerank: 1 },
      error_code: null,
    }],
    policy_compliance: {
      schema_version: 'policy-compliance/1',
      disposition: 'NO_CONFIRMED_COMPLIANT_SUPPLIER',
      recommendation_scope: 'PROCUREMENT_COMPARISON_ONLY',
      requires_human_review: false,
      counts: { COMPLIANT: 0, NON_COMPLIANT: 0, REVIEW_REQUIRED: 0, NOT_EVALUATED: 1 },
      assessments: [],
    },
  }
}

function renderRoute(path: string, route: string, element: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path={route} element={element} />
            <Route path="/tasks/:taskId" element={<div>任务概览</div>} />
            <Route path="/tasks/:taskId/decision" element={<div>决策页</div>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  }
}

describe('frontend and backend version consistency', () => {
  beforeEach(() => vi.restoreAllMocks())

  test('decision page links batch approval directly to pending items', async () => {
    vi.spyOn(api, 'getTask').mockResolvedValue(makeTask({
      status: 'NEEDS_INPUT',
      current_result_id: null,
      current_issue: {
        issue_id: 'issue-1',
        task_id: 'task-1',
        graph_run_id: 'run-current',
        quote_id: null,
        field_name: 'batch_review:6',
        issue_type: 'BATCH_FIELD_REVIEW',
        status: 'OPEN',
        question: '请统一确认。',
        answer_schema: {
          answer_type: 'BATCH_FIELD_CORRECTIONS',
          expected_task_revision: 6,
          cards: [],
        },
        created_revision: 6,
      },
    }))

    renderRoute('/tasks/task-1/decision', '/tasks/:taskId/decision', <DecisionPage />)

    const link = await screen.findByRole('link', { name: '进入待处理事项' })
    expect(link).toHaveAttribute('href', '/tasks/task-1/review')
    expect(screen.queryByRole('link', { name: '返回报价与证据处理' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '按当前版本重新分析' })).toBeInTheDocument()
  })

  test('decision page does not redirect to a cached result older than the applied scenario revision', async () => {
    vi.spyOn(api, 'getTask').mockResolvedValue(makeTask({
      task_revision: 6,
      current_result_id: 'result-old',
      status: 'COMPLETED',
    }))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[{
          pathname: '/tasks/task-1/decision',
          state: { expectedRevision: 7 },
        }] }>
          <Routes>
            <Route path="/tasks/:taskId/decision" element={<DecisionPage />} />
            <Route path="/tasks/:taskId/results/:resultId" element={<div>旧结果页</div>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )

    expect(await screen.findByRole('heading', { name: '决策比较' })).toBeInTheDocument()
    expect(screen.queryByText('旧结果页')).not.toBeInTheDocument()
  })

  test('decision page waits for an explicit rerun instead of reopening the previous result', async () => {
    vi.spyOn(api, 'getTask').mockResolvedValue(makeTask({
      status: 'QUEUED',
      current_graph_run_id: 'run-new',
      current_result_id: 'result-old',
    }))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[{
          pathname: '/tasks/task-1/decision',
          state: {
            expectedGraphRunId: 'run-new',
            previousResultId: 'result-old',
          },
        }] }>
          <Routes>
            <Route path="/tasks/:taskId/decision" element={<DecisionPage />} />
            <Route path="/tasks/:taskId/results/:resultId" element={<div>旧结果页</div>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )

    expect(await screen.findByText(/正在按当前代码重新分析/)).toBeInTheDocument()
    expect(screen.queryByText('旧结果页')).not.toBeInTheDocument()
  })

  test('historical result uses its frozen requirement and requests frozen quote evidence', async () => {
    vi.spyOn(api, 'getTask').mockResolvedValue(makeTask())
    vi.spyOn(api, 'getResult').mockResolvedValue(makeHistoricalResult())
    const fields = vi.spyOn(api, 'getQuoteFields').mockResolvedValue({
      quote_id: 'quote-1', review_status: 'REVIEW_REQUIRED', review_findings: [], fields: [],
    })
    const conversations = vi.spyOn(api, 'listDecisionConversations').mockResolvedValue({
      task_id: 'task-1',
      task_revision: 6,
      items: [
        {
          conversation_id: 'conversation-current',
          task_id: 'task-1',
          base_task_revision: 6,
          base_result_id: 'result-current',
          status: 'ACTIVE',
          title: '当前版本对话',
          messages: [{
            message_id: 'message-current',
            sequence: 1,
            role: 'ASSISTANT',
            status: 'SUCCEEDED',
            content: '这是当前第六版的回答。',
            reference_ids: ['RESULT:result-current'],
            proposed_changes: null,
            decision_intent_id: null,
            reply_to_message_id: null,
            provider: 'fixed',
            model_id: 'fixed',
            prompt_version: 'conversation/1',
            attempts: 1,
            error_code: null,
            error_message: null,
            created_at: '2026-09-13T00:00:00Z',
          }],
          created_at: '2026-09-13T00:00:00Z',
          updated_at: '2026-09-13T00:00:00Z',
        },
        {
          conversation_id: 'conversation-old',
          task_id: 'task-1',
          base_task_revision: 5,
          base_result_id: 'result-old',
          status: 'STALE',
          title: '历史版本对话',
          messages: [{
            message_id: 'message-old',
            sequence: 1,
            role: 'ASSISTANT',
            status: 'SUCCEEDED',
            content: '这是历史第五版的回答；模型文本中的 quote_fake_999 不应成为引用。',
            reference_ids: ['RESULT:result-old', 'QUOTE:quote-1', 'POLICY:CIT-old'],
            proposed_changes: null,
            decision_intent_id: null,
            reply_to_message_id: null,
            provider: 'fixed',
            model_id: 'fixed',
            prompt_version: 'conversation/1',
            attempts: 1,
            error_code: null,
            error_message: null,
            created_at: '2026-09-12T00:00:00Z',
          }],
          created_at: '2026-09-12T00:00:00Z',
          updated_at: '2026-09-12T00:00:00Z',
        },
      ],
    })
    vi.spyOn(api, 'listDecisionScenarios').mockResolvedValue({ task_id: 'task-1', task_revision: 6, items: [] })
    vi.spyOn(api, 'listDecisionIntents').mockResolvedValue({ task_id: 'task-1', task_revision: 6, items: [] })

    renderRoute(
      '/tasks/task-1/results/result-old',
      '/tasks/:taskId/results/:resultId',
      <ResultPage />,
    )

    expect(await screen.findByRole('heading', { name: 'FROZEN-PART' })).toBeInTheDocument()
    expect(screen.getByText('历史结果第 5 版')).toBeInTheDocument()
    expect(screen.getByText('该结果仅用于采购比较；供应商合规仍需单独核验。')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'CURRENT-PART' })).not.toBeInTheDocument()
    expect(screen.getByText(/这是历史第五版的回答/)).toBeInTheDocument()
    expect(screen.queryByText('这是当前第六版的回答。')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '重新分析' })).not.toBeInTheDocument()
    await waitFor(() => expect(fields).toHaveBeenCalledWith('task-1', 'quote-1', 'result-old'))
    expect(conversations).toHaveBeenCalledWith('task-1')
    expect(screen.getByRole('option', { name: /当前版本 · 当前版本对话/ })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /第 5 版 · 历史版本对话 · 历史/ })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /^查看引用/ })).toHaveLength(3)

    await userEvent.click(screen.getByRole('button', { name: '查看引用 [2] 供应商报价' }))
    const quoteEvidenceDialog = screen.getByRole('dialog', { name: 'Supplier One 字段证据' })
    expect(quoteEvidenceDialog).toBeInTheDocument()
    expect(quoteEvidenceDialog.parentElement?.parentElement).toBe(document.body)
    await userEvent.click(screen.getByRole('button', { name: '关闭' }))
    await userEvent.click(screen.getByRole('button', { name: '查看引用 [3] 制度证据' }))
    const policyCitationDialog = screen.getByRole('dialog', { name: '制度引用详情' })
    expect(policyCitationDialog.parentElement?.parentElement).toBe(document.body)
    expect(policyCitationDialog).toHaveTextContent(
      'A current supplier registry record is required before approval.',
    )
  })

  test('keeps infeasible quotes visible while explaining that they do not enter ranking', async () => {
    const result = makeHistoricalResult()
    result.result.supplier_results.push({
      ...result.result.supplier_results[0],
      quote_id: 'quote-wrong-part',
      supplier_name: 'Wrong Part Devices',
      status: 'INFEASIBLE',
      total_cost: '9660.00',
      pending_reasons: [],
      failed_reasons: [{
        code: 'MANUFACTURER_PART_NUMBER_MISMATCH',
        fields: ['manufacturer_part_number'],
        message: 'Quoted manufacturer_part_number does not match the procurement requirement.',
      }],
    })
    vi.spyOn(api, 'getTask').mockResolvedValue(makeTask())
    vi.spyOn(api, 'getResult').mockResolvedValue(result)
    vi.spyOn(api, 'getQuoteFields').mockImplementation(async (_taskId, quoteId) => ({
      quote_id: quoteId,
      review_status: 'READY_FOR_DOWNSTREAM',
      review_findings: [],
      fields: [],
    }))
    vi.spyOn(api, 'listDecisionConversations').mockResolvedValue({ task_id: 'task-1', task_revision: 6, items: [] })
    vi.spyOn(api, 'listDecisionScenarios').mockResolvedValue({ task_id: 'task-1', task_revision: 6, items: [] })
    vi.spyOn(api, 'listDecisionIntents').mockResolvedValue({ task_id: 'task-1', task_revision: 6, items: [] })

    renderRoute(
      '/tasks/task-1/results/result-old',
      '/tasks/:taskId/results/:resultId',
      <ResultPage />,
    )

    expect(await screen.findByText(/1 家不符合/)).toBeInTheDocument()
    expect(screen.getByText(/不符合项仍保留在矩阵中，但不参与排序/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Wrong Part Devices' })).toBeInTheDocument()
  })

  test('recommended quote remains a comparison recommendation while compliance needs review', async () => {
    const result = makeHistoricalResult()
    Object.assign(result.result, {
      disposition: 'RECOMMENDATION_AVAILABLE', ranked_quote_ids: ['quote-1'], recommended_quote_ids: ['quote-1'],
      pending_quote_ids: [], blocking_pending_quote_ids: [], final_recommendation_allowed: true,
    })
    Object.assign(result.result.supplier_results[0], {
      status: 'FEASIBLE', total_cost: '7000.00', known_cost_subtotal: '7000.00', pending_reasons: [],
    })
    result.policy_compliance.requires_human_review = true
    result.policy_compliance.counts = { REVIEW_REQUIRED: 1 }
    vi.spyOn(api, 'getTask').mockResolvedValue(makeTask())
    vi.spyOn(api, 'getResult').mockResolvedValue(result)
    vi.spyOn(api, 'getQuoteFields').mockResolvedValue({
      quote_id: 'quote-1', review_status: 'READY_FOR_DOWNSTREAM', review_findings: [], fields: [],
    })
    vi.spyOn(api, 'listDecisionConversations').mockResolvedValue({ task_id: 'task-1', task_revision: 6, items: [] })
    vi.spyOn(api, 'listDecisionScenarios').mockResolvedValue({ task_id: 'task-1', task_revision: 6, items: [] })
    vi.spyOn(api, 'listDecisionIntents').mockResolvedValue({ task_id: 'task-1', task_revision: 6, items: [] })

    renderRoute('/tasks/task-1/results/result-old', '/tasks/:taskId/results/:resultId', <ResultPage />)

    expect(await screen.findByRole('heading', { name: '建议优先：Supplier One' })).toBeInTheDocument()
    expect(screen.getByText(/满足当前报价比较条件/)).toBeInTheDocument()
    expect(screen.queryByText(/满足当前采购要求/)).not.toBeInTheDocument()
    expect(screen.getByText('该结果仅用于采购比较；供应商合规仍需单独核验。')).toBeInTheDocument()
  })

  test('stale result can start analysis for the current quote version', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'getTask').mockResolvedValue(makeTask({
      status: 'DRAFT',
      current_graph_run_id: null,
      current_snapshot_id: null,
      current_result_id: null,
      progress: {
        requirement_completed: true,
        quote_review_completed: true,
        decision_completed: false,
        summary_completed: false,
      },
    }))
    vi.spyOn(api, 'getResult').mockResolvedValue(makeHistoricalResult())
    vi.spyOn(api, 'getQuoteFields').mockResolvedValue({
      quote_id: 'quote-1', review_status: 'REVIEW_REQUIRED', review_findings: [], fields: [],
    })
    vi.spyOn(api, 'listDecisionConversations').mockResolvedValue({ task_id: 'task-1', task_revision: 6, items: [] })
    vi.spyOn(api, 'listDecisionScenarios').mockResolvedValue({ task_id: 'task-1', task_revision: 6, items: [] })
    vi.spyOn(api, 'listDecisionIntents').mockResolvedValue({ task_id: 'task-1', task_revision: 6, items: [] })
    const startRun = vi.spyOn(api, 'startRun').mockResolvedValue({
      task_id: 'task-1',
      task_revision: 6,
      graph_run_id: 'run-new',
      job_id: 'job-new',
      job_type: 'GRAPH_RUN',
      job_status: 'PENDING',
    })

    renderRoute(
      '/tasks/task-1/results/result-old',
      '/tasks/:taskId/results/:resultId',
      <ResultPage />,
    )

    await user.click(await screen.findByRole('button', { name: '重新分析' }))

    await waitFor(() => expect(startRun).toHaveBeenCalledWith(
      'task-1',
      6,
      expect.any(String),
    ))
    expect(await screen.findByText('决策页')).toBeInTheDocument()
  })

  test('current completed result can be rerun after deterministic logic changes', async () => {
    const user = userEvent.setup()
    const currentTask = makeTask({
      quotes: [
        { quote_id: 'quote-1', quote_version: 1, supplier_id: 'SUP-1', document_id: 'document-1', document_version: 1, original_filename: 'quote.pdf' },
        { quote_id: 'quote-excluded', quote_version: 1, supplier_id: 'SUP-030', document_id: 'document-excluded', document_version: 1, original_filename: 'excluded.pdf' },
      ],
      decision_profile: {
        decision_profile_id: 'profile-current',
        task_revision: 6,
        profile_version: 1,
        preferences: {
          ranking_mode: 'FASTEST_CONFIRMED_DELIVERY',
          excluded_supplier_ids: ['SUP-030'],
          cost_tolerance_amount: null,
        },
        source_scenario_id: null,
      },
    })
    const currentResult = makeHistoricalResult()
    currentResult.input_snapshot!.decision_profile = currentTask.decision_profile
    vi.spyOn(api, 'getTask').mockResolvedValue(currentTask)
    vi.spyOn(api, 'getResult').mockResolvedValue({
      ...currentResult,
      result_id: 'result-current',
      task_revision: 6,
      graph_run_id: 'run-current',
      is_current: true,
    })
    vi.spyOn(api, 'getQuoteFields').mockResolvedValue({
      quote_id: 'quote-1', review_status: 'REVIEW_REQUIRED', review_findings: [], fields: [],
    })
    vi.spyOn(api, 'listDecisionConversations').mockResolvedValue({ task_id: 'task-1', task_revision: 6, items: [] })
    vi.spyOn(api, 'listDecisionScenarios').mockResolvedValue({ task_id: 'task-1', task_revision: 6, items: [] })
    vi.spyOn(api, 'listDecisionIntents').mockResolvedValue({ task_id: 'task-1', task_revision: 6, items: [] })
    const startRun = vi.spyOn(api, 'startRun').mockResolvedValue({
      task_id: 'task-1',
      task_revision: 6,
      graph_run_id: 'run-new',
      job_id: 'job-new',
      job_type: 'GRAPH_RUN',
      job_status: 'PENDING',
    })

    renderRoute(
      '/tasks/task-1/results/result-current',
      '/tasks/:taskId/results/:resultId',
      <ResultPage />,
    )

    expect(await screen.findByText(/1 家待确认 · 1 家已排除/)).toBeInTheDocument()
    expect(screen.getByText(/当前 2 份有效报价中，1 份进入比较，1 份按设置排除（SUP-030）/)).toBeInTheDocument()
    expect(screen.getByText('当前未绑定制度。')).toBeInTheDocument()
    expect(screen.getAllByText('当前第 6 版').length).toBeGreaterThan(0)
    expect(screen.queryByText('确定性比较')).not.toBeInTheDocument()
    expect(screen.queryByText('第 1 版')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '查看报价原文' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '查看选择依据' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '查看补充信息' })).not.toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: '重新分析' }))
    await waitFor(() => expect(startRun).toHaveBeenCalledWith('task-1', 6, expect.any(String)))
    expect(await screen.findByText('决策页')).toBeInTheDocument()
  })

  test('compliance page never falls back to a historical result', async () => {
    vi.spyOn(api, 'getCompliance').mockResolvedValue({ task_id: 'task-1', task_revision: 6,
      stage: { status: 'NOT_STARTED', confirmed: false, can_compare: false },
      policy_binding: { policy_set_version: '2026.09.1', policy_index_version: 'index-1', category: 'Electronics', region: 'SG' },
      plan: null, assessment: null, evidence: [], legacy_result: true })
    vi.spyOn(api, 'getTask').mockResolvedValue(makeTask({
      current_result_id: null,
      policy_binding: {
        policy_set_version: '2026.09.1',
        policy_index_version: 'index-1',
        category: 'Electronics',
        region: 'SG',
      },
    }))
    const history = vi.spyOn(api, 'listResults').mockResolvedValue([makeHistoricalResult()])

    renderRoute('/tasks/task-1/compliance', '/tasks/:taskId/compliance', <CompliancePage />)

    expect(await screen.findByRole('heading', { name: '检查前准备证明材料' })).toBeInTheDocument()
    expect(screen.getByText(/这是旧流程任务，历史结果尚未经过本阶段确认/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '开始制度检查' })).toBeInTheDocument()
    expect(history).not.toHaveBeenCalled()
  })

  test('historical summary renders its frozen requirement instead of the current task', async () => {
    vi.spyOn(api, 'getTask').mockResolvedValue(makeTask({ current_result_id: null }))
    const historical = makeHistoricalResult()
    historical.policy_compliance.assessments = [{
      quote_id: 'quote-1',
      quote_version: 1,
      supplier_name: 'Supplier One',
      status: 'REVIEW_REQUIRED',
      eligibility: 'UNVERIFIED',
      checks: [{
        control_code: 'APPROVED_SUPPLIER',
        status: 'REVIEW_REQUIRED',
        citation_ids: ['CIT-old'],
      }],
    }]
    vi.spyOn(api, 'getResult').mockResolvedValue(historical)
    vi.spyOn(api, 'listSummaries').mockResolvedValue({
      task_id: 'task-1',
      task_revision: 6,
      items: [{
        summary_id: 'summary-old',
        task_id: 'task-1',
        task_revision: 5,
        result_id: 'result-old',
        status: 'STALE',
        is_current: false,
        input_sha256: 'abc',
        facts: {
          requirement: frozenRequirement,
          policy_binding: null,
          recommended_quote_ids: [],
          references: {},
        },
        narrative: {
          title: '历史采购总结',
          overview: '这是历史结果。',
          sections: [],
          disclaimer: '不构成采购批准。',
        },
        provider: 'fixed',
        model_id: 'fixed',
        environment: 'TEST',
        prompt_version: 'summary/1',
        calls_used: 1,
        max_calls: 4,
        error_code: null,
        error_message: null,
        job: null,
        created_at: '2026-09-12T00:00:00Z',
        updated_at: '2026-09-12T00:00:00Z',
      }],
    })

    renderRoute('/tasks/task-1/summary', '/tasks/:taskId/summary', <SummaryPage />)

    expect(await screen.findByRole('heading', { name: 'FROZEN-PART 采购总结' })).toBeInTheDocument()
    expect(screen.getByText(/预算上限为 SGD 8000.00/)).toBeInTheDocument()
    expect(screen.getByText(/第 5 版历史采购总结/)).toBeInTheDocument()
    expect((await screen.findAllByText('待补充或复核')).length).toBeGreaterThan(0)
    expect((await screen.findAllByText(/供应商资质尚未完成核验；有已核验合格候选时不优先推荐/)).length).toBeGreaterThan(0)
    expect(await screen.findByText(/完成复核后再比较价格与交期/)).toBeInTheDocument()
  })

  test('requirement update invalidates all task caches before navigation', async () => {
    vi.spyOn(api, 'getTask').mockResolvedValue(makeTask())
    vi.spyOn(api, 'updateRequirement').mockResolvedValue({
      task_id: 'task-1', task_revision: 7, status: 'QUEUED',
    })
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const rendered = renderRoute('/tasks/task-1/edit', '/tasks/:taskId/edit', <EditTaskPage />)
    const invalidate = vi.spyOn(rendered.queryClient, 'invalidateQueries')
    await userEvent.click(await screen.findByRole('button', { name: '保存新版本' }))

    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['tasks'] }))
    expect(await screen.findByText('任务概览')).toBeInTheDocument()
  })

  test('maps FastAPI location/message validation fields', () => {
    const error = new ApiClientError(422, 'validation_error', 'invalid', {
      errors: [{ location: ['body', 'requirement', 'budget_amount'], message: '预算格式无效。' }],
    })
    expect(backendFieldErrors(error, ['budget_amount'])).toEqual({ budget_amount: '预算格式无效。' })
  })

  test('policy management prioritizes published and pending lists before upload', async () => {
    vi.spyOn(api, 'listPolicyImports').mockResolvedValue({
      items: [], total: 0, limit: 8, offset: 0,
    })
    vi.spyOn(api, 'listPolicySets').mockResolvedValue({
      items: [], total: 0, limit: 8, offset: 0,
    })

    renderRoute('/resources', '/resources', <ResourcePage />)

    expect(await screen.findByRole('heading', { name: '规则资源库' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '已发布制度' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '待处理版本' })).toBeInTheDocument()
    expect(screen.queryByLabelText('制度集名称')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '上传制度版本' }))
    expect(screen.getByLabelText('上传方式')).toHaveValue('NEW')
    expect(screen.getByLabelText('制度集名称')).toBeInTheDocument()
    expect(screen.getByText('选择本版本的全部制度文件')).toBeInTheDocument()
    const policyFileInput = screen.getByLabelText(/选择本版本的全部制度文件/)
    expect(policyFileInput).toHaveAttribute('multiple')
    expect(policyFileInput).toHaveAttribute(
      'accept',
      '.pdf,.txt,.md,application/pdf,text/plain,text/markdown',
    )
    await userEvent.upload(
      policyFileInput,
      new File(['# Documentation'], 'README.md', { type: 'text/markdown' }),
    )
    expect(screen.getByText(/README\.md.*说明文件/)).toBeInTheDocument()
    expect(screen.getByText(/PDF \/ UTF-8 TXT \/ Markdown/)).toBeInTheDocument()
    expect(screen.queryByLabelText('策略集 ID')).not.toBeInTheDocument()
    expect(screen.queryByText('KNOWLEDGE RESOURCES')).not.toBeInTheDocument()
    expect(await screen.findByText('当前没有待处理版本。')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '筛选' })).not.toBeInTheDocument()
  })

  test('only the most recently updated pending version is highlighted', async () => {
    const makeImport = (overrides: Partial<PolicyImportSummary>): PolicyImportSummary => ({
      policy_import_id: 'import-default',
      status: 'READY_TO_PUBLISH',
      revision: 1,
      original_filename: 'rule.md',
      media_type: 'text/markdown',
      size_bytes: 900,
      policy_set_id: 'electronics-procurement',
      policy_set_version: 'v2',
      policy_id: 'policy-default',
      document_id: 'document-default',
      document_version: '1',
      title: 'Rule',
      categories: ['Electronics'],
      regions: ['SG'],
      policy_index_version: null,
      clause_count: 1,
      created_at: '2026-09-24T10:00:00Z',
      updated_at: '2026-09-24T10:00:00Z',
      ...overrides,
    })
    const pending = [
      makeImport({ policy_import_id: 'v2-admission', original_filename: 'admission.md', status: 'REVIEW_REQUIRED' }),
      makeImport({ policy_import_id: 'v2-rohs', original_filename: 'rohs.md' }),
      makeImport({ policy_import_id: 'v2-amount', original_filename: 'amount.md' }),
      makeImport({
        policy_import_id: 'v1-admission',
        policy_set_version: 'v1',
        original_filename: 'admission.md',
        updated_at: '2026-09-23T10:00:00Z',
      }),
      makeImport({
        policy_import_id: 'hardware-v1-rohs',
        policy_set_id: 'data-center-hardware',
        policy_set_version: 'v1',
        original_filename: 'rohs.md',
        updated_at: '2026-09-22T10:00:00Z',
      }),
    ]
    vi.spyOn(api, 'listPolicyImports').mockImplementation(async (query) => ({
      items: pending.filter((item) => item.status === query.status),
      total: pending.filter((item) => item.status === query.status).length,
      limit: 100,
      offset: 0,
    }))
    vi.spyOn(api, 'listPolicySets').mockResolvedValue({ items: [], total: 0, limit: 100, offset: 0 })

    renderRoute('/resources', '/resources', <ResourcePage />)

    expect(await screen.findByText('最新 1 个版本')).toBeInTheDocument()
    expect(screen.getByText('3 个文件 · 1 个待处理 · 2 个已识别')).toBeInTheDocument()
    expect(screen.getByText('版本 v2')).toBeVisible()
    for (const version of screen.getAllByText('版本 v1')) expect(version).not.toBeVisible()
    expect(screen.getByText('data-center-hardware')).not.toBeVisible()
    expect(screen.getByRole('link', { name: '继续处理' })).toHaveAttribute('href', '/resources/policies/v2-admission')

    await userEvent.click(screen.getByText('其他待处理版本（2）'))
    expect(screen.getAllByText('版本 v1')).toHaveLength(2)
    expect(screen.getByText('data-center-hardware')).toBeVisible()
    expect(screen.getAllByRole('link', { name: '查看草稿' })).toHaveLength(2)
  })

  test('published policy versions can start a replacement version or be deactivated', async () => {
    vi.spyOn(api, 'listPolicyImports').mockResolvedValue({
      items: [], total: 0, limit: 100, offset: 0,
    })
    vi.spyOn(api, 'listPolicySets').mockResolvedValue({
      items: [{
        policy_set_id: 'electronics-sg-procurement',
        policy_set_version: '2026.09.1',
        policy_index_version: 'pidx-1',
        status: 'PUBLISHED',
        categories: ['Electronics'],
        regions: ['SG'],
        document_count: 3,
        clause_count: 30,
        provider: 'fixed',
        embedding_model: 'BAAI/bge-m3',
        embedding_dimension: 1024,
        preprocessing_version: 'policy-text/v1',
        published_at: '2026-09-24T00:00:00Z',
      }],
      total: 1,
      limit: 8,
      offset: 0,
    })
    const deactivate = vi.spyOn(api, 'deactivatePolicySet').mockResolvedValue({
      policy_set_id: 'electronics-sg-procurement',
      policy_set_version: '2026.09.1',
      status: 'INACTIVE',
    })
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    renderRoute('/resources', '/resources', <ResourcePage />)

    await userEvent.click(await screen.findByRole('button', { name: '发布新版本' }))
    expect(screen.getByLabelText('上传方式')).toHaveValue('UPDATE')
    expect(screen.getByLabelText('选择已有制度')).toHaveValue(
      JSON.stringify(['electronics-sg-procurement', '2026.09.1', 'pidx-1']),
    )
    expect(screen.getByLabelText('制度集名称')).toHaveValue('electronics-sg-procurement')
    expect(screen.getByLabelText('制度集名称')).toHaveAttribute('readonly')
    expect(screen.getByLabelText(/适用采购类别/)).toHaveValue('Electronics')
    expect(screen.getByLabelText(/适用地区/)).toHaveValue('SG')
    expect(screen.getByText(/正在更新 electronics-sg-procurement 的版本 2026\.09\.1/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '停用制度版本' }))
    await waitFor(() => expect(deactivate).toHaveBeenCalledWith(
      'electronics-sg-procurement',
      '2026.09.1',
      expect.any(String),
    ))
  })

  test('policy management groups old versions under the current policy set', async () => {
    vi.spyOn(api, 'listPolicyImports').mockResolvedValue({
      items: [], total: 0, limit: 100, offset: 0,
    })
    vi.spyOn(api, 'listPolicySets').mockResolvedValue({
      items: [
        {
          policy_set_id: 'regional-electronics-ui-test',
          policy_set_version: '2026.09.24-123928',
          policy_index_version: 'pidx-3',
          status: 'PUBLISHED',
          categories: ['Electronics'],
          regions: ['SG'],
          document_count: 5,
          clause_count: 25,
          provider: 'fixed',
          embedding_model: 'BAAI/bge-m3',
          embedding_dimension: 1024,
          preprocessing_version: 'policy-text/v1',
          published_at: '2026-09-24T04:39:00Z',
        },
        {
          policy_set_id: 'regional-electronics-ui-test',
          policy_set_version: '2026.09.24-122203',
          policy_index_version: 'pidx-2',
          status: 'PUBLISHED',
          categories: ['Electronics'],
          regions: ['SG'],
          document_count: 5,
          clause_count: 25,
          provider: 'fixed',
          embedding_model: 'BAAI/bge-m3',
          embedding_dimension: 1024,
          preprocessing_version: 'policy-text/v1',
          published_at: '2026-09-24T04:38:00Z',
        },
        {
          policy_set_id: 'regional-electronics-ui-test',
          policy_set_version: '2026.09.24-121015',
          policy_index_version: 'pidx-1',
          status: 'INACTIVE',
          categories: ['Electronics'],
          regions: ['SG'],
          document_count: 5,
          clause_count: 25,
          provider: 'fixed',
          embedding_model: 'BAAI/bge-m3',
          embedding_dimension: 1024,
          preprocessing_version: 'policy-text/v1',
          published_at: '2026-09-24T04:17:00Z',
        },
      ],
      total: 3,
      limit: 100,
      offset: 0,
    })

    renderRoute('/resources', '/resources', <ResourcePage />)

    expect(await screen.findByText('1 个制度集')).toBeInTheDocument()
    expect(screen.getAllByText('regional-electronics-ui-test')).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: '发布新版本' })).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: '停用制度版本' })).toHaveLength(1)
    expect(screen.getByText('版本 2026.09.24-123928')).toBeVisible()

    await userEvent.click(screen.getByText('查看历史版本（2）'))
    expect(screen.getByText('版本 2026.09.24-122203')).toBeVisible()
    expect(screen.getByText('版本 2026.09.24-121015')).toBeVisible()
    expect(screen.getByText('已被替代')).toBeVisible()
  })

  test('inactive policy sets stay hidden until the user asks to show them', async () => {
    vi.spyOn(api, 'listPolicyImports').mockResolvedValue({
      items: [], total: 0, limit: 100, offset: 0,
    })
    vi.spyOn(api, 'listPolicySets').mockResolvedValue({
      items: [
        {
          policy_set_id: 'active-electronics-policy',
          policy_set_version: '2026.09.2',
          policy_index_version: 'pidx-active',
          status: 'PUBLISHED',
          categories: ['Electronics'],
          regions: ['SG'],
          document_count: 3,
          clause_count: 18,
          provider: 'fixed',
          embedding_model: 'BAAI/bge-m3',
          embedding_dimension: 1024,
          preprocessing_version: 'policy-text/v1',
          published_at: '2026-09-24T05:00:00Z',
        },
        {
          policy_set_id: 'inactive-electronics-policy',
          policy_set_version: '2026.08.1',
          policy_index_version: 'pidx-inactive',
          status: 'INACTIVE',
          categories: ['Electronics'],
          regions: ['SG'],
          document_count: 3,
          clause_count: 15,
          provider: 'fixed',
          embedding_model: 'BAAI/bge-m3',
          embedding_dimension: 1024,
          preprocessing_version: 'policy-text/v1',
          published_at: '2026-08-01T00:00:00Z',
        },
      ],
      total: 2,
      limit: 100,
      offset: 0,
    })

    renderRoute('/resources', '/resources', <ResourcePage />)

    expect(await screen.findByText('active-electronics-policy')).toBeVisible()
    expect(screen.queryByText('inactive-electronics-policy')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '显示已停用制度（1）' }))
    expect(screen.getByText('inactive-electronics-policy')).toBeVisible()
    expect(screen.getByRole('button', { name: '隐藏已停用制度' })).toHaveAttribute('aria-pressed', 'true')

    await userEvent.click(screen.getByRole('button', { name: '隐藏已停用制度' }))
    expect(screen.queryByText('inactive-electronics-policy')).not.toBeInTheDocument()
  })

  test('new task form starts empty and clear form removes every selected default', async () => {
    vi.spyOn(api, 'listPolicySets').mockResolvedValue({
      items: [], total: 0, limit: 100, offset: 0,
    })

    renderRoute('/tasks/new', '/tasks/new', <NewTaskPage />)

    expect(await screen.findByRole('heading', { name: '新建任务' })).toBeInTheDocument()
    expect(screen.getByLabelText(/^任务名称/)).toHaveValue('')
    expect(screen.queryByLabelText('基础单位')).not.toBeInTheDocument()
    expect(screen.queryByText('更多设置')).not.toBeInTheDocument()
    for (const label of [
      '制造商', '制造商料号', '封装', '物料版本', '物料状态',
      '数量单位', '币种', '成本比较口径', '计划下单日期 可选',
      '交付截止日期', '交付地点', '主要排序偏好', '次要偏好 可选，仅在主指标并列时使用',
    ]) {
      expect(screen.getByLabelText(label), label).toHaveValue('')
    }
    const primaryPreference = screen.getByLabelText('主要排序偏好')
    const secondaryPreference = screen.getByLabelText('次要偏好 可选，仅在主指标并列时使用')
    expect(primaryPreference.closest('fieldset')).toBe(secondaryPreference.closest('fieldset'))
    expect(secondaryPreference.closest('details')).toBeNull()
    expect(screen.getByLabelText('需求数量')).toHaveValue(null)
    expect(screen.getByLabelText(/预算金额/)).toHaveValue('')
    expect(screen.getByLabelText('允许替代料')).not.toBeChecked()
    expect(screen.getByLabelText('预算包含运费')).not.toBeChecked()
    expect(screen.getByLabelText('要求计入其他费用')).not.toBeChecked()

    await userEvent.type(screen.getByLabelText('制造商'), 'Example Maker')
    await userEvent.type(screen.getByLabelText('制造商料号'), 'EXAMPLE-PART')
    expect((screen.getByLabelText(/^任务名称/) as HTMLInputElement).value).toMatch(/^EXAMPLE-PART · \d{4}-\d{2}-\d{2}$/)
    await userEvent.selectOptions(screen.getByLabelText('物料状态'), 'NEW')
    await userEvent.selectOptions(screen.getByLabelText('币种'), 'SGD')
    await userEvent.click(screen.getByLabelText('预算包含运费'))
    await userEvent.click(screen.getByRole('button', { name: '清空表单' }))

    expect(screen.getByLabelText('制造商')).toHaveValue('')
    expect(screen.getByLabelText(/^任务名称/)).toHaveValue('')
    expect(screen.getByLabelText('物料状态')).toHaveValue('')
    expect(screen.getByLabelText('币种')).toHaveValue('')
    expect(screen.getByLabelText('预算包含运费')).not.toBeChecked()
  })
})


function policyFixture(status = 'REVIEW_REQUIRED'): PolicyImportResponse {
  return { policy_import_id: 'policy-test', title: 'Test policy', policy_id: 'P', document_id: 'D',
    document_version: '1', policy_set_id: 'S', policy_set_version: '1', revision: 2, status,
    categories: ['Electronics'], regions: ['SG'], effective_from: '2026-01-01', effective_to: null,
    original_filename: 'test.txt', size_bytes: 100, media_type: 'text/plain', source_sha256: 'a'.repeat(64),
    extracted_text: 'Original', extraction_metadata: { parser: 'txt', page_count: null },
    policy_index_version: null, published_import_run_id: null,
    clauses: [{ clause_id: 'C1', title: 'Clause', text: 'Saved text', control_code: 'AMOUNT_APPROVAL', rule_parameters: {}, position: 0 }],
  }
}
function renderPolicy(policyImportId = 'policy-test') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[`/policy/${policyImportId}`]}>
    <Routes><Route path="/policy/:policyImportId" element={<PolicyImportPage />} /><Route path="/resources/policies/:policyImportId" element={<PolicyImportPage />} /></Routes>
  </MemoryRouter></QueryClientProvider>)
  return client
}
test('policy editor locks fields during save and permits editing after completion', async () => {
  vi.restoreAllMocks()
  const data = policyFixture()
  vi.spyOn(api, 'getPolicyImport').mockResolvedValue(data)
  let finish!: (value: PolicyImportResponse) => void
  vi.spyOn(api, 'reviewPolicyClauses').mockImplementation(() => new Promise((resolve) => { finish = resolve }))
  const client = renderPolicy()
  const field = await screen.findByRole('textbox', { name: /条款正文/ })
  const user = userEvent.setup()
  await user.clear(field); await user.type(field, 'Submitted')
  await user.click(screen.getByRole('button', { name: '保存确认结果' }))
  expect(field).toBeDisabled()
  expect(screen.getByRole('button', { name: '删除' })).toBeDisabled()
  await user.type(field, 'Must not append')
  await act(async () => finish({ ...data, revision: 3, status: 'READY_TO_PUBLISH', clauses: [{ ...data.clauses[0], text: 'Submitted' }] }))
  expect(field).toHaveValue('Submitted')
  expect(field).not.toBeDisabled()
  client.clear()
})
test('editing a policy clause id keeps its rule settings expanded', async () => {
  vi.restoreAllMocks()
  const data = policyFixture()
  vi.spyOn(api, 'getPolicyImport').mockResolvedValue(data)
  const client = renderPolicy()
  const user = userEvent.setup()

  const summary = await screen.findByText('检查规则设置')
  const settings = summary.closest('details')!
  expect(settings).toHaveAttribute('open')
  await user.click(summary)
  expect(settings).not.toHaveAttribute('open')
  await user.click(summary)
  expect(settings).toHaveAttribute('open')

  const clauseId = screen.getByLabelText('条款编号')
  await user.clear(clauseId)
  await user.type(clauseId, 'CCD-ADM-001')

  expect(clauseId).toHaveValue('CCD-ADM-001')
  expect(settings).toHaveAttribute('open')
  client.clear()
})
test('switching policy files replaces an unsaved clause draft instead of showing it under another filename', async () => {
  vi.restoreAllMocks()
  const admission = { ...policyFixture(), policy_import_id: 'policy-admission', original_filename: 'admission.md',
    clauses: [{ ...policyFixture().clauses[0], title: 'Supplier admission', control_code: null }] }
  const amount = { ...policyFixture(), policy_import_id: 'policy-amount', original_filename: 'amount.md',
    clauses: [{ ...policyFixture().clauses[0], clause_id: 'AMT-1', title: 'Amount approval', control_code: null }] }
  vi.spyOn(api, 'getPolicyImport').mockImplementation(async (id) => id === 'policy-admission' ? admission : amount)
  vi.spyOn(api, 'listPolicyImports').mockResolvedValue({
    items: [admission, amount].map((item) => ({
      ...item,
      clause_count: item.clauses.length,
      created_at: '2026-09-24T00:00:00Z',
      updated_at: '2026-09-24T00:00:00Z',
    })),
    total: 2, limit: 100, offset: 0,
  } as never)
  const client = renderPolicy('policy-admission')
  const user = userEvent.setup()

  const title = await screen.findByLabelText('标题')
  await user.clear(title)
  await user.type(title, 'Unsaved admission edit')
  await user.click(screen.getByRole('link', { name: /amount\.md/ }))

  expect(await screen.findByText('正在审核：amount.md')).toBeInTheDocument()
  expect(await screen.findByLabelText('标题')).toHaveValue('Amount approval')
  expect(screen.queryByDisplayValue('Unsaved admission edit')).not.toBeInTheDocument()
  client.clear()
})
test('advanced policy review opens by default and can be collapsed', async () => {
  vi.restoreAllMocks()
  const data = {
    ...policyFixture(),
    clauses: [{ ...policyFixture().clauses[0], control_code: null }],
  }
  vi.spyOn(api, 'getPolicyImport').mockResolvedValue(data)
  const client = renderPolicy()

  expect(await screen.findByLabelText('检查类型')).toBeInTheDocument()
  expect(screen.getByRole('option', { name: '供应商准入' })).toHaveValue('APPROVED_SUPPLIER')
  await userEvent.click(screen.getByRole('button', { name: '收起高级审核' }))
  expect(screen.queryByLabelText('检查类型')).not.toBeInTheDocument()
  expect(screen.getByText(/系统无法判断该条款属于哪类采购检查/)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '展开高级审核（1 条）' }))
  expect(screen.getByLabelText('检查类型')).toBeInTheDocument()
  client.clear()
})
test('unsupported policy clauses explain the capability gap without asking ordinary users for codes', async () => {
  vi.restoreAllMocks()
  const data = {
    ...policyFixture(),
    clauses: [{
      ...policyFixture().clauses[0],
      control_code: null,
      classification: {
        status: 'UNSUPPORTED' as const,
        base_status: 'UNSUPPORTED' as const,
        method: 'CAPABILITY_REGISTRY',
        reason_codes: ['UNSUPPORTED_CAPABILITY'],
        conflicts_with: [],
        unsupported_capability: 'CYBERSECURITY_ASSESSMENT',
      },
    }],
  }
  vi.spyOn(api, 'getPolicyImport').mockResolvedValue(data)
  const client = renderPolicy()

  await screen.findByRole('button', { name: '收起高级审核' })
  await userEvent.click(screen.getByRole('button', { name: '收起高级审核' }))
  expect(screen.getByText('当前不支持自动执行')).toBeInTheDocument()
  expect(screen.getByText(/当前系统没有对应的检查器或数据来源/)).toBeInTheDocument()
  expect(screen.queryByLabelText('检查类型')).not.toBeInTheDocument()
  client.clear()
})
test('interrupted policy publication can be retried after reopening the page', async () => {
  vi.restoreAllMocks()
  const data = policyFixture('PUBLISHING')
  vi.spyOn(api, 'getPolicyImport').mockResolvedValue(data)
  const publish = vi.spyOn(api, 'publishPolicy').mockResolvedValue({ ...data, revision: 3, status: 'PUBLISHED', policy_index_version: 'pidx-restored' })
  const client = renderPolicy()
  await userEvent.click(await screen.findByRole('button', { name: '重试恢复发布' }))
  expect(await screen.findByText('制度已发布，可绑定采购任务')).toBeInTheDocument()
  expect(publish).toHaveBeenCalledWith('policy-test', 2, expect.any(String))
  client.clear()
})
