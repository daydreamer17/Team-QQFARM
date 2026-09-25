import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import type { PolicyImportStatus, PolicyImportSummary, PolicyImportUploadMetadata, PolicySetSummary } from '../api/types'
import { EnglishDateInput } from '../components/EnglishDateInput'
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
      policy_metadata_invalid: 'Policy information did not pass validation. Check the title, effective dates, categories, and regions.',
      unsupported_policy_media_type: 'Only PDF, UTF-8 TXT, or Markdown policy documents are supported.',
      file_too_large: 'A policy document cannot exceed 5 MiB.',
      policy_pdf_requires_ocr: 'This PDF contains no extractable text, and policy upload does not currently support OCR.',
      policy_document_no_text: 'The policy document has no usable body text.',
      policy_document_not_policy: 'A README is an explanatory file, not policy content. Upload only the actual policy document.',
      policy_set_not_found: 'This policy revision was not found. Refresh and try again.',
      policy_set_not_published: 'This policy revision has not been published and cannot be deactivated.',
      policy_set_index_unavailable: 'This policy revision has no usable published index.',
    }
    return messages[error.code] ?? error.message
  }
  return 'Policy document upload failed.'
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  return `${(bytes / 1024 / 1024).toFixed(2)} MiB`
}

function formatDate(value: string | null) {
  if (!value) return '—'
  return new Intl.DateTimeFormat('en-SG', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  }).format(new Date(value))
}

