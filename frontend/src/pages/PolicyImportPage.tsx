import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import { ExecutableRuleEditor } from '../components/ExecutableRuleEditor'
import { validateExecutableRule } from '../lib/executableRule'
import type {
  PolicyClauseInput,
  PolicyDraftClause,
  PolicyImportResponse,
} from '../api/types'

const CONTROL_CODE_OPTIONS = [
  { value: 'APPROVED_SUPPLIER', label: '供应商准入' },
  { value: 'ROHS_COMPLIANCE', label: 'RoHS 与环保合规' },
  { value: 'AMOUNT_APPROVAL', label: '采购金额审批' },
  { value: 'INFORMATIONAL', label: '流程说明与审计记录' },
]

interface EditableClause {
  editor_key: string
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

function toEditable(clause: PolicyDraftClause, index: number): EditableClause {
  return {
    editor_key: `source-${index}`,
    clause_id: clause.clause_id,
    title: clause.title,
    text: clause.text,
    control_code: clause.control_code ?? '',
    rule_parameters: JSON.stringify(clause.rule_parameters, null, 2),
  }
}

function clauseStatus(clause: PolicyDraftClause | EditableClause) {
  if ('classification' in clause && clause.classification?.status) {
    return clause.classification.status
  }
  return clause.control_code ? 'AUTO_ACCEPTED' : 'ADMIN_REVIEW'
}

const CLASSIFICATION_REASONS: Record<string, string> = {
  UNRECOGNIZED_CONTROL: '系统无法判断该条款属于哪类采购检查。',
  AMBIGUOUS_CONTROL: '该条款同时匹配多种检查用途，无法可靠自动归类。',
  MISSING_REQUIRED_PARAMETER: '条款缺少执行所需的金额、币种或门槛参数。',
  CONFLICTING_RULE: '该条款与本版本中的另一条结构化规则结论不一致。',
  DUPLICATE_CLAUSE_ID: '本版本存在重复的条款编号。',
  UNSUPPORTED_CAPABILITY: '当前系统没有对应的检查器或数据来源，不能自动执行该条款。',
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
    policy_set_review_incomplete: '同一制度集仍有文件未完成审核，请逐份审核后再统一发布。',
    policy_version_content_mismatch: '相同策略版本已经发布了不同内容，请使用新的版本号重新上传。',
    policy_publish_failed: 'Embedding 或索引发布失败，条款仍保留，可直接重试发布。',
  }
  return messages[error.code] ?? error.message
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    REVIEW_REQUIRED: '等待条款审核',
    READY_TO_PUBLISH: '自动识别完成，等待发布',
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
  return <PolicyImportEditor key={policyImportId} policyImportId={policyImportId} />
}

