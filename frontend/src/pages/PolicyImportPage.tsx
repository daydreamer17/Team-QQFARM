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
  return new Intl.DateTimeFormat('en-SG', { dateStyle: 'medium' }).format(date)
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
    REVIEW_REQUIRED: 'Review needed',
    READY_TO_PUBLISH: 'Ready to publish',
    PUBLISHING: 'Publishing',
    PUBLISHED: 'Published',
  }
  return labels[status] ?? status
}

function statusClass(status: string) {
  if (status === 'PUBLISHED') return 'status-ready'
  if (status === 'READY_TO_PUBLISH' || status === 'PUBLISHING') return 'status-pending'
  return 'status-muted'
}

const EXECUTION_STAGE_LABELS: Record<string, string> = {
  BEFORE_RECOMMENDATION: 'Before recommendation',
  BEFORE_PUBLICATION: 'Before report publication',
  AFTER_SELECTION: 'After supplier selection',
}

const OPERATOR_LABELS: Record<string, string> = {
  '>=': '≥',
  GTE: '≥',
  '>': '>',
  GT: '>',
  '<=': '≤',
  LTE: '≤',
  '<': '<',
  LT: '<',
}

function parseRuleParameters(value: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(value)
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {}
  } catch {
    return {}
  }
}

function controlLabel(controlCode: string) {
  return CONTROL_CODE_OPTIONS.find((option) => option.value === controlCode)?.label
    ?? 'Check type not selected'
}

function amountRuleIsComplete(parameters: Record<string, unknown>) {
  if ('version' in parameters) return validateExecutableRule(parameters, 'AMOUNT_APPROVAL') === null
  return /^[A-Z]{3}$/.test(String(parameters.currency ?? ''))
    && /^\d+(\.\d+)?$/.test(String(parameters.threshold ?? ''))
    && ['>', '>=', '<', '<='].includes(String(parameters.operator ?? ''))
}

function ruleExplanation(clause: EditableClause) {
  const parameters = parseRuleParameters(clause.rule_parameters)
  const stage = EXECUTION_STAGE_LABELS[String(parameters.execution_stage ?? 'BEFORE_RECOMMENDATION')]
    ?? 'Configured execution stage'
  if (clause.control_code === 'APPROVED_SUPPLIER') {
    return {
      summary: 'Match current eligibility evidence to the exact Supplier ID.',
      detail: `The check runs ${stage.toLowerCase()}. Missing, expired or mismatched evidence is sent for human review.`,
    }
  }
  if (clause.control_code === 'ROHS_COMPLIANCE') {
    return {
      summary: 'Match valid RoHS evidence to the supplier, manufacturer and part number.',
      detail: `The check runs ${stage.toLowerCase()}. Missing evidence requires review; expired or mismatched evidence does not pass the default check.`,
    }
  }
  if (clause.control_code === 'AMOUNT_APPROVAL') {
    const currency = typeof parameters.currency === 'string' ? parameters.currency : ''
    const threshold = typeof parameters.threshold === 'string' || typeof parameters.threshold === 'number'
      ? String(parameters.threshold)
      : ''
    const operator = OPERATOR_LABELS[String(parameters.operator ?? '')] ?? ''
    if (!amountRuleIsComplete(parameters) || !currency || !threshold || !operator) {
      return {
        summary: 'The approval threshold is incomplete.',
        detail: 'Add only the missing currency, amount and comparison condition before publishing.',
      }
    }
    return {
      summary: `Confirmed total cost ${operator} ${currency} ${threshold} triggers amount approval.`,
      detail: `The check runs ${stage.toLowerCase()}. A triggered rule records the required approval step; it does not approve the purchase automatically.`,
    }
  }
  if (clause.control_code === 'INFORMATIONAL') {
    return {
      summary: 'Keep this clause as policy guidance and audit context.',
      detail: 'This clause is searchable and citable, but it does not block or rank a supplier automatically.',
    }
  }
  return {
    summary: 'The system could not determine how to apply this clause.',
    detail: 'Select a check type or keep the clause as informational guidance.',
  }
}

