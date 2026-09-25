import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import type { StartRunResponse, TaskDetail } from '../api/types'
import { taskStatusLabel } from '../lib/presentation'

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
      return 'The task revision has changed. Refresh the task before starting again.'
    }
    if (error.code === 'graph_run_active') {
      return 'This task already has an active or input-waiting run. Refresh its status first.'
    }
    return error.message
  }
  return 'Unable to start analysis.'
}

function statusCopy(task: TaskDetail) {
  if (task.status === 'QUEUED' && task.current_job?.correction_batch_incomplete) {
    return ['Batch corrections pending', 'Stage all blocking-field corrections below, then submit them together. Do not restart analysis yet.']
  }
  switch (task.status) {
    case 'QUEUED':
      return ['Waiting for backend processing', 'The analysis job is queued and will start automatically.']
    case 'RUNNING':
      return ['Analysis in progress', 'The backend is processing quotations. This page will refresh automatically.']
    case 'NEEDS_INPUT':
      return task.current_issue?.issue_type === 'BATCH_FIELD_REVIEW'
        ? ['Consolidated confirmation required', 'Analysis is paused. Open Action Items and complete all confirmations for this round.']
        : ['Human confirmation required', 'Analysis is paused until the current issue is resolved.']
    case 'COMPLETED':
      return ['Analysis complete', 'Comparison results are available for the current revision.']
    case 'FAILED':
      return ['Analysis failed', 'The run did not complete. Review the safe error message before deciding whether to retry.']
    default:
      return ['Ready to start analysis', 'After a quotation is formally submitted, you can create a new decision-analysis run.']
  }
}

function jobErrorMessage(task: TaskDetail) {
  const code = task.current_job?.error_code
  const messages: Record<string, string> = {
    review_required: 'Quotation fields require human review. Open Action Items and resolve all blocking issues together.',
    csv_header_unregistered: 'The CSV header is not a supported quotation template. Use a registered V1, V2, V3 or V5 supplier template, or the fixed V6 template.',
    csv_duplicate_headers: 'The CSV contains duplicate column names, so field sources cannot be determined. Remove duplicate columns and upload it again.',
    csv_header_missing: 'The CSV has no header and its fields cannot be identified.',
    csv_authority_mismatch: 'Identity information in the CSV does not match the current task. Refresh and upload again. File-supplied task IDs never override backend records.',
    pdf_page_requires_ocr: 'This PDF is scanned and OCR is not enabled. Ask an administrator to enable it before reanalysing.',
    blank_pdf: 'The PDF contains no readable quotation text. Upload a file containing the quotation.',
    corrupted_pdf: 'The PDF is corrupted and cannot be opened. Obtain a complete file from the supplier and upload it again.',
    encrypted_pdf_unsupported: 'The PDF is encrypted. Obtain an unencrypted version before uploading.',
    pdf_page_limit_exceeded: 'The PDF exceeds the page limit (5 pages by default).',
    pdf_size_limit_exceeded: 'The PDF exceeds the size limit (5 MiB by default).',
    workflow_failed: 'An unexpected backend analysis error occurred. Review the first exception in the service logs.',
  }
  if (code && messages[code]) return messages[code]
  return task.current_job?.error_message ?? 'The workflow did not provide more safe error details.'
}

function elapsedLabel(totalSeconds: number) {
  if (totalSeconds < 60) return `${totalSeconds} sec`
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes} min ${String(seconds).padStart(2, '0')} sec`
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
      <span>{task.status === 'RUNNING' ? 'Analysing' : 'Queued'}</span>
      <small>Running for {elapsedLabel(elapsedSeconds)}</small>
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
  const canRestartInterrupted =
    task.status === 'NEEDS_INPUT' &&
    !task.current_job?.correction_batch_incomplete
  const canStart =
    (task.status === 'DRAFT' ||
      canRestartInterrupted ||
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
            {taskStatusLabel(task.status)}
          </span>
        )}
      </div>

      {task.task_revision === 1 && task.status === 'DRAFT' && (
        <p className="run-notice">Register at least one quotation before starting analysis.</p>
      )}

      {task.status === 'FAILED' && task.current_job?.error_code && (
        <div className="form-error compact-error" role="alert">
          <div>
            <strong>Analysis incomplete</strong>
            <p>{jobErrorMessage(task)}</p>
          </div>
        </div>
      )}

      {task.status === 'QUEUED' &&
        jobId &&
        !task.current_job?.correction_batch_incomplete && (
        <div className="worker-instruction">
          <strong>Backend analysis queued</strong>
          <span>The backend will process the job automatically. You do not need to copy commands or leave this page.</span>
          <small>This page refreshes automatically. If the model or network fails, reanalyse the current revision.</small>
        </div>
      )}

      {task.status === 'QUEUED' && task.current_job?.correction_batch_incomplete && (
        <div className="run-notice">
          This correction batch is incomplete. Resolve all blocking fields below; the system creates one new analysis only for the complete batch.
        </div>
      )}

      {task.status === 'NEEDS_INPUT' && (
        <div className="run-notice">
          {task.current_issue?.issue_type === 'BATCH_FIELD_REVIEW'
            ? 'Open Action Items and complete the consolidated confirmation. Submission triggers one review and recalculation.'
            : 'Resolve the current issue. Analysis will resume from the paused point after submission.'}
        </div>
      )}

      {task.status === 'COMPLETED' && task.current_result_id && !compact && (
        <div className="run-notice">
          Analysis for the current revision is complete. Comparison results are available.
        </div>
      )}

      {startRun.isError && (
        <div className="form-error compact-error" role="alert">
          <div>
            <strong>Run not started</strong>
            <p>{runErrorMessage(startRun.error)}</p>
          </div>
          {lastSubmission && !revisionConflict && (
            <button
              className="button button-secondary"
              type="button"
              onClick={() => startRun.mutate(lastSubmission)}
            >
              Retry same request
            </button>
          )}
        </div>
      )}

      {retryResume.isError && (
        <div className="form-error compact-error" role="alert">
          <div>
            <strong>Checkpoint retry was not queued</strong>
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
            View parsing and comparison results
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
              ? 'Queueing…'
              : task.status === 'FAILED' || task.status === 'NEEDS_INPUT'
                ? 'Reanalyse current revision'
                : 'Start analysis'}
          </button>
        )}
        {canRetryResume && (
          <button
            className="button button-submit"
            type="button"
            onClick={() => retryResume.mutate()}
            disabled={retryResume.isPending}
          >
            {retryResume.isPending ? 'Resuming…' : 'Retry from checkpoint (without reparsing)'}
          </button>
        )}
        {(isActive || task.status === 'NEEDS_INPUT' || task.status === 'FAILED') && (
          <button className="button button-secondary" type="button" onClick={onRefresh}>
            Refresh status
          </button>
        )}
      </div>
    </section>
  )
}
