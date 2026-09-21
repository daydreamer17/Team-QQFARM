import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { api, ApiClientError } from '../src/api/client'
import type { ComparisonResultResponse, ProcurementRequirement, TaskDetail } from '../src/api/types'
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
            content: '这是历史第五版的回答。',
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
    expect(screen.getByText('这是历史第五版的回答。')).toBeInTheDocument()
    expect(screen.queryByText('这是当前第六版的回答。')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '重新分析' })).not.toBeInTheDocument()
    await waitFor(() => expect(fields).toHaveBeenCalledWith('task-1', 'quote-1', 'result-old'))
    expect(conversations).toHaveBeenCalledWith('task-1', 'result-old')

    await userEvent.click(screen.getByRole('button', { name: '查看引用 [2] 供应商报价' }))
    expect(screen.getByRole('dialog', { name: 'Supplier One 字段证据' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '关闭' }))
    await userEvent.click(screen.getByRole('button', { name: '查看引用 [3] 制度证据' }))
    expect(screen.getByRole('dialog', { name: '制度引用详情' })).toHaveTextContent(
      'A current supplier registry record is required before approval.',
    )
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

  test('compliance page never falls back to a historical result', async () => {
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

    expect(await screen.findByRole('heading', { name: '当前版本没有有效决策结果' })).toBeInTheDocument()
    expect(screen.getByText(/历史结果不会作为当前制度结论显示/)).toBeInTheDocument()
    expect(history).not.toHaveBeenCalled()
  })

  test('historical summary renders its frozen requirement instead of the current task', async () => {
    vi.spyOn(api, 'getTask').mockResolvedValue(makeTask({ current_result_id: null }))
    vi.spyOn(api, 'getResult').mockResolvedValue(makeHistoricalResult())
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

  test('policy upload asks for business fields and hides empty history filters', async () => {
    vi.spyOn(api, 'listPolicyImports').mockResolvedValue({
      items: [], total: 0, limit: 8, offset: 0,
    })
    vi.spyOn(api, 'listPolicySets').mockResolvedValue({
      items: [], total: 0, limit: 8, offset: 0,
    })

    renderRoute('/resources', '/resources', <ResourcePage />)

    expect(await screen.findByRole('heading', { name: '规则资源库' })).toBeInTheDocument()
    expect(screen.getByLabelText('制度标题')).toBeInTheDocument()
    expect(screen.queryByLabelText('策略集 ID')).not.toBeInTheDocument()
    expect(screen.queryByText('KNOWLEDGE RESOURCES')).not.toBeInTheDocument()
    expect(await screen.findByText('暂无导入记录。')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '筛选' })).not.toBeInTheDocument()
  })

  test('new task form starts empty and clear form removes every selected default', async () => {
    vi.spyOn(api, 'listPolicySets').mockResolvedValue({
      items: [], total: 0, limit: 100, offset: 0,
    })

    renderRoute('/tasks/new', '/tasks/new', <NewTaskPage />)

    expect(await screen.findByRole('heading', { name: '创建采购任务' })).toBeInTheDocument()
    for (const label of [
      '制造商', '制造商料号', '封装', '物料版本', '物料状态',
      '数量单位', '币种', '成本比较口径', '计划下单日期 可选',
      '交付截止日期', '交付地点', '主要排序偏好',
    ]) {
      expect(screen.getByLabelText(label), label).toHaveValue('')
    }
    expect(screen.getByLabelText('需求数量')).toHaveValue(null)
    expect(screen.getByLabelText(/预算金额/)).toHaveValue('')
    expect(screen.getByLabelText('允许替代料')).not.toBeChecked()
    expect(screen.getByLabelText('预算包含运费')).not.toBeChecked()
    expect(screen.getByLabelText('要求计入其他费用')).not.toBeChecked()

    await userEvent.type(screen.getByLabelText('制造商'), 'Example Maker')
    await userEvent.selectOptions(screen.getByLabelText('物料状态'), 'NEW')
    await userEvent.selectOptions(screen.getByLabelText('币种'), 'SGD')
    await userEvent.click(screen.getByLabelText('预算包含运费'))
    await userEvent.click(screen.getByRole('button', { name: '清空表单' }))

    expect(screen.getByLabelText('制造商')).toHaveValue('')
    expect(screen.getByLabelText('物料状态')).toHaveValue('')
    expect(screen.getByLabelText('币种')).toHaveValue('')
    expect(screen.getByLabelText('预算包含运费')).not.toBeChecked()
  })
})
