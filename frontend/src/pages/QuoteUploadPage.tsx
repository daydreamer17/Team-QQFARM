import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import type { QuoteUploadResponse, TaskDetail } from '../api/types'

const MAX_FILE_BYTES = 5 * 1024 * 1024

interface UploadSubmission {
  expectedTaskRevision: number
  supplierId: string
  isSynthetic: boolean
  file: File
  idempotencyKey: string
}

interface UploadedQuote extends QuoteUploadResponse {
  supplierId: string
  originalFilename: string
  sizeBytes: number
}

function prepareFile(file: File) {
  const name = file.name.toLowerCase()
  const mediaType = name.endsWith('.pdf')
    ? 'application/pdf'
    : name.endsWith('.csv')
      ? 'text/csv'
      : null

  if (!mediaType) {
    throw new Error('仅支持 PDF 或 CSV 报价文件。')
  }
  if (file.size === 0) {
    throw new Error('不能上传空文件。')
  }
  if (file.size > MAX_FILE_BYTES) {
    throw new Error('单个文件不能超过 5 MiB。')
  }
  if (file.type === mediaType) return file

  return new File([file], file.name, {
    type: mediaType,
    lastModified: file.lastModified,
  })
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  return `${(bytes / 1024).toFixed(1)} KiB`
}

function getErrorMessage(error: unknown) {
  if (error instanceof ApiClientError) {
    if (error.code === 'task_revision_conflict') {
      const actual = error.details.actual
      return `任务版本已经变化，服务器当前 revision 为 ${String(actual ?? '未知')}。请刷新后重新提交。`
    }
    return error.message
  }
  return '报价上传失败。'
}

