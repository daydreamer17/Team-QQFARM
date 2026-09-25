import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { api } from '../src/api/client'
import type { SupplierInformationResponse, TaskDetail } from '../src/api/types'
import { SupplierInfoPage } from '../src/pages/SupplierInfoPage'

const task: TaskDetail = {
  task_id: 'task-1',
  task_name: 'PREFERENCE-DEMO-001',
  task_revision: 8,
  status: 'COMPLETED',
  scenario_id: 'PREFERENCE-DEMO-001',
  current_graph_run_id: 'run-1',
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
    decision_profile_id: 'profile-1',
    task_revision: 8,
    profile_version: 1,
    preferences: {
      schema_version: 'decision-preferences/2.0.0',
      primary_criterion: 'HIGHEST_HISTORICAL_ON_TIME_RATE',
      secondary_criterion: 'LOWEST_CONFIRMED_TOTAL_COST',
      excluded_supplier_ids: [],
      cost_tolerance_amount: null,
    },
    source_scenario_id: null,
  },
  supplier_history_binding: {
    binding_status: 'AVAILABLE',
    dataset_version: '2026-08-06-v1',
    as_of_date: '2026-08-06',
    is_synthetic: true,
  },
  current_issue: null,
  current_job: null,
  quotes: [],
  requirement: {
    manufacturer: 'QQ Demo Components',
    manufacturer_part_number: 'QW-MCU9-DEMO',
    package: 'QFN-32',
    revision: 'R1',
    condition: 'NEW',
    allow_substitutes: false,
    base_unit: 'piece',
    required_quantity: 1000,
    quantity_unit: 'piece',
    budget_amount: '8000.00',
    currency: 'SGD',
    includes_shipping: true,
    tax_mode: 'EXCLUDED',
    other_fees_required: true,
    planned_order_date: '2026-10-10',
    delivery_deadline: '2026-10-20',
    delivery_location: 'SG-DEMO-01',
    ranking_preference: 'HIGHEST_HISTORICAL_ON_TIME_RATE',
    secondary_preference: 'LOWEST_CONFIRMED_TOTAL_COST',
  },
}

