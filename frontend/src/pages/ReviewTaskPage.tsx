import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { api, ApiClientError } from '../api/client'
import { IssuePanel } from '../components/IssuePanel'
import { ReviewPanel } from '../components/ReviewPanel'
import { RunPanel } from '../components/RunPanel'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'

function analysisStatus(status: string | undefined) {
  const labels: Record<string, string> = {
    PENDING: '等待处理',
    RUNNING: '分析中',
    SUCCEEDED: '已完成',
    FAILED: '失败',
  }
  return status ? labels[status] ?? status : '未启动'
}

export function ReviewTaskPage() {
  const { taskId = '' } = useParams()
  const task = useQuery({
    queryKey: ['tasks', taskId],
    queryFn: () => api.getTask(taskId),
    enabled: Boolean(taskId),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === 'QUEUED' || status === 'RUNNING' ? 2_000 : 5_000
    },
  })

  if (task.isPending) return <section className='card loading-panel'>正在读取审核任务…</section>
  if (task.isError) {
    const message = task.error instanceof ApiClientError ? task.error.message : '审核任务读取失败。'
    return <section className='card error-panel' role='alert'>{message}</section>
  }

  const data = task.data
  const fieldReview = data.status === 'FAILED' && data.current_job?.error_code === 'review_required'
  const issueReview = data.status === 'NEEDS_INPUT' && Boolean(data.current_issue)
  const correctionBatchOpen =
    data.status === 'QUEUED' &&
    data.current_job?.job_status === 'PENDING' &&
    data.current_job.correction_batch_incomplete
  const reviewRequired = fieldReview || issueReview || correctionBatchOpen
  const blockerCount = Number(issueReview) + Number(fieldReview || correctionBatchOpen)
  const reviewStatus = reviewRequired
    ? '待处理'
    : data.status === 'COMPLETED'
      ? '已完成'
      : '无待办'

  return (
    <div className='page-stack'>
      <TaskWorkspaceHeader
        taskId={data.task_id}
        scenarioId={data.scenario_id}
        title={data.requirement.manufacturer_part_number}
        subtitle={`${data.requirement.required_quantity} ${data.requirement.quantity_unit} · ${data.quotes.length} 份报价`}
        status={data.status}
        revision={data.task_revision}
        resultId={data.current_result_id}
        quoteCount={data.quotes.length}
        reviewBlocked={reviewRequired}
        active='review'
      />

      <section className='review-workspace-lead'>
        <div>
          <p className='eyebrow'>REVIEW &amp; ANALYSIS</p>
          <h2>审核与分析</h2>
          <p>集中处理阻塞项，并查看当前审核与分析状态。</p>
        </div>
        <span className={blockerCount > 0 ? 'review-blocker-count review-blocker-count-active' : 'review-blocker-count'}>
          {blockerCount > 0 ? '存在阻塞项' : '0 个阻塞项'}
        </span>
      </section>

      <section className='review-status-strip' aria-label='审核与分析状态'>
        <div><span>阻塞项</span><strong>{blockerCount > 0 ? '待处理' : '无'}</strong></div>
        <div><span>字段审核</span><strong>{reviewStatus}</strong></div>
        <div><span>分析任务</span><strong>{analysisStatus(data.current_job?.job_status)}</strong></div>
        <div><span>已登记报价</span><strong>{data.quotes.length} 份 · Rev {data.task_revision}</strong></div>
      </section>

      <div className='review-content-stack'>
        {issueReview && data.current_issue && (
          <IssuePanel task={data} onRefresh={() => task.refetch()} />
        )}
        {(fieldReview || correctionBatchOpen) && (
          <ReviewPanel task={data} onRefresh={() => task.refetch()} />
        )}
        {!reviewRequired && (
          <section className='review-empty-compact'>
            <div>
              <strong>当前没有阻塞项</strong>
              <span>{data.status === 'COMPLETED' ? '审核与分析已经完成，可直接查看比较结果。' : '后台处理会自动推进；出现需要确认的内容时会显示在这里。'}</span>
            </div>
          </section>
        )}

        <RunPanel compact task={data} onRefresh={() => task.refetch()} />
      </div>
    </div>
  )
}
