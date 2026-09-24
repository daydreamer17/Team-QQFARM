import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { api } from '../src/api/client'
import type { InvestigationCase, TaskDetail } from '../src/api/types'
import { InvestigationPage } from '../src/pages/InvestigationPage'

const task: TaskDetail = {
  task_id: 'task-1', task_revision: 6, status: 'COMPLETED', scenario_id: 'AGENT-DEMO',
  current_graph_run_id: 'graph-1', current_snapshot_id: 'snapshot-1', current_result_id: 'result-1',
  summary_completed: false,
  progress: { requirement_completed: true, quote_review_completed: true, decision_completed: true, summary_completed: false },
  policy_binding: null, current_issue: null, current_job: null,
  decision_profile: {
    decision_profile_id: null, task_revision: 6, profile_version: 1,
    preferences: { ranking_mode: 'LOWEST_CONFIRMED_TOTAL_COST', excluded_supplier_ids: [], cost_tolerance_amount: null },
    source_scenario_id: null,
  },
  quotes: [{
    quote_id: 'quote-1', quote_version: 1, supplier_id: 'SUP-1', document_id: 'doc-1',
    document_version: 1, original_filename: 'supplier.pdf',
  }],
  requirement: {
    manufacturer: 'Maker', manufacturer_part_number: 'PART-1', package: 'QFN-32', revision: 'R1',
    condition: 'NEW', allow_substitutes: false, base_unit: 'piece', required_quantity: 1000,
    quantity_unit: 'piece', budget_amount: '8000.00', currency: 'SGD', includes_shipping: true,
    tax_mode: 'EXCLUDED', other_fees_required: false, planned_order_date: '2026-09-24',
    delivery_deadline: '2026-10-20', delivery_location: 'SG',
    ranking_preference: 'LOWEST_CONFIRMED_TOTAL_COST', secondary_preference: null,
  },
}

const investigation: InvestigationCase = {
  schema_version: 'investigation/1.0.0', case_id: 'case-1', artifact_id: 'artifact-1',
  task_id: 'task-1', task_revision: 6, graph_run_id: 'graph-1', kind: 'QUOTE', quote_id: 'quote-1',
  quote_version: 1, impact_input_sha256: 'a'.repeat(64), policy_binding: {},
  goal: '先分析报价差距，再生成未发送的澄清草稿。',
  known_facts: { requested_investigation: true }, unknown_fields: [], impact_status: 'REQUIRES_INVESTIGATION',
  plan: ['分析差距', '形成澄清草稿'], status: 'RESOLVED', stored_status: 'RESOLVED',
  stop_reason: 'REQUEST_COMPLETED', model_calls: 2, model_id: 'test-agent', error_code: null,
  started_at: '2026-09-24T00:00:00Z', clarification: [], is_current: true,
  observations: [{
    sequence: 1, reason: '先读取确定性差距', arguments: {}, latency_ms: 3,
    result: { tool_name: 'analyze_selection_gap', task_id: 'task-1', task_revision: 6, quote_id: 'quote-1', input_sha256: 'a'.repeat(64), status: 'OK', data: { cost_difference_vs_other: '200.00' }, sources: [], error_code: null },
  }, {
    sequence: 2, reason: '根据差距生成草稿', arguments: {}, latency_ms: 2,
    result: { tool_name: 'draft_clarification', task_id: 'task-1', task_revision: 6, quote_id: 'quote-1', input_sha256: 'a'.repeat(64), status: 'OK', data: { text: '请确认最终运费。' }, sources: [], error_code: null },
  }],
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/tasks/task-1/investigations']}>
        <Routes><Route path="/tasks/:taskId/investigations" element={<InvestigationPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('InvestigationPage', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(api, 'getTask').mockResolvedValue(task)
    vi.spyOn(api, 'listInvestigations').mockResolvedValue([investigation])
  })

  test('shows the historical trace without a separate investigation form', async () => {
    renderPage()
    expect(await screen.findByText(/分析入选差距/)).toBeInTheDocument()
    expect(screen.getByText('请确认最终运费。')).toBeInTheDocument()
    expect(screen.getByText('调查目标已完成')).toBeInTheDocument()
    expect(screen.queryByLabelText('调查目标')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: '返回决策结果' })).toBeInTheDocument()
  })

  test('shows the actual hypothetical winner in an old simulation record', async () => {
    vi.spyOn(api, 'listInvestigations').mockResolvedValue([{
      ...investigation,
      observations: [{
        ...investigation.observations[0],
        result: { ...investigation.observations[0].result,
          tool_name: 'simulate_requirement_change',
          data: { changes: { budget_amount: '9000.00' },
            comparison: { recommended_quote_ids: ['quote-1'], supplier_results: [{ quote_id: 'quote-1', supplier_name: 'Alpha' }] } },
        },
      }],
    }])
    renderPage()
    expect(await screen.findByText(/试算推荐：Alpha/)).toBeInTheDocument()
  })
})
