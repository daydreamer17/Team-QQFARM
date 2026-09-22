import { Link } from 'react-router-dom'
import type { TaskDetail } from '../api/types'
import { taskStatusLabel } from '../lib/presentation'

type WorkspaceSection = 'overview' | 'quotes' | 'review' | 'suppliers' | 'investigations' | 'decision' | 'gaps' | 'compliance' | 'summary' | 'audit'

interface TaskWorkspaceHeaderProps {
  taskId: string
  scenarioId: string | null
  title: string
  subtitle: string
  status: string
  revision: number
  resultId: string | null
  quoteCount: number
  summaryComplete: boolean
  progress: TaskDetail['progress']
  reviewBlocked?: boolean
  policyReviewBlocked?: boolean
  revisionContext?: 'current' | 'historical'
  active: WorkspaceSection
}

function tabClass(active: boolean) {
  return 'workspace-tab' + (active ? ' workspace-tab-active' : '')
}

export function TaskWorkspaceHeader({
  taskId,
  scenarioId,
  title,
  subtitle,
  status,
  revision,
  progress,
  revisionContext = 'current',
  active,
}: TaskWorkspaceHeaderProps) {
  const completedStage = progress.summary_completed
    ? 4
    : progress.decision_completed
      ? 3
      : progress.quote_review_completed
        ? 2
        : progress.requirement_completed
          ? 1
          : 0
  const stages = ['采购需求', '报价与审核', '决策比较', '采购总结']

  return (
    <section className="workspace-header">
      <div className="workspace-task-head">
        <div>
          <span className="workspace-task-label">采购任务{scenarioId ? ` · ${scenarioId}` : ''}</span>
          <h1>{title}</h1>
          <p>{subtitle}</p>
        </div>
        <div className="workspace-state-stack">
          <span className="status-pill status-ready">{taskStatusLabel(status)}</span>
          <span>{revisionContext === 'historical' ? `历史结果第 ${revision} 版` : `当前第 ${revision} 版`}</span>
        </div>
      </div>
      <div className="task-timeline-shell">
        <ol className={`task-timeline task-timeline-stage-${completedStage}`} aria-label="任务完成进度">
          {stages.map((label, index) => {
            const stage = index + 1
            const state = stage <= completedStage ? 'complete' : 'upcoming'
            return (
              <li className={`task-timeline-${state}`} key={label} aria-label={`${label}：${state === 'complete' ? '已完成' : '未完成'}`}>
                <span>{stage}</span>
                <strong>{label}</strong>
              </li>
            )
          })}
        </ol>
      </div>
      <nav className="workspace-tabs" aria-label="任务工作台页面">
        <Link className={tabClass(active === 'overview')} to={`/tasks/${taskId}`}>概览</Link>
        <Link className={tabClass(active === 'quotes')} to={`/tasks/${taskId}/quotes/new`}>报价与证据</Link>
        <Link className={tabClass(active === 'review')} to={`/tasks/${taskId}/review`}>待处理事项</Link>
        <Link className={tabClass(active === 'suppliers')} to={`/tasks/${taskId}/suppliers`}>供应商信息</Link>
        <Link className={tabClass(active === 'decision')} to={`/tasks/${taskId}/decision`}>决策结果</Link>
        {active === 'gaps' && <Link className={tabClass(true)} to={`/tasks/${taskId}/gaps`}>差距详情</Link>}
        {active === 'investigations' && <Link className={tabClass(true)} to={`/tasks/${taskId}/investigations`}>调查详情</Link>}
        <Link className={tabClass(active === 'compliance')} to={`/tasks/${taskId}/compliance`}>制度检查</Link>
        <Link className={tabClass(active === 'summary')} to={`/tasks/${taskId}/summary`}>采购总结</Link>
        <Link className={tabClass(active === 'audit')} to={`/tasks/${taskId}/audit`}>版本记录</Link>
      </nav>
    </section>
  )
}
