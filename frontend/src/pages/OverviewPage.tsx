import { useQuery } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiClientError } from '../api/client'
import type { TaskListItem } from '../api/types'

const EMPTY_TASKS: TaskListItem[] = []

const statusLabels: Record<string, string> = {
  DRAFT: '草稿',
  QUEUED: '等待执行',
  RUNNING: '分析中',
  NEEDS_INPUT: '等待确认',
  COMPLETED: '已完成',
  FAILED: '执行失败',
  ABANDONED: '已废弃',
}

function getErrorMessage(error: unknown) {
  if (error instanceof ApiClientError) return error.message
  return '后端连接检查失败。'
}

function taskTitle(task: TaskListItem) {
  return task.scenario_id ?? task.manufacturer_part_number ?? '采购任务'
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
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function displayDay(value: string | null) {
  if (!value) return '未设置'
  const date = new Date(`${value}T00:00:00`)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
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
  const [offset, setOffset] = useState(0)
  const searchTimer = useRef<number | null>(null)
  const health = useQuery({
    queryKey: ['health', 'ready'],
    queryFn: api.healthReady,
    refetchInterval: 30_000,
  })
  const liveness = useQuery({
    queryKey: ['health', 'live'],
    queryFn: api.healthLive,
    refetchInterval: 30_000,
  })
  const tasks = useQuery({
    queryKey: ['tasks', 'center', serverQuery, statusFilter, sortBy, offset],
    queryFn: () => api.listTasks({ limit: 20, offset, query: serverQuery, status: statusFilter === 'ALL' ? undefined : statusFilter, sort: sortBy }),
    refetchInterval: 5_000,
  })
  const items = tasks.data?.items ?? EMPTY_TASKS
  const counts = tasks.data?.status_counts ?? {}
  const waitingCount = counts.NEEDS_INPUT ?? 0
  const runningCount = (counts.QUEUED ?? 0) + (counts.RUNNING ?? 0)
  const completedCount = counts.COMPLETED ?? 0
  const isReady = health.data?.status === 'ready'
  const visibleItems = items

  function updateSearch(value: string) {
    setSearch(value)
    if (searchTimer.current !== null) window.clearTimeout(searchTimer.current)
    searchTimer.current = window.setTimeout(() => { setOffset(0); setServerQuery(value.trim()) }, 300)
  }

  return (
    <div className="page-stack task-center-page">
      <section className="task-center-heading">
        <div>
          <p className="eyebrow">PROCUREMENT OPERATIONS</p>
          <h1>任务中心</h1>
          <p>集中查看采购任务状态，并从同一工作台进入报价、审核和决策结果。</p>
        </div>
        <Link className="button button-submit" to="/tasks/new">＋ 新建采购任务</Link>
      </section>

      <section className="task-center-metrics" aria-label="任务概况">
        <article><span>全部任务</span><strong>{Object.values(counts).reduce((sum, count) => sum + count, 0)}</strong><small>服务端全部任务</small></article>
        <article><span>待我处理</span><strong>{waitingCount}</strong><small>需要补充或确认</small></article>
        <article><span>运行中</span><strong>{runningCount}</strong><small>排队或正在分析</small></article>
        <article><span>已完成</span><strong>{completedCount}</strong><small>已生成比较结果</small></article>
        <article className="service-metric">
          <span>后端服务</span>
          <strong className={isReady ? 'service-online' : 'service-offline'}>
            {health.isPending ? '检查中' : isReady ? '已连接' : '未连接'}
          </strong>
          <small>进程：{liveness.data?.status === 'alive' ? '存活' : liveness.isPending ? '检查中' : '不可达'} · 数据库：{isReady ? '就绪' : '未就绪'}</small>
          <button type="button" onClick={() => void Promise.all([health.refetch(), liveness.refetch()])} disabled={health.isFetching || liveness.isFetching}>
            {health.isFetching || liveness.isFetching ? '正在检查…' : '重新检查'}
          </button>
        </article>
      </section>

      {!isReady && !health.isPending && (
        <div className="service-warning" role="alert">{getErrorMessage(health.error)}</div>
      )}

      <section className="task-list-panel">
        <div className="section-heading task-list-heading">
          <div>
            <p className="eyebrow">ACTIVE WORK</p>
            <h2>最近采购任务</h2>
          </div>
          <span>每 5 秒自动刷新</span>
        </div>

        <div className="task-list-tools">
          <label className="task-search">
            <span className="visually-hidden">搜索采购任务</span>
            <span className="task-search-icon" aria-hidden="true">⌕</span>
            <input
              type="search"
              placeholder="搜索任务编号、制造商或料号"
              value={search}
              onChange={(event) => updateSearch(event.target.value)}
            />
          </label>
          <label>
            <span>状态</span>
            <select value={statusFilter} onChange={(event) => { setOffset(0); setStatusFilter(event.target.value) }}>
              <option value="ALL">全部状态</option>
              <option value="DRAFT">草稿</option>
              <option value="QUEUED">等待执行</option>
              <option value="RUNNING">分析中</option>
              <option value="NEEDS_INPUT">等待确认</option>
              <option value="COMPLETED">已完成</option>
              <option value="FAILED">执行失败</option>
              <option value="ABANDONED">已废弃</option>
            </select>
          </label>
          <label>
            <span>排列</span>
            <select value={sortBy} onChange={(event) => { setOffset(0); setSortBy(event.target.value as TaskSort) }}>
              <option value="updated_desc">最近更新</option>
              <option value="planned_asc">计划下单：从近到远</option>
              <option value="planned_desc">计划下单：从远到近</option>
              <option value="created_desc">最近创建</option>
            </select>
          </label>
        </div>

        {tasks.isPending && <div className="task-center-empty">正在读取任务…</div>}
        {tasks.isError && <div className="task-center-empty">任务列表读取失败，请确认后端服务。</div>}
        {!tasks.isPending && !tasks.isError && items.length === 0 && !serverQuery && statusFilter === 'ALL' && (
          <div className="task-center-empty">
            <strong>还没有采购任务</strong>
            <p>创建第一项需求后，就可以上传供应商 PDF 或 CSV 报价。</p>
            <Link className="button button-secondary" to="/tasks/new">创建任务</Link>
          </div>
        )}
        {(tasks.data?.total ?? 0) === 0 && (serverQuery || statusFilter !== 'ALL') && (
          <div className="task-center-empty task-center-empty-filtered">
            <strong>没有匹配的采购任务</strong>
            <p>可以更换搜索词或状态筛选条件。</p>
          </div>
        )}
        {visibleItems.length > 0 && (
          <div className="task-center-table-wrap">
            <table className="task-center-table">
              <thead>
                <tr>
                  <th>任务</th>
                  <th>物料</th>
                  <th>Revision</th>
                  <th>当前状态</th>
                  <th>计划下单</th>
                  <th>最近更新</th>
                  <th aria-label="操作" />
                </tr>
              </thead>
              <tbody>
                {visibleItems.map((task) => (
                  <tr key={task.task_id}>
                    <td>
                      <Link className="task-name-link" to={`/tasks/${task.task_id}`}>{taskTitle(task)}</Link>
                      <small>{task.task_id}</small>
                    </td>
                    <td>{task.manufacturer_part_number ?? '—'}<small>{task.manufacturer ?? '—'}</small></td>
                    <td>Rev {task.task_revision}</td>
                    <td><span className={`status-pill ${statusTone(task.status)}`}>{statusLabels[task.status] ?? task.status}</span></td>
                    <td>{displayDay(task.planned_order_date)}</td>
                    <td>{displayDate(task.updated_at)}</td>
                    <td><Link className="table-open-action" to={`/tasks/${task.task_id}`}>打开工作台 →</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {(tasks.data?.total ?? 0) > 20 && <div className="policy-pagination"><span>第 {offset + 1}–{Math.min(offset + items.length, tasks.data!.total)} 条，共 {tasks.data!.total} 条</span><div><button className="button button-secondary" type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>上一页</button><button className="button button-secondary" type="button" disabled={offset + 20 >= tasks.data!.total} onClick={() => setOffset(offset + 20)}>下一页</button></div></div>}
      </section>
    </div>
  )
}
