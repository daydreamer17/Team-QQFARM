import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { api } from '../src/api/client'
import type { TaskListItem } from '../src/api/types'
import { AppShell } from '../src/components/AppShell'

const tasks: TaskListItem[] = Array.from({ length: 9 }, (_, index) => ({
  task_id: `task-${index + 1}`,
  task_name: `SCENARIO-${index + 1}`,
  task_revision: 1,
  status: 'COMPLETED',
  policy_binding: null,
  scenario_id: `SCENARIO-${index + 1}`,
  current_result_id: null,
  manufacturer: 'Demo Maker',
  manufacturer_part_number: `PART-${index + 1}`,
  planned_order_date: null,
  created_at: '2026-09-21T00:00:00Z',
  updated_at: '2026-09-21T00:00:00Z',
}))

describe('AppShell 历史任务分页', () => {
  beforeEach(() => vi.restoreAllMocks())

  test('废弃任务不会显示为报价已登记', async () => {
    vi.spyOn(api, 'listTasks').mockResolvedValue({
      items: [{ ...tasks[0], status: 'ABANDONED', task_revision: 2 }],
      total: 1, limit: 8, offset: 0, status_counts: { ABANDONED: 1 },
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><MemoryRouter><AppShell /></MemoryRouter></QueryClientProvider>)
    expect(await screen.findByText('已废弃')).toBeInTheDocument()
    expect(screen.queryByText('报价已登记')).not.toBeInTheDocument()
    client.clear()
  })

  test('每页请求八条并使用 offset 翻页', async () => {
    const listTasks = vi.spyOn(api, 'listTasks').mockImplementation(async (values = 8) => {
      const limit = typeof values === 'number' ? values : values.limit ?? 8
      const offset = typeof values === 'number' ? 0 : values.offset ?? 0
      return { items: tasks.slice(offset, offset + limit), total: tasks.length, limit, offset, status_counts: { COMPLETED: tasks.length } }
    })
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const user = userEvent.setup()

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <Routes>
            <Route element={<AppShell />}>
              <Route index element={<div>首页</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )

    const history = await screen.findByRole('region', { name: '历史任务' })
    expect(await within(history).findByText('SCENARIO-1')).toBeInTheDocument()
    expect(within(history).queryByText('SCENARIO-9')).not.toBeInTheDocument()
    expect(within(history).getByText('1 / 2')).toBeInTheDocument()

    await user.click(within(history).getByRole('button', { name: '下一页历史任务' }))

    await waitFor(() => expect(within(history).getByText('SCENARIO-9')).toBeInTheDocument())
    expect(within(history).getByText('09')).toBeInTheDocument()
    expect(listTasks).toHaveBeenLastCalledWith({ limit: 8, offset: 8, sort: 'updated_desc' })
  })
})
