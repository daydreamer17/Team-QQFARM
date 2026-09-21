import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import type { PolicyImportStatus, PolicyImportUploadMetadata } from '../api/types'
import { FilePreviewDialog, type PreviewFileSource } from '../components/FilePreviewDialog'

const MAX_POLICY_BYTES = 5 * 1024 * 1024
const DIRECTORY_PAGE_SIZE = 8

interface PolicyUploadForm {
  title: string
  effective_from: string
  effective_to: string
  categories: string
  regions: string
}

interface UploadSubmission {
  metadata: PolicyImportUploadMetadata
  file: File
  idempotencyKey: string
}

interface ImportFilters {
  status: '' | PolicyImportStatus
  policy_set_version: string
  category: string
  region: string
}

const initialForm: PolicyUploadForm = {
  title: '',
  effective_from: new Date().toISOString().slice(0, 10),
  effective_to: '',
  categories: 'Electronics',
  regions: 'SG',
}

function splitList(value: string) {
  return [...new Set(value.split(',').map((item) => item.trim()).filter(Boolean))]
}

function dateToIso(value: string) {
  return new Date(`${value}T00:00:00`).toISOString()
}

function errorMessage(error: unknown) {
  if (error instanceof ApiClientError) {
    const messages: Record<string, string> = {
      policy_metadata_invalid: '制度信息未通过校验，请检查标题、生效时间、分类和地区。',
      unsupported_policy_media_type: '只支持 PDF 或 UTF-8 TXT 制度文件。',
      file_too_large: '制度文件不能超过 5 MiB。',
      policy_pdf_requires_ocr: '该 PDF 没有可提取文字，当前策略上传不支持 OCR。',
      policy_document_no_text: '制度文件没有可用正文。',
    }
    return messages[error.code] ?? error.message
  }
  return '制度文件上传失败。'
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  return `${(bytes / 1024 / 1024).toFixed(2)} MiB`
}

function formatDate(value: string | null) {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  }).format(new Date(value))
}

function statusLabel(status: PolicyImportStatus) {
  const labels: Record<PolicyImportStatus, string> = {
    REVIEW_REQUIRED: '待审核',
    READY_TO_PUBLISH: '待发布',
    PUBLISHING: '发布中',
    PUBLISHED: '已发布',
  }
  return labels[status]
}

function statusClass(status: PolicyImportStatus) {
  if (status === 'PUBLISHED') return 'status-ready'
  if (status === 'READY_TO_PUBLISH' || status === 'PUBLISHING') return 'status-pending'
  return 'status-muted'
}

const emptyFilters: ImportFilters = {
  status: '',
  policy_set_version: '',
  category: '',
  region: '',
}

