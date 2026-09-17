import { Link } from 'react-router-dom'

type WorkspaceSection = 'overview' | 'quotes' | 'review' | 'decision'

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
  const currentStage = status === 'COMPLETED' || resultId
    ? 5
    : reviewBlocked || status === 'NEEDS_INPUT'
      ? 3
      : status === 'QUEUED' || status === 'RUNNING' || status === 'FAILED'
        ? 4
        : quoteCount > 0
          ? 2
          : 1
  const stages = ['采购需求', '报价上传', '人工审核', '规则分析', '比较结果']

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
        <Link className={tabClass(active === 'review')} to={`/reviews/${taskId}`}>审核与分析</Link>
        {resultId ? (
          <Link
            className={tabClass(active === 'decision')}
            to={`/tasks/${taskId}/results/${resultId}`}
          >
            决策比较
          </Link>
        ) : (
          <span className="workspace-tab workspace-tab-disabled">决策比较</span>
        )}
        <span className="workspace-tab workspace-tab-target">合规</span>
        <span className="workspace-tab workspace-tab-target">审批 / 报告</span>
        <span className="workspace-tab workspace-tab-disabled">版本 / 审计</span>
      </nav>
    </section>
  )
}
