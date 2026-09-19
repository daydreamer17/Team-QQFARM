import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import type {
  PolicyClauseInput,
  PolicyDraftClause,
  PolicyImportResponse,
} from '../api/types'

const CONTROL_CODES = [
  'APPROVED_SUPPLIER',
  'ROHS_COMPLIANCE',
  'AMOUNT_APPROVAL',
]

interface EditableClause {
  clause_id: string
  title: string
  text: string
  control_code: string
  rule_parameters: string
}

interface ReviewSubmission {
  revision: number
  clauses: PolicyClauseInput[]
  idempotencyKey: string
}

interface PublishSubmission {
  revision: number
  idempotencyKey: string
}

function toEditable(clause: PolicyDraftClause): EditableClause {
  return {
    clause_id: clause.clause_id,
    title: clause.title,
    text: clause.text,
    control_code: clause.control_code ?? '',
    rule_parameters: JSON.stringify(clause.rule_parameters, null, 2),
  }
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  return `${(bytes / 1024 / 1024).toFixed(2)} MiB`
}

function displayDate(value: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium' }).format(date)
}

function errorMessage(error: unknown) {
  if (!(error instanceof ApiClientError)) return '策略操作失败。'
  const messages: Record<string, string> = {
    policy_import_revision_conflict: '服务端策略版本已经变化。你的编辑仍保留，请核对服务端新版本后再提交。',
    duplicate_policy_clause_id: '条款 ID 不能重复。',
    policy_clauses_empty: '至少保留一个可发布条款。',
    published_policy_immutable: '该策略已经发布，内容不可再修改。',
    policy_publish_in_progress: '策略正在发布，请稍后刷新状态。',
    policy_import_not_ready: '请先保存条款审核，再发布策略。',
    policy_version_content_mismatch: '相同策略版本已经发布了不同内容，请使用新的版本号重新上传。',
    policy_publish_failed: 'Embedding 或索引发布失败，条款仍保留，可直接重试发布。',
  }
  return messages[error.code] ?? error.message
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    REVIEW_REQUIRED: '等待条款审核',
    READY_TO_PUBLISH: '审核完成，等待发布',
    PUBLISHING: '正在生成索引',
    PUBLISHED: '已发布',
  }
  return labels[status] ?? status
}

function statusClass(status: string) {
  if (status === 'PUBLISHED') return 'status-ready'
  if (status === 'READY_TO_PUBLISH' || status === 'PUBLISHING') return 'status-pending'
  return 'status-muted'
}