function PolicyImportEditor({ policyImportId }: { policyImportId: string }) {
  const queryClient = useQueryClient()
  const syncedRevision = useRef<number | null>(null)
  const [clauses, setClauses] = useState<EditableClause[]>([])
  const [dirty, setDirty] = useState(false)
  const [localError, setLocalError] = useState('')
  const [revisionConflict, setRevisionConflict] = useState(false)
  const [lastReview, setLastReview] = useState<ReviewSubmission | null>(null)
  const [lastPublish, setLastPublish] = useState<PublishSubmission | null>(null)
  const [showAdvanced, setShowAdvanced] = useState(false)

  const policy = useQuery({
    queryKey: ['policy-imports', policyImportId],
    queryFn: () => api.getPolicyImport(policyImportId),
    enabled: Boolean(policyImportId),
    refetchInterval: (query) => query.state.data?.status === 'PUBLISHING' ? 2_000 : false,
  })

  const policySetFiles = useQuery({
    queryKey: ['policy-imports', 'set', policy.data?.policy_set_id, policy.data?.policy_set_version],
    queryFn: () => api.listPolicyImports({
      policy_set_id: policy.data!.policy_set_id,
      policy_set_version: policy.data!.policy_set_version,
      limit: 100,
    }),
    enabled: Boolean(policy.data),
    refetchInterval: (query) => query.state.data?.items.some((item) => item.status === 'PUBLISHING') ? 2_000 : false,
  })

  useEffect(() => {
    if (!policy.data || policy.data.policy_import_id !== policyImportId || dirty || syncedRevision.current === policy.data.revision) return
    setClauses(policy.data.clauses.map(toEditable))
    syncedRevision.current = policy.data.revision
  }, [dirty, policy.data, policyImportId])

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
      void queryClient.invalidateQueries({ queryKey: ['policy-imports', 'set'] })
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
      void queryClient.invalidateQueries({ queryKey: ['policy-imports', 'set'] })
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
        editor_key: `draft-${Date.now()}-${current.length}`,
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
      const ruleError = validateExecutableRule(parameters as Record<string, unknown>, clause.control_code)
      if (ruleError) { setLocalError(`第 ${index + 1} 个条款：${ruleError}`); return null }
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
        <Link className="button button-secondary" to="/resources">返回制度资源库</Link>
      </section>
    )
  }

  const data = policy.data
  const readonly = data.status === 'PUBLISHED' || data.status === 'PUBLISHING'
  const setFiles = policySetFiles.data?.items ?? []
  const allFilesReviewed = setFiles.length > 0 && setFiles.every((item) =>
    item.status === 'READY_TO_PUBLISH' || item.status === 'PUBLISHING' || item.status === 'PUBLISHED'
  )
  const sourceClauses = data.clauses
  const unresolvedIndexes = new Set(
    sourceClauses
      .map((clause, index) => clauseStatus(clause) === 'AUTO_ACCEPTED' ? -1 : index)
      .filter((index) => index >= 0),
  )
  const unresolvedCount = unresolvedIndexes.size
  const recognizedCount = clauses.length - unresolvedCount

  return (
    <div className="page-stack policy-review-page">
      <section className="page-heading policy-review-heading">
        <div>
          <Link className="back-link" to="/resources">← 返回制度资源库</Link>
          <h1>{data.policy_set_id}</h1>
          <p>正在审核：{data.original_filename}</p>
        </div>
        <div className="workspace-state-stack">
          <span className={`status-pill ${statusClass(data.status)}`}>{statusLabel(data.status)}</span>
        </div>
      </section>

      <section className="policy-metadata-grid" aria-label="策略元数据">
        <article><span>制度版本</span><strong>{data.policy_set_version}</strong><small>{setFiles.length || 1} 个文件</small></article>
        <article><span>适用范围</span><strong>{data.categories.join('、')}</strong><small>地区：{data.regions.join('、')}</small></article>
        <article><span>有效期</span><strong>{displayDate(data.effective_from)}</strong><small>至 {displayDate(data.effective_to)}</small></article>
        <article><span>来源文件</span><strong>{data.original_filename}</strong><small>{formatBytes(data.size_bytes)} · {data.media_type}</small></article>
      </section>

      {setFiles.length > 0 && (
        <section className="card policy-set-files">
          <div className="section-heading">
            <div><h2>本版本文件</h2><p>系统自动整理条款；只有无法识别的内容需要人工确认。</p></div>
            <span>{setFiles.filter((item) => item.status === 'READY_TO_PUBLISH' || item.status === 'PUBLISHED').length} / {setFiles.length} 已就绪</span>
          </div>
          <div className="policy-set-file-list">
            {setFiles.map((item) => (
              <Link key={item.policy_import_id} className={item.policy_import_id === policyImportId ? 'is-current' : ''} to={`/resources/policies/${item.policy_import_id}`}>
                <span><strong>{item.original_filename}</strong><small>{item.clause_count} 条条款</small></span>
                <span className={`status-pill ${statusClass(item.status)}`}>{statusLabel(item.status)}</span>
              </Link>
            ))}
          </div>
        </section>
      )}

      {data.status === 'PUBLISHED' && (
        <section className="card policy-published-summary">
          <div><h2>制度已发布，可绑定采购任务</h2></div>
          <p>该版本内容已冻结。后续修改请上传完整文件集并发布为新版本。</p>
        </section>
      )}

      <details className="card policy-source-details">
        <summary>查看文件解析结果</summary>
        {data.extraction_metadata.page_count && <p>共 {data.extraction_metadata.page_count} 页</p>}
        <pre>{data.extracted_text}</pre>
      </details>

      {!readonly && (
        <section className="card policy-published-summary">
          <div><h2>{unresolvedCount === 0 ? '当前文件已自动整理' : `还有 ${unresolvedCount} 条需要确认`}</h2></div>
          <p>
            已识别 {recognizedCount} / {clauses.length} 条条款用途。
            {unresolvedCount === 0
              ? ' 用于自动检查前，请进入高级审核，配置并人工确认执行规则。'
              : ' 请只处理下方未识别条款，其他内容无需重复确认。'}
          </p>
        </section>
      )}

      {revisionConflict && (
        <div className="policy-conflict-notice" role="alert">
          <div><strong>服务端已有更新</strong><span>你的本地编辑没有被覆盖。请先加载服务端 Rev {policy.data.revision}，再重新修改。</span></div>
          <button className="button button-secondary" type="button" disabled={policy.isFetching} onClick={() => acceptServerVersion(policy.data)}>
            {policy.isFetching ? '正在读取服务端版本…' : '加载服务端版本'}
          </button>
        </div>
      )}

      <form className="policy-clause-editor" onSubmit={saveReview}>
        <fieldset className="policy-clause-editor" disabled={review.isPending || publish.isPending} style={{ border: 0, margin: 0, padding: 0, minWidth: 0 }}>
        {data.status === 'PUBLISHING' && (
          <div className="policy-conflict-notice" role="status">
            <p>制度正在发布。如果此前发布进程已中断，可重试恢复；仍在运行的发布会由服务端阻止重复执行。</p>
            <button className="button button-secondary" type="button" onClick={() => {
              if (lastPublish) publish.mutate(lastPublish)
              else startPublish()
            }}>重试恢复发布</button>
          </div>
        )}
        <div className="section-heading">
          <div><h2>{showAdvanced ? '全部条款与高级设置' : '需要确认的条款'}</h2></div>
          {!readonly && (
            <div className="policy-clause-actions">
              <button className="button button-secondary" type="button" onClick={() => setShowAdvanced((current) => !current)}>
                {showAdvanced ? '退出高级审核' : `进入高级审核（${clauses.length} 条）`}
              </button>
              {showAdvanced && <button className="button button-secondary" type="button" onClick={addClause}>＋ 添加条款</button>}
            </div>
          )}
        </div>
        <p className="section-helper">分类结果不等于可执行规则。进入高级审核可逐项配置材料匹配、有效期处理和金额条件；旧参数不会自动执行。</p>

        <div className="policy-clause-list">
          {clauses.map((clause, index) => (!showAdvanced && !unresolvedIndexes.has(index) ? null : (
            <article className="card policy-clause-card" key={clause.editor_key}>
              <header>
                <div><span>条款 {index + 1}</span><strong>{clause.title || '未命名条款'}</strong></div>
                {!readonly && showAdvanced && (
                  <div className="policy-clause-actions">
                    <button type="button" disabled={index === 0} onClick={() => moveClause(index, -1)} aria-label="上移条款">↑</button>
                    <button type="button" disabled={index === clauses.length - 1} onClick={() => moveClause(index, 1)} aria-label="下移条款">↓</button>
                    <button type="button" onClick={() => removeClause(index)}>删除</button>
                  </div>
                )}
              </header>
              <div className="policy-clause-grid">
                <label className="field policy-field-wide"><span>标题</span><input required readOnly={readonly || !showAdvanced} value={clause.title} onChange={(event) => changeClause(index, 'title', event.target.value)} /></label>
                <label className="field policy-field-wide"><span>条款正文</span><textarea required readOnly={readonly || !showAdvanced} rows={5} value={clause.text} onChange={(event) => changeClause(index, 'text', event.target.value)} /></label>
                {!showAdvanced && unresolvedIndexes.has(index) && <div className="policy-clause-needs-admin policy-field-wide">
                  <strong>{clauseStatus(sourceClauses[index]) === 'UNSUPPORTED' ? '当前不支持自动执行' : '需要管理员处理'}</strong>
                  {(sourceClauses[index].classification?.reason_codes ?? ['UNRECOGNIZED_CONTROL']).map((reason) => <span key={reason}>{CLASSIFICATION_REASONS[reason] ?? reason}</span>)}
                  {(sourceClauses[index].classification?.conflicts_with?.length ?? 0) > 0 && <span>冲突对象：{sourceClauses[index].classification?.conflicts_with.join('、')}</span>}
                  <span>普通用户无需填写技术代码；请由制度管理员进入高级审核处理。</span>
                </div>}
                {showAdvanced && <details className="policy-clause-rule-settings policy-field-wide" open={!clause.control_code}>
                  <summary>检查规则设置{!clause.control_code ? '（发布前必填）' : ''}</summary>
                  <div className="policy-clause-grid">
                    <label className="field"><span>条款编号</span><input required readOnly={readonly} value={clause.clause_id} onChange={(event) => changeClause(index, 'clause_id', event.target.value)} /></label>
                    <label className="field"><span>检查类型</span><select required disabled={readonly} value={clause.control_code} onChange={(event) => changeClause(index, 'control_code', event.target.value)}><option value="">请选择条款用途</option>{CONTROL_CODE_OPTIONS.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}</select></label>
                    <div className="policy-field-wide"><ExecutableRuleEditor controlCode={clause.control_code} value={clause.rule_parameters} onChange={(value) => changeClause(index, 'rule_parameters', value)} readonly={readonly} /></div>
                    <details className="policy-field-wide"><summary>查看原始规则参数</summary><label className="field"><span>高级规则参数 <small>JSON</small></span><textarea required readOnly={readonly} rows={4} spellCheck={false} value={clause.rule_parameters} onChange={(event) => changeClause(index, 'rule_parameters', event.target.value)} /></label></details>
                  </div>
                </details>}
              </div>
            </article>
          )))}
          {!showAdvanced && unresolvedCount === 0 && <p className="policy-directory-state">当前文件没有需要人工处理的条款。</p>}
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
            {showAdvanced && (unresolvedCount > 0 || dirty || data.status === 'REVIEW_REQUIRED') && <button className="button button-submit" type="submit" disabled={revisionConflict || review.isPending || publish.isPending}>
              {review.isPending ? '正在保存…' : '保存确认结果'}
            </button>}
            <button className="button button-submit" type="button" disabled={data.status !== 'READY_TO_PUBLISH' || !allFilesReviewed || dirty || review.isPending || publish.isPending} onClick={startPublish}>
              {publish.isPending ? '正在发布制度…' : allFilesReviewed ? '确认并发布本版本' : '请先处理待确认条款'}
            </button>
            {publish.isError && lastPublish && !revisionConflict && (
              <button className="button button-secondary" type="button" onClick={() => publish.mutate(lastPublish)}>重试相同发布请求</button>
            )}
          </div>
        )}
        </fieldset>
      </form>
    </div>
  )
}
