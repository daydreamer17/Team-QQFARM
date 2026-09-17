import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import type { StartRunResponse, TaskDetail } from '../api/types'

interface RunSubmission {
  expectedTaskRevision: number
  idempotencyKey: string
}

interface RunPanelProps {
  task: TaskDetail
  onRefresh: () => void
  compact?: boolean
}

function runErrorMessage(error: unknown) {
  if (error instanceof ApiClientError) {
    if (error.code === 'task_revision_conflict') {
      return '任务 revision 已变化，请刷新任务后重新启动。'
    }
    if (error.code === 'graph_run_active') {
      return '该任务已有正在进行或等待输入的运行，请先刷新状态。'
    }
    return error.message
  }
  return '启动分析失败。'
}

function statusCopy(task: TaskDetail) {
  if (task.status === 'QUEUED' && task.current_job?.correction_batch_incomplete) {
    return ['待完成批量修正', '请先在下方暂存所有阻塞字段，再统一提交；现在不要运行 Worker。']
  }
  switch (task.status) {
    case 'QUEUED':
      return ['等待 Worker', '运行已经持久化，正在等待一次性 worker 领取。']
    case 'RUNNING':
      return ['分析进行中', 'Worker 正在处理报价，页面会自动刷新状态。']
    case 'NEEDS_INPUT':
      return ['需要人工确认', '分析已暂停，必须处理当前问题后才能继续。']
    case 'COMPLETED':
      return ['分析已完成', '当前 revision 已生成比较结果。']
    case 'FAILED':
      return ['分析失败', '运行没有完成，请查看安全错误信息并决定是否重试。']
    default:
      return ['准备启动分析', '报价上传完成后，可创建一次新的分析运行。']
  }
}

function jobErrorMessage(task: TaskDetail) {
  const code = task.current_job?.error_code
  const messages: Record<string, string> = {
    review_required: '报价中有字段需要人工核对。请进入“人工审核”，一次性处理全部阻塞项。',
    csv_header_unregistered: 'CSV 表头不是当前支持的报价模板。请使用 V1、V2、V3、V5 的已登记供应商模板，或 V6 固定模板。',
    csv_duplicate_headers: 'CSV 表头包含重复列名，无法确定字段来源。请修正重复列后重新上传。',
    csv_header_missing: 'CSV 没有表头，无法识别字段。',
    csv_authority_mismatch: 'CSV 内部身份信息与当前任务不一致。请刷新后重新上传；系统不会采用文件中的任务 ID 覆盖后端记录。',
    pdf_page_requires_ocr: '该 PDF 是扫描件，但 Worker 未启用 OCR。请启用 SUPPLIER_PDF_OCR_ENABLED 后重新分析。',
    blank_pdf: 'PDF 没有可读取的报价文字。请上传包含报价内容的文件。',
    corrupted_pdf: 'PDF 文件已损坏，无法打开。请向供应商索取完整文件后重新上传。',
    encrypted_pdf_unsupported: 'PDF 已加密。请先取得未加密版本再上传。',
    pdf_page_limit_exceeded: 'PDF 页数超过系统上限（默认 5 页）。',
    pdf_size_limit_exceeded: 'PDF 大小超过系统上限（默认 5 MiB）。',
    workflow_failed: '工作流发生未预期错误。请查看 Worker 日志中的首个异常。',
  }
  if (code && messages[code]) return messages[code]
  return task.current_job?.error_message ?? '工作流未提供更多安全错误信息。'
}