export function PolicyImportPage() {
  const { policyImportId = '' } = useParams()
  const queryClient = useQueryClient()
  const syncedRevision = useRef<number | null>(null)
  const [clauses, setClauses] = useState<EditableClause[]>([])
  const [dirty, setDirty] = useState(false)
  const [localError, setLocalError] = useState('')
  const [revisionConflict, setRevisionConflict] = useState(false)
  const [lastReview, setLastReview] = useState<ReviewSubmission | null>(null)
  const [lastPublish, setLastPublish] = useState<PublishSubmission | null>(null)

  const policy = useQuery({
    queryKey: ['policy-imports', policyImportId],
    queryFn: () => api.getPolicyImport(policyImportId),
    enabled: Boolean(policyImportId),
    refetchInterval: (query) => query.state.data?.status === 'PUBLISHING' ? 2_000 : false,
  })

  useEffect(() => {
    if (!policy.data || dirty || syncedRevision.current === policy.data.revision) return
    setClauses(policy.data.clauses.map(toEditable))
    syncedRevision.current = policy.data.revision
  }, [dirty, policy.data])

  function acceptServerVersion(data: PolicyImportResponse) {
    setClauses(data.clauses.map(toEditable))
    syncedRevision.current = data.revision
    setDirty(false)
    setRevisionConflict(false)
    setLocalError('')
    setLastReview(null)
  }

  const review = useMutation({
    mutationFn: (submission: ReviewSubmission) =>
      api.reviewPolicyClauses(
        policyImportId,
        submission.revision,
        submission.clauses,
        submission.idempotencyKey,
      ),
    onSuccess: (result) => {
      queryClient.setQueryData(['policy-imports', policyImportId], result)
      void queryClient.invalidateQueries({ queryKey: ['policy-imports', 'list'] })
      acceptServerVersion(result)
      setLastReview(null)
    },
    onError: async (error) => {
      if (error instanceof ApiClientError && error.code === 'policy_import_revision_conflict') {
        setDirty(true)
        setRevisionConflict(true)
        await policy.refetch()
      }
    },
  })

  const publish = useMutation({
    mutationFn: (submission: PublishSubmission) =>
      api.publishPolicy(policyImportId, submission.revision, submission.idempotencyKey),
    onSuccess: (result) => {
      queryClient.setQueryData(['policy-imports', policyImportId], result)
      void queryClient.invalidateQueries({ queryKey: ['policy-imports', 'list'] })
      void queryClient.invalidateQueries({ queryKey: ['policy-sets', 'list'] })
      acceptServerVersion(result)
      setLastPublish(null)
    },
    onError: async (error) => {
      if (error instanceof ApiClientError && error.code === 'policy_import_revision_conflict') {
        setDirty(true)
        setRevisionConflict(true)
        await policy.refetch()
      }
    },
  })

  function changeClause(index: number, field: keyof EditableClause, value: string) {
    setClauses((current) => current.map((clause, clauseIndex) =>
      clauseIndex === index ? { ...clause, [field]: value } : clause,
    ))
    setDirty(true)
    setLocalError('')
    setLastReview(null)
    review.reset()
  }

  function addClause() {
    setClauses((current) => [
      ...current,
      {
        clause_id: `CLAUSE-${String(current.length + 1).padStart(3, '0')}`,
        title: '',
        text: '',
        control_code: '',
        rule_parameters: '{}',
      },
    ])
    setDirty(true)
    setLocalError('')
    setLastReview(null)
    review.reset()
  }

  function removeClause(index: number) {
    setClauses((current) => current.filter((_, clauseIndex) => clauseIndex !== index))
    setDirty(true)
    setLocalError('')
    setLastReview(null)
    review.reset()
  }

  function moveClause(index: number, direction: -1 | 1) {
    const target = index + direction
    if (target < 0 || target >= clauses.length) return
    setClauses((current) => {
      const next = [...current]
      ;[next[index], next[target]] = [next[target], next[index]]
      return next
    })
    setDirty(true)
    setLocalError('')
    setLastReview(null)
    review.reset()
  }

  function parseClauses(): PolicyClauseInput[] | null {
    if (clauses.length === 0) {
      setLocalError('至少保留一个条款。')
      return null
    }
    const ids = clauses.map((clause) => clause.clause_id.trim())
    if (ids.some((id) => !id) || new Set(ids).size !== ids.length) {
      setLocalError('每个条款都需要唯一且非空的条款 ID。')
      return null
    }
    const parsed: PolicyClauseInput[] = []
    for (const [index, clause] of clauses.entries()) {
      if (!clause.title.trim() || !clause.text.trim() || !clause.control_code.trim()) {
        setLocalError(`第 ${index + 1} 个条款缺少标题、正文或控制代码。`)
        return null
      }
      let parameters: unknown
      try {
        parameters = JSON.parse(clause.rule_parameters)
      } catch {
        setLocalError(`第 ${index + 1} 个条款的 rule_parameters 不是合法 JSON。`)
        return null
      }
      if (!parameters || typeof parameters !== 'object' || Array.isArray(parameters)) {
        setLocalError(`第 ${index + 1} 个条款的 rule_parameters 必须是 JSON 对象。`)
        return null
      }
      parsed.push({
        clause_id: clause.clause_id.trim(),
        title: clause.title.trim(),
        text: clause.text.trim(),
        control_code: clause.control_code.trim().toUpperCase(),
        rule_parameters: parameters as Record<string, unknown>,
      })
    }
    return parsed
  }

  function saveReview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setLocalError('')
    if (!policy.data) return
    if (revisionConflict) {
      setLocalError('请先加载并核对服务端新版本，再重新提交。')
      return
    }
    const parsed = parseClauses()
    if (!parsed) return
    const submission = {
      revision: policy.data.revision,
      clauses: parsed,
      idempotencyKey: createIdempotencyKey(),
    }
    setLastReview(submission)
    review.mutate(submission)
  }

  function startPublish() {
    if (!policy.data) return
    const submission = {
      revision: policy.data.revision,
      idempotencyKey: createIdempotencyKey(),
    }
    setLastPublish(submission)
    publish.mutate(submission)
  }

  if (policy.isPending) return <section className="card loading-panel">正在读取策略审核记录…</section>
  if (policy.isError) {
    return (
      <section className="card error-panel" role="alert">
        <p className="eyebrow">POLICY IMPORT ERROR</p>
        <h1>无法读取策略</h1>
        <p>{errorMessage(policy.error)}</p>
        <Link className="button button-secondary" to="/resources">返回规则资源库</Link>
      </section>
    )
  }

  const data = policy.data
  const readonly = data.status === 'PUBLISHED' || data.status === 'PUBLISHING'

  return (
    <div className="page-stack policy-review-page">
      <section className="page-heading policy-review-heading">
        <div>
          <Link className="back-link" to="/resources">← 返回规则资源库</Link>
          <p className="eyebrow">POLICY REVIEW &amp; PUBLISH</p>
          <h1>{data.title}</h1>
          <p>{data.policy_id} · 文档 {data.document_id} / {data.document_version}</p>
        </div>
        <div className="workspace-state-stack">
          <span className={`status-pill ${statusClass(data.status)}`}>{statusLabel(data.status)}</span>
          <span>Policy Rev {data.revision}</span>
        </div>
      </section>

      <section className="policy-metadata-grid" aria-label="策略元数据">
        <article><span>策略集版本</span><strong>{data.policy_set_id}</strong><small>{data.policy_set_version}</small></article>
        <article><span>适用范围</span><strong>{data.categories.join('、')}</strong><small>{data.regions.join('、')}</small></article>
        <article><span>有效期</span><strong>{displayDate(data.effective_from)}</strong><small>至 {displayDate(data.effective_to)}</small></article>
        <article><span>来源文件</span><strong>{data.original_filename}</strong><small>{formatBytes(data.size_bytes)} · {data.media_type}</small></article>
      </section>

      {data.status === 'PUBLISHED' && (
        <section className="card policy-published-summary">
          <div><p className="eyebrow">PUBLISHED INDEX</p><h2>制度索引已发布</h2></div>
          <dl>
            <div><dt>Policy Index Version</dt><dd>{data.policy_index_version ?? '—'}</dd></div>
            <div><dt>Import Run ID</dt><dd>{data.published_import_run_id ?? '—'}</dd></div>
          </dl>
          <p>发布内容不可修改；后续修改制度时应以新版本重新上传和发布。</p>
        </section>
      )}

      <details className="card policy-source-details">
        <summary>查看来源与解析正文</summary>
        <dl>
          <div><dt>SHA-256</dt><dd>{data.source_sha256}</dd></div>
          <div><dt>解析器</dt><dd>{String(data.extraction_metadata.parser ?? '—')}</dd></div>
          <div><dt>页数</dt><dd>{data.extraction_metadata.page_count ?? '—'}</dd></div>
        </dl>
        <pre>{data.extracted_text}</pre>
      </details>

      {revisionConflict && (
        <div className="policy-conflict-notice" role="alert">
          <div><strong>服务端已有更新</strong><span>你的本地编辑没有被覆盖。请先加载服务端 Rev {policy.data.revision}，再重新修改。</span></div>
          <button className="button button-secondary" type="button" disabled={policy.isFetching} onClick={() => acceptServerVersion(policy.data)}>
            {policy.isFetching ? '正在读取服务端版本…' : '加载服务端版本'}
          </button>
        </div>
      )}

      <form className="policy-clause-editor" onSubmit={saveReview}>
        <div className="section-heading">
          <div><p className="eyebrow">REVIEWED CLAUSES</p><h2>条款审核</h2></div>
          {!readonly && <button className="button button-secondary" type="button" onClick={addClause}>＋ 添加条款</button>}
        </div>
        <p className="section-helper">核对条款正文、控制代码和规则参数。排序即发布顺序。</p>

        <datalist id="policy-control-codes">
          {CONTROL_CODES.map((code) => <option value={code} key={code} />)}
        </datalist>

        <div className="policy-clause-list">
          {clauses.map((clause, index) => (
            <article className="card policy-clause-card" key={`${clause.clause_id}-${index}`}>
              <header>
                <div><span>条款 {index + 1}</span><strong>{clause.clause_id || '未命名条款'}</strong></div>
                {!readonly && (
                  <div className="policy-clause-actions">
                    <button type="button" disabled={index === 0} onClick={() => moveClause(index, -1)} aria-label="上移条款">↑</button>
                    <button type="button" disabled={index === clauses.length - 1} onClick={() => moveClause(index, 1)} aria-label="下移条款">↓</button>
                    <button type="button" onClick={() => removeClause(index)}>删除</button>
                  </div>
                )}
              </header>
              <div className="policy-clause-grid">
                <label className="field"><span>Clause ID</span><input required readOnly={readonly} value={clause.clause_id} onChange={(event) => changeClause(index, 'clause_id', event.target.value)} /></label>
                <label className="field"><span>控制代码</span><input required readOnly={readonly} list="policy-control-codes" value={clause.control_code} onChange={(event) => changeClause(index, 'control_code', event.target.value)} placeholder="选择或输入自定义代码" /></label>
                <label className="field policy-field-wide"><span>标题</span><input required readOnly={readonly} value={clause.title} onChange={(event) => changeClause(index, 'title', event.target.value)} /></label>
                <label className="field policy-field-wide"><span>条款正文</span><textarea required readOnly={readonly} rows={5} value={clause.text} onChange={(event) => changeClause(index, 'text', event.target.value)} /></label>
                <label className="field policy-field-wide"><span>rule_parameters <small>JSON 对象</small></span><textarea required readOnly={readonly} rows={4} spellCheck={false} value={clause.rule_parameters} onChange={(event) => changeClause(index, 'rule_parameters', event.target.value)} /></label>
              </div>
            </article>
          ))}
        </div>

        {(localError || review.isError || publish.isError) && (
          <div className="form-error compact-error" role="alert">
            {localError || errorMessage(review.error ?? publish.error)}
          </div>
        )}

        {!readonly && (
          <div className="policy-review-actions">
            {review.isError && lastReview && !revisionConflict && (
              <button className="button button-secondary" type="button" onClick={() => review.mutate(lastReview)}>重试相同保存请求</button>
            )}
            <button className="button button-submit" type="submit" disabled={revisionConflict || review.isPending || publish.isPending}>
              {review.isPending ? '正在保存…' : dirty || data.status === 'REVIEW_REQUIRED' ? '保存条款审核' : '重新保存审核'}
            </button>
            <button className="button button-submit" type="button" disabled={data.status !== 'READY_TO_PUBLISH' || dirty || review.isPending || publish.isPending} onClick={startPublish}>
              {publish.isPending ? '正在生成 Embedding 与索引…' : '发布策略索引'}
            </button>
            {publish.isError && lastPublish && !revisionConflict && (
              <button className="button button-secondary" type="button" onClick={() => publish.mutate(lastPublish)}>重试相同发布请求</button>
            )}
          </div>
        )}
      </form>
    </div>
  )
}
