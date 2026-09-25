import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { api } from '../src/api/client'
import type { TaskDetail } from '../src/api/types'
import { QuoteUploadPage } from '../src/pages/QuoteUploadPage'


const task = {
  task_id: 'task-1', task_revision: 1, status: 'DRAFT', scenario_id: null,
  current_graph_run_id: null, current_snapshot_id: null, current_result_id: null,
  summary_completed: false,
  progress: { requirement_completed: true, quote_review_completed: false, decision_completed: false, summary_completed: false },
  policy_binding: null, current_issue: null, current_job: null, quotes: [],
  decision_profile: {
    decision_profile_id: null, task_revision: null, profile_version: 1,
    preferences: { primary_criterion: null, secondary_criterion: null, excluded_supplier_ids: [], cost_tolerance_amount: null },
    source_scenario_id: null,
  },
  requirement: {
    manufacturer: 'Maker', manufacturer_part_number: 'PART-1', package: 'QFN-32', revision: 'R1',
    condition: 'NEW', allow_substitutes: false, base_unit: 'piece', required_quantity: 1000,
    quantity_unit: 'piece', budget_amount: '8000.00', currency: 'SGD', includes_shipping: true,
    tax_mode: 'EXCLUDED', other_fees_required: false, planned_order_date: '2026-09-24',
    delivery_deadline: '2026-10-20', delivery_location: 'SG',
    ranking_preference: 'LOWEST_CONFIRMED_TOTAL_COST', secondary_preference: null,
  },
} as unknown as TaskDetail


function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/tasks/task-1/quotes/new']}>
        <Routes><Route path="/tasks/:taskId/quotes/new" element={<QuoteUploadPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}


describe('QuoteUploadPage supplier identification', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(api, 'getTask').mockResolvedValue(task)
    vi.spyOn(api, 'listQuotes').mockResolvedValue({ task_id: 'task-1', task_revision: 1, items: [] })
    vi.spyOn(api, 'listQuoteDrafts').mockResolvedValue({ task_id: 'task-1', task_revision: 1, items: [] })
  })

  test('fills the supplier ID after selecting a quote file and keeps it editable', async () => {
    vi.spyOn(api, 'identifyQuoteSupplier').mockResolvedValue({
      status: 'FOUND', supplier_id: 'SUP-029', candidates: ['SUP-029'], source: 'CSV_FIELD',
    })
    renderPage()
    const user = userEvent.setup()
    const supplier = await screen.findByLabelText(/Supplier ID/)
    const file = screen.getByLabelText(/^Quotation Document/)

    await user.upload(file, new File(['supplier_id,price\nSUP-029,6.20\n'], 'quote.csv', { type: 'text/csv' }))
    await waitFor(() => expect(supplier).toHaveValue('SUP-029'))
    expect(screen.getByText('Identified from the file. You may edit it before continuing.')).toBeInTheDocument()

    await user.clear(supplier)
    await user.type(supplier, 'SUP-MANUAL')
    expect(supplier).toHaveValue('SUP-MANUAL')

    await user.upload(file, new File(['supplier_id,price\nSUP-029,6.30\n'], 'revised.csv', { type: 'text/csv' }))
    await screen.findByText(/your manually entered value was retained/)
    expect(supplier).toHaveValue('SUP-MANUAL')
  })

  test('clears an earlier automatic value when the next file has no explicit ID', async () => {
    vi.spyOn(api, 'identifyQuoteSupplier')
      .mockResolvedValueOnce({ status: 'FOUND', supplier_id: 'SUP-029', candidates: ['SUP-029'], source: 'CSV_FIELD' })
      .mockResolvedValueOnce({ status: 'NOT_FOUND', supplier_id: null, candidates: [], source: 'CSV_FIELD' })
    renderPage()
    const user = userEvent.setup()
    const supplier = await screen.findByLabelText(/Supplier ID/)
    const file = screen.getByLabelText(/^Quotation Document/)

    await user.upload(file, new File(['supplier_id\nSUP-029\n'], 'one.csv', { type: 'text/csv' }))
    await waitFor(() => expect(supplier).toHaveValue('SUP-029'))
    await user.upload(file, new File(['supplier_name\nExample\n'], 'two.csv', { type: 'text/csv' }))
    await screen.findByText('No clear supplier ID was found in the file. Enter it manually.')
    expect(supplier).toHaveValue('')
  })
})
