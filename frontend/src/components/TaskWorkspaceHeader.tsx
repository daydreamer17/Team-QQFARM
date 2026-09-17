import { Link } from 'react-router-dom'

type WorkspaceSection = 'overview' | 'quotes' | 'decision' | 'compliance' | 'summary' | 'audit'

interface TaskWorkspaceHeaderProps {
  taskId: string
  scenarioId: string | null
  title: string
  subtitle: string
  status: string
  revision: number
  resultId: string | null
  quoteCount: number
  reviewBlocked?: boolean
  policyReviewBlocked?: boolean
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
  resultId,
  quoteCount,
  reviewBlocked = false,
  active,
}: TaskWorkspaceHeaderProps) {
  let currentStage = 1
  if (active === 'summary' || status === 'COMPLETED' || resultId) currentStage = 4
  else if (active === 'decision' || status === 'QUEUED' || status === 'RUNNING' || status === 'FAILED') currentStage = 3
  else if (quoteCount > 0 || reviewBlocked || status === 'NEEDS_INPUT') currentStage = 2
  const stages = ['采购需求', '报价与审核', '决策比较', 'Summary']

  return (
    <section className="workspace-header">
      <div className="workspace-task-head">
        <div>
          <span className="workspace-task-label">{scenarioId ?? 'PROCUREMENT TASK'}</span>
          <h1>{title}</h1>
          <p>{subtitle}</p>
          <small className="workspace-task-id">{taskId}</small>
        </div>
        <div className="workspace-state-stack">
          <span className="status-pill status-ready">{status}</span>
          <span>Task Rev {revision}</span>
        </div>
      </div>
      <div className="task-timeline-shell">
        <ol className={`task-timeline task-timeline-stage-${currentStage}`} aria-label="任务进度">
          {stages.map((label, index) => {
            const stage = index + 1
            const state = stage < currentStage
              ? 'complete'
              : stage === currentStage
                ? 'current'
                : 'upcoming'
            return (
              <li className={`task-timeline-${state}`} key={label} aria-current={state === 'current' ? 'step' : undefined}>
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
        <Link className={tabClass(active === 'decision')} to={`/tasks/${taskId}/decision`}>决策比较</Link>
        <Link className={tabClass(active === 'compliance')} to={`/tasks/${taskId}/compliance`}>合规</Link>
        <Link className={tabClass(active === 'summary')} to={`/tasks/${taskId}/summary`}>Summary</Link>
        <Link className={tabClass(active === 'audit')} to={`/tasks/${taskId}/audit`}>版本 / 审计</Link>
      </nav>
    </section>
  )
}