const supplierInformation: SupplierInformationResponse = {
  schema_version: 'supplier-information/1.0.0',
  task_id: 'task-1',
  snapshot_revision: 7,
  result_id: 'result-old',
  snapshot_id: 'snapshot-old',
  view_state: 'HISTORICAL_RESULT',
  is_current: false,
  context_sha256: 'frozen-context-sha',
  data_availability: 'RECORDED',
  dataset_status: 'AVAILABLE',
  history_dataset_context: {
    category: 'Electronics',
    item: 'Microcontroller MCU-9',
    dataset_version: '2026-08-06-v1',
    as_of_date: '2026-08-06',
    is_synthetic: true,
  },
  history_binding: { binding_status: 'AVAILABLE', dataset_version: '2026-08-06-v1' },
  effective_preferences: {
    schema_version: 'decision-preferences/2.0.0',
    primary_criterion: 'HIGHEST_HISTORICAL_ON_TIME_RATE',
    secondary_criterion: 'LOWEST_CONFIRMED_TOTAL_COST',
    excluded_supplier_ids: [],
    cost_tolerance_amount: null,
  },
  ranking_trace: {
    ordered_criteria: ['HIGHEST_HISTORICAL_ON_TIME_RATE', 'LOWEST_CONFIRMED_TOTAL_COST'],
    excluded_quote_ids: [],
    secondary_applied: true,
    tie_group: [],
    comparison_disposition: 'RANKED',
  },
  quote_count: 2,
  matched_supplier_count: 2,
  unresolved_identity_quote_count: 0,
  draft_count: null,
  inactive_quote_count: null,
  suppliers: [
    {
      supplier_identity_id: 'SUP-024',
      display_name: 'Sterling Components',
      supplier_id: 'SUP-024',
      identity_match_status: 'MATCHED',
      history_availability_status: 'AVAILABLE',
      history_snapshot: {
        quote_id: 'quote-1', supplier_id: 'SUP-024', supplier_name: 'Sterling Components',
        identity_match_status: 'MATCHED', history_availability_status: 'AVAILABLE', overall_grade: 'A',
        on_time: { numerator: 51, denominator: 55, rate: '0.9272727273' },
        rejected_lines: { numerator: 0, denominator: 55, rate: '0' }, evidence_refs: ['HISTORY:SUP-024'],
      },
      quotes: [{
        quote_id: 'quote-1', quote_version: 1, active: null, in_scope_at_result: true,
        document_id: 'doc-1', document_sha256: 'sha-1', is_synthetic: true,
        evaluation: {
          status: 'FEASIBLE', quote_id: 'quote-1', quote_version: 1, supplier_name: 'Sterling Components',
          goods_cost: '7100.00', known_cost_subtotal: '7100.00', total_cost: '7100.00', actual_quantity: 1000,
          estimated_arrival_date: '2026-10-18', failed_reasons: [], pending_reasons: [],
        },
        policy_assessment: null, decision_impact: null,
      }],
    },
    {
      supplier_identity_id: 'SUP-022',
      display_name: 'Redwood Components',
      supplier_id: 'SUP-022',
      identity_match_status: 'MATCHED',
      history_availability_status: 'AVAILABLE',
      history_snapshot: {
        quote_id: 'quote-2', supplier_id: 'SUP-022', supplier_name: 'Redwood Components',
        identity_match_status: 'MATCHED', history_availability_status: 'AVAILABLE', overall_grade: 'B',
        on_time: { numerator: 74, denominator: 86, rate: '0.8604651163' },
        rejected_lines: { numerator: 2, denominator: 86, rate: '0.0232558140' }, evidence_refs: ['HISTORY:SUP-022'],
      },
      quotes: [{
        quote_id: 'quote-2', quote_version: 1, active: null, in_scope_at_result: true,
        document_id: 'doc-2', document_sha256: 'sha-2', is_synthetic: true,
        evaluation: {
          status: 'FEASIBLE', quote_id: 'quote-2', quote_version: 1, supplier_name: 'Redwood Components',
          goods_cost: '6900.00', known_cost_subtotal: '6900.00', total_cost: '6900.00', actual_quantity: 1000,
          estimated_arrival_date: '2026-10-17', failed_reasons: [], pending_reasons: [],
        },
        policy_assessment: null, decision_impact: null,
      }],
    },
  ],
  unresolved_identity_quotes: [],
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/tasks/task-1/suppliers?result_id=result-old']}>
        <Routes><Route path="/tasks/:taskId/suppliers" element={<SupplierInfoPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('SupplierInfoPage', () => {
  beforeEach(() => vi.restoreAllMocks())

  test('renders the frozen historical dataset and switches supplier details', async () => {
    vi.spyOn(api, 'getTask').mockResolvedValue(task)
    vi.spyOn(api, 'getSupplierInformation').mockResolvedValue(supplierInformation)

    renderPage()

    const context = (await screen.findByText('Historical')).closest('.supplier-context')!
    expect(within(context).getByText('2026-08-06')).toBeInTheDocument()
    expect(within(context).getByText('Synthetic')).toBeInTheDocument()
    const overview = screen.getByLabelText('Supplier overview')
    expect(within(overview).getAllByText('2')).toHaveLength(2)
    expect(within(overview).getByText('Matched')).toBeInTheDocument()
    expect(overview.closest('.supplier-context')).toBeInTheDocument()
    const chart = screen.getByRole('img', { name: 'Supplier historical on-time and rejected order-line rates' })
    expect(chart).toBeInTheDocument()
    expect(chart.querySelectorAll('.supplier-bubble-index')).toHaveLength(2)
    expect(chart.querySelectorAll('.supplier-bubble-label')).toHaveLength(0)
    const chartLegend = screen.getByLabelText('Supplier chart legend')
    expect(within(chartLegend).getByRole('button', { name: /Sterling Components/ })).toBeInTheDocument()
    expect(within(chartLegend).getByRole('button', { name: /Redwood Components/ })).toBeInTheDocument()
    expect(screen.getByText('92.7%')).toBeInTheDocument()
    expect(screen.queryByText('Current第 7 版')).not.toBeInTheDocument()
    expect(screen.getByText('Historical result · Revision 7')).toBeInTheDocument()

    const supplierList = document.querySelector('.supplier-list-card')!
    expect(within(supplierList).getByText('SUP-024 · Feasible')).toBeInTheDocument()
    await userEvent.click(within(supplierList).getByRole('button', { name: /Redwood Components/ }))
    const detail = screen.getByText('SELECTED').closest('section')!
    expect(within(detail).getByRole('heading', { name: 'Redwood Components' })).toBeInTheDocument()
    expect(within(detail).getByText('86.0%')).toBeInTheDocument()
    expect(within(detail).getByText('SGD 6900.00')).toBeInTheDocument()
    expect(within(detail).getByText('Yes')).toBeInTheDocument()
  })

  test('does not render a quote twice when a legacy response repeats it as unresolved', async () => {
    const duplicate = supplierInformation.suppliers[0]
    vi.spyOn(api, 'getTask').mockResolvedValue(task)
    vi.spyOn(api, 'getSupplierInformation').mockResolvedValue({
      ...supplierInformation,
      unresolved_identity_quote_count: 1,
      unresolved_identity_quotes: [{
        ...duplicate.quotes[0],
        display_name: duplicate.display_name,
        supplier_id: duplicate.supplier_id,
        identity_match_status: 'REVIEW_REQUIRED',
        history_availability_status: 'NOT_RECORDED',
      }],
    })

    renderPage()

    await screen.findByLabelText('Supplier overview')
    const supplierList = document.querySelector('.supplier-list-card')!
    expect(within(supplierList).getAllByRole('button')).toHaveLength(2)
  })

  test('explains why active quotes and the frozen comparison scope have different counts', async () => {
    vi.spyOn(api, 'getTask').mockResolvedValue({
      ...task,
      current_result_id: 'result-current',
      quotes: [
        { quote_id: 'quote-1', quote_version: 1, supplier_id: 'SUP-024', document_id: 'doc-1', document_version: 1, original_filename: 'one.pdf' },
        { quote_id: 'quote-2', quote_version: 1, supplier_id: 'SUP-022', document_id: 'doc-2', document_version: 1, original_filename: 'two.pdf' },
        { quote_id: 'quote-3', quote_version: 1, supplier_id: 'SUP-030', document_id: 'doc-3', document_version: 1, original_filename: 'excluded.pdf' },
      ],
    })
    vi.spyOn(api, 'getSupplierInformation').mockResolvedValue({
      ...supplierInformation,
      result_id: 'result-current',
      snapshot_revision: 8,
      view_state: 'CURRENT_RESULT',
      is_current: true,
      effective_preferences: {
        ...supplierInformation.effective_preferences!,
        excluded_supplier_ids: ['SUP-030'],
      },
    })

    renderPage()

    expect(await screen.findByText('3 active · 1 excluded · 2 compared')).toBeInTheDocument()
    const overview = screen.getByLabelText('Supplier overview')
    expect(within(overview).getByText('Compared')).toBeInTheDocument()
    expect(within(overview).getByText('Excluded')).toBeInTheDocument()
    expect(screen.getByText('Excludes SUP-030 under the current settings')).toBeInTheDocument()
  })
})
