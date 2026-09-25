import { useQuery } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiClientError } from '../api/client'
import type { TaskListItem } from '../api/types'
import { TablePagination } from '../components/TablePagination'

const EMPTY_TASKS: TaskListItem[] = []

const statusLabels: Record<string, string> = {
  DRAFT: 'Draft',
  QUEUED: 'Queued',
  RUNNING: 'Analysing',
  NEEDS_INPUT: 'Action required',
  COMPLETED: 'Completed',
  FAILED: 'Failed',
  ABANDONED: 'Abandoned',
}

function getErrorMessage(error: unknown) {
  if (error instanceof ApiClientError) return error.message
  return 'Backend connectivity check failed.'
}

function taskTitle(task: TaskListItem) {
  return task.task_name
}

function statusTone(status: string) {
  if (status === 'COMPLETED') return 'status-ready'
  if (status === 'QUEUED' || status === 'RUNNING') return 'status-pending'
  if (status === 'NEEDS_INPUT' || status === 'FAILED') return 'status-offline'
  return 'status-muted'
}

function displayDate(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('en-SG', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function displayDay(value: string | null) {
  if (!value) return 'Not set'
  const date = new Date(`${value}T00:00:00`)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('en-SG', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }).format(date)
}

type TaskSort = 'updated_desc' | 'planned_asc' | 'planned_desc' | 'created_desc'

export function OverviewPage() {
  const [search, setSearch] = useState('')
  const [serverQuery, setServerQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('ALL')
  const [sortBy, setSortBy] = useState<TaskSort>('updated_desc')
  const [pageSize, setPageSize] = useState<10 | 15>(10)
  const [offset, setOffset] = useState(0)
  const searchTimer = useRef<number | null>(null)
  const health = useQuery({
    queryKey: ['health', 'ready'],
    queryFn: api.healthReady,
    refetchInterval: 30_000,
  })
  const tasks = useQuery({
    queryKey: ['tasks', 'center', serverQuery, statusFilter, sortBy, pageSize, offset],
    queryFn: () => api.listTasks({ limit: pageSize, offset, query: serverQuery, status: statusFilter === 'ALL' ? undefined : statusFilter, sort: sortBy }),
    refetchInterval: 5_000,
  })
  const items = tasks.data?.items ?? EMPTY_TASKS
  const counts = tasks.data?.status_counts ?? {}
  const waitingCount = counts.NEEDS_INPUT ?? 0
  const runningCount = (counts.QUEUED ?? 0) + (counts.RUNNING ?? 0)
  const completedCount = counts.COMPLETED ?? 0
  const isReady = health.data?.status === 'ready'
  const visibleItems = items
  const total = tasks.data?.total ?? 0
  const page = Math.floor(offset / pageSize)
  const pageCount = Math.max(1, Math.ceil(total / pageSize))

  function updateSearch(value: string) {
    setSearch(value)
    if (searchTimer.current !== null) window.clearTimeout(searchTimer.current)
    searchTimer.current = window.setTimeout(() => { setOffset(0); setServerQuery(value.trim()) }, 300)
  }

  return (
    <div className="page-stack task-center-page">
      <section className="page-heading app-page-heading task-center-heading">
        <div>
          <h1>Task Centre</h1>
          <p>View and manage procurement tasks.</p>
        </div>
        <Link className="button button-submit" to="/tasks/new">+ Create procurement task</Link>
      </section>

      <section className="task-center-metrics" aria-label="Task overview">
        <article><span>All tasks</span><strong>{Object.values(counts).reduce((sum, count) => sum + count, 0)}</strong></article>
        <article><span>Action required</span><strong>{waitingCount}</strong></article>
        <article><span>In progress</span><strong>{runningCount}</strong></article>
        <article><span>Completed</span><strong>{completedCount}</strong></article>
      </section>

      {!isReady && !health.isPending && (
        <div className="service-warning" role="alert">{getErrorMessage(health.error)}</div>
      )}

      <section className="task-list-panel">
        <div className="section-heading task-list-heading">
          <h2>Recent Procurement Tasks</h2>
        </div>

        <div className="task-list-tools">
          <label className="task-search">
            <span className="visually-hidden">Search procurement tasks</span>
            <span className="task-search-icon" aria-hidden="true">⌕</span>
            <input
              type="search"
              placeholder="Search by task name, manufacturer or part number"
              value={search}
              onChange={(event) => updateSearch(event.target.value)}
            />
          </label>
          <label>
            <span>Status</span>
            <select value={statusFilter} onChange={(event) => { setOffset(0); setStatusFilter(event.target.value) }}>
              <option value="ALL">All statuses</option>
              <option value="DRAFT">Draft</option>
              <option value="QUEUED">Queued</option>
              <option value="RUNNING">Analysing</option>
              <option value="NEEDS_INPUT">Action required</option>
              <option value="COMPLETED">Completed</option>
              <option value="FAILED">Failed</option>
              <option value="ABANDONED">Abandoned</option>
            </select>
          </label>
          <label>
            <span>Sort by</span>
            <select value={sortBy} onChange={(event) => { setOffset(0); setSortBy(event.target.value as TaskSort) }}>
              <option value="updated_desc">Recently updated</option>
              <option value="planned_asc">Planned order date: earliest first</option>
              <option value="planned_desc">Planned order date: latest first</option>
              <option value="created_desc">Recently created</option>
            </select>
          </label>
        </div>

        {tasks.isPending && <div className="task-center-empty">Loading tasks…</div>}
        {tasks.isError && <div className="task-center-empty">Unable to load the task list. Please try again later.</div>}
        {!tasks.isPending && !tasks.isError && items.length === 0 && !serverQuery && statusFilter === 'ALL' && (
          <div className="task-center-empty">
            <strong>No procurement tasks yet</strong>
            <p>Create your first requirements record, then upload supplier quotations in PDF or CSV format.</p>
            <Link className="button button-secondary" to="/tasks/new">Create task</Link>
          </div>
        )}
        {total === 0 && (serverQuery || statusFilter !== 'ALL') && (
          <div className="task-center-empty task-center-empty-filtered">
            <strong>No matching procurement tasks</strong>
            <p>Try a different search term or status filter.</p>
          </div>
        )}
        {visibleItems.length > 0 && (
          <div className="task-center-table-wrap">
            <table className="task-center-table">
              <thead>
                <tr>
                  <th>Task</th>
                  <th>Item</th>
                  <th>Current status</th>
                  <th>Planned order date</th>
                  <th>Recently updated</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {visibleItems.map((task) => (
                  <tr key={task.task_id}>
                    <td>
                      <Link className="task-name-link" to={`/tasks/${task.task_id}`}>{taskTitle(task)}</Link>
                    </td>
                    <td>{task.manufacturer_part_number ?? '—'}<small>{task.manufacturer ?? '—'}</small></td>
                    <td><span className={`status-pill ${statusTone(task.status)}`}>{statusLabels[task.status] ?? task.status}</span></td>
                    <td>{displayDay(task.planned_order_date)}</td>
                    <td>{displayDate(task.updated_at)}</td>
                    <td><Link className="table-open-action" to={`/tasks/${task.task_id}`}>Open</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {total > 0 && <div className="task-center-pagination">
          <label className="task-page-size">
            <span>Per page</span>
            <select
              aria-label="Tasks per page"
              value={pageSize}
              onChange={(event) => {
                setPageSize(Number(event.target.value) as 10 | 15)
                setOffset(0)
              }}
            >
              <option value={10}>10</option>
              <option value={15}>15</option>
            </select>
          </label>
          <TablePagination
            page={page}
            pageSize={pageSize}
            pageCount={pageCount}
            total={total}
            onPageChange={(nextPage) => setOffset(nextPage * pageSize)}
          />
        </div>}
      </section>
    </div>
  )
}
