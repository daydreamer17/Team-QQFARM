import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api, ApiClientError } from '../api/client'
import { IssuePanel } from '../components/IssuePanel'
import { ReviewPanel } from '../components/ReviewPanel'
import { RunPanel } from '../components/RunPanel'

function stepState(current: number, step: number) {
  if (step < current) return 'complete'
  if (step === current) return 'current'
  return 'upcoming'
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
  const currentStep = data.status === 'COMPLETED'
    ? 5
    : data.status === 'QUEUED' || data.status === 'RUNNING'
      ? 4
      : reviewRequired
        ? 3
        : data.quotes.length > 0
          ? 2
          : 1
  const steps = ['采购需求', '报价上传', '人工审核', '规则分析', '比较结果']

  return (
    <div className='page-stack'>
      <section className='review-detail-header'>
        <div>
          <p className='eyebrow'>REVIEW TASK</p>
          <h1>{data.scenario_id ?? data.requirement.manufacturer_part_number}</h1>
          <p className='task-id'>{data.task_id}</p>
        </div>
        <div className='inline-actions'>
          <Link className='button button-secondary' to='/reviews'>返回审核列表</Link>
          <Link className='button button-secondary' to={'/tasks/' + data.task_id}>任务详情</Link>
        </div>
      </section>

      <ol className='review-progress' aria-label='任务进度'>
        {steps.map((label, index) => {
          const step = index + 1
          return (
            <li className={'review-progress-' + stepState(currentStep, step)} key={label}>
              <span>{step}</span>
              <strong>{label}</strong>
            </li>
          )
        })}
      </ol>

      {issueReview && data.current_issue && (
        <IssuePanel task={data} onRefresh={() => task.refetch()} />
      )}
      {(fieldReview || correctionBatchOpen) && (
        <ReviewPanel task={data} onRefresh={() => task.refetch()} />
      )}
      {!reviewRequired && (
        <section className='card review-state-card'>
          <div>
            <p className='eyebrow'>CURRENT STATE</p>
            <h2>{data.status === 'COMPLETED' ? '审核与分析已完成' : '当前没有待处理的人工审核项'}</h2>
            <p>
              {data.status === 'COMPLETED'
                ? '可以查看所有报价的不符合项、可行报价和最终推荐。'
                : '若任务正在排队，请先执行页面给出的 Worker 命令。'}
            </p>
          </div>
          {data.current_result_id && (
            <Link className='button button-submit' to={'/tasks/' + data.task_id + '/results/' + data.current_result_id}>
              查看比较结果
            </Link>
          )}
        </section>
      )}

      <RunPanel task={data} onRefresh={() => task.refetch()} />
    </div>
  )
}
