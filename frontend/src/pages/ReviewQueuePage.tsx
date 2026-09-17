import { useQueries, useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { TaskDetail, TaskListItem } from '../api/types'

function requiresReview(task: TaskDetail) {
  return (
    task.status === 'NEEDS_INPUT' ||
    (task.status === 'FAILED' && task.current_job?.error_code === 'review_required') ||
    Boolean(task.current_job?.correction_batch_incomplete)
  )
}

function taskStage(task: TaskDetail) {
  if (requiresReview(task)) return '待人工审核'
  if (task.status === 'QUEUED') return '等待 Worker'
  if (task.status === 'RUNNING') return '正在分析'
  if (task.status === 'COMPLETED') return '分析完成'
  if (task.status === 'FAILED') return '执行失败'
  return task.quotes.length > 0 ? '报价已上传' : '等待上传报价'
}

function taskDescription(task: TaskDetail) {
  if (task.status === 'NEEDS_INPUT') return '系统需要你确认报价中缺失或不明确的信息。'
  if (task.status === 'FAILED' && task.current_job?.error_code === 'review_required') {
    return '字段或证据未通过自动审核，请一次性处理全部阻塞项。'
  }
  if (task.current_job?.correction_batch_incomplete) {
    return '已有部分字段暂存或修正，仍需完成其余阻塞项。'
  }
  if (task.status === 'COMPLETED') return '比较结果已经生成，可查看不符合项与推荐。'
  if (task.status === 'QUEUED' || task.status === 'RUNNING') {
    return '人工处理已经完成，正在等待或执行后续分析。'
  }
  if (task.status === 'FAILED') return task.current_job?.error_message ?? '任务执行失败。'
  return '当前没有需要人工处理的阻塞项。'
}

function taskTitle(task: TaskListItem) {
  return task.scenario_id ?? task.manufacturer_part_number ?? '采购任务'
}

export function ReviewQueuePage() {
  const taskList = useQuery({
    queryKey: ['tasks', 'review-queue'],
    queryFn: () => api.listTasks(50),
    refetchInterval: 5_000,
  })
  const items = taskList.data?.items ?? []
  const detailQueries = useQueries({
    queries: items.map((task) => ({
      queryKey: ['tasks', task.task_id],
      queryFn: () => api.getTask(task.task_id),
      refetchInterval: 5_000,
    })),
  })
  const rows = items
    .map((summary, index) => ({ summary, detail: detailQueries[index]?.data }))
    .filter((row): row is { summary: TaskListItem; detail: TaskDetail } => Boolean(row.detail))
    .sort((left, right) => Number(requiresReview(right.detail)) - Number(requiresReview(left.detail)))
  const pendingCount = rows.filter((row) => requiresReview(row.detail)).length

  return (
    <div className='page-stack'>
      <section className='review-queue-header'>
        <div>
          <p className='eyebrow'>HUMAN REVIEW</p>
          <h1>审核与分析</h1>
          <p>集中查看所有采购任务的审核状态，并一次性处理每个任务的全部阻塞项。</p>
        </div>
        <div className='review-queue-count'>
          <strong>{pendingCount}</strong>
          <span>个任务待处理</span>
        </div>
      </section>

      {taskList.isPending && <section className='card loading-panel'>正在读取审核队列…</section>}
      {taskList.isError && <section className='card error-panel'>审核队列读取失败。</section>}
      {!taskList.isPending && rows.length === 0 && (
        <section className='card review-queue-empty'>还没有采购任务。</section>
      )}

      <section className='review-task-list' aria-label='审核任务列表'>
        {rows.map(({ summary, detail }, index) => {
          const actionRequired = requiresReview(detail)
          return (
            <article className={'card review-task-row' + (actionRequired ? ' review-task-row-action' : '')} key={summary.task_id}>
              <span className='review-task-number'>{String(index + 1).padStart(2, '0')}</span>
              <div className='review-task-main'>
                <div className='review-task-title-line'>
                  <h2>{taskTitle(summary)}</h2>
                  <span className={'status-pill ' + (actionRequired ? 'status-offline' : 'status-ready')}>
                    {taskStage(detail)}
                  </span>
                </div>
                <p>{taskDescription(detail)}</p>
                <div className='review-task-meta'>
                  <span>Revision {detail.task_revision}</span>
                  <span>{detail.quotes.length} 份报价</span>
                  <span>{detail.requirement.manufacturer_part_number}</span>
                </div>
              </div>
              <Link className='button button-secondary' to={'/reviews/' + detail.task_id}>
                {actionRequired ? '进入审核' : '查看进度'}
              </Link>
            </article>
          )
        })}
      </section>
    </div>
  )
}
