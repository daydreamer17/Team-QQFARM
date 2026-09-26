import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { api } from '../src/api/client'
import type { ComparisonResultResponse, DecisionScenario, TaskDetail } from '../src/api/types'
import { DecisionScenarioWorkspace } from '../src/components/DecisionScenarioWorkspace'

afterEach(() => vi.unstubAllGlobals())

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}{location.search}:{location.state?.expectedRevision}</output>
}

const comparison = {
  disposition: 'FINAL',
  evaluated_at: '2026-09-22T00:00:00Z',
  rule_version: 'rules/1',
  ranked_quote_ids: ['quote-1'],
  supplier_results: [{
    status: 'FEASIBLE',
    quote_id: 'quote-1',
    quote_version: 1,
    supplier_name: 'Supplier One',
    goods_cost: '7000.00',
    known_cost_subtotal: '7000.00',
    total_cost: '7000.00',
    actual_quantity: 1000,
    estimated_arrival_date: '2026-09-19',
    failed_reasons: [],
    pending_reasons: [],
  }],
  pending_quote_ids: [],
  comparison_reasons: [],
  recommended_quote_ids: ['quote-1'],
  blocking_pending_quote_ids: [],
  final_recommendation_allowed: true,
}

const task = {
  task_id: 'task-1',
  task_name: 'SCENARIO-1',
  task_revision: 6,
  status: 'COMPLETED',
  scenario_id: 'SCENARIO-1',
  current_result_id: 'result-1',
  quotes: [{
    quote_id: 'quote-1',
    quote_version: 1,
    supplier_id: 'SUP-1',
    document_id: 'document-1',
    document_version: 1,
    original_filename: 'quote.pdf',
  }],
  requirement: {
    currency: 'SGD',
    ranking_preference: 'LOWEST_CONFIRMED_TOTAL_COST',
  },
} as TaskDetail

const result = {
  result_id: 'result-1',
  task_revision: 6,
  is_current: true,
  input_snapshot: null,
  result: comparison,
  policy_retrievals: [],
} as ComparisonResultResponse

const generatedScenario = {
  decision_scenario_id: 'decision-scenario-1',
  task_id: 'task-1',
  base_task_revision: 6,
  base_result_id: 'result-1',
  input_sha256: 'abc',
  status: 'READY',
  is_current: true,
  changes: { delivery_deadline: '2026-09-18' },
  baseline: comparison,
  simulated: {
    hypothetical: true,
    formal_recommendation_allowed: false,
    policy_assessment_performed: false,
    changes: { delivery_deadline: '2026-09-18' },
    decision_preferences: {
      schema_version: 'decision-preferences/1',
      primary_criterion: 'LOWEST_CONFIRMED_TOTAL_COST',
      secondary_criterion: null,
      excluded_supplier_ids: [],
      cost_tolerance_amount: null,
    },
    excluded_quote_ids: [],
    assumptions: [],
    comparison,
  },
  delta: {
    recommendation_changed: false,
    baseline_disposition: 'FINAL',
    simulated_disposition: 'FINAL',
    baseline_recommended_quote_ids: ['quote-1'],
    simulated_recommended_quote_ids: ['quote-1'],
    added_recommended_quote_ids: [],
    removed_recommended_quote_ids: [],
    supplier_deltas: [],
  },
  applied_task_revision: null,
  created_at: '2026-09-22T00:00:00Z',
  updated_at: '2026-09-22T00:00:00Z',
} as DecisionScenario

