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
  { value: 'APPROVED_SUPPLIER', label: 'Supplier Eligibility' },
  { value: 'ROHS_COMPLIANCE', label: 'RoHS and environmental compliance' },
  { value: 'AMOUNT_APPROVAL', label: 'Procurement amount approval' },
  { value: 'INFORMATIONAL', label: 'Process guidance and audit trail' },
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
  UNRECOGNIZED_CONTROL: 'The system cannot determine which procurement check this clause supports.',
  AMBIGUOUS_CONTROL: 'This clause matches multiple check purposes and cannot be classified reliably.',
  MISSING_REQUIRED_PARAMETER: 'The clause is missing an amount, currency, or threshold required for execution.',
  CONFLICTING_RULE: 'This clause conflicts with another structured rule in the same revision.',
  DUPLICATE_CLAUSE_ID: 'This revision contains a duplicate clause ID.',
  UNSUPPORTED_CAPABILITY: 'The system has no corresponding checker or data source, so this clause cannot run automatically.',
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
  if (!(error instanceof ApiClientError)) return 'Policy operation failed.'
  const messages: Record<string, string> = {
    policy_import_revision_conflict: 'The server policy revision has changed. Your edits were retained; review the new server revision before submitting.',
    duplicate_policy_clause_id: 'Clause IDs must be unique.',
    policy_clauses_empty: 'Retain at least one publishable clause.',
    published_policy_immutable: 'This policy has been published and can no longer be changed.',
    policy_publish_in_progress: 'Policy publication is in progress. Refresh the status later.',
    policy_import_not_ready: 'Save the clause review before publishing the policy.',
    policy_set_review_incomplete: 'Other files in this policy set still require review. Review every file before publishing the set.',
    policy_version_content_mismatch: 'Different content has already been published under this policy revision. Upload it under a new revision number.',
    policy_publish_failed: 'Embedding or index publication failed. The clauses were retained, so publication can be retried directly.',
  }
  return messages[error.code] ?? error.message
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    REVIEW_REQUIRED: 'Awaiting clause review',
    READY_TO_PUBLISH: 'Automatic identification complete; awaiting publication',
    PUBLISHING: 'Generating index',
    PUBLISHED: 'Published',
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
  const [showAdvanced, setShowAdvanced] = useState(true)
  const [openRuleSettings, setOpenRuleSettings] = useState<Record<string, boolean>>({})

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
      setLocalError('Retain at least one clause.')
      return null
    }
    const ids = clauses.map((clause) => clause.clause_id.trim())
    if (ids.some((id) => !id) || new Set(ids).size !== ids.length) {
      setLocalError('Every clause requires a unique, non-empty clause ID.')
      return null
    }
    const parsed: PolicyClauseInput[] = []
    for (const [index, clause] of clauses.entries()) {
      if (!clause.title.trim() || !clause.text.trim() || !clause.control_code.trim()) {
        setLocalError(`Clause ${index + 1} is missing a title, body, or control code.`)
        return null
      }
      let parameters: unknown
      try {
        parameters = JSON.parse(clause.rule_parameters)
      } catch {
        setLocalError(`Clause ${index + 1} has invalid JSON in rule_parameters.`)
        return null
      }
      if (!parameters || typeof parameters !== 'object' || Array.isArray(parameters)) {
        setLocalError(`Clause ${index + 1} rule_parameters must be a JSON object.`)
        return null
      }
      const ruleError = validateExecutableRule(parameters as Record<string, unknown>, clause.control_code)
      if (ruleError) { setLocalError(`Clause ${index + 1}: ${ruleError}`); return null }
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
      setLocalError('Load and review the new server revision before submitting again.')
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

  if (policy.isPending) return <section className="card loading-panel">Loading policy review record…</section>
  if (policy.isError) {
    return (
      <section className="card error-panel" role="alert">
        <p className="eyebrow">POLICY IMPORT ERROR</p>
        <h1>Unable to load policy</h1>
        <p>{errorMessage(policy.error)}</p>
        <Link className="button button-secondary" to="/resources">Back to policy library</Link>
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
          <Link className="back-link" to="/resources">← Back to policy library</Link>
          <h1>{data.policy_set_id}</h1>
          <p>Reviewing: {data.original_filename}</p>
        </div>
        <div className="workspace-state-stack">
          <span className={`status-pill ${statusClass(data.status)}`}>{statusLabel(data.status)}</span>
        </div>
      </section>

      <section className="policy-metadata-grid" aria-label="Policy metadata">
        <article><span>Policy revision</span><strong>{data.policy_set_version}</strong><small>{setFiles.length || 1} files</small></article>
        <article><span>Scope</span><strong>{data.categories.join(', ')}</strong><small>Regions: {data.regions.join(', ')}</small></article>
        <article><span>Validity period</span><strong>{displayDate(data.effective_from)}</strong><small>to {displayDate(data.effective_to)}</small></article>
        <article><span>Source documents</span><strong>{data.original_filename}</strong><small>{formatBytes(data.size_bytes)} · {data.media_type}</small></article>
      </section>

      {setFiles.length > 0 && (
        <section className="card policy-set-files">
          <div className="section-heading">
            <div><h2>Documents in this version</h2><p>The system organises clauses automatically. Only unrecognised content requires manual confirmation.</p></div>
            <span>{setFiles.filter((item) => item.status === 'READY_TO_PUBLISH' || item.status === 'PUBLISHED').length} / {setFiles.length} ready</span>
          </div>
          <div className="policy-set-file-list">
            {setFiles.map((item) => (
              <Link key={item.policy_import_id} className={item.policy_import_id === policyImportId ? 'is-current' : ''} to={`/resources/policies/${item.policy_import_id}`}>
                <span><strong>{item.original_filename}</strong><small>{item.clause_count} clauses</small></span>
                <span className={`status-pill ${statusClass(item.status)}`}>{statusLabel(item.status)}</span>
              </Link>
            ))}
          </div>
          <details className="policy-source-details policy-source-inline">
            <summary>View parsing results</summary>
            {data.extraction_metadata.page_count && <p>{data.extraction_metadata.page_count} pages</p>}
            <pre>{data.extracted_text}</pre>
          </details>
        </section>
      )}

      {data.status === 'PUBLISHED' && (
        <section className="card policy-published-summary">
          <div><h2>Policy published and available for procurement tasks</h2></div>
          <p>This version is frozen. Upload the complete file set and publish a new version for further changes.</p>
        </section>
      )}

      {!readonly && (
        <section className="card policy-published-summary">
          <div><h2>{unresolvedCount === 0 ? 'This document has been processed automatically' : `${unresolvedCount} items still require attention`}</h2></div>
          <p>
            Identified the purpose of {recognizedCount} / {clauses.length} clauses.
            {unresolvedCount === 0
              ? ' Before automated checks can run, configure and manually confirm the executable rules in Advanced Review below.'
              : ' Only the unidentified clauses below require attention; other content does not need to be reconfirmed.'}
          </p>
        </section>
      )}

      {revisionConflict && (
        <div className="policy-conflict-notice" role="alert">
          <div><strong>A newer server revision is available</strong><span>Your local edits were not overwritten. Load server revision {policy.data.revision} before editing again.</span></div>
          <button className="button button-secondary" type="button" disabled={policy.isFetching} onClick={() => acceptServerVersion(policy.data)}>
            {policy.isFetching ? 'Loading server revision…' : 'Load server revision'}
          </button>
        </div>
      )}

      <form className="policy-clause-editor" onSubmit={saveReview}>
        <fieldset className="policy-clause-editor" disabled={review.isPending || publish.isPending} style={{ border: 0, margin: 0, padding: 0, minWidth: 0 }}>
        {data.status === 'PUBLISHING' && (
          <div className="policy-conflict-notice" role="status">
            <p>The policy is being published. Retry to recover an interrupted publication; the server will prevent duplicate execution while a publication is still running.</p>
            <button className="button button-secondary" type="button" onClick={() => {
              if (lastPublish) publish.mutate(lastPublish)
              else startPublish()
            }}>Retry publication</button>
          </div>
        )}
        <div className="section-heading">
          <div><h2>{showAdvanced ? 'Advanced Review' : 'Clauses requiring confirmation'}</h2></div>
          <div className="policy-clause-actions">
            <button className="button button-secondary" type="button" onClick={() => setShowAdvanced((current) => !current)}>
              {showAdvanced ? 'Collapse advanced review' : `Expand advanced review (${clauses.length} ${clauses.length === 1 ? 'clause' : 'clauses'})`}
            </button>
            {!readonly && showAdvanced && <button className="button button-secondary" type="button" onClick={addClause}>+ Add clause</button>}
          </div>
        </div>
        <p className="section-helper">Clause classification does not create an executable rule. Use advanced review to configure evidence matching, validity handling and amount conditions; legacy parameters are not executed automatically.</p>

        <div className="policy-clause-list">
          {clauses.map((clause, index) => (!showAdvanced && !unresolvedIndexes.has(index) ? null : (
            <article className="card policy-clause-card" key={clause.editor_key}>
              <header>
                <div><span>Clause {index + 1}</span><strong>{clause.title || 'Untitled clause'}</strong></div>
                {!readonly && showAdvanced && (
                  <div className="policy-clause-actions">
                    <button type="button" disabled={index === 0} onClick={() => moveClause(index, -1)} aria-label="Move clause up">↑</button>
                    <button type="button" disabled={index === clauses.length - 1} onClick={() => moveClause(index, 1)} aria-label="Move clause down">↓</button>
                    <button type="button" onClick={() => removeClause(index)}>Delete</button>
                  </div>
                )}
              </header>
              <div className="policy-clause-grid">
                <label className="field policy-field-wide"><span>Title</span><input required readOnly={readonly || !showAdvanced} value={clause.title} onChange={(event) => changeClause(index, 'title', event.target.value)} /></label>
                <label className="field policy-field-wide"><span>Clause Text</span><textarea required readOnly={readonly || !showAdvanced} rows={5} value={clause.text} onChange={(event) => changeClause(index, 'text', event.target.value)} /></label>
                {!showAdvanced && unresolvedIndexes.has(index) && <div className="policy-clause-needs-admin policy-field-wide">
                  <strong>{clauseStatus(sourceClauses[index]) === 'UNSUPPORTED' ? 'Automated execution is not currently supported' : 'Administrator action required'}</strong>
                  {(sourceClauses[index].classification?.reason_codes ?? ['UNRECOGNIZED_CONTROL']).map((reason) => <span key={reason}>{CLASSIFICATION_REASONS[reason] ?? reason}</span>)}
                  {(sourceClauses[index].classification?.conflicts_with?.length ?? 0) > 0 && <span>Conflicts with: {sourceClauses[index].classification?.conflicts_with.join(', ')}</span>}
                  <span>General users do not need to enter technical codes. A policy administrator should complete the advanced review.</span>
                </div>}
                {showAdvanced && <details
                  className="policy-clause-rule-settings policy-field-wide"
                  open={openRuleSettings[clause.editor_key] ?? true}
                  onToggle={(event) => {
                    const open = event.currentTarget.open
                    setOpenRuleSettings((current) => current[clause.editor_key] === open
                      ? current
                      : { ...current, [clause.editor_key]: open })
                  }}
                >
                  <summary>Check-rule settings{!clause.control_code ? ' (required before publication)' : ''}</summary>
                  <div className="policy-clause-grid">
                    <label className="field"><span>Clause ID</span><input required readOnly={readonly} value={clause.clause_id} onChange={(event) => changeClause(index, 'clause_id', event.target.value)} /></label>
                    <label className="field"><span>Check Type</span><select required disabled={readonly} value={clause.control_code} onChange={(event) => changeClause(index, 'control_code', event.target.value)}><option value="">Select a clause purpose</option>{CONTROL_CODE_OPTIONS.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}</select></label>
                    <div className="policy-field-wide"><ExecutableRuleEditor controlCode={clause.control_code} value={clause.rule_parameters} onChange={(value) => changeClause(index, 'rule_parameters', value)} readonly={readonly} /></div>
                    <details className="policy-field-wide"><summary>View raw rule parameters</summary><label className="field"><span>Advanced rule parameters <small>JSON</small></span><textarea required readOnly={readonly} rows={4} spellCheck={false} value={clause.rule_parameters} onChange={(event) => changeClause(index, 'rule_parameters', event.target.value)} /></label></details>
                  </div>
                </details>}
              </div>
            </article>
          )))}
          {!showAdvanced && unresolvedCount === 0 && <p className="policy-directory-state">This document has no clauses requiring manual action.</p>}
        </div>

        {(localError || review.isError || publish.isError) && (
          <div className="form-error compact-error" role="alert">
            {localError || errorMessage(review.error ?? publish.error)}
          </div>
        )}

        {!readonly && (
          <div className="policy-review-actions">
            {review.isError && lastReview && !revisionConflict && (
              <button className="button button-secondary" type="button" onClick={() => review.mutate(lastReview)}>Retry the same save request</button>
            )}
            {showAdvanced && (unresolvedCount > 0 || dirty || data.status === 'REVIEW_REQUIRED') && <button className="button button-submit" type="submit" disabled={revisionConflict || review.isPending || publish.isPending}>
              {review.isPending ? 'Saving…' : 'Save confirmed result'}
            </button>}
            <button className="button button-submit" type="button" disabled={data.status !== 'READY_TO_PUBLISH' || !allFilesReviewed || dirty || review.isPending || publish.isPending} onClick={startPublish}>
              {publish.isPending ? 'Publishing policy…' : allFilesReviewed ? 'Confirm and publish this revision' : 'Resolve pending clauses first'}
            </button>
            {publish.isError && lastPublish && !revisionConflict && (
              <button className="button button-secondary" type="button" onClick={() => publish.mutate(lastPublish)}>Retry the same publication request</button>
            )}
          </div>
        )}
        </fieldset>
      </form>
    </div>
  )
}
