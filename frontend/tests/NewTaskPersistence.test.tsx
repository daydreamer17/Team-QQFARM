import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { api } from '../src/api/client'
import type { RequirementDraftResponse } from '../src/api/types'
import { NewTaskPage } from '../src/pages/NewTaskPage'

const readyDraft: RequirementDraftResponse = {
  requirement_draft_id: 'reqdraft-persisted',
  draft_revision: 1,
  status: 'READY',
  original_filename: 'procurement_requirement.txt',
  media_type: 'text/plain',
  size_bytes: 128,
  document_sha256: 'a'.repeat(64),
  parsed: { sources: [] },
  candidates: [{
    field_name: 'manufacturer',
    raw_value: 'Parsed Maker',
    normalized_value: 'Parsed Maker',
    validation_status: 'EXTRACTED',
    origin: 'DOCUMENT',
    source_refs: [{ source_id: 'line-1', quoted_text: 'Manufacturer: Parsed Maker' }],
  }],
  calls_used: 1,
  max_calls: 3,
  provider: 'fixed',
  model_id: 'fixed-model',
  environment: 'test',
  prompt_version: 'requirement/1',
  error_code: null,
  error_message: null,
  submitted_task_id: null,
  job: null,
  created_at: '2026-09-21T00:00:00Z',
  updated_at: '2026-09-21T00:00:01Z',
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/tasks/new']}>
        <NewTaskPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('NewTaskPage 未提交Draft恢复', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    window.sessionStorage.clear()
  })

  test('离开页面再Back后保留解析结果和用户修正', async () => {
    vi.spyOn(api, 'listPolicySets').mockResolvedValue({ items: [], total: 0, limit: 100, offset: 0 })
    const upload = vi.spyOn(api, 'uploadRequirementDraft').mockResolvedValue(readyDraft)
    vi.spyOn(api, 'getRequirementDraft').mockResolvedValue(readyDraft)
    const user = userEvent.setup()
    const firstRender = renderPage()
    const fileInput = firstRender.container.querySelector<HTMLInputElement>('input[type="file"]')
    expect(fileInput).not.toBeNull()

    await user.upload(fileInput!, new File(['Manufacturer: Parsed Maker'], 'procurement_requirement.txt', { type: 'text/plain' }))
    await user.click(screen.getByRole('button', { name: 'Parse and populate' }))
    await waitFor(() => expect(screen.getByLabelText('Manufacturer')).toHaveValue('Parsed Maker'))
    await user.clear(screen.getByLabelText('Manufacturer'))
    await user.type(screen.getByLabelText('Manufacturer'), '用户修正Manufacturer')
    await waitFor(() => expect(window.sessionStorage.getItem('quotewise.new-task.v1')).not.toBeNull())

    firstRender.unmount()
    renderPage()

    expect(await screen.findByRole('status')).toHaveTextContent('Your unsaved procurement task has been restored. Continue reviewing or create the task.')
    expect(screen.getByLabelText('Manufacturer')).toHaveValue('用户修正Manufacturer')
    expect(screen.getByText('procurement_requirement.txt')).toBeInTheDocument()
    expect(screen.getByText('Parsed')).toBeInTheDocument()
    expect(upload).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: 'Clear' }))
    expect(window.sessionStorage.getItem('quotewise.new-task.v1')).toBeNull()
  })
})