function statusLabel(status: PolicyImportStatus) {
  const labels: Record<PolicyImportStatus, string> = {
    REVIEW_REQUIRED: 'Review Required',
    READY_TO_PUBLISH: 'Ready to publish',
    PUBLISHING: 'Publishing',
    PUBLISHED: 'Published',
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
    ? 'View draft'
    : version.status === 'REVIEW_REQUIRED'
    ? 'Continue'
    : version.status === 'PUBLISHING' ? 'View progress' : 'Publish this revision'
  return <article className={`pending-version-card${historical ? ' pending-version-card-history' : ''}`}>
    <header>
      <div><strong>{version.policy_set_id}</strong><span>Revision {version.policy_set_version}</span></div>
      <span className={`status-pill ${statusClass(version.status)}`}>{statusLabel(version.status)}</span>
    </header>
    <p className="pending-version-summary">
      {version.items.length} files
      {reviewCount > 0 && ` · ${reviewCount} require attention`}
      {readyCount > 0 && ` · ${readyCount} identified`}
      {publishingCount > 0 && ` · ${publishingCount} publishing`}
    </p>
    <dl>
      <div><dt>Scope</dt><dd>{version.categories.join(' / ') || '—'} · {version.regions.join(' / ') || '—'}</dd></div>
      <div><dt>Clauses</dt><dd>{version.clause_count}</dd></div>
      <div><dt>Updated At</dt><dd>{formatDate(version.updated_at)}</dd></div>
    </dl>
    <details className="pending-version-files">
      <summary>View files ({version.items.length})</summary>
      <div>{version.items.map((item) => <div className="pending-version-file" key={item.policy_import_id}>
        <span><strong>{item.original_filename}</strong><small>{item.clause_count} clauses · {formatBytes(item.size_bytes)}</small></span>
        <span><span className={`status-pill ${statusClass(item.status)}`}>{statusLabel(item.status)}</span><Link to={`/resources/policies/${item.policy_import_id}`}>View</Link></span>
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
        setLocalError(`“${selectedFile.name}” is an explanatory file, not policy content. Do not upload it.`)
        if (fileInput.current) fileInput.current.value = ''
        return
      }
      if (extension !== 'pdf' && extension !== 'txt' && extension !== 'md') {
        setFiles([])
        setLocalError(`“${selectedFile.name}” is not supported. Upload a PDF, UTF-8 TXT, or Markdown file.`)
        if (fileInput.current) fileInput.current.value = ''
        return
      }
      if (selectedFile.size > MAX_POLICY_BYTES) {
        setFiles([])
        setLocalError(`“${selectedFile.name}” exceeds 5 MiB.`)
        if (fileInput.current) fileInput.current.value = ''
        return
      }
      if (selectedFile.size === 0) {
        setFiles([])
        setLocalError(`“${selectedFile.name}” is empty.`)
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
    if (!window.confirm(`Deactivate policy ${policy.policy_set_id} · Revision ${policy.policy_set_version}?\nNew procurement tasks will no longer be able to bind this revision. Task history is unaffected.`)) return
    deactivate.mutate(policy)
  }

  function buildSubmission(): UploadSubmission | null {
    if (uploadMode === 'UPDATE' && basePolicy === null) {
      setLocalError('Select the existing policy revision to update.')
      return null
    }
    if (files.length === 0) {
      setLocalError('Select at least one PDF, TXT, or Markdown policy document.')
      return null
    }
    const required: (keyof PolicyUploadForm)[] = [
      'policy_set_name', 'effective_from', 'categories', 'regions',
    ]
    if (required.some((field) => !form[field].trim())) {
      setLocalError('Complete all required metadata.')
      return null
    }
    const categories = splitList(form.categories)
    const regions = splitList(form.regions)
    if (categories.length === 0 || regions.length === 0) {
      setLocalError('Enter at least one category and one region.')
      return null
    }
    if (form.effective_to && form.effective_to <= form.effective_from) {
      setLocalError('Effective-to date must be later than the effective-from date.')
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
          <h1>Policy Library</h1>
          <p>View published policies, process files awaiting review and publish new versions as needed.</p>
        </div>
        <button className="button button-submit" type="button" onClick={toggleBlankUpload}>{showUpload ? 'Close' : 'Upload'}</button>
      </section>

      <section className="card policy-directory">
        <div className="section-heading">
          <div><h2>Published policy</h2><p className="section-helper">Each policy set shows only its current version. Older versions remain in the history.</p></div>
          <div className="policy-directory-heading-actions">
            {visiblePolicySetGroups.length > 0 && <span>{visiblePolicySetGroups.length} policy sets</span>}
            {inactivePolicySetCount > 0 && (
              <button
                className="button button-secondary"
                type="button"
                aria-pressed={showInactivePolicies}
                onClick={() => setShowInactivePolicies((current) => !current)}
              >
                {showInactivePolicies ? 'Hide inactive policies' : `Show inactive policies (${inactivePolicySetCount})`}
              </button>
            )}
          </div>
        </div>
        {policySets.isPending && <div className="policy-directory-state">Loading published policies…</div>}
        {policySets.isError && <div className="policy-directory-state policy-directory-error"><span>Unable to load published policies.</span><button className="button button-secondary" type="button" onClick={() => void policySets.refetch()}>Retry</button></div>}
        {policySets.data && visiblePolicySetGroups.length === 0 && (
          <div className="policy-directory-state">
            {inactivePolicySetCount > 0
              ? `No policy is available; ${inactivePolicySetCount} inactive policies are hidden.`
              : 'No published policies.'}
          </div>
        )}
        {visiblePolicySetGroups.length > 0 && (
          <div className="published-policy-grid">{visiblePolicySetGroups.map(({ current: item, history }) => (
            <article key={`${item.policy_set_id}:${item.policy_set_version}:${item.policy_index_version}`} className="published-policy-card">
              <header><div><strong>{item.policy_set_id}</strong><span>Revision {item.policy_set_version}</span></div><span className={`status-pill ${item.status === 'PUBLISHED' ? 'status-ready' : 'status-muted'}`}>{item.status === 'PUBLISHED' ? 'Available' : 'Inactive'}</span></header>
              <dl><div><dt>Documents / clauses</dt><dd>{item.document_count} / {item.clause_count}</dd></div><div><dt>Procurement category</dt><dd>{item.categories.join(' / ') || '—'}</dd></div><div><dt>Regions</dt><dd>{item.regions.join(' / ') || '—'}</dd></div><div><dt>Published at</dt><dd>{formatDate(item.published_at)}</dd></div></dl>
              {history.length > 0 && <details className="published-policy-history">
                <summary>View revision history ({history.length})</summary>
                <div className="published-policy-history-list">{history.map((version) => (
                  <div className="published-policy-history-row" key={`${version.policy_set_version}:${version.policy_index_version}`}>
                    <span><strong>Revision {version.policy_set_version}</strong><small>{formatDate(version.published_at)}</small></span>
                    <span className="status-pill status-muted">{version.status === 'PUBLISHED' ? 'Superseded' : 'Inactive'}</span>
                  </div>
                ))}</div>
              </details>}
              <div className="published-policy-actions">
                <button className="button button-secondary" type="button" onClick={() => beginNewVersion(item)}>Update</button>
                {item.status === 'PUBLISHED' && <button className="button button-danger" type="button" disabled={deactivate.isPending} onClick={() => deactivateVersion(item)}>Deactivate</button>}
              </div>
            </article>
          ))}</div>
        )}
        {deactivate.isError && <div className="form-error compact-error" role="alert">{errorMessage(deactivate.error)}</div>}
      </section>

      <section className="card policy-directory pending-policy-directory">
        <div className="section-heading">
          <div><h2>Versions awaiting review</h2><p className="section-helper">Only the most recently updated version is highlighted; other versions are collapsed by default.</p></div>
          {imports.data && currentPendingVersions.length > 0 && <span>Latest revision</span>}
        </div>
        {imports.isPending && <div className="policy-directory-state">Loading versions awaiting review…</div>}
        {imports.isError && <div className="policy-directory-state policy-directory-error"><span>Unable to load versions awaiting review.</span><button className="button button-secondary" type="button" onClick={() => void imports.refetch()}>Retry</button></div>}
        {imports.data && imports.data.length === 0 && <div className="policy-directory-state">No versions are awaiting review.</div>}
        {currentPendingVersions.length > 0 && <div className="pending-version-grid">
          {currentPendingVersions.map((version) => <PendingVersionCard key={version.key} version={version} />)}
        </div>}
        {otherPendingVersions.length > 0 && <details className="pending-draft-history">
          <summary>Other pending revisions ({otherPendingVersions.length})</summary>
          <p>These versions can still be processed, but are not expanded alongside the latest version.</p>
          <div className="pending-version-grid pending-version-history-grid">
            {otherPendingVersions.map((version) => <PendingVersionCard key={version.key} version={version} historical />)}
          </div>
        </details>}
      </section>

      {showUpload && <form className="card policy-upload-form" onSubmit={submit}>
        <div className="section-heading"><div><h2>Upload policy revision</h2><p className="section-helper">{basePolicy ? `Updating revision ${basePolicy.policy_set_version} of ${basePolicy.policy_set_id}; upload the complete file set for the new revision.` : uploadMode === 'UPDATE' ? 'Select an existing policy to update, then upload the complete file set for the new revision.' : 'Create a new policy set. It becomes available to procurement tasks only after upload and review are complete.'}</p></div><button className="button button-secondary" type="button" onClick={() => setShowUpload(false)}>Cancel</button></div>
        <div className="policy-form-grid">
          <label className="field"><span>Upload method</span><select value={uploadMode} onChange={(event) => changeUploadMode(event.target.value as PolicyUploadMode)}><option value="NEW">Create policy set</option><option value="UPDATE">Update existing policy</option></select></label>
          {uploadMode === 'UPDATE' && <label className="field policy-field-wide"><span>Select an existing policy</span><select required value={basePolicy ? policyKey(basePolicy) : ''} onChange={(event) => selectBasePolicy(updateBasePolicies.find((policy) => policyKey(policy) === event.target.value) ?? null)}><option value="">Select a policy</option>{updateBasePolicies.map((policy) => <option key={policyKey(policy)} value={policyKey(policy)}>{policy.policy_set_id} · Current revision {policy.policy_set_version}{policy.status === 'INACTIVE' ? ' (inactive)' : ''}</option>)}</select>{updatePolicySets.isPending && <small>Loading policies…</small>}{updatePolicySets.isError && <small className="field-error">Unable to load policies. Please try again later.</small>}</label>}
          <label className="field policy-field-wide"><span>Policy set name</span><input required readOnly={uploadMode === 'UPDATE'} value={form.policy_set_name} onChange={(event) => update('policy_set_name', event.target.value)} placeholder="For example: Electronic Components Procurement Policy" /></label>
          <label className="field"><span>Applicable procurement categories <small>Separate multiple values with commas</small></span><input required value={form.categories} onChange={(event) => update('categories', event.target.value)} /></label>
          <label className="field"><span>Applicable region <small>Separate multiple values with commas</small></span><input required value={form.regions} onChange={(event) => update('regions', event.target.value)} /></label>
          <label className="field"><span>Effective from</span><EnglishDateInput required value={form.effective_from} onChange={(value) => update('effective_from', value)} /></label>
          <label className="field"><span>Effective to <small>Optional</small></span><EnglishDateInput value={form.effective_to} onChange={(value) => update('effective_to', value)} /></label>
        </div>
        <label className="resource-dropzone policy-dropzone">
          <span className="resource-dropzone-icon" aria-hidden="true">↑</span>
          <strong>{files.length > 0 ? `${files.length} files selected` : 'Select all policy documents for this revision'}</strong>
          <small>{files.length > 0 ? `${formatBytes(files.reduce((sum, item) => sum + item.size, 0))} total` : 'Multiple PDF, UTF-8 TXT and Markdown files supported · Maximum 5 MiB per file'}</small>
          <input ref={fileInput} multiple type="file" accept=".pdf,.txt,.md,application/pdf,text/plain,text/markdown" onChange={(event) => selectFiles(Array.from(event.target.files ?? []))} />
        </label>
        {files.length > 0 && <div className="policy-selected-files"><p>Upload the complete file set used by the new policy version.</p>{files.map((selectedFile) => <div className="policy-selected-file" key={`${selectedFile.name}:${selectedFile.lastModified}`}><span><strong>{selectedFile.name}</strong><small>{formatBytes(selectedFile.size)}</small></span><button type="button" onClick={() => setPreview({ name: selectedFile.name, mediaType: selectedFile.type, sizeBytes: selectedFile.size, file: selectedFile })}>Preview</button></div>)}</div>}
        {(localError || upload.isError) && <div className="form-error compact-error" role="alert">{localError || errorMessage(upload.error)}</div>}
        <div className="policy-form-actions">
          {upload.isError && lastSubmission && <button className="button button-secondary" type="button" onClick={() => upload.mutate(lastSubmission)}>Retry</button>}
          <button className="button button-submit" type="submit" disabled={upload.isPending}>{upload.isPending ? 'Uploading…' : 'Upload'}</button>
        </div>
      </form>}

      {preview && <FilePreviewDialog source={preview} onClose={() => setPreview(null)} />}
    </div>
  )
}