export function QuoteUploadPage() {
  const { taskId = '' } = useParams()
  const queryClient = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const [supplierId, setSupplierId] = useState('')
  const [isSynthetic, setIsSynthetic] = useState(true)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [localError, setLocalError] = useState('')
  const [lastSubmission, setLastSubmission] = useState<UploadSubmission | null>(null)
  const [uploadedQuotes, setUploadedQuotes] = useState<UploadedQuote[]>([])

  const task = useQuery({
    queryKey: ['tasks', taskId],
    queryFn: () => api.getTask(taskId),
    enabled: Boolean(taskId),
  })

  const upload = useMutation({
    mutationFn: (submission: UploadSubmission) =>
      api.uploadQuote(
        taskId,
        {
          expectedTaskRevision: submission.expectedTaskRevision,
          supplierId: submission.supplierId,
          isSynthetic: submission.isSynthetic,
          file: submission.file,
        },
        submission.idempotencyKey,
      ),
    onSuccess: async (result, submission) => {
      setUploadedQuotes((current) => [
        ...current,
        {
          ...result,
          supplierId: submission.supplierId,
          originalFilename: submission.file.name,
          sizeBytes: submission.file.size,
        },
      ])
      setSupplierId('')
      setSelectedFile(null)
      setLastSubmission(null)
      if (fileInput.current) fileInput.current.value = ''
      queryClient.setQueryData<TaskDetail>(['tasks', taskId], (current) =>
        current ? { ...current, task_revision: result.task_revision } : current,
      )
      await queryClient.invalidateQueries({ queryKey: ['tasks', taskId] })
    },
  })

  function clearError() {
    setLocalError('')
    upload.reset()
  }

  function handleFile(file: File | null) {
    clearError()
    if (!file) {
      setSelectedFile(null)
      return
    }
    try {
      setSelectedFile(prepareFile(file))
    } catch (error) {
      setSelectedFile(null)
      if (fileInput.current) fileInput.current.value = ''
      setLocalError(error instanceof Error ? error.message : '文件无效。')
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    clearError()
    if (!task.data) {
      setLocalError('任务尚未加载完成。')
      return
    }
    if (!supplierId.trim()) {
      setLocalError('请输入供应商编号。')
      return
    }
    if (!selectedFile) {
      setLocalError('请选择一份 PDF 或 CSV 报价文件。')
      return
    }

    const submission: UploadSubmission = {
      expectedTaskRevision: task.data.task_revision,
      supplierId: supplierId.trim(),
      isSynthetic,
      file: selectedFile,
      idempotencyKey: createIdempotencyKey(),
    }
    setLastSubmission(submission)
    upload.mutate(submission)
  }

  const revisionConflict =
    upload.error instanceof ApiClientError &&
    upload.error.code === 'task_revision_conflict'

  return (
    <div className="page-stack">
      <section className="page-heading">
        <div>
          <p className="eyebrow">QUOTE INTAKE</p>
          <h1>上传供应商报价</h1>
          <p>
            任务 <code>{taskId}</code> · 当前 Revision {task.data?.task_revision ?? '…'}
          </p>
        </div>
        <Link className="button button-secondary" to={`/tasks/${taskId}`}>返回任务</Link>
      </section>

      <section className="upload-layout">
        <form className="card upload-form" onSubmit={handleSubmit}>
          <div>
            <p className="eyebrow">NEW QUOTE</p>
            <h2>登记一份报价</h2>
            <p className="helper-text">每次只上传一份文件；成功后使用新的 revision 上传下一份。</p>
          </div>

          <label className="field">
            <span>供应商编号</span>
            <input
              required
              placeholder="例如 SUP-001"
              value={supplierId}
              onChange={(event) => {
                setSupplierId(event.target.value)
                clearError()
              }}
            />
          </label>

          <label className="field">
            <span>报价文件</span>
            <input
              ref={fileInput}
              required
              type="file"
              accept=".pdf,.csv,application/pdf,text/csv"
              onChange={(event) => handleFile(event.target.files?.[0] ?? null)}
            />
            <small>仅限 PDF/CSV，文件必须非空且不超过 5 MiB。</small>
          </label>

          {selectedFile && (
            <div className="selected-file">
              <strong>{selectedFile.name}</strong>
              <span>{formatBytes(selectedFile.size)} · {selectedFile.type}</span>
            </div>
          )}

          <label className="field checkbox-field">
            <input
              type="checkbox"
              checked={isSynthetic}
              onChange={(event) => {
                setIsSynthetic(event.target.checked)
                clearError()
              }}
            />
            <span>这是合成测试数据</span>
          </label>

          {(localError || upload.isError) && (
            <div className="form-error compact-error" role="alert">
              <div>
                <strong>上传未完成</strong>
                <p>{localError || getErrorMessage(upload.error)}</p>
              </div>
              {!localError && lastSubmission && !revisionConflict && (
                <button className="button button-secondary" type="button" onClick={() => upload.mutate(lastSubmission)}>
                  重试相同请求
                </button>
              )}
              {revisionConflict && (
                <button className="button button-secondary" type="button" onClick={() => void task.refetch()}>
                  刷新任务版本
                </button>
              )}
            </div>
          )}

          <button
            className="button button-submit upload-submit"
            type="submit"
            disabled={upload.isPending || task.isPending}
          >
            {upload.isPending ? '正在上传…' : '上传报价'}
          </button>
        </form>

        <aside className="card upload-guidance">
          <p className="eyebrow">REVISION SAFETY</p>
          <h2>版本保护</h2>
          <ol>
            <li>上传请求携带当前 task revision。</li>
            <li>成功后后端将 revision 加 1。</li>
            <li>若收到 409，先刷新，绝不自动重放旧内容。</li>
          </ol>
        </aside>
      </section>

      <section>
        <div className="section-heading">
          <div>
            <p className="eyebrow">CURRENT SESSION</p>
            <h2>本次上传</h2>
          </div>
          <span>{uploadedQuotes.length} 份</span>
        </div>
        {uploadedQuotes.length === 0 ? (
          <div className="card empty-upload-list">尚未在本页面上传报价。</div>
        ) : (
          <div className="uploaded-list">
            {uploadedQuotes.map((quote) => (
              <article className="card uploaded-quote" key={quote.document_id}>
                <div>
                  <span className="status-pill status-ready">已登记</span>
                  <h3>{quote.supplierId}</h3>
                  <p>{quote.originalFilename} · {formatBytes(quote.sizeBytes)}</p>
                </div>
                <dl>
                  <div><dt>Revision</dt><dd>{quote.task_revision}</dd></div>
                  <div><dt>Quote ID</dt><dd>{quote.quote_id}</dd></div>
                  <div><dt>Document ID</dt><dd>{quote.document_id}</dd></div>
                  <div><dt>SHA-256</dt><dd>{quote.document_sha256.slice(0, 16)}…</dd></div>
                </dl>
              </article>
            ))}
          </div>
        )}
        <p className="session-note">
          当前后端尚未提供报价列表接口，因此这里仅展示本次页面会话上传成功的文件。
        </p>
      </section>
    </div>
  )
}
