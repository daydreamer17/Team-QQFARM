import { Link } from 'react-router-dom'
import type { TaskDetail } from '../api/types'
import { taskStatusLabel } from '../lib/presentation'
import { complianceStageLabel } from '../lib/compliance'

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
  title,
  subtitle,
  status,
  revision,
  progress,
  revisionContext = 'current',
  active,
}: TaskWorkspaceHeaderProps) {
  const completed = [progress.requirement_completed, progress.quote_review_completed,
    Boolean(progress.compliance?.confirmed), progress.decision_completed, progress.summary_completed]
  const completedStage = completed.findIndex((value) => !value) === -1 ? 5 : completed.findIndex((value) => !value)
  const stages = ['Procurement Requirements', 'Quotations and review', 'Compliance Review', 'Decision Comparison', 'Procurement Decision Brief']

  return (
    <section className="workspace-header">
      <div className="workspace-task-head">
        <div>
          <span className="workspace-task-label">Procurement Task</span>
          <h1>{title}</h1>
          <p>{subtitle}</p>
        </div>
        <div className="workspace-state-stack">
          <span className="status-pill status-ready">{taskStatusLabel(status)}</span>
          <span>{revisionContext === 'historical' ? `Historical result · Revision ${revision}` : `Current revision · ${revision}`}</span>
        </div>
      </div>
      <div className="task-timeline-shell">
        <ol className={`task-timeline task-timeline-stage-${completedStage}`} aria-label="Task progress">
          {stages.map((label, index) => {
            const stage = index + 1
            // The timeline describes whether a workflow stage was completed, not
            // whether every supplier passed that stage. Keep the detailed
            // compliance outcome in its accessible label and on the compliance
            // page, while a confirmed stage uses the same completed styling as
            // the surrounding workflow stages.
            const state = completed[index] ? 'complete' : 'upcoming'
            const description = index === 2 ? complianceStageLabel(progress.compliance) : state === 'complete' ? 'Completed' : 'Incomplete'
            return (
              <li className={`task-timeline-${state}`} key={label} aria-label={`${label}: ${description}`}>
                <span>{stage}</span>
                <strong>{label}</strong>
              </li>
            )
          })}
        </ol>
      </div>
      <nav className="workspace-tabs" aria-label="Task workspace pages">
        <Link className={tabClass(active === 'overview')} to={`/tasks/${taskId}`}>Procurement Requirements</Link>
        <Link className={tabClass(active === 'quotes')} to={`/tasks/${taskId}/quotes/new`}>Quotations and Evidence</Link>
        <Link className={tabClass(active === 'review')} to={`/tasks/${taskId}/review`}>Action Items</Link>
        <Link className={tabClass(active === 'suppliers')} to={`/tasks/${taskId}/suppliers`}>Supplier Information</Link>
        <Link className={tabClass(active === 'compliance')} to={`/tasks/${taskId}/compliance`}>Compliance Review</Link>
        <Link className={tabClass(active === 'decision')} to={`/tasks/${taskId}/decision`}>Decision results</Link>
        {active === 'gaps' && <Link className={tabClass(true)} to={`/tasks/${taskId}/gaps`}>Selection gap</Link>}
        <Link className={tabClass(active === 'summary')} to={`/tasks/${taskId}/summary`}>Procurement Decision Brief</Link>
        <Link className={tabClass(active === 'audit')} to={`/tasks/${taskId}/audit`}>Version History</Link>
      </nav>
    </section>
  )
}
