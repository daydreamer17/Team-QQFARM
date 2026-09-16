import { useQuery } from '@tanstack/react-query'
import { NavLink, Outlet } from 'react-router-dom'
import { api } from '../api/client'
import type { TaskListItem } from '../api/types'

function navClass({ isActive }: { isActive: boolean }) {
  return `nav-link${isActive ? ' nav-link-active' : ''}`
}

function taskStage(task: TaskListItem) {
  switch (task.status) {
    case 'QUEUED':
      return '等待执行'
    case 'RUNNING':
      return '分析中'
    case 'NEEDS_INPUT':
      return '等待确认'
    case 'COMPLETED':
      return '已完成'
    case 'FAILED':
      return '执行失败'
    default:
      return task.task_revision > 1 ? '报价已登记' : '需求已创建'
  }
}

export function AppShell() {
  const taskHistory = useQuery({
    queryKey: ['tasks', 'history'],
    queryFn: () => api.listTasks(8),
    refetchInterval: 5_000,
  })

  return (
    <div className="app-shell">
      <header className="topbar">
        <NavLink className="brand" to="/" aria-label="Supplier Compare 首页">
          <span className="brand-mark" aria-hidden="true">Q</span>
          <span>
            <strong>Supplier Compare</strong>
            <small>采购报价比较工作台</small>
          </span>
        </NavLink>
        <span className="environment-badge">LOCAL</span>
      </header>
      <div className="app-frame">
        <nav className="sidebar" aria-label="主导航">
          <p className="nav-label">工作区</p>
          <NavLink to="/" end className={navClass}>工作台</NavLink>
          <NavLink to="/tasks/new" className={navClass}>新建任务</NavLink>
          <NavLink to="/reviews" className={navClass}>人工审核</NavLink>
          <section className="sidebar-history" aria-label="历史任务">
            <div className="sidebar-history-heading">
              <span>历史任务</span>
              <small>{taskHistory.data?.items.length ?? 0}</small>
            </div>
            {taskHistory.isPending && (
              <p className="sidebar-history-message">正在读取…</p>
            )}
            {taskHistory.isError && (
              <p className="sidebar-history-message">暂时无法读取</p>
            )}
            {taskHistory.data?.items.length === 0 && (
              <p className="sidebar-history-message">还没有任务</p>
            )}
            <div className="history-task-list">
              {taskHistory.data?.items.map((task, index) => (
                <NavLink
                  className={({ isActive }) =>
                    'history-task' + (isActive ? ' history-task-active' : '')
                  }
                  key={task.task_id}
                  title={task.task_id}
                  to={'/tasks/' + task.task_id}
                >
                  <span className="history-task-index">
                    {String(index + 1).padStart(2, '0')}
                  </span>
                  <span className="history-task-copy">
                    <strong>
                      {task.scenario_id ??
                        task.manufacturer_part_number ??
                        '采购任务'}
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
          </section>
          <div className="sidebar-note">
            <strong>Week 2</strong>
            <span>先打通任务、上传、审核和比较闭环。</span>
          </div>
        </nav>
        <main className="page-content"><Outlet /></main>
      </div>
    </div>
  )
}