function ruleDisplayStatus(source: PolicyDraftClause | undefined, clause: EditableClause) {
  const status = source ? clauseStatus(source) : clause.control_code ? 'AUTO_ACCEPTED' : 'ADMIN_REVIEW'
  if (clause.control_code === 'INFORMATIONAL') {
    return { label: 'Reference only', className: 'is-reference' }
  }
  const parameters = parseRuleParameters(clause.rule_parameters)
  if (status === 'AUTO_ACCEPTED'
    && (clause.control_code !== 'AMOUNT_APPROVAL' || amountRuleIsComplete(parameters))) {
    return { label: 'Ready', className: 'is-ready' }
  }
  return { label: 'Needs attention', className: 'is-attention' }
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
  const [openRuleSettings, setOpenRuleSettings] = useState<Record<string, boolean>>({})
  const [editingClauses, setEditingClauses] = useState<Record<string, boolean>>({})

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

  function changeRuleParameter(index: number, key: string, value: unknown) {
    const parameters = parseRuleParameters(clauses[index].rule_parameters)
    changeClause(index, 'rule_parameters', JSON.stringify({ ...parameters, [key]: value }, null, 2))
  }

  function addClause() {
    const editorKey = `draft-${Date.now()}-${clauses.length}`
    setClauses((current) => [
      ...current,
      {
        editor_key: editorKey,
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
    setEditingClauses((current) => ({ ...current, [editorKey]: true }))
    setOpenRuleSettings((current) => ({ ...current, [editorKey]: true }))
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
      const ruleParameters = parameters as Record<string, unknown>
      if (clause.control_code === 'AMOUNT_APPROVAL' && !('version' in ruleParameters)) {
        const currency = String(ruleParameters.currency ?? '')
        const threshold = String(ruleParameters.threshold ?? '')
        const operator = String(ruleParameters.operator ?? '')
        if (!/^[A-Z]{3}$/.test(currency) || !/^\d+(\.\d+)?$/.test(threshold)
          || !['>', '>=', '<', '<='].includes(operator)) {
          setLocalError(`Clause ${index + 1}: enter a three-letter currency, numeric threshold and comparison condition.`)
          return null
        }
      }
      const ruleError = validateExecutableRule(ruleParameters, clause.control_code)
      if (ruleError) { setLocalError(`Clause ${index + 1}: ${ruleError}`); return null }
      parsed.push({
        clause_id: clause.clause_id.trim(),
        title: clause.title.trim(),
        text: clause.text.trim(),
        control_code: clause.control_code.trim().toUpperCase(),
        rule_parameters: ruleParameters,
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
    clauses
      .map((clause, index) => ruleDisplayStatus(sourceClauses[index], clause).className === 'is-attention' ? index : -1)
      .filter((index) => index >= 0),
  )
  const unresolvedCount = unresolvedIndexes.size
  const recognizedCount = clauses.filter((clause) => Boolean(clause.control_code)).length
  const referenceCount = clauses.filter((clause, index) =>
    ruleDisplayStatus(sourceClauses[index], clause).className === 'is-reference'
  ).length
  const readyCount = Math.max(0, clauses.length - unresolvedCount - referenceCount)

  return (
    <div className="page-stack policy-review-page">
      <section className="page-heading policy-review-heading">
        <div>
          <Link className="back-link" to="/resources">← Policies</Link>
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
            <div><h2>Documents</h2><p>Clauses are organised automatically; only unrecognised content needs review.</p></div>
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
            <summary>Parsing results</summary>
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
          <div><h2>{unresolvedCount === 0 ? 'Automated policy rules are ready' : `${unresolvedCount} ${unresolvedCount === 1 ? 'rule needs' : 'rules need'} attention`}</h2></div>
          <p>
            The system understood {recognizedCount} / {clauses.length} clauses.
            {unresolvedCount === 0
              ? ' Ready rules will use the displayed safe defaults when this policy is published; no additional setup is required.'
              : ' Complete only the highlighted information below; recognised rules do not need to be entered again.'}
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
            }}>Retry</button>
          </div>
        )}
        <div className="section-heading">
          <div><h2>Policy rule review</h2><p>Review what the system will enforce. Open a row only when you need the source text or want to change the defaults.</p></div>
        </div>
        <div className="policy-rule-summary" aria-label="Policy rule status">
          <span><strong>{readyCount}</strong> ready</span>
          <span><strong>{unresolvedCount}</strong> need attention</span>
          <span><strong>{referenceCount}</strong> reference only</span>
        </div>

        <div className="policy-rule-table" role="table" aria-label="Policy rules">
          <div className="policy-rule-table-head" role="row">
            <span role="columnheader">Clause</span>
            <span role="columnheader">How the system will apply it</span>
            <span role="columnheader">Status</span>
            <span aria-hidden="true">Actions</span>
          </div>
          {clauses.map((clause, index) => {
            const source = sourceClauses[index]
            const unresolved = unresolvedIndexes.has(index)
            const unsupported = Boolean(source && clauseStatus(source) === 'UNSUPPORTED')
            const explanation = ruleExplanation(clause)
            const displayStatus = ruleDisplayStatus(source, clause)
            const parameters = parseRuleParameters(clause.rule_parameters)
            const legacyAmountRule = clause.control_code === 'AMOUNT_APPROVAL' && !('version' in parameters)
            const isEditing = editingClauses[clause.editor_key] === true
            return (
              <details
                className={`policy-rule-row ${displayStatus.className}`}
                key={clause.editor_key}
                open={openRuleSettings[clause.editor_key] ?? unresolved}
                onToggle={(event) => {
                  const open = event.currentTarget.open
                  setOpenRuleSettings((current) => current[clause.editor_key] === open
                    ? current
                    : { ...current, [clause.editor_key]: open })
                }}
              >
                <summary>
                  <span className="policy-rule-title"><strong>{clause.title || 'Untitled clause'}</strong><small>{controlLabel(clause.control_code)}</small></span>
                  <span className="policy-rule-explanation">{explanation.summary}</span>
                  <span className={`policy-rule-status ${displayStatus.className}`}>{displayStatus.label}</span>
                  <span className="policy-rule-actions">
                    <span className="policy-rule-details-label">View</span>
                    {!readonly && <button type="button" aria-label={`${isEditing ? 'Finish editing' : 'Edit'} ${clause.title || 'untitled clause'}`} onClick={(event) => {
                      event.preventDefault()
                      event.stopPropagation()
                      setEditingClauses((current) => ({ ...current, [clause.editor_key]: !isEditing }))
                      if (!isEditing) setOpenRuleSettings((current) => ({ ...current, [clause.editor_key]: true }))
                    }}>{isEditing ? 'Done' : 'Edit'}</button>}
                  </span>
                </summary>
                <div className="policy-rule-row-body">
                  <section>
                    <h3>Source clause</h3>
                    <blockquote>{clause.text}</blockquote>
                    <small>Clause reference: {clause.clause_id}</small>
                  </section>
                  <section>
                    <h3>Execution summary</h3>
                    <p>{explanation.detail}</p>
                    {unresolved && <div className="policy-clause-needs-admin">
                      <strong>{source && clauseStatus(source) === 'UNSUPPORTED' ? 'Automatic execution is not supported' : 'Information required'}</strong>
                      {(source?.classification?.reason_codes ?? ['UNRECOGNIZED_CONTROL']).map((reason) => <span key={reason}>{CLASSIFICATION_REASONS[reason] ?? reason}</span>)}
                      {(source?.classification?.conflicts_with?.length ?? 0) > 0 && <span>Conflicts with: {source.classification?.conflicts_with.join(', ')}</span>}
                    </div>}
                  </section>

                  {!readonly && (isEditing || unresolved) && <section className="policy-rule-edit-panel">
                    <div className="policy-rule-edit-heading">
                      <div><h3>{unresolved ? 'Complete this rule' : 'Edit clause'}</h3><p>Only change values when the system interpretation does not match the policy text.</p></div>
                      {isEditing && <div className="policy-clause-actions">
                        <button type="button" disabled={index === 0} onClick={() => moveClause(index, -1)} aria-label="Move clause up">↑</button>
                        <button type="button" disabled={index === clauses.length - 1} onClick={() => moveClause(index, 1)} aria-label="Move clause down">↓</button>
                        <button type="button" onClick={() => removeClause(index)}>Delete</button>
                      </div>}
                    </div>
                    <div className="policy-clause-grid">
                      {isEditing && <>
                        <label className="field"><span>Title</span><input required value={clause.title} onChange={(event) => changeClause(index, 'title', event.target.value)} /></label>
                        <label className="field"><span>Check Type</span><select required value={clause.control_code} onChange={(event) => changeClause(index, 'control_code', event.target.value)}><option value="">Select a clause purpose</option>{CONTROL_CODE_OPTIONS.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}</select></label>
                        <label className="field policy-field-wide"><span>Clause Text</span><textarea required rows={4} value={clause.text} onChange={(event) => changeClause(index, 'text', event.target.value)} /></label>
                      </>}
                      {!isEditing && !clause.control_code && unsupported && <div className="policy-reference-action policy-field-wide">
                        <div><strong>Keep the clause for reference?</strong><span>It will remain searchable and citable, but it will not produce an automatic pass or fail result.</span></div>
                        <button className="button button-secondary" type="button" onClick={() => changeClause(index, 'control_code', 'INFORMATIONAL')}>Keep as reference only</button>
                      </div>}
                      {!isEditing && !clause.control_code && !unsupported && <label className="field policy-field-wide"><span>What should this clause check?</span><select required value={clause.control_code} onChange={(event) => changeClause(index, 'control_code', event.target.value)}><option value="">Select a check type</option>{CONTROL_CODE_OPTIONS.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}</select></label>}
                      {legacyAmountRule && <div className="policy-amount-rule-fields policy-field-wide">
                        <label className="field"><span>Currency</span><input maxLength={3} value={String(parameters.currency ?? '')} placeholder="SGD" onChange={(event) => changeRuleParameter(index, 'currency', event.target.value.toUpperCase())} /></label>
                        <label className="field"><span>Amount threshold</span><input inputMode="decimal" value={String(parameters.threshold ?? '')} placeholder="10000.00" onChange={(event) => changeRuleParameter(index, 'threshold', event.target.value)} /></label>
                        <label className="field"><span>Trigger when total cost is</span><select value={String(parameters.operator ?? '>=')} onChange={(event) => changeRuleParameter(index, 'operator', event.target.value)}><option value=">=">Greater than or equal to</option><option value=">">Greater than</option><option value="<=">Less than or equal to</option><option value="<">Less than</option></select></label>
                        <label className="field"><span>Check timing</span><select value={String(parameters.execution_stage ?? 'BEFORE_RECOMMENDATION')} onChange={(event) => changeRuleParameter(index, 'execution_stage', event.target.value)}>{Object.entries(EXECUTION_STAGE_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
                      </div>}
                      {isEditing && <details className="policy-clause-rule-settings policy-field-wide">
                        <summary>Advanced execution settings</summary>
                        <div className="policy-clause-grid">
                          <div className="policy-field-wide"><ExecutableRuleEditor controlCode={clause.control_code} value={clause.rule_parameters} onChange={(value) => changeClause(index, 'rule_parameters', value)} readonly={readonly} /></div>
                          <details className="policy-field-wide"><summary>Technical parameters</summary><label className="field"><span>Rule parameters <small>JSON</small></span><textarea required rows={4} spellCheck={false} value={clause.rule_parameters} onChange={(event) => changeClause(index, 'rule_parameters', event.target.value)} /></label></details>
                          <label className="field policy-field-wide"><span>Clause ID</span><input required value={clause.clause_id} onChange={(event) => changeClause(index, 'clause_id', event.target.value)} /></label>
                        </div>
                      </details>}
                    </div>
                  </section>}
                </div>
              </details>
            )
          })}
        </div>
        {!readonly && <div className="policy-add-clause-row"><button className="button button-secondary" type="button" onClick={addClause}>+ Add policy clause</button></div>}

        {(localError || review.isError || publish.isError) && (
          <div className="form-error compact-error" role="alert">
            {localError || errorMessage(review.error ?? publish.error)}
          </div>
        )}

        {!readonly && (
          <div className="policy-review-actions">
            {review.isError && lastReview && !revisionConflict && (
              <button className="button button-secondary" type="button" onClick={() => review.mutate(lastReview)}>Retry</button>
            )}
            {(unresolvedCount > 0 || dirty || data.status === 'REVIEW_REQUIRED') && <button className="button button-submit" type="submit" disabled={revisionConflict || review.isPending || publish.isPending}>
              {review.isPending ? 'Saving…' : 'Save'}
            </button>}
            <button className="button button-submit" type="button" disabled={data.status !== 'READY_TO_PUBLISH' || !allFilesReviewed || dirty || review.isPending || publish.isPending} onClick={startPublish}>
              {publish.isPending ? 'Publishing…' : 'Publish'}
            </button>
            {publish.isError && lastPublish && !revisionConflict && (
              <button className="button button-secondary" type="button" onClick={() => publish.mutate(lastPublish)}>Retry</button>
            )}
          </div>
        )}
        </fieldset>
      </form>
    </div>
  )
}
