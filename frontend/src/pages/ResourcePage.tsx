import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import type { PolicyImportStatus, PolicyImportSummary, PolicyImportUploadMetadata, PolicySetSummary } from '../api/types'
import { FilePreviewDialog, type PreviewFileSource } from '../components/FilePreviewDialog'

const MAX_POLICY_BYTES = 5 * 1024 * 1024

interface PolicyUploadForm {
  policy_set_name: string
  effective_from: string
  effective_to: string
  categories: string
  regions: string
}

interface UploadSubmission {
  items: Array<{
    metadata: PolicyImportUploadMetadata
    file: File
    idempotencyKey: string
  }>
}

type PolicyUploadMode = 'NEW' | 'UPDATE'

const initialForm: PolicyUploadForm = {
  policy_set_name: '',
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
      unsupported_policy_media_type: '只支持 PDF、UTF-8 TXT 或 Markdown 制度文件。',
      file_too_large: '制度文件不能超过 5 MiB。',
      policy_pdf_requires_ocr: '该 PDF 没有可提取文字，当前策略上传不支持 OCR。',
      policy_document_no_text: '制度文件没有可用正文。',
      policy_document_not_policy: 'README 是说明文件，不属于制度正文，请只上传实际制度文件。',
      policy_set_not_found: '没有找到该制度版本，请刷新后重试。',
      policy_set_not_published: '该制度版本尚未发布，不能停用。',
      policy_set_index_unavailable: '该制度版本没有可用的发布索引。',
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

function policyKey(policy: PolicySetSummary) {
  return JSON.stringify([
    policy.policy_set_id,
    policy.policy_set_version,
    policy.policy_index_version,
  ])
}

function groupPolicySets(items: PolicySetSummary[]) {
  const groups = new Map<string, PolicySetSummary[]>()
  for (const item of items) {
    const versions = groups.get(item.policy_set_id) ?? []
    versions.push(item)
    groups.set(item.policy_set_id, versions)
  }
  return [...groups.values()].map((versions) => {
    versions.sort((left, right) => {
      const published = (right.published_at ?? '').localeCompare(left.published_at ?? '')
      return published || right.policy_set_version.localeCompare(left.policy_set_version)
    })
    const current = versions.find((item) => item.status === 'PUBLISHED') ?? versions[0]
    return {
      current,
      history: versions.filter((item) => item !== current),
    }
  })
}

interface PendingPolicyVersion {
  key: string
  policy_set_id: string
  policy_set_version: string
  items: PolicyImportSummary[]
  status: PolicyImportStatus
  updated_at: string
  categories: string[]
  regions: string[]
  clause_count: number
}

function groupPendingPolicyVersions(items: PolicyImportSummary[]) {
  const versions = new Map<string, PolicyImportSummary[]>()
  for (const item of items) {
    const key = JSON.stringify([item.policy_set_id, item.policy_set_version])
    const files = versions.get(key) ?? []
    files.push(item)
    versions.set(key, files)
  }
  const grouped: PendingPolicyVersion[] = [...versions.entries()].map(([key, files]) => {
    const ordered = [...files].sort((left, right) => {
      const priority = { REVIEW_REQUIRED: 0, READY_TO_PUBLISH: 1, PUBLISHING: 2, PUBLISHED: 3 }
      return priority[left.status] - priority[right.status]
        || left.original_filename.localeCompare(right.original_filename)
    })
    const status: PolicyImportStatus = ordered.some((item) => item.status === 'REVIEW_REQUIRED')
      ? 'REVIEW_REQUIRED'
      : ordered.some((item) => item.status === 'PUBLISHING') ? 'PUBLISHING' : 'READY_TO_PUBLISH'
    return {
      key,
      policy_set_id: ordered[0].policy_set_id,
      policy_set_version: ordered[0].policy_set_version,
      items: ordered,
      status,
      updated_at: ordered.reduce((latest, item) => item.updated_at > latest ? item.updated_at : latest, ''),
      categories: [...new Set(ordered.flatMap((item) => item.categories))],
      regions: [...new Set(ordered.flatMap((item) => item.regions))],
      clause_count: ordered.reduce((total, item) => total + item.clause_count, 0),
    }
  })
  return grouped.sort((left, right) => right.updated_at.localeCompare(left.updated_at)
    || right.policy_set_version.localeCompare(left.policy_set_version))
}

function PendingVersionCard({ version, historical = false }: {
  version: PendingPolicyVersion
  historical?: boolean
}) {
  const reviewCount = version.items.filter((item) => item.status === 'REVIEW_REQUIRED').length
  const readyCount = version.items.filter((item) => item.status === 'READY_TO_PUBLISH').length
  const publishingCount = version.items.filter((item) => item.status === 'PUBLISHING').length
  const target = version.items.find((item) => item.status === 'REVIEW_REQUIRED') ?? version.items[0]
  const action = historical
    ? '查看草稿'
    : version.status === 'REVIEW_REQUIRED'
    ? '继续处理'
    : version.status === 'PUBLISHING' ? '查看进度' : '发布此版本'
  return <article className={`pending-version-card${historical ? ' pending-version-card-history' : ''}`}>
    <header>
      <div><strong>{version.policy_set_id}</strong><span>版本 {version.policy_set_version}</span></div>
      <span className={`status-pill ${statusClass(version.status)}`}>{statusLabel(version.status)}</span>
    </header>
    <p className="pending-version-summary">
      {version.items.length} 个文件
      {reviewCount > 0 && ` · ${reviewCount} 个待处理`}
      {readyCount > 0 && ` · ${readyCount} 个已识别`}
      {publishingCount > 0 && ` · ${publishingCount} 个发布中`}
    </p>
    <dl>
      <div><dt>适用范围</dt><dd>{version.categories.join(' / ') || '—'} · {version.regions.join(' / ') || '—'}</dd></div>
      <div><dt>条款</dt><dd>{version.clause_count}</dd></div>
      <div><dt>更新时间</dt><dd>{formatDate(version.updated_at)}</dd></div>
    </dl>
    <details className="pending-version-files">
      <summary>查看文件（{version.items.length}）</summary>
      <div>{version.items.map((item) => <div className="pending-version-file" key={item.policy_import_id}>
        <span><strong>{item.original_filename}</strong><small>{item.clause_count} 条 · {formatBytes(item.size_bytes)}</small></span>
        <span><span className={`status-pill ${statusClass(item.status)}`}>{statusLabel(item.status)}</span><Link to={`/resources/policies/${item.policy_import_id}`}>查看</Link></span>
      </div>)}</div>
    </details>
    <div className="pending-version-actions">
      <Link className={`button ${!historical && version.status === 'READY_TO_PUBLISH' ? 'button-submit' : 'button-secondary'}`} to={`/resources/policies/${target.policy_import_id}`}>{action}</Link>
    </div>
  </article>
}

export function ResourcePage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const [form, setForm] = useState(initialForm)
  const [files, setFiles] = useState<File[]>([])
  const [localError, setLocalError] = useState('')
  const [preview, setPreview] = useState<PreviewFileSource | null>(null)
  const [lastSubmission, setLastSubmission] = useState<UploadSubmission | null>(null)
  const [showUpload, setShowUpload] = useState(false)
  const [showInactivePolicies, setShowInactivePolicies] = useState(false)
  const [uploadMode, setUploadMode] = useState<PolicyUploadMode>('NEW')
  const [basePolicy, setBasePolicy] = useState<PolicySetSummary | null>(null)

  const imports = useQuery({
    queryKey: ['policy-imports', 'list', 'pending'],
    queryFn: async () => {
      const responses = await Promise.all(
        (['REVIEW_REQUIRED', 'READY_TO_PUBLISH', 'PUBLISHING'] as PolicyImportStatus[])
          .map((status) => api.listPolicyImports({ status, limit: 100, offset: 0 })),
      )
      return responses
        .flatMap((response) => response.items)
        .sort((left, right) => right.updated_at.localeCompare(left.updated_at))
    },
  })

  const policySets = useQuery({
    queryKey: ['policy-sets', 'list'],
    queryFn: () => api.listPolicySets({
      include_inactive: true,
      limit: 100,
      offset: 0,
    }),
  })

  const upload = useMutation({
    mutationFn: async (submission: UploadSubmission) => {
      const results = []
      for (const item of submission.items) {
        results.push(await api.uploadPolicy(
          { metadata: item.metadata, file: item.file },
          item.idempotencyKey,
        ))
      }
      return results
    },
    onSuccess: async (results) => {
      await queryClient.invalidateQueries({ queryKey: ['policy-imports', 'list'] })
      void navigate(`/resources/policies/${results[0].policy_import_id}`)
    },
  })

  const updatePolicySets = useQuery({
    queryKey: ['policy-sets', 'update-options'],
    queryFn: () => api.listPolicySets({
      include_inactive: true,
      limit: 100,
      offset: 0,
    }),
    enabled: showUpload && uploadMode === 'UPDATE',
  })

  const deactivate = useMutation({
    mutationFn: (policy: PolicySetSummary) => api.deactivatePolicySet(
      policy.policy_set_id,
      policy.policy_set_version,
      createIdempotencyKey(),
    ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['policy-sets', 'list'] })
    },
  })

  const policySetGroups = groupPolicySets(policySets.data?.items ?? [])
  const inactivePolicySetCount = policySetGroups.filter(
    (group) => group.current.status === 'INACTIVE',
  ).length
  const visiblePolicySetGroups = showInactivePolicies
    ? policySetGroups
    : policySetGroups.filter((group) => group.current.status === 'PUBLISHED')
  const updateBasePolicies = groupPolicySets(updatePolicySets.data?.items ?? [])
    .map((group) => group.current)
  const pendingPolicyVersions = groupPendingPolicyVersions(imports.data ?? [])
  const currentPendingVersions = pendingPolicyVersions.slice(0, 1)
  const otherPendingVersions = pendingPolicyVersions.slice(1)

  function update(field: keyof PolicyUploadForm, value: string) {
    setForm((current) => ({ ...current, [field]: value }))
    setLocalError('')
    upload.reset()
  }

  function selectFiles(selected: File[]) {
    setLocalError('')
    upload.reset()
    if (selected.length === 0) {
      setFiles([])
      return
    }
    const prepared: File[] = []
    for (const selectedFile of selected) {
      const extension = selectedFile.name.split('.').pop()?.toLowerCase()
      if (['readme.md', 'readme.txt'].includes(selectedFile.name.toLowerCase())) {
        setFiles([])
        setLocalError(`“${selectedFile.name}”是说明文件，不属于制度正文，请不要上传。`)
        if (fileInput.current) fileInput.current.value = ''
        return
      }
      if (extension !== 'pdf' && extension !== 'txt' && extension !== 'md') {
        setFiles([])
        setLocalError(`“${selectedFile.name}”格式不支持，只能上传 PDF、UTF-8 TXT 或 Markdown。`)
        if (fileInput.current) fileInput.current.value = ''
        return
      }
      if (selectedFile.size > MAX_POLICY_BYTES) {
        setFiles([])
        setLocalError(`“${selectedFile.name}”超过 5 MiB。`)
        if (fileInput.current) fileInput.current.value = ''
        return
      }
      if (selectedFile.size === 0) {
        setFiles([])
        setLocalError(`“${selectedFile.name}”是空文件。`)
        if (fileInput.current) fileInput.current.value = ''
        return
      }
      const mediaType = extension === 'pdf'
        ? 'application/pdf'
        : extension === 'md' ? 'text/markdown' : 'text/plain'
      prepared.push(selectedFile.type === mediaType
        ? selectedFile
        : new File([selectedFile], selectedFile.name, { type: mediaType, lastModified: selectedFile.lastModified }))
    }
    setFiles(prepared)
    if (!form.policy_set_name && prepared.length === 1) {
      update('policy_set_name', prepared[0].name.replace(/\.[^.]+$/, ''))
    }
  }

  function resetUploadFiles() {
    setFiles([])
    setLocalError('')
    setLastSubmission(null)
    upload.reset()
    if (fileInput.current) fileInput.current.value = ''
  }

  function toggleBlankUpload() {
    if (showUpload) {
      setShowUpload(false)
      return
    }
    setForm(initialForm)
    setUploadMode('NEW')
    setBasePolicy(null)
    resetUploadFiles()
    setShowUpload(true)
  }

  function selectBasePolicy(policy: PolicySetSummary | null) {
    setBasePolicy(policy)
    setForm({
      ...initialForm,
      policy_set_name: policy?.policy_set_id ?? '',
      categories: policy?.categories.join(', ') ?? initialForm.categories,
      regions: policy?.regions.join(', ') ?? initialForm.regions,
    })
    resetUploadFiles()
  }

  function changeUploadMode(mode: PolicyUploadMode) {
    setUploadMode(mode)
    selectBasePolicy(null)
  }

  function beginNewVersion(policy: PolicySetSummary) {
    setUploadMode('UPDATE')
    selectBasePolicy(policy)
    setShowUpload(true)
  }

  function deactivateVersion(policy: PolicySetSummary) {
    if (!window.confirm(`停用制度 ${policy.policy_set_id} · 版本 ${policy.policy_set_version}？\n停用后新采购任务将不能再绑定该版本，历史任务不受影响。`)) return
    deactivate.mutate(policy)
  }

  function buildSubmission(): UploadSubmission | null {
    if (uploadMode === 'UPDATE' && basePolicy === null) {
      setLocalError('请选择需要更新的已有制度版本。')
      return null
    }
    if (files.length === 0) {
      setLocalError('请至少选择一个 PDF、TXT 或 Markdown 制度文件。')
      return null
    }
    const required: (keyof PolicyUploadForm)[] = [
      'policy_set_name', 'effective_from', 'categories', 'regions',
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
    const policySetId = form.policy_set_name.trim()
    const now = new Date()
    const pad = (value: number) => String(value).padStart(2, '0')
    const policySetVersion = `${now.getFullYear()}.${pad(now.getMonth() + 1)}.${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
    return {
      items: files.map((selectedFile) => {
        const suffix = createIdempotencyKey().replace(/-/g, '').slice(0, 12).toUpperCase()
        return {
          file: selectedFile,
          idempotencyKey: createIdempotencyKey(),
          metadata: {
            policy_set_id: policySetId,
            policy_set_version: policySetVersion,
            policy_id: `POL-${suffix}`,
            document_id: `DOC-${suffix}`,
            document_version: '1.0.0',
            title: selectedFile.name.replace(/\.[^.]+$/, ''),
            effective_from: dateToIso(form.effective_from),
            effective_to: form.effective_to ? dateToIso(form.effective_to) : null,
            categories,
            regions,
          },
        }
      }),
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const submission = buildSubmission()
    if (!submission) return
    setLastSubmission(submission)
    upload.mutate(submission)
  }

  return (
    <div className="page-stack resource-page">
      <section className="page-heading resource-heading app-page-heading">
        <div>
          <h1>规则资源库</h1>
          <p>查看已发布制度，处理待审核文件，并按需发布新版本。</p>
        </div>
        <button className="button button-submit" type="button" onClick={toggleBlankUpload}>{showUpload ? '收起上传' : '上传制度版本'}</button>
      </section>

      <section className="card policy-directory">
        <div className="section-heading">
          <div><h2>已发布制度</h2><p className="section-helper">每个制度集只展示当前版本；旧版本保留在历史记录中。</p></div>
          <div className="policy-directory-heading-actions">
            {visiblePolicySetGroups.length > 0 && <span>{visiblePolicySetGroups.length} 个制度集</span>}
            {inactivePolicySetCount > 0 && (
              <button
                className="button button-secondary"
                type="button"
                aria-pressed={showInactivePolicies}
                onClick={() => setShowInactivePolicies((current) => !current)}
              >
                {showInactivePolicies ? '隐藏已停用制度' : `显示已停用制度（${inactivePolicySetCount}）`}
              </button>
            )}
          </div>
        </div>
        {policySets.isPending && <div className="policy-directory-state">正在读取已发布制度…</div>}
        {policySets.isError && <div className="policy-directory-state policy-directory-error"><span>已发布制度读取失败。</span><button className="button button-secondary" type="button" onClick={() => void policySets.refetch()}>重试</button></div>}
        {policySets.data && visiblePolicySetGroups.length === 0 && (
          <div className="policy-directory-state">
            {inactivePolicySetCount > 0
              ? `暂无可用制度；${inactivePolicySetCount} 个已停用制度已隐藏。`
              : '暂无已发布制度。'}
          </div>
        )}
        {visiblePolicySetGroups.length > 0 && (
          <div className="published-policy-grid">{visiblePolicySetGroups.map(({ current: item, history }) => (
            <article key={`${item.policy_set_id}:${item.policy_set_version}:${item.policy_index_version}`} className="published-policy-card">
              <header><div><strong>{item.policy_set_id}</strong><span>版本 {item.policy_set_version}</span></div><span className={`status-pill ${item.status === 'PUBLISHED' ? 'status-ready' : 'status-muted'}`}>{item.status === 'PUBLISHED' ? '可使用' : '已停用'}</span></header>
              <dl><div><dt>文件 / 条款</dt><dd>{item.document_count} / {item.clause_count}</dd></div><div><dt>采购类别</dt><dd>{item.categories.join(' / ') || '—'}</dd></div><div><dt>地区</dt><dd>{item.regions.join(' / ') || '—'}</dd></div><div><dt>发布时间</dt><dd>{formatDate(item.published_at)}</dd></div></dl>
              {history.length > 0 && <details className="published-policy-history">
                <summary>查看历史版本（{history.length}）</summary>
                <div className="published-policy-history-list">{history.map((version) => (
                  <div className="published-policy-history-row" key={`${version.policy_set_version}:${version.policy_index_version}`}>
                    <span><strong>版本 {version.policy_set_version}</strong><small>{formatDate(version.published_at)}</small></span>
                    <span className="status-pill status-muted">{version.status === 'PUBLISHED' ? '已被替代' : '已停用'}</span>
                  </div>
                ))}</div>
              </details>}
              <div className="published-policy-actions">
                <button className="button button-secondary" type="button" onClick={() => beginNewVersion(item)}>发布新版本</button>
                {item.status === 'PUBLISHED' && <button className="button button-danger" type="button" disabled={deactivate.isPending} onClick={() => deactivateVersion(item)}>停用制度版本</button>}
              </div>
            </article>
          ))}</div>
        )}
        {deactivate.isError && <div className="form-error compact-error" role="alert">{errorMessage(deactivate.error)}</div>}
      </section>

      <section className="card policy-directory pending-policy-directory">
        <div className="section-heading">
          <div><h2>待处理版本</h2><p className="section-helper">只突出最近更新的一个版本；其他版本默认收起。</p></div>
          {imports.data && currentPendingVersions.length > 0 && <span>最新 1 个版本</span>}
        </div>
        {imports.isPending && <div className="policy-directory-state">正在读取待处理版本…</div>}
        {imports.isError && <div className="policy-directory-state policy-directory-error"><span>待处理版本读取失败。</span><button className="button button-secondary" type="button" onClick={() => void imports.refetch()}>重试</button></div>}
        {imports.data && imports.data.length === 0 && <div className="policy-directory-state">当前没有待处理版本。</div>}
        {currentPendingVersions.length > 0 && <div className="pending-version-grid">
          {currentPendingVersions.map((version) => <PendingVersionCard key={version.key} version={version} />)}
        </div>}
        {otherPendingVersions.length > 0 && <details className="pending-draft-history">
          <summary>其他待处理版本（{otherPendingVersions.length}）</summary>
          <p>这些版本仍可处理，但不会和最近更新的版本同时铺开。</p>
          <div className="pending-version-grid pending-version-history-grid">
            {otherPendingVersions.map((version) => <PendingVersionCard key={version.key} version={version} historical />)}
          </div>
        </details>}
      </section>

      {showUpload && <form className="card policy-upload-form" onSubmit={submit}>
        <div className="section-heading"><div><h2>上传制度版本</h2><p className="section-helper">{basePolicy ? `正在更新 ${basePolicy.policy_set_id} 的版本 ${basePolicy.policy_set_version}；请上传新版本使用的完整文件集。` : uploadMode === 'UPDATE' ? '请选择需要更新的已有制度，再上传新版本完整文件集。' : '创建全新制度集；上传并审核完成后才能供采购任务使用。'}</p></div><button className="button button-secondary" type="button" onClick={() => setShowUpload(false)}>取消上传</button></div>
        <div className="policy-form-grid">
          <label className="field"><span>上传方式</span><select value={uploadMode} onChange={(event) => changeUploadMode(event.target.value as PolicyUploadMode)}><option value="NEW">新建制度集</option><option value="UPDATE">更新已有制度</option></select></label>
          {uploadMode === 'UPDATE' && <label className="field policy-field-wide"><span>选择已有制度</span><select required value={basePolicy ? policyKey(basePolicy) : ''} onChange={(event) => selectBasePolicy(updateBasePolicies.find((policy) => policyKey(policy) === event.target.value) ?? null)}><option value="">请选择制度</option>{updateBasePolicies.map((policy) => <option key={policyKey(policy)} value={policyKey(policy)}>{policy.policy_set_id} · 当前版本 {policy.policy_set_version}{policy.status === 'INACTIVE' ? '（已停用）' : ''}</option>)}</select>{updatePolicySets.isPending && <small>正在读取制度…</small>}{updatePolicySets.isError && <small className="field-error">制度读取失败，请稍后重试。</small>}</label>}
          <label className="field policy-field-wide"><span>制度集名称</span><input required readOnly={uploadMode === 'UPDATE'} value={form.policy_set_name} onChange={(event) => update('policy_set_name', event.target.value)} placeholder="例如：电子元器件采购制度" /></label>
          <label className="field"><span>适用采购类别 <small>多项用逗号分隔</small></span><input required value={form.categories} onChange={(event) => update('categories', event.target.value)} /></label>
          <label className="field"><span>适用地区 <small>多项用逗号分隔</small></span><input required value={form.regions} onChange={(event) => update('regions', event.target.value)} /></label>
          <label className="field"><span>生效日期</span><input required type="date" value={form.effective_from} onChange={(event) => update('effective_from', event.target.value)} /></label>
          <label className="field"><span>失效日期 <small>可选</small></span><input type="date" value={form.effective_to} onChange={(event) => update('effective_to', event.target.value)} /></label>
        </div>
        <label className="resource-dropzone policy-dropzone">
          <span className="resource-dropzone-icon" aria-hidden="true">↑</span>
          <strong>{files.length > 0 ? `已选择 ${files.length} 个文件` : '选择本版本的全部制度文件'}</strong>
          <small>{files.length > 0 ? `共 ${formatBytes(files.reduce((sum, item) => sum + item.size, 0))}` : '支持多选 PDF / UTF-8 TXT / Markdown · 单个文件最大 5 MiB'}</small>
          <input ref={fileInput} multiple type="file" accept=".pdf,.txt,.md,application/pdf,text/plain,text/markdown" onChange={(event) => selectFiles(Array.from(event.target.files ?? []))} />
        </label>
        {files.length > 0 && <div className="policy-selected-files"><p>发布新版本时，请上传该版本使用的完整文件集。</p>{files.map((selectedFile) => <div className="policy-selected-file" key={`${selectedFile.name}:${selectedFile.lastModified}`}><span><strong>{selectedFile.name}</strong><small>{formatBytes(selectedFile.size)}</small></span><button type="button" onClick={() => setPreview({ name: selectedFile.name, mediaType: selectedFile.type, sizeBytes: selectedFile.size, file: selectedFile })}>预览</button></div>)}</div>}
        {(localError || upload.isError) && <div className="form-error compact-error" role="alert">{localError || errorMessage(upload.error)}</div>}
        <div className="policy-form-actions">
          {upload.isError && lastSubmission && <button className="button button-secondary" type="button" onClick={() => upload.mutate(lastSubmission)}>重试相同请求</button>}
          <button className="button button-submit" type="submit" disabled={upload.isPending}>{upload.isPending ? '正在上传并解析…' : files.length > 0 ? `上传 ${files.length} 个文件并审核` : '上传文件并审核'}</button>
        </div>
      </form>}

      {preview && <FilePreviewDialog source={preview} onClose={() => setPreview(null)} />}
    </div>
  )
}