export function ResourcePage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const [form, setForm] = useState(initialForm)
  const [file, setFile] = useState<File | null>(null)
  const [localError, setLocalError] = useState('')
  const [preview, setPreview] = useState<PreviewFileSource | null>(null)
  const [lastSubmission, setLastSubmission] = useState<UploadSubmission | null>(null)
  const [filterForm, setFilterForm] = useState<ImportFilters>(emptyFilters)
  const [filters, setFilters] = useState<ImportFilters>(emptyFilters)
  const [importOffset, setImportOffset] = useState(0)
  const [policySetOffset, setPolicySetOffset] = useState(0)

  const imports = useQuery({
    queryKey: ['policy-imports', 'list', filters, importOffset],
    queryFn: () => api.listPolicyImports({
      status: filters.status || undefined,
      policy_set_version: filters.policy_set_version.trim() || undefined,
      category: filters.category.trim() || undefined,
      region: filters.region.trim() || undefined,
      limit: DIRECTORY_PAGE_SIZE,
      offset: importOffset,
    }),
  })

  const policySets = useQuery({
    queryKey: ['policy-sets', 'list', policySetOffset],
    queryFn: () => api.listPolicySets({
      limit: DIRECTORY_PAGE_SIZE,
      offset: policySetOffset,
    }),
  })

  const upload = useMutation({
    mutationFn: (submission: UploadSubmission) =>
      api.uploadPolicy(
        { metadata: submission.metadata, file: submission.file },
        submission.idempotencyKey,
      ),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ['policy-imports', 'list'] })
      void navigate(`/resources/policies/${result.policy_import_id}`)
    },
  })

  function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setImportOffset(0)
    setFilters({
      ...filterForm,
      policy_set_version: filterForm.policy_set_version.trim(),
      category: filterForm.category.trim(),
      region: filterForm.region.trim(),
    })
  }

  function clearFilters() {
    setFilterForm(emptyFilters)
    setFilters(emptyFilters)
    setImportOffset(0)
  }

  function update(field: keyof PolicyUploadForm, value: string) {
    setForm((current) => ({ ...current, [field]: value }))
    setLocalError('')
    upload.reset()
  }

  function selectFile(selected: File | null) {
    setLocalError('')
    upload.reset()
    if (!selected) {
      setFile(null)
      return
    }
    const extension = selected.name.split('.').pop()?.toLowerCase()
    if (extension !== 'pdf' && extension !== 'txt') {
      setFile(null)
      setLocalError('后端只支持单个 PDF 或 UTF-8 TXT 文件。')
      if (fileInput.current) fileInput.current.value = ''
      return
    }
    if (selected.size > MAX_POLICY_BYTES) {
      setFile(null)
      setLocalError('策略文件不能超过 5 MiB。')
      if (fileInput.current) fileInput.current.value = ''
      return
    }
    if (selected.size === 0) {
      setFile(null)
      setLocalError('不能上传空策略文件。')
      if (fileInput.current) fileInput.current.value = ''
      return
    }
    const mediaType = extension === 'pdf' ? 'application/pdf' : 'text/plain'
    const prepared = selected.type === mediaType
      ? selected
      : new File([selected], selected.name, { type: mediaType, lastModified: selected.lastModified })
    setFile(prepared)
    if (!form.title) update('title', selected.name.replace(/\.[^.]+$/, ''))
  }

  function buildSubmission(): UploadSubmission | null {
    if (!file) {
      setLocalError('请选择一个 PDF 或 TXT 策略文件。')
      return null
    }
    const required: (keyof PolicyUploadForm)[] = [
      'title', 'effective_from', 'categories', 'regions',
    ]
    if (required.some((field) => !form[field].trim())) {
      setLocalError('请填写所有必填元数据。')
      return null
    }
    const categories = splitList(form.categories)
    const regions = splitList(form.regions)
    if (categories.length === 0 || regions.length === 0) {
      setLocalError('分类和地区至少各填写一项。')
      return null
    }
    if (form.effective_to && form.effective_to <= form.effective_from) {
      setLocalError('失效日期必须晚于生效日期。')
      return null
    }
    return {
      file,
      idempotencyKey: createIdempotencyKey(),
      metadata: {
        title: form.title.trim(),
        effective_from: dateToIso(form.effective_from),
        effective_to: form.effective_to ? dateToIso(form.effective_to) : null,
        categories,
        regions,
      },
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const submission = buildSubmission()
    if (!submission) return
    setLastSubmission(submission)
    upload.mutate(submission)
  }

  const hasActiveImportFilters = Object.values(filters).some(Boolean)
  const showImportFilters = hasActiveImportFilters || (imports.data?.total ?? 0) > 0

  return (
    <div className="page-stack resource-page">
      <section className="page-heading resource-heading">
        <div>
          <h1>规则资源库</h1>
          <p>填写制度信息，上传文件，审核条款后发布。</p>
        </div>
      </section>

      <form className="card policy-upload-form" onSubmit={submit}>
        <div className="section-heading">
          <div><h2>上传制度文件</h2></div>
        </div>

        <div className="policy-form-grid">
          <label className="field policy-field-wide"><span>制度标题</span><input required value={form.title} onChange={(event) => update('title', event.target.value)} /></label>
          <label className="field"><span>分类 <small>逗号分隔</small></span><input required value={form.categories} onChange={(event) => update('categories', event.target.value)} /></label>
          <label className="field"><span>地区 <small>逗号分隔</small></span><input required value={form.regions} onChange={(event) => update('regions', event.target.value)} /></label>
          <label className="field"><span>生效日期</span><input required type="date" value={form.effective_from} onChange={(event) => update('effective_from', event.target.value)} /></label>
          <label className="field"><span>失效日期 <small>可选</small></span><input type="date" value={form.effective_to} onChange={(event) => update('effective_to', event.target.value)} /></label>
        </div>

        <label className="resource-dropzone policy-dropzone">
          <span className="resource-dropzone-icon" aria-hidden="true">↑</span>
          <strong>{file ? file.name : '选择一个策略文件'}</strong>
          <small>{file ? formatBytes(file.size) : 'PDF 或 UTF-8 TXT · 最大 5 MiB'}</small>
          <input ref={fileInput} type="file" accept=".pdf,.txt,application/pdf,text/plain" onChange={(event) => selectFile(event.target.files?.[0] ?? null)} />
        </label>

        <details className="policy-generated-settings">
          <summary>更多设置</summary>
          <p>策略集 ID、策略集版本、Policy ID、文档 ID 和文档版本将在上传时自动生成。</p>
        </details>

        {file && (
          <div className="policy-selected-file">
            <span><strong>{file.name}</strong><small>{file.type || '按扩展名识别'} · {formatBytes(file.size)}</small></span>
            <button type="button" onClick={() => setPreview({ name: file.name, mediaType: file.type, sizeBytes: file.size, file })}>本地预览</button>
          </div>
        )}

        {(localError || upload.isError) && (
          <div className="form-error compact-error" role="alert">
            {localError || errorMessage(upload.error)}
          </div>
        )}

        <div className="policy-form-actions">
          {upload.isError && lastSubmission && (
            <button className="button button-secondary" type="button" onClick={() => upload.mutate(lastSubmission)}>重试相同请求</button>
          )}
          <button className="button button-submit" type="submit" disabled={upload.isPending}>
            {upload.isPending ? '正在上传并解析…' : '上传并审核'}
          </button>
        </div>
      </form>

      <section className="card policy-directory">
        <div className="section-heading">
          <div><h2>制度导入记录</h2></div>
          {imports.data && imports.data.total > 0 && <span>{imports.data.total} 条记录</span>}
        </div>

        {showImportFilters && <form className="policy-directory-filters" onSubmit={applyFilters}>
          <label><span>状态</span><select value={filterForm.status} onChange={(event) => setFilterForm((current) => ({ ...current, status: event.target.value as ImportFilters['status'] }))}><option value="">全部状态</option><option value="REVIEW_REQUIRED">待审核</option><option value="READY_TO_PUBLISH">待发布</option><option value="PUBLISHING">发布中</option><option value="PUBLISHED">已发布</option></select></label>
          <label><span>策略版本</span><input value={filterForm.policy_set_version} onChange={(event) => setFilterForm((current) => ({ ...current, policy_set_version: event.target.value }))} placeholder="例如 2026.09.1" /></label>
          <label><span>分类</span><input value={filterForm.category} onChange={(event) => setFilterForm((current) => ({ ...current, category: event.target.value }))} placeholder="例如 Electronics" /></label>
          <label><span>地区</span><input value={filterForm.region} onChange={(event) => setFilterForm((current) => ({ ...current, region: event.target.value }))} placeholder="例如 SG" /></label>
          <div className="policy-filter-actions"><button className="button button-secondary" type="button" onClick={clearFilters}>清除</button><button className="button button-submit" type="submit">筛选</button></div>
        </form>}

        {imports.isPending && <div className="policy-directory-state">正在读取制度导入记录…</div>}
        {imports.isError && <div className="policy-directory-state policy-directory-error"><span>导入记录读取失败，不影响上传新制度。</span><button className="button button-secondary" type="button" onClick={() => void imports.refetch()}>重试</button></div>}
        {imports.data && imports.data.items.length === 0 && (
          <div className="policy-directory-state">{hasActiveImportFilters ? '没有符合条件的记录。' : '暂无导入记录。'}</div>
        )}
        {imports.data && imports.data.items.length > 0 && (
          <div className="policy-directory-table-wrap">
            <table className="policy-directory-table">
              <thead><tr><th>制度</th><th>状态</th><th>策略版本</th><th>范围</th><th>条款</th><th>更新时间</th><th /></tr></thead>
              <tbody>{imports.data.items.map((item) => (
                <tr key={item.policy_import_id}>
                  <td><strong>{item.title}</strong><span>{item.original_filename} · {formatBytes(item.size_bytes)}</span><small>{item.document_id} v{item.document_version}</small></td>
                  <td><span className={`status-pill ${statusClass(item.status)}`}>{statusLabel(item.status)}</span><small>第 {item.revision} 版</small></td>
                  <td><strong>{item.policy_set_id}</strong><span>{item.policy_set_version}</span>{item.policy_index_version && <small>{item.policy_index_version}</small>}</td>
                  <td><span>{item.categories.join(' / ') || '—'}</span><small>{item.regions.join(' / ') || '—'}</small></td>
                  <td>{item.clause_count}</td>
                  <td>{formatDate(item.updated_at)}</td>
                  <td><Link className="policy-row-link" to={`/resources/policies/${item.policy_import_id}`}>{item.status === 'PUBLISHED' ? '查看' : '审核'}</Link></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
        {imports.data && imports.data.total > 0 && (
          <div className="policy-pagination"><span>第 {imports.data.offset + 1}–{Math.min(imports.data.offset + imports.data.items.length, imports.data.total)} 条，共 {imports.data.total} 条</span><div><button className="button button-secondary" type="button" disabled={importOffset === 0} onClick={() => setImportOffset(Math.max(0, importOffset - DIRECTORY_PAGE_SIZE))}>上一页</button><button className="button button-secondary" type="button" disabled={importOffset + DIRECTORY_PAGE_SIZE >= imports.data.total} onClick={() => setImportOffset(importOffset + DIRECTORY_PAGE_SIZE)}>下一页</button></div></div>
        )}
      </section>

      <section className="card policy-directory">
        <div className="section-heading">
          <div><h2>可绑定的制度版本</h2></div>
          {policySets.data && policySets.data.total > 0 && <span>{policySets.data.total} 个版本</span>}
        </div>
        {policySets.isPending && <div className="policy-directory-state">正在读取已发布制度…</div>}
        {policySets.isError && <div className="policy-directory-state policy-directory-error"><span>已发布制度读取失败。</span><button className="button button-secondary" type="button" onClick={() => void policySets.refetch()}>重试</button></div>}
        {policySets.data && policySets.data.items.length === 0 && <div className="policy-directory-state">暂无已发布制度。</div>}
        {policySets.data && policySets.data.items.length > 0 && (
          <div className="published-policy-grid">{policySets.data.items.map((item) => (
            <article key={`${item.policy_set_id}:${item.policy_set_version}:${item.policy_index_version}`} className="published-policy-card">
              <header><div><strong>{item.policy_set_id}</strong><span>版本 {item.policy_set_version}</span></div><span className="status-pill status-ready">已发布</span></header>
              <dl><div><dt>索引版本</dt><dd>{item.policy_index_version}</dd></div><div><dt>文档 / 条款</dt><dd>{item.document_count} / {item.clause_count}</dd></div><div><dt>分类</dt><dd>{item.categories.join(' / ') || '—'}</dd></div><div><dt>地区</dt><dd>{item.regions.join(' / ') || '—'}</dd></div><div><dt>发布时间</dt><dd>{formatDate(item.published_at)}</dd></div></dl>
            </article>
          ))}</div>
        )}
        {policySets.data && policySets.data.total > 0 && (
          <div className="policy-pagination"><span>第 {policySets.data.offset + 1}–{Math.min(policySets.data.offset + policySets.data.items.length, policySets.data.total)} 个，共 {policySets.data.total} 个</span><div><button className="button button-secondary" type="button" disabled={policySetOffset === 0} onClick={() => setPolicySetOffset(Math.max(0, policySetOffset - DIRECTORY_PAGE_SIZE))}>上一页</button><button className="button button-secondary" type="button" disabled={policySetOffset + DIRECTORY_PAGE_SIZE >= policySets.data.total} onClick={() => setPolicySetOffset(policySetOffset + DIRECTORY_PAGE_SIZE)}>下一页</button></div></div>
        )}
      </section>

      {preview && <FilePreviewDialog source={preview} onClose={() => setPreview(null)} />}
    </div>
  )
}
