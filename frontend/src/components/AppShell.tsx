import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { api } from '../api/client'
import type { TaskListItem } from '../api/types'

const HISTORY_PAGE_SIZE = 8

function navClass({ isActive }: { isActive: boolean }) {
  return `nav-link${isActive ? ' nav-link-active' : ''}`
}

function taskStage(task: TaskListItem) {
  switch (task.status) {
    case 'QUEUED':
      return 'Queued'
    case 'RUNNING':
      return 'Analysing'
    case 'NEEDS_INPUT':
      return 'Action required'
    case 'COMPLETED':
      return 'Completed'
    case 'FAILED':
      return 'Failed'
    case 'ABANDONED':
      return 'Abandoned'
    default:
      return task.task_revision > 1 ? 'Quotation registered' : 'Requirements created'
  }
}

export function AppShell() {
  const [historyPage, setHistoryPage] = useState(0)
  const taskHistory = useQuery({
    queryKey: ['tasks', 'history', historyPage],
    queryFn: () => api.listTasks({ limit: HISTORY_PAGE_SIZE, offset: historyPage * HISTORY_PAGE_SIZE, sort: 'updated_desc' }),
    placeholderData: (previousData) => previousData,
    refetchInterval: 5_000,
  })
  const historyTotal = taskHistory.data?.total ?? 0
  const historyPageCount = Math.max(1, Math.ceil(historyTotal / HISTORY_PAGE_SIZE))

  return (
    <div className="app-shell">
      <nav className="sidebar" aria-label="Main navigation">
        <NavLink className="brand" to="/" aria-label="QuoteWise home">
          <span className="brand-mark" aria-hidden="true">Q</span>
          <span>
            <strong>QuoteWise</strong>
            <small>Supplier Intelligence</small>
          </span>
        </NavLink>
        <p className="nav-label">Procurement Workspace</p>
        <NavLink to="/" end className={navClass}>
          <span className="nav-icon" aria-hidden="true">▦</span>
          <span>Task Centre</span>
        </NavLink>
        <NavLink to="/tasks/new" className={navClass}>
          <span className="nav-icon" aria-hidden="true">＋</span>
          <span>New task</span>
        </NavLink>
        <NavLink to="/resources" className={navClass}>
          <span className="nav-icon" aria-hidden="true">▤</span>
          <span>Policy Library</span>
        </NavLink>
          <section className="sidebar-history" aria-label="Task History">
            <div className="sidebar-history-heading">
              <span>Task History</span>
              <small>{historyTotal}</small>
            </div>
            {taskHistory.isPending && (
              <p className="sidebar-history-message">Loading…</p>
            )}
            {taskHistory.isError && (
              <p className="sidebar-history-message">Temporarily unavailable</p>
            )}
            {taskHistory.data?.items.length === 0 && (
              <p className="sidebar-history-message">No tasks yet</p>
            )}
            <div className="history-task-list">
              {taskHistory.data?.items.map((task, index) => (
                <NavLink
                  className={({ isActive }) =>
                    'history-task' + (isActive ? ' history-task-active' : '')
                  }
                  key={task.task_id}
                  title={task.task_name}
                  to={'/tasks/' + task.task_id}
                >
                  <span className="history-task-index">
                    {String(historyPage * HISTORY_PAGE_SIZE + index + 1).padStart(2, '0')}
                  </span>
                  <span className="history-task-copy">
                    <strong>
                      {task.task_name}
                    </strong>
                    <small>{taskStage(task)}</small>
                  </span>
                  <span
                    className={
                      'history-task-status history-task-status-' +
                      task.status.toLowerCase().replaceAll('_', '-')
                    }
                    aria-label={task.status}
                  />
                </NavLink>
              ))}
            </div>
            {historyTotal > HISTORY_PAGE_SIZE && (
              <nav className="sidebar-history-pagination" aria-label="Task history pagination">
                <button type="button" aria-label="Previous task-history page" disabled={historyPage === 0 || taskHistory.isFetching} onClick={() => setHistoryPage((page) => Math.max(0, page - 1))}>‹</button>
                <span>{historyPage + 1} / {historyPageCount}</span>
                <button type="button" aria-label="Next task-history page" disabled={historyPage + 1 >= historyPageCount || taskHistory.isFetching} onClick={() => setHistoryPage((page) => Math.min(historyPageCount - 1, page + 1))}>›</button>
              </nav>
            )}
          </section>
          <div className="sidebar-note">
            <strong>Current Workspace</strong>
            <span>Requirements, quotation reviews and decision results are based on the current task revision.</span>
          </div>
      </nav>
      <div className="app-main">
        <header className="topbar">
          <div className="topbar-title">
            <strong>Supplier Selection Workspace</strong>
          </div>
          <div className="topbar-actions">
            <span className="user-avatar" aria-label="Current user">USER</span>
          </div>
        </header>
        <main className="page-content"><Outlet /></main>
      </div>
    </div>
  )
}