describe('DecisionScenarioWorkspace', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(api, 'listDecisionConversations').mockResolvedValue({
      task_id: 'task-1',
      task_revision: 6,
      items: [{
        conversation_id: 'conversation-1',
        task_id: 'task-1',
        base_task_revision: 6,
        base_result_id: 'result-1',
        status: 'ACTIVE',
        title: 'Decision Discussion',
        messages: [{
          message_id: 'message-1',
          sequence: 1,
          role: 'ASSISTANT',
          status: 'SUCCEEDED',
          content: '可以生成一个提前交付的情景。',
          reference_ids: ['RESULT:result-1'],
          proposed_changes: { delivery_deadline: '2026-09-18' },
          decision_intent_id: 'intent-1',
          reply_to_message_id: null,
          provider: 'fixed',
          model_id: 'fixed',
          prompt_version: 'conversation/1',
          attempts: 1,
          error_code: null,
          error_message: null,
          created_at: '2026-09-22T00:00:00Z',
        }],
        created_at: '2026-09-22T00:00:00Z',
        updated_at: '2026-09-22T00:00:00Z',
      }],
    })
    vi.spyOn(api, 'listDecisionScenarios').mockResolvedValue({
      task_id: 'task-1', task_revision: 6, items: [],
    })
    vi.spyOn(api, 'listDecisionIntents').mockResolvedValue({
      task_id: 'task-1', task_revision: 6, items: [],
    })
  })

  test('keeps the compact chat focused on the conversation instead of repeated metadata', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={queryClient}><MemoryRouter>
      <DecisionScenarioWorkspace task={task} result={result} compact />
    </MemoryRouter></QueryClientProvider>)

    await screen.findByText('可以生成一个提前交付的情景。')
    expect(screen.getByText('Hello! I’m QuoteWise. I can explain the recommendation, review quotation and policy evidence, and simulate changes to the budget or delivery deadline.')).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Current version · Decision Discussion' })).toBeInTheDocument()
    expect(screen.queryByText('SCENARIO-1')).not.toBeInTheDocument()
    expect(screen.queryByText('AI Decision Assistant')).not.toBeInTheDocument()
    expect(screen.queryByText('Completed')).not.toBeInTheDocument()
    expect(screen.getByText('View 1 sources')).toBeInTheDocument()
    expect(screen.queryByText('RESULT:result-1')).not.toBeInTheDocument()
    expect(screen.getByText('Suggested Questions').parentElement).not.toHaveTextContent('3')
  })

  test('opens Scenario management after confirming an intent and still allows collapse', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'confirmDecisionIntent').mockResolvedValue({
      decision_intent_id: 'intent-1',
      status: 'CONFIRMED',
      scenario: generatedScenario,
    })
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <DecisionScenarioWorkspace task={task} result={result} compact />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    await screen.findByText('可以生成一个提前交付的情景。')
    const summary = screen.getByText('Scenario management · 0')
    const manager = summary.closest('details')
    expect(manager).not.toHaveAttribute('open')

    await user.click(screen.getByRole('button', { name: 'Confirm and generate scenario' }))
    await waitFor(() => expect(manager).toHaveAttribute('open'))

    await user.click(summary)
    await waitFor(() => expect(manager).not.toHaveAttribute('open'))
  })

  test('renders a simulated recommendation as emphasis with a distinct hypothetical citation', async () => {
    const response = await api.listDecisionConversations('task-1')
    response.items[0].messages[0].content = '建议选择 **Sterling Semitech（SUP-030）**。（SIMULATION:preview-1）'
    response.items[0].messages[0].reference_ids = ['SIMULATION:preview-1']
    vi.mocked(api.listDecisionConversations).mockResolvedValue(response)
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={queryClient}><MemoryRouter>
      <DecisionScenarioWorkspace task={task} result={result} compact />
    </MemoryRouter></QueryClientProvider>)
    const winner = await screen.findByText('Sterling Semitech（SUP-030）')
    expect(winner.tagName).toBe('STRONG')
    expect(screen.getByText('Deterministic simulation for these conditions (not applied)')).toBeInTheDocument()
  })

  test('stale assistant messages keep their original content and cannot apply old proposals', async () => {
    const response = await api.listDecisionConversations('task-1')
    response.items[0].messages[0].status = 'STALE'
    vi.mocked(api.listDecisionConversations).mockResolvedValue(response)
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={queryClient}><MemoryRouter>
      <DecisionScenarioWorkspace task={task} result={result} compact />
    </MemoryRouter></QueryClientProvider>)
    expect(await screen.findByText('可以生成一个提前交付的情景。')).toBeInTheDocument()
    expect(screen.getByText(/The underlying evidence has changed. This response is retained for history/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Confirm and generate scenario' })).toBeDisabled()
  })

  test('restores the conversation selected by a historical citation link', async () => {
    const response = await api.listDecisionConversations('task-1')
    const original = response.items[0]
    response.items.push({ ...original, conversation_id: 'linked-conversation',
      messages: [{ ...original.messages[0], message_id: 'linked-message', content: 'HistoryCitations对应的Conversation。' }],
    })
    vi.mocked(api.listDecisionConversations).mockResolvedValue(response)
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[{ pathname: '/', state: { conversationId: 'linked-conversation' } }]}>
        <DecisionScenarioWorkspace task={task} result={result} compact />
      </MemoryRouter>
    </QueryClientProvider>)
    expect(await screen.findByText('HistoryCitations对应的Conversation。')).toBeInTheDocument()
    expect(screen.queryByText('可以生成一个提前交付的情景。')).not.toBeInTheDocument()
    queryClient.clear()
  })

  test('shows progress for the matching pending reply and ignores other turns', async () => {
    const handlers: Record<string, (event: Event) => void> = {}
    vi.stubGlobal('EventSource', class {
      addEventListener(name: string, callback: (event: Event) => void) { handlers[name] = callback }
      close() {}
    })
    const response = await api.listDecisionConversations('task-1')
    response.items[0].messages[0].role = 'USER'
    response.items[0].messages[0].content = '请模拟'
    response.items[0].messages[0].decision_intent_id = null
    response.items[0].messages[0].proposed_changes = null
    vi.mocked(api.listDecisionConversations).mockResolvedValue(response)
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={queryClient}><MemoryRouter>
      <DecisionScenarioWorkspace task={task} result={result} compact />
    </MemoryRouter></QueryClientProvider>)
    await waitFor(() => {
      expect(handlers['assistant.stage']).toBeDefined()
      expect(handlers['assistant.tool']).toBeDefined()
    })
    act(() => handlers['assistant.stage'](new MessageEvent('assistant.stage', {
      data: JSON.stringify({ reply_to_message_id: 'other', stage: 'narration' }),
    })))
    expect(screen.queryByText('Generating a factual explanation and validating citations')).not.toBeInTheDocument()
    act(() => handlers['assistant.stage'](new MessageEvent('assistant.stage', {
      data: JSON.stringify({ reply_to_message_id: 'message-1', stage: 'simulation' }),
    })))
    expect(await screen.findByText('正在按新条件进行确定性模拟；正式结果不会改变')).toBeInTheDocument()
    act(() => handlers['assistant.tool'](new MessageEvent('assistant.tool', {
      data: JSON.stringify({
        reply_to_message_id: 'message-1',
        tool_name: 'inspect_quote_evidence',
        status: 'OK',
        reason: '核对最低价Source Quotation',
      }),
    })))
    expect(await screen.findByText('核对报价原文: 已完成; 核对最低价Source Quotation')).toBeInTheDocument()
    queryClient.clear()
  })

  test('review-required failures provide a direct review link', async () => {
    const response = await api.listDecisionConversations('task-1')
    const message = response.items[0].messages[0]
    message.status = 'FAILED'
    message.content = null
    message.error_code = 'selection_review_required'
    message.error_message = '请先审核重新纳入的Supplier。'
    message.decision_intent_id = null
    message.proposed_changes = null
    vi.mocked(api.listDecisionConversations).mockResolvedValue(response)
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={queryClient}><MemoryRouter>
      <DecisionScenarioWorkspace task={task} result={result} compact />
    </MemoryRouter></QueryClientProvider>)
    expect(await screen.findByRole('link', { name: 'Go to action items' }))
      .toHaveAttribute('href', '/tasks/task-1/review#excluded-review')
  })

  test('uses user-facing statuses and can retry a failed generated answer', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('EventSource', class {
      addEventListener() {}
      close() {}
    })
    const response = await api.listDecisionConversations('task-1')
    const original = response.items[0].messages[0]
    const userMessage = {
      ...original,
      message_id: 'user-message',
      sequence: 1,
      role: 'USER' as const,
      content: '为什么没有选择最低价？',
      reference_ids: [],
      proposed_changes: null,
      decision_intent_id: null,
    }
    const failed = {
      ...original,
      message_id: 'failed-message',
      sequence: 2,
      status: 'FAILED',
      content: null,
      reference_ids: [],
      proposed_changes: null,
      decision_intent_id: null,
      reply_to_message_id: 'user-message',
      error_code: 'conversation_model_output_invalid',
      error_message: '本次回答未Passed事实核验。',
    }
    response.items[0].messages = [userMessage, failed]
    vi.mocked(api.listDecisionConversations).mockResolvedValue(response)
    vi.spyOn(api, 'sendDecisionMessage').mockResolvedValue({
      conversation_id: 'conversation-1',
      message: { ...userMessage, message_id: 'retry-message', sequence: 3 },
      job: {
        job_id: 'retry-job', task_id: 'task-1', graph_run_id: null,
        conversation_id: 'conversation-1', conversation_message_id: 'retry-message',
        issue_id: null, job_type: 'DECISION_CONVERSATION', status: 'PENDING',
        task_revision: 6, attempts: 0,
      },
    })
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    render(<QueryClientProvider client={queryClient}><MemoryRouter>
      <DecisionScenarioWorkspace task={task} result={result} compact />
    </MemoryRouter></QueryClientProvider>)

    expect(await screen.findByText('生成失败')).toBeInTheDocument()
    expect(screen.queryByText('SUCCEEDED')).not.toBeInTheDocument()
    expect(screen.queryByText('FAILED')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '重新生成' }))
    await waitFor(() => expect(api.sendDecisionMessage).toHaveBeenCalledWith(
      'task-1', 'conversation-1', 6, '为什么没有选择最低价？', expect.any(String),
    ))
    queryClient.clear()
  })

  test('applying a scenario opens the latest decision route and clears the old result cache', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.mocked(api.listDecisionScenarios).mockResolvedValue({
      task_id: 'task-1', task_revision: 6, items: [generatedScenario],
    })
    vi.spyOn(api, 'applyDecisionScenario').mockResolvedValue({
      task_id: 'task-1', task_revision: 7, status: 'QUEUED',
      decision_scenario_id: generatedScenario.decision_scenario_id,
      decision_profile_id: 'profile-7', changed_requirement_fields: ['delivery_deadline'],
      changed_decision_preference_fields: [], graph_run_id: 'graph-7', job_id: 'job-7', job_status: 'PENDING',
    })
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    queryClient.setQueryData(['tasks', 'task-1'], task)
    render(<QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/tasks/task-1/decision?result_id=result-1']}>
        <DecisionScenarioWorkspace task={task} result={result} compact />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>)
    await user.click(await screen.findByText('Scenario management · 1'))
    await user.click(screen.getByRole('button', { name: 'Apply and rerun full analysis' }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/tasks/task-1/decision:7'))
    expect(queryClient.getQueryData<TaskDetail>(['tasks', 'task-1'])?.current_result_id).toBeNull()
    expect(queryClient.getQueryData<TaskDetail>(['tasks', 'task-1'])?.task_revision).toBe(7)
  })
})
