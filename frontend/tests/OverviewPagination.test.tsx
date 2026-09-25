import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { api } from '../src/api/client'
import type { TaskListItem } from '../src/api/types'
import { OverviewPage } from '../src/pages/OverviewPage'

const tasks: TaskListItem[] = Array.from({ length: 17 }, (_, index) => ({
  task_id: `task-${index + 1}`,
  task_name: `采购任务 ${index + 1}`,
  task_revision: 1,
  status: 'COMPLETED',
  policy_binding: null,
  scenario_id: `SCENARIO-${index + 1}`,
  current_result_id: null,
  manufacturer: 'Demo Maker',
  manufacturer_part_number: `PART-${index + 1}`,
  planned_order_date: '2026-11-02',
  created_at: '2026-09-25T00:00:00Z',
  updated_at: '2026-09-25T00:00:00Z',
}))

describe('任务中心分页', () => {
  beforeEach(() => vi.restoreAllMocks())

  test('默认每页十条，并可切换为十五条后继续服务端翻页', async () => {
    vi.spyOn(api, 'healthReady').mockResolvedValue({ status: 'ready' })
    const listTasks = vi.spyOn(api, 'listTasks').mockImplementation(async (values = 8) => {
      const limit = typeof values === 'number' ? values : values.limit ?? 8
      const offset = typeof values === 'number' ? 0 : values.offset ?? 0
      return {
        items: tasks.slice(offset, offset + limit),
        total: tasks.length,
        limit,
        offset,
        status_counts: { COMPLETED: tasks.length },
      }
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const user = userEvent.setup()

    render(<QueryClientProvider client={client}><MemoryRouter><OverviewPage /></MemoryRouter></QueryClientProvider>)

    expect(await screen.findByText('采购任务 10')).toBeInTheDocument()
    expect(screen.queryByText('采购任务 11')).not.toBeInTheDocument()
    expect(screen.getByText('第 1–10 条，共 17 条')).toBeInTheDocument()
    expect(listTasks).toHaveBeenLastCalledWith({ limit: 10, offset: 0, query: '', status: undefined, sort: 'updated_desc' })

    await user.selectOptions(screen.getByLabelText('每页任务数'), '15')
    expect(await screen.findByText('采购任务 15')).toBeInTheDocument()
    expect(screen.queryByText('采购任务 16')).not.toBeInTheDocument()
    expect(listTasks).toHaveBeenLastCalledWith({ limit: 15, offset: 0, query: '', status: undefined, sort: 'updated_desc' })

    await user.click(screen.getByRole('button', { name: '下一页' }))
    await waitFor(() => expect(screen.getByText('采购任务 16')).toBeInTheDocument())
    expect(screen.getByText('第 16–17 条，共 17 条')).toBeInTheDocument()
    expect(listTasks).toHaveBeenLastCalledWith({ limit: 15, offset: 15, query: '', status: undefined, sort: 'updated_desc' })
    client.clear()
  })
})