function elapsedLabel(totalSeconds: number) {
  if (totalSeconds < 60) return `${totalSeconds} 秒`
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes} 分 ${String(seconds).padStart(2, '0')} 秒`
}

function ActiveRunIndicator({ task }: { task: TaskDetail }) {
  const [fallbackStartedAt] = useState(() => Date.now())
  const [now, setNow] = useState(() => Date.now())
  const timestamp = task.status === 'RUNNING'
    ? task.current_job?.started_at ?? task.current_job?.created_at
    : task.current_job?.created_at
  const parsedTimestamp = timestamp ? Date.parse(timestamp) : Number.NaN
  const startedAt = Number.isNaN(parsedTimestamp) ? fallbackStartedAt : parsedTimestamp
  const elapsedSeconds = Math.max(0, Math.floor((now - startedAt) / 1_000))

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1_000)
    return () => window.clearInterval(timer)
  }, [])

  return (
    <span className={`run-state run-state-${task.status.toLowerCase()} run-state-active`}>
      <i className="activity-spinner" aria-hidden="true" />
      <span>{task.status === 'RUNNING' ? '分析中' : '等待执行'}</span>
      <small>已运行 {elapsedLabel(elapsedSeconds)}</small>
    </span>
  )
}

export function RunPanel({ task, onRefresh, compact = false }: RunPanelProps) {
  const queryClient = useQueryClient()
  const [lastSubmission, setLastSubmission] = useState<RunSubmission | null>(null)

  const startRun = useMutation({
    mutationFn: (submission: RunSubmission) =>
      api.startRun(
        task.task_id,
        submission.expectedTaskRevision,
        submission.idempotencyKey,
      ),
    onSuccess: async (result: StartRunResponse) => {
      queryClient.setQueryData<TaskDetail>(['tasks', task.task_id], (current) =>
        current
          ? {
              ...current,
              status: 'QUEUED',
              current_graph_run_id: result.graph_run_id,
              current_job: {
                job_id: result.job_id,
                job_type: result.job_type,
                job_status: result.job_status,
                task_revision: result.task_revision,
                error_code: null,
                error_message: null,
                has_corrections: false,
                correction_batch_incomplete: false,
                created_at: new Date().toISOString(),
                started_at: null,
              },
            }
          : current,
      )
      await queryClient.invalidateQueries({ queryKey: ['tasks', task.task_id] })
    },
  })
  const retryResume = useMutation({
    mutationFn: () =>
      api.retryJob(
        task.task_id,
        task.current_job!.job_id,
        task.task_revision,
        createIdempotencyKey(),
      ),
    onSuccess: async (result: StartRunResponse) => {
      queryClient.setQueryData<TaskDetail>(['tasks', task.task_id], (current) =>
        current
          ? {
              ...current,
              status: 'QUEUED',
              current_job: {
                job_id: result.job_id,
                job_type: result.job_type,
                job_status: result.job_status,
                task_revision: result.task_revision,
                error_code: null,
                error_message: null,
                has_corrections: current.current_job?.has_corrections ?? false,
                correction_batch_incomplete:
                  current.current_job?.correction_batch_incomplete ?? false,
                created_at: current.current_job?.created_at ?? new Date().toISOString(),
                started_at: null,
              },
            }
          : current,
      )
      await queryClient.invalidateQueries({ queryKey: ['tasks', task.task_id] })
    },
  })

  function handleStart() {
    const submission = {
      expectedTaskRevision: task.task_revision,
      idempotencyKey: createIdempotencyKey(),
    }
    setLastSubmission(submission)
    startRun.mutate(submission)
  }

  const [title, description] = statusCopy(task)
  const jobId = task.current_job?.job_id
  const canRetryResume =
    task.status === 'FAILED' &&
    task.current_job?.job_type === 'RESUME' &&
    task.current_job.error_code !== 'review_required'
  const canStart =
    (task.status === 'DRAFT' ||
      (task.status === 'FAILED' &&
        task.current_job?.error_code !== 'review_required' &&
        !canRetryResume)) &&
    task.task_revision > 1
  const isActive = task.status === 'QUEUED' || task.status === 'RUNNING'
  const revisionConflict =
    startRun.error instanceof ApiClientError &&
    startRun.error.code === 'task_revision_conflict'

  return (
    <section className={`card run-panel${compact ? ' run-panel-compact' : ''}`}>
      <div className="run-heading">
        <div>
          <p className="eyebrow">ANALYSIS RUN</p>
          <h2>{title}</h2>
          <p>{description}</p>
        </div>
        {isActive ? (
          <ActiveRunIndicator task={task} />
        ) : (
          <span className={`run-state run-state-${task.status.toLowerCase()}`}>
            {task.status}
          </span>
        )}
      </div>

      {task.task_revision === 1 && task.status === 'DRAFT' && (
        <p className="run-notice">至少登记一份报价后才能启动分析。</p>
      )}

      {jobId && !compact && (
        <dl className="job-summary">
          <div><dt>Job ID</dt><dd>{jobId}</dd></div>
          <div><dt>Job type</dt><dd>{task.current_job?.job_type}</dd></div>
          <div><dt>Job status</dt><dd>{task.current_job?.job_status}</dd></div>
        </dl>
      )}

      {task.status === 'FAILED' && task.current_job?.error_code && (
        <div className="form-error compact-error" role="alert">
          <div>
            <strong>分析未完成：{task.current_job.error_code}</strong>
            <p>{jobErrorMessage(task)}</p>
          </div>
        </div>
      )}

      {task.status === 'QUEUED' &&
        jobId &&
        !task.current_job?.correction_batch_incomplete && (
        <div className="worker-instruction">
          <strong>后台分析已排队</strong>
          <span>Worker 会自动领取任务并调用解析器或模型，无需复制命令或离开当前页面。</span>
          <small>页面会自动刷新；若模型或网络失败，可以按当前版本重新分析。</small>
        </div>
      )}

      {task.status === 'QUEUED' && task.current_job?.correction_batch_incomplete && (
        <div className="run-notice">
          这是尚未完成的修正批次。请在下方补齐全部阻塞字段；系统会废弃当前未运行 Job，并只为完整批次创建一个新 Job。
        </div>
      )}

      {task.status === 'NEEDS_INPUT' && (
        <div className="run-notice">
          请在下方人工确认区处理当前问题；提交后会生成新的续跑 Job。
        </div>
      )}

      {task.status === 'COMPLETED' && task.current_result_id && !compact && (
        <div className="run-notice">
          当前结果：<code>{task.current_result_id}</code>
        </div>
      )}

      {startRun.isError && (
        <div className="form-error compact-error" role="alert">
          <div>
            <strong>运行未启动</strong>
            <p>{runErrorMessage(startRun.error)}</p>
          </div>
          {lastSubmission && !revisionConflict && (
            <button
              className="button button-secondary"
              type="button"
              onClick={() => startRun.mutate(lastSubmission)}
            >
              重试相同请求
            </button>
          )}
        </div>
      )}

      {retryResume.isError && (
        <div className="form-error compact-error" role="alert">
          <div>
            <strong>断点重试未排队</strong>
            <p>{runErrorMessage(retryResume.error)}</p>
          </div>
        </div>
      )}

      <div className="run-actions">
        {task.status === 'COMPLETED' && task.current_result_id && (
          <Link
            className="button button-submit"
            to={
              '/tasks/' +
              task.task_id +
              '/results/' +
              task.current_result_id
            }
          >
            查看解析与比较结果
          </Link>
        )}
        {canStart && (
          <button
            className="button button-submit"
            type="button"
            onClick={handleStart}
            disabled={startRun.isPending}
          >
            {startRun.isPending
              ? '正在排队…'
              : task.status === 'FAILED'
                ? '按当前版本重新分析'
                : '启动分析'}
          </button>
        )}
        {canRetryResume && (
          <button
            className="button button-submit"
            type="button"
            onClick={() => retryResume.mutate()}
            disabled={retryResume.isPending}
          >
            {retryResume.isPending ? '正在恢复…' : '从断点重试（不重新解析）'}
          </button>
        )}
        {(isActive || task.status === 'NEEDS_INPUT' || task.status === 'FAILED') && (
          <button className="button button-secondary" type="button" onClick={onRefresh}>
            刷新状态
          </button>
        )}
      </div>
    </section>
  )
}
