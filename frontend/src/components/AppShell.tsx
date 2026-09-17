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
      <nav className="sidebar" aria-label="主导航">
        <NavLink className="brand" to="/" aria-label="QuoteWise 首页">
          <span className="brand-mark" aria-hidden="true">Q</span>
          <span>
            <strong>QuoteWise</strong>
            <small>Supplier Intelligence</small>
          </span>
        </NavLink>
        <p className="nav-label">采购工作区</p>
        <NavLink to="/" end className={navClass}>
          <span className="nav-icon" aria-hidden="true">▦</span>
          <span>任务中心</span>
        </NavLink>
        <NavLink to="/tasks/new" className={navClass}>
          <span className="nav-icon" aria-hidden="true">＋</span>
          <span>新建任务</span>
        </NavLink>
        <NavLink to="/reviews" className={navClass}>
          <span className="nav-icon" aria-hidden="true">✓</span>
          <span>审核与分析</span>
        </NavLink>
        <NavLink to="/resources" className={navClass}>
          <span className="nav-icon" aria-hidden="true">▤</span>
          <span>规则资源库</span>
        </NavLink>
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
            <strong>当前工作区</strong>
            <span>任务、报价、审核与比较结果均以当前 revision 为准。</span>
          </div>
      </nav>
      <div className="app-main">
        <header className="topbar">
          <div className="topbar-title">
            <small>Electronics Procurement</small>
            <strong>供应商比选工作区</strong>
          </div>
          <div className="topbar-actions">
            <span className="environment-badge">LOCAL</span>
            <span className="user-avatar" aria-label="当前用户 LC">LC</span>
          </div>
        </header>
        <main className="page-content"><Outlet /></main>
      </div>
    </div>
  )
}
