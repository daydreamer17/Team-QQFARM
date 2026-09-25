import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Fragment, type FormEvent, useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { api, ApiClientError, complianceEvidenceUrl, createIdempotencyKey } from '../api/client'
import type { ComplianceClause, ComplianceEvidenceFacts, ComplianceEvidenceRecord, ComplianceWorkspace, PolicyComplianceSupplierAssessment, TaskDetail } from '../api/types'
import { EnglishDateInput } from '../components/EnglishDateInput'
import { EnglishFilePicker } from '../components/EnglishFilePicker'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'
import { TablePagination } from '../components/TablePagination'
import { useTablePagination } from '../hooks/useTablePagination'
import { controlLabel } from '../lib/presentation'
import { aggregateControlStatus, compactCheckStatusLabel, compactComplianceStatusLabel, complianceAnchorId, complianceReasonLabel, executionStageLabel, groupComplianceChecks } from '../lib/compliance'

function errorMessage(error: unknown) {
  if (error instanceof ApiClientError && (error.status === 409 || error.code.includes('revision'))) return 'The task or check revision has changed. Refresh and review it again. Unsaved input has been retained.'
  return error instanceof Error ? error.message : 'Compliance review operation failed.'
}
function errorText(error: string | Record<string, unknown>) {
  return typeof error === 'string' ? complianceReasonLabel(error) : String(error.message ?? error.code ?? 'Policy evidence is incomplete. Contact the policy administrator.')
}
function uniqueErrors(...groups: Array<Array<string | Record<string, unknown>> | undefined>) {
  const values = groups.flatMap((group) => group ?? [])
  return [...new Map(values.map((error) => [typeof error === 'string' ? error : JSON.stringify(error), error])).values()]
}
type EvidenceTarget = {
  supplier: PolicyComplianceSupplierAssessment
  control: ComplianceEvidenceFacts['control_code']
  record?: ComplianceEvidenceRecord
  revision: number
  runAfterSave: boolean
}

const PRECHECK_CONTROLS: ComplianceEvidenceFacts['control_code'][] = [
  'APPROVED_SUPPLIER',
  'ROHS_COMPLIANCE',
  'AMOUNT_APPROVAL',
]

function evidenceMaterialLabel(control: ComplianceEvidenceFacts['control_code']) {
  return control === 'APPROVED_SUPPLIER' ? 'Supplier eligibility record'
    : control === 'ROHS_COMPLIANCE' ? 'RoHS evidence'
      : 'Amount approval record'
}

function stateTone(status: string) {
  if (['PASS', 'COMPLIANT', 'VERIFIED', 'PROCESSED'].includes(status)) return 'success'
  if (['FAIL', 'NON_COMPLIANT', 'EXCLUDED', 'BLOCKED'].includes(status)) return 'danger'
  if (['REVIEW_REQUIRED', 'UNVERIFIED', 'AWAITING_CONFIRMATION'].includes(status)) return 'warning'
  return 'neutral'
}

function StateBadge({ status, label }: { status: string; label: string }) {
  return <span className={`compliance-state compliance-state-${stateTone(status)}`}>{label}</span>
}

function ComplianceLoading({ label, detail }: { label: string; detail: string }) {
  return <section className="card compliance-loading-panel" role="status" aria-live="polite">
    <span className="compliance-spinner" aria-hidden="true" />
    <div><h3>{label}</h3><p>{detail}</p></div>
  </section>
}

function uniqueAmountRequirements(items: NonNullable<ComplianceWorkspace['assessment']>['amount_requirements'] = []) {
  return items.filter((item, index) => {
    const key = [item.quote_id, item.execution_stage, item.amount, item.currency, item.threshold, item.triggered, item.action].join('|')
    return items.findIndex((candidate) => [candidate.quote_id, candidate.execution_stage, candidate.amount,
      candidate.currency, candidate.threshold, candidate.triggered, candidate.action].join('|') === key) === index
  })
}

function checkIcon(status: ReturnType<typeof aggregateControlStatus>) {
  return status === 'PASS' ? '✓' : status === 'FAIL' ? '×' : status === 'REVIEW_REQUIRED' ? '!' : '—'
}

function SupplierCheckDetails({ supplier, clauses, evidence, taskId, linkedCheck, readonly, onEdit }: {
  supplier: PolicyComplianceSupplierAssessment
  clauses: ComplianceClause[]
  evidence: ComplianceEvidenceRecord[]
  taskId: string
  linkedCheck: string | null
  readonly: boolean
  onEdit: (control: ComplianceEvidenceFacts['control_code'], record?: ComplianceEvidenceRecord) => void
}) {
  const groups = groupComplianceChecks(supplier.checks)
  return <div className="compliance-detail-panel">
    <div className="compliance-detail-heading"><div><strong>Details</strong><span>{groups.length} checks · {supplier.checks.length} requirements · {evidence.length} records</span></div><StateBadge status={supplier.status} label={compactComplianceStatusLabel(supplier.status)} /></div>
    <div className="compliance-control-grid">
      {groups.map(([control, checks]) => {
        const status = aggregateControlStatus(checks, control)
        const reasons = [...new Set(checks.flatMap((check) => check.reason_codes ?? [check.reason_code ?? '']).filter(Boolean))]
        const records = evidence.filter((record) => record.control_code === control)
        const currentRecord = records.find((record) => !record.superseded)
        const linked = checks.some((check) => check.clause_id === linkedCheck)
        return <article className={`compliance-control-card compliance-control-${stateTone(status)}`} key={control}>
          <header><span className="compliance-control-icon" aria-hidden="true">{checkIcon(status)}</span><div><small>Check</small><h4>{controlLabel(control)}</h4></div><StateBadge status={status} label={compactCheckStatusLabel(status)} /></header>
          {reasons.length > 0 && <ul className="compliance-control-reasons">{reasons.map((reason) => <li key={reason}>{complianceReasonLabel(reason)}</li>)}</ul>}
          <details className="compliance-source-details" open={linked || undefined}><summary>Policy ({checks.length})</summary>
            <div className="compliance-source-list">{checks.map((check, index) => {
              const matchingClauses = clauses.filter((clause) => clause.clause_id === check.clause_id)
              return <div className="compliance-source-row" id={complianceAnchorId(supplier.quote_id, check.clause_id)} tabIndex={-1} key={check.clause_id ?? index}>
                <div><strong>{compactCheckStatusLabel(check.status)}</strong>{check.execution_stage && <small>{executionStageLabel(check.execution_stage)}</small>}</div>
                {matchingClauses.length > 0 ? matchingClauses.map((clause) => <details key={clause.clause_id}><summary>{clause.title ?? clause.section ?? 'Policy source text'} · Revision {clause.document_version}</summary><blockquote>{clause.text}</blockquote><small>{clause.document_id} · {clause.clause_id}</small></details>) : <span>No displayable policy source text is linked.</span>}
              </div>
            })}</div>
          </details>
          {records.length > 0 && <details className="compliance-source-details"><summary>Evidence ({records.length})</summary><div className="compliance-material-list">{records.map((record) => <article key={record.evidence_id}><strong>{record.facts.material_number} · Revision {record.version}{record.superseded ? ' (replaced)' : ''}</strong><p>{record.facts.supplier_id} · {record.facts.manufacturer} · {record.facts.manufacturer_part_number}</p><p>Valid: {record.facts.effective_from ?? 'Not specified'} to {record.facts.permanent ? 'Explicitly permanent' : record.facts.expires_on ?? 'Not specified'}</p><p>Reviewed by: {record.confirmed_by} · {record.confirmed_at}</p>{record.facts.source_refs.map((ref) => <p key={ref}>{ref}</p>)}{record.files.map((file) => <a className="compliance-file" key={file.file_id} href={complianceEvidenceUrl(taskId, record.evidence_id, file.file_id)}>{file.original_filename}</a>)}</article>)}</div></details>}
          {!readonly && ['APPROVED_SUPPLIER', 'ROHS_COMPLIANCE'].includes(control) && <div className="compliance-control-action"><button className="button button-secondary" type="button" onClick={() => onEdit(control as ComplianceEvidenceFacts['control_code'], currentRecord)}>{currentRecord ? 'Replace' : 'Upload'}</button></div>}
        </article>
      })}
    </div>
  </div>
}

function EvidenceEditor({ task, target, onClose, onSaved }: {
  task: TaskDetail; target: EvidenceTarget; onClose: () => void; onSaved: () => Promise<void>
}) {
  const isAmountApproval = target.control === 'AMOUNT_APPROVAL'
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const element = dialog.current!
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    if (typeof element.showModal === 'function') element.showModal()
    else element.setAttribute('open', '')
    element.querySelector<HTMLInputElement>('input')?.focus()
    return () => {
      if (typeof element.close === 'function') element.close()
      previous?.focus()
    }
  }, [])
  const [facts, setFacts] = useState<ComplianceEvidenceFacts>(() => ({
    quote_id: target.supplier.quote_id, control_code: target.control, supplier_id: target.supplier.supplier_id ?? '',
    manufacturer: isAmountApproval ? null : task.requirement.manufacturer,
    manufacturer_part_number: isAmountApproval ? null : task.requirement.manufacturer_part_number,
    approval_amount: null, currency: isAmountApproval ? task.requirement.currency : null,
    material_number: '', outcome: 'PASS', effective_from: null, expires_on: null, permanent: false, source_refs: [], note: '',
    ...target.record?.facts, coverage_confirmed: false,
  }))
  const [file, setFile] = useState<File | null>(null)
  const [localError, setLocalError] = useState('')
  const [parseMessage, setParseMessage] = useState('')
  const [idempotencyKey, setIdempotencyKey] = useState(createIdempotencyKey)
  const parseFile = useMutation({
    mutationFn: (selected: File) => api.parseComplianceEvidence(task.task_id, target.control, selected),
    onSuccess: (response) => {
      if (response.status === 'TYPE_MISMATCH') {
        setParseMessage(`Evidence type mismatch: this appears to be ${response.detected_control_code ? evidenceMaterialLabel(response.detected_control_code) : 'another type of evidence'}. Close this form and upload it from the matching entry point.`)
        return
      }
      setFacts((current) => ({ ...current, ...response.facts, coverage_confirmed: false }))
      const parsedSupplier = typeof response.facts.supplier_id === 'string' ? response.facts.supplier_id : null
      const expectedSupplier = target.supplier.supplier_id ?? null
      const parsedPart = typeof response.facts.manufacturer_part_number === 'string' ? response.facts.manufacturer_part_number : null
      const messages = [response.status === 'FOUND'
        ? `Automatically parsed and filled ${response.parsed_fields.length} fields.`
        : `Filled ${response.parsed_fields.length} fields; review the remaining fields against the source document.`]
      if (parsedSupplier && expectedSupplier && parsedSupplier !== expectedSupplier) {
        messages.push(`Warning: supplier ID ${parsedSupplier} in the file does not match ${expectedSupplier} in the current quotation.`)
      }
      if (parsedPart && task.requirement.manufacturer_part_number && parsedPart !== task.requirement.manufacturer_part_number) {
        messages.push(`Warning: part number ${parsedPart} in the file does not match procurement part number ${task.requirement.manufacturer_part_number}.`)
      }
      setParseMessage(messages.join(' '))
      setIdempotencyKey(createIdempotencyKey())
    },
    onError: (error) => setParseMessage(`Automatic parsing failed: ${errorMessage(error)} You can still enter the fields manually and save.`),
  })
  const save = useMutation({ mutationFn: () => api.saveComplianceEvidence(task.task_id,
    { expectedTaskRevision: target.revision, facts: { ...facts, source_refs: facts.source_refs.map((ref) => ref.trim()).filter(Boolean) }, file, evidenceId: target.record?.evidence_id, runAfterSave: target.runAfterSave }, idempotencyKey),
    onSuccess: async () => { await onSaved(); onClose() },
    onError: async (error) => { if (error instanceof ApiClientError && error.status === 409) await onSaved() },
  })
  const stale = task.task_revision !== target.revision
  const ready = facts.coverage_confirmed && facts.material_number.trim() && facts.supplier_id.trim()
    && (file || facts.source_refs.some((ref) => ref.trim()))
    && (facts.control_code !== 'ROHS_COMPLIANCE' || (facts.manufacturer?.trim() && facts.manufacturer_part_number?.trim()))
    && (facts.control_code !== 'AMOUNT_APPROVAL' || (facts.approval_amount && Number(facts.approval_amount) >= 0 && facts.currency?.trim()))
  function change<K extends keyof ComplianceEvidenceFacts>(field: K, value: ComplianceEvidenceFacts[K]) {
    setFacts((current) => ({ ...current, [field]: value })); setIdempotencyKey(createIdempotencyKey()); setLocalError(''); save.reset()
  }
  function submit(event: FormEvent) {
    event.preventDefault()
    if (!ready || stale) return
    if (facts.effective_from && facts.expires_on && facts.effective_from >= facts.expires_on) return setLocalError('Effective-to date must be later than the effective-from date.')
    save.mutate()
  }
  return <dialog ref={dialog} className="card compliance-evidence-editor" aria-modal="true" aria-labelledby="compliance-editor-title" aria-describedby="compliance-editor-description" onCancel={(event) => { event.preventDefault(); if (!save.isPending) onClose() }} onKeyDown={(event) => {
    if (event.key === 'Escape') { event.preventDefault(); if (!save.isPending) onClose() }
    if (event.key !== 'Tab') return
    const focusable = [...event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href]')]
    const first = focusable[0], last = focusable.at(-1)
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
  }}>
    <div className="section-heading"><div><h3 id="compliance-editor-title">{isAmountApproval ? target.record ? 'Replace amount approval record and retain the previous revision' : 'Add amount approval record' : target.record ? `Replace evidence: ${evidenceMaterialLabel(target.control)}` : `Add evidence: ${evidenceMaterialLabel(target.control)}`} · {target.supplier.supplier_name}</h3><p>{evidenceMaterialLabel(target.control)} · Quotation revision {target.supplier.quote_version}</p></div><button type="button" className="button button-secondary" disabled={save.isPending} onClick={onClose}>Cancel</button></div>
    <p id="compliance-editor-description">{target.runAfterSave
      ? isAmountApproval ? 'Verify the approved supplier, amount, currency, and validity period against the approval source document. Checks will rerun after saving.' : 'Verify the supplier and product scope against the evidence source document. Checks will rerun after saving.'
      : 'After saving, you can upload other available evidence. The system will evaluate everything together when you start the compliance review.'}</p>
    <form onSubmit={submit}><fieldset disabled={save.isPending || stale} className="compliance-form-grid">
      <label className="field">{isAmountApproval ? 'Approval record number' : 'Evidence ID'}<input required value={facts.material_number} onChange={(e) => change('material_number', e.target.value)} /></label>
      <label className="field">{isAmountApproval ? 'Approved supplier ID' : 'Evidence supplier ID'}<input required value={facts.supplier_id} onChange={(e) => change('supplier_id', e.target.value)} /></label>
      {!isAmountApproval && <label className="field">Evidence manufacturer<input required={target.control === 'ROHS_COMPLIANCE'} value={facts.manufacturer ?? ''} onChange={(e) => change('manufacturer', e.target.value)} /></label>}
      {!isAmountApproval && <label className="field">Evidence manufacturer part number<input required={target.control === 'ROHS_COMPLIANCE'} value={facts.manufacturer_part_number ?? ''} onChange={(e) => change('manufacturer_part_number', e.target.value)} /></label>}
      {isAmountApproval && <label className="field">Approved Amount<input required inputMode="decimal" value={facts.approval_amount ?? ''} onChange={(e) => change('approval_amount', e.target.value || null)} /></label>}
      {isAmountApproval && <label className="field">Currency<input required maxLength={3} value={facts.currency ?? ''} onChange={(e) => change('currency', e.target.value.toUpperCase())} /></label>}
      <label className="field">{isAmountApproval ? 'Approval outcome' : 'Evidence outcome'}<select value={facts.outcome} onChange={(e) => change('outcome', e.target.value as 'PASS' | 'FAIL')}><option value="PASS">{isAmountApproval ? 'Approved' : 'Evidence indicates compliance'}</option><option value="FAIL">{isAmountApproval ? 'Not approved / rejected' : 'Evidence indicates non-compliance'}</option></select></label>
      <label className="field">Effective from<EnglishDateInput value={facts.effective_from ?? ''} onChange={(value) => change('effective_from', value || null)} /></label>
      <label className="field">Effective to<EnglishDateInput disabled={facts.permanent} value={facts.expires_on ?? ''} onChange={(value) => change('expires_on', value || null)} /></label>
      <label className="compliance-checkbox"><input type="checkbox" checked={facts.permanent} onChange={(e) => { setFacts((current) => ({ ...current, permanent: e.target.checked, expires_on: e.target.checked ? null : current.expires_on })); setIdempotencyKey(createIdempotencyKey()); setLocalError('') }} />Evidence explicitly states that it is permanently valid</label>
      <label className="field compliance-wide">Source references<textarea rows={2} placeholder="Original record number or source link, one per line; you may also upload an attachment" value={facts.source_refs.join('\n')} onChange={(e) => change('source_refs', e.target.value.split('\n'))} /></label>
      <div className="field compliance-wide"><span>Evidence attachment (PDF, TXT, or MD; up to 10 MiB)</span><EnglishFilePicker aria-label="Evidence attachment" fileName={file?.name} accept=".pdf,.txt,.md" onChange={(e) => {
        const selected = e.target.files?.[0] ?? null
        if (selected && (!/\.(pdf|txt|md)$/i.test(selected.name) || selected.size > 10 * 1024 * 1024 || selected.size === 0)) {
          setLocalError('Select a non-empty PDF, TXT, or MD file no larger than 10 MiB.'); setFile(null); return
        }
        setLocalError(''); setParseMessage(''); setFile(selected); setIdempotencyKey(createIdempotencyKey())
        if (selected) {
          setFacts((current) => ({
            ...current,
            supplier_id: target.supplier.supplier_id ?? '',
            manufacturer: isAmountApproval ? null : task.requirement.manufacturer,
            manufacturer_part_number: isAmountApproval ? null : task.requirement.manufacturer_part_number,
            approval_amount: null,
            currency: isAmountApproval ? task.requirement.currency : null,
            material_number: '', outcome: 'PASS', effective_from: null, expires_on: null,
            permanent: false, source_refs: [], coverage_confirmed: false,
          }))
          parseFile.mutate(selected)
        }
      }} /></div>
      {(parseFile.isPending || parseMessage) && <p className="compliance-wide compliance-parse-status" role="status" aria-live="polite">{parseFile.isPending ? 'Automatically parsing evidence file…' : parseMessage}</p>}
      <label className="field compliance-wide">Review notes<textarea rows={2} value={facts.note} onChange={(e) => change('note', e.target.value)} /></label>
      <label className="compliance-checkbox compliance-wide"><input type="checkbox" checked={facts.coverage_confirmed} onChange={(e) => change('coverage_confirmed', e.target.checked)} />{isAmountApproval ? 'I have reviewed the approval source document and confirmed the supplier, amount, currency, validity period, and outcome' : 'I have reviewed the evidence source document and confirmed the supplier, product scope, and outcome entered above'}</label>
      <button className="button button-submit" disabled={!ready || Boolean(localError) || parseFile.isPending} type="submit">{save.isPending ? 'Saving…' : target.runAfterSave
        ? isAmountApproval ? 'Save approval record and rerun checks' : 'Save evidence and rerun checks'
        : isAmountApproval ? 'Save approval record' : 'Save evidence'}</button>
    </fieldset></form>
    {(stale || localError || save.isError) && <p role="alert" className="form-error">{stale ? 'The task revision has changed. Close and reopen the form.' : localError || errorMessage(save.error)}</p>}
  </dialog>
}

function Confirmation({ workspace, onRefresh }: { workspace: ComplianceWorkspace; onRefresh: () => Promise<void> }) {
  const navigate = useNavigate()
  const [acknowledged, setAcknowledged] = useState<string[]>([])
  const [noPolicy, setNoPolicy] = useState(false)
  const [idempotencyKey, setIdempotencyKey] = useState(createIdempotencyKey)
  const assessment = workspace.assessment
  const missing = assessment?.missing_item_ids ?? []
  const missingSet = new Set(missing)
  const groupedMissing = [...(assessment?.assessments ?? []).reduce((groups, supplier) => {
    supplier.checks.filter((check) => check.item_id && missingSet.has(check.item_id)).forEach((check) => {
      const key = `${supplier.quote_id}:${check.control_code}`
      const existing = groups.get(key)
      if (existing) existing.itemIds.push(check.item_id!)
      else groups.set(key, {
        key,
        supplier: supplier.supplier_name ?? supplier.supplier_id ?? supplier.quote_id,
        control: check.control_code,
        status: check.status,
        stage: check.execution_stage,
        itemIds: [check.item_id!],
      })
    })
    return groups
  }, new Map<string, { key: string; supplier: string; control: string; status: string; stage?: string; itemIds: string[] }>()).values()]
  const represented = new Set(groupedMissing.flatMap((row) => row.itemIds))
  missing.filter((id) => !represented.has(id)).forEach((id) => groupedMissing.push({
    key: id, supplier: 'Current task', control: 'Pending confirmation item', status: 'REVIEW_REQUIRED', itemIds: [id],
  }))
  const confirm = useMutation({ mutationFn: () => api.confirmCompliance(workspace.task_id, {
    expected_task_revision: workspace.task_revision, expected_assessment_id: workspace.stage.assessment_id ?? assessment!.assessment_id!,
    acknowledged_missing_item_ids: acknowledged, acknowledge_no_policy: noPolicy,
  }, idempotencyKey), onSuccess: async (response) => {
    await onRefresh(); navigate(`/tasks/${workspace.task_id}/decision`, { state: { expectedRevision: response.task_revision } })
  }, onError: async (error) => { if (error instanceof ApiClientError && error.status === 409) await onRefresh() } })
  const errors = uniqueErrors(workspace.plan?.policy_errors, assessment?.policy_errors)
  const allAcknowledged = missing.length > 0 && missing.every((id) => acknowledged.includes(id))
  const partlyAcknowledged = !allAcknowledged && missing.some((id) => acknowledged.includes(id))
  const disabled = !assessment || !workspace.stage.can_confirm || errors.length > 0
    || missing.some((id) => !acknowledged.includes(id)) || (!assessment.policy_enabled && !noPolicy)
  if (workspace.stage.can_compare) return <section className="card compliance-confirmation compliance-complete-bar"><div className="compliance-complete-copy"><span className="compliance-complete-icon" aria-hidden="true">✓</span><div><h3>Review complete</h3><p>Saved for this revision. New or replaced evidence requires another review.</p></div></div><Link className="button button-submit" to={`/tasks/${workspace.task_id}/decision`}>Continue</Link></section>
  return <section className="card compliance-confirmation"><div className="section-heading"><div><h3>Confirm processing outcome</h3><p>Suppliers with missing evidence remain unverified.</p></div><span>{groupedMissing.length} pending categories</span></div>
    {groupedMissing.length > 0 && <div className="compliance-table-scroll"><table aria-label="Items requiring additional evidence" className="supplier-data-table compliance-pending-table"><thead><tr><th><label className="compliance-select-all"><input aria-label="Select all items requiring additional evidence" type="checkbox" disabled={confirm.isPending} checked={allAcknowledged} ref={(element) => { if (element) element.indeterminate = partlyAcknowledged }} onChange={(event) => {
      setAcknowledged(event.target.checked ? [...missing] : [])
      setIdempotencyKey(createIdempotencyKey())
    }} />Select all</label></th><th>Supplier</th><th>Missing item</th><th>Current status</th><th>Handling</th></tr></thead><tbody>
      {groupedMissing.map((row) => {
        const checked = row.itemIds.every((id) => acknowledged.includes(id))
        return <tr key={row.key}><td><input aria-label={`Defer ${row.supplier} ${controlLabel(row.control)}`} type="checkbox" disabled={confirm.isPending} checked={checked} onChange={(event) => {
          setAcknowledged((current) => event.target.checked
            ? [...new Set([...current, ...row.itemIds])]
            : current.filter((id) => !row.itemIds.includes(id)))
          setIdempotencyKey(createIdempotencyKey())
        }} /></td><td><strong>{row.supplier}</strong></td><td>{controlLabel(row.control)}{row.itemIds.length > 1 && <small>{row.itemIds.length} similar policy requirements combined</small>}</td><td><StateBadge status={row.status} label={compactCheckStatusLabel(row.status as Parameters<typeof compactCheckStatusLabel>[0])} /></td><td><span className="compliance-defer-label">Deferred</span>{row.stage && <small>{executionStageLabel(row.stage)}</small>}</td></tr>
      })}
    </tbody></table></div>}
    {assessment && !assessment.policy_enabled && <label className="compliance-checkbox compliance-no-policy"><input type="checkbox" disabled={confirm.isPending} checked={noPolicy} onChange={(e) => { setNoPolicy(e.target.checked); setIdempotencyKey(createIdempotencyKey()) }} />Confirm that compliance review is disabled for this task and later results are for procurement comparison only</label>}
    <div className="compliance-confirm-actions"><button className="button button-submit" type="button" disabled={disabled || confirm.isPending} onClick={() => confirm.mutate()}>{confirm.isPending ? 'Confirming…' : 'Confirm'}</button></div>
    {confirm.isError && <p role="alert" className="form-error">{errorMessage(confirm.error)}</p>}
  </section>
}

export function CompliancePage() {
  const { taskId = '' } = useParams()
  const location = useLocation()
  const navigate = useNavigate()
  const client = useQueryClient()
  const [target, setTarget] = useState<EvidenceTarget | null>(null)
  const [expandedQuotes, setExpandedQuotes] = useState<string[]>([])
  const [expandedPrecheckQuote, setExpandedPrecheckQuote] = useState<string | null>(null)
  const task = useQuery({ queryKey: ['tasks', taskId], queryFn: () => api.getTask(taskId), enabled: Boolean(taskId),
    refetchInterval: (query) => ['QUEUED', 'RUNNING', 'PROCESSING'].includes(query.state.data?.status ?? '') ? 1500 : false })
  const workspace = useQuery({ queryKey: ['tasks', taskId, 'compliance'], queryFn: () => api.getCompliance(taskId), enabled: Boolean(taskId),
    refetchInterval: (query) => query.state.data?.stage.status === 'PROCESSING'
      || ['QUEUED', 'RUNNING', 'PROCESSING'].includes(task.data?.status ?? '') ? 1500 : false })
  const suppliers = workspace.data?.assessment?.assessments ?? []
  const fragment = new URLSearchParams(location.hash.slice(1))
  const linkedQuote = fragment.get('quote')
  const linkedCheck = fragment.get('check')
  const linkedIndex = suppliers.findIndex((supplier) => supplier.quote_id === linkedQuote)
  const pagination = useTablePagination(suppliers, 8, linkedIndex >= 0 ? Math.floor(linkedIndex / 8) : undefined)
  useEffect(() => {
    if (!linkedQuote || linkedIndex < 0) return
    const element = document.getElementById(complianceAnchorId(linkedQuote, linkedCheck ?? undefined))
      ?? document.getElementById(complianceAnchorId(linkedQuote))
    element?.scrollIntoView?.({ block: 'center' })
    element?.focus({ preventScroll: true })
  }, [linkedQuote, linkedCheck, linkedIndex, workspace.data?.stage.assessment_id])
  const refresh = async () => { await client.invalidateQueries({ queryKey: ['tasks', taskId] }) }
  const start = useMutation({ mutationFn: () => api.startRun(taskId, task.data!.task_revision, createIdempotencyKey()), onSuccess: refresh })
  if (task.isPending || workspace.isPending) return <ComplianceLoading label="Loading compliance review" detail="Retrieving the current task, policy revision, and existing verification results…" />
  if (task.isError || workspace.isError) return <section className="card error-panel" role="alert">{errorMessage(task.error ?? workspace.error)}<button className="button button-secondary" onClick={() => void refresh()}>Refresh and retry</button></section>
  const data = workspace.data
  const clauses = data.plan?.clauses ?? []
  const errors = uniqueErrors(data.plan?.policy_errors, data.assessment?.policy_errors)
  const currentEvidence = (quoteId: string, control: ComplianceEvidenceFacts['control_code']) =>
    data.evidence.find((record) => record.quote_id === quoteId && record.control_code === control && !record.superseded)
  const activeEvidence = data.evidence.filter((record) => !record.superseded)
  const precheckSuppliers: PolicyComplianceSupplierAssessment[] = task.data.quotes.map((quote) => ({
    quote_id: quote.quote_id,
    quote_version: quote.quote_version,
    supplier_id: quote.supplier_id,
    supplier_name: quote.supplier_id,
    status: 'NOT_EVALUATED',
    eligibility: 'UNVERIFIED',
    checks: [],
  }))
  const readonly = task.data.status === 'ABANDONED'
  const failed = task.data.status === 'FAILED' && task.data.current_job?.job_status === 'FAILED'
  const counts = data.assessment?.counts
  const amountRequirements = uniqueAmountRequirements(data.assessment?.amount_requirements)
  const policyCategory = data.policy_binding?.category === 'Electronics' ? 'Electronics procurement' : data.policy_binding?.category
  const policyRegion = data.policy_binding?.region === 'SG' ? 'Singapore' : data.policy_binding?.region
  const policySummary = data.policy_binding
    ? [data.policy_binding.policy_set_version && `Policy revision v${data.policy_binding.policy_set_version}`, policyCategory, policyRegion].filter(Boolean).join(' · ')
    : 'Compliance review is not enabled for this task'
  return <div className="page-stack compliance-page">
    <TaskWorkspaceHeader taskId={taskId} scenarioId={task.data.scenario_id} title={task.data.task_name} subtitle={`${task.data.requirement.required_quantity} ${task.data.requirement.quantity_unit} · ${task.data.quotes.length} quotations`} status={task.data.status} revision={data.task_revision} resultId={task.data.current_result_id} quoteCount={task.data.quotes.length} summaryComplete={task.data.summary_completed} progress={{ ...task.data.progress, compliance: data.stage }} active="compliance" />
    <section className="card compliance-context workspace-page-lead">
      <div className="compliance-context-heading workspace-page-lead-copy"><h2>Compliance</h2><p>{policySummary}</p></div>
      {data.assessment && <div className="compliance-kpis" aria-label="Compliance overview">
        <div><strong>{counts?.COMPLIANT ?? 0}</strong><span>Verified</span></div>
        <div><strong>{counts?.REVIEW_REQUIRED ?? 0}</strong><span>Pending</span></div>
        <div><strong>{counts?.NON_COMPLIANT ?? 0}</strong><span>Excluded</span></div>
        <div><strong>{counts?.NOT_EVALUATED ?? 0}</strong><span>Unchecked</span></div>
      </div>}
      <div className="compliance-context-tags"><StateBadge status={data.stage.status} label={compactComplianceStatusLabel(data.stage.status)} />{Boolean(data.stage.pending_count) && <span>{data.stage.pending_count} {data.stage.pending_count === 1 ? 'item' : 'items'}</span>}</div>
    </section>
    {data.legacy_result && <p className="run-notice">This task uses a legacy workflow, and its historical result has not been confirmed at this stage. <Link to={`/tasks/${taskId}/audit`}>View history</Link></p>}
    {!data.policy_binding && <section className="card"><h3>Compliance review is not enabled for this task</h3><p>Explicitly confirm this below before continuing. The system does not interpret disabled review as supplier compliance.</p></section>}
    {errors.length > 0 && <section className="card error-panel" role="alert"><h3>Policy basis requires attention</h3><ul>{errors.map((error, index) => <li key={index}>{errorText(error)}</li>)}</ul><p>Review the policy clauses or retrieval basis first; confirmation is currently unavailable.</p><Link to="/resources">View policy resources</Link></section>}
    {data.assessment?.publication_blocked && <p className="run-notice">Some pre-publication conditions remain unmet. Confirming the processing record will not remove these conditions, and the decision result will retain the corresponding restrictions.</p>}
    {!data.assessment && data.stage.status === 'PROCESSING' && <ComplianceLoading label="Running compliance review" detail="Retrieving applicable clauses and checking supplier evidence and amount conditions for the current revision…" />}
    {!data.assessment && data.stage.status !== 'PROCESSING' && (failed || !task.data.progress.quote_review_completed) && <section className={`card ${failed ? 'error-panel' : ''}`} role={failed ? 'alert' : undefined}><h3>{failed ? 'Compliance review incomplete' : 'Waiting for quotation processing to complete'}</h3><p>{failed ? task.data.current_job?.error_message ?? 'The background job failed. Restart the compliance review.' : 'Complete quotation review before preparing evidence.'}</p><Link to={`/tasks/${taskId}/quotes/new`}>View quotations and review</Link></section>}
    {!readonly && data.policy_binding && !data.assessment && data.stage.status === 'NOT_STARTED' && task.data.progress.quote_review_completed && <section className="card compliance-precheck-card">
      <div className="section-heading"><div><p className="eyebrow">BEFORE CHECK</p><h3>Prepare evidence before checking</h3><p>Prepare available evidence for each supplier. Missing items may be skipped; the system will identify gaps after checking.</p></div><span>{activeEvidence.length} / {precheckSuppliers.length * PRECHECK_CONTROLS.length} prepared</span></div>
      <div className="compliance-precheck-list">{precheckSuppliers.map((supplier) => {
        const records = PRECHECK_CONTROLS.map((control) => ({ control, record: currentEvidence(supplier.quote_id, control) }))
        const completed = records.filter(({ record }) => Boolean(record)).length
        const expanded = expandedPrecheckQuote === supplier.quote_id
        const panelId = `precheck-${supplier.quote_id}`
        return <article key={supplier.quote_id} className={`compliance-precheck-supplier${expanded ? ' is-expanded' : ''}`}>
          <button className="compliance-precheck-summary" type="button" aria-expanded={expanded} aria-controls={panelId} aria-label={`${expanded ? 'Collapse' : 'Prepare'} evidence for ${supplier.supplier_name}`} onClick={() => setExpandedPrecheckQuote(expanded ? null : supplier.quote_id)}>
            <span className="compliance-precheck-name"><strong>{supplier.supplier_name}</strong><small>Quotation revision {supplier.quote_version}</small></span>
            <span className="compliance-precheck-chips" aria-label={`${supplier.supplier_name} evidence status`}>{records.map(({ control, record }) => <span className={record ? 'is-ready' : ''} key={control}>{control === 'APPROVED_SUPPLIER' ? 'Eligibility' : control === 'ROHS_COMPLIANCE' ? 'RoHS' : 'Approval'} {record ? '✓' : '—'}</span>)}</span>
            <span className="compliance-precheck-progress"><strong>{completed}/{PRECHECK_CONTROLS.length}</strong><small>Prepared</small></span>
            <span className="compliance-precheck-action">{expanded ? 'Collapse' : 'Prepare evidence'}</span>
          </button>
          {expanded && <div className="compliance-precheck-controls" id={panelId}>{records.map(({ control, record }) => <div className="compliance-precheck-control" key={control}>
            <span><strong>{evidenceMaterialLabel(control)}</strong><small>{record ? `Uploaded: ${record.facts.material_number}` : 'Not uploaded'}</small></span>
            <button className="button button-secondary" type="button" aria-label={`${record ? 'Replace' : 'Upload'} ${evidenceMaterialLabel(control)} · ${supplier.supplier_name}`} onClick={() => setTarget({ supplier, control, record, revision: data.task_revision, runAfterSave: false })}>{record ? 'Replace' : 'Upload'}</button>
          </div>)}</div>}
        </article>
      })}</div>
    </section>}
    {!readonly && data.stage.status === 'NOT_STARTED' && task.data.progress.quote_review_completed && <section className="card compliance-start-card"><div><h3>Start compliance review</h3><p>{activeEvidence.length > 0 ? `The ${activeEvidence.length} uploaded evidence records will be used to retrieve applicable rules and run all checks together.` : 'No evidence has been uploaded. You can still start the review and fill the identified gaps afterwards.'}</p></div><button className="button button-submit" disabled={start.isPending || ['QUEUED', 'RUNNING', 'PROCESSING'].includes(task.data.status)} onClick={() => start.mutate()}>{start.isPending ? 'Starting…' : failed ? 'Restart' : 'Start'}</button>{start.isError && <p role="alert">{errorMessage(start.error)}</p>}</section>}
    {suppliers.length > 0 && <section className="card compliance-results-card"><div className="section-heading"><div><h3>Checks</h3></div><span>{suppliers.length} suppliers</span></div>
      <div className="compliance-table-scroll"><table aria-label="Supplier checks" className="supplier-data-table compliance-table"><thead><tr><th>Supplier</th><th>Eligibility</th><th>RoHS</th><th>Status</th><th>Recommendation</th><th>Evidence</th></tr></thead><tbody>
        {pagination.pageItems.map((supplier) => { const admission = aggregateControlStatus(supplier.checks, 'APPROVED_SUPPLIER'); const rohs = aggregateControlStatus(supplier.checks, 'ROHS_COMPLIANCE'); const supplierName = supplier.supplier_name ?? supplier.supplier_id ?? supplier.quote_id; const expanded = supplier.quote_id === linkedQuote || expandedQuotes.includes(supplier.quote_id); const supplierEvidence = data.evidence.filter((record) => record.quote_id === supplier.quote_id); return <Fragment key={supplier.quote_id}><tr id={complianceAnchorId(supplier.quote_id)} tabIndex={-1}><td><strong>{supplierName}</strong><small>Revision {supplier.quote_version}</small></td><td><StateBadge status={admission} label={compactCheckStatusLabel(admission)} /></td><td><StateBadge status={rohs} label={compactCheckStatusLabel(rohs)} /></td><td><StateBadge status={supplier.status} label={compactComplianceStatusLabel(supplier.status)} /></td><td><StateBadge status={supplier.eligibility ?? 'UNVERIFIED'} label={compactComplianceStatusLabel(supplier.eligibility ?? 'UNVERIFIED')} /></td><td>
          {!readonly && errors.length > 0 && <small>Policy rules require administrator attention; adding evidence cannot currently remove the block.</small>}
          <button type="button" className="compliance-details-toggle" aria-expanded={expanded} aria-label={`${expanded ? 'Collapse' : 'Expand'} ${supplierName} Checks and Evidence`} onClick={() => {
            navigate({ pathname: location.pathname, hash: '' }, { replace: true })
            setExpandedQuotes((current) => expanded ? current.filter((quoteId) => quoteId !== supplier.quote_id) : [...current, supplier.quote_id])
          }}>{expanded ? 'Hide' : 'Details'} ({supplier.checks.length + supplierEvidence.length})</button>
        </td></tr>{expanded && <tr className="compliance-detail-row"><td colSpan={6}><SupplierCheckDetails supplier={supplier} clauses={clauses} evidence={supplierEvidence} taskId={taskId} linkedCheck={linkedCheck} readonly={readonly} onEdit={(control, record) => setTarget({ supplier, control, record, revision: data.task_revision, runAfterSave: true })} /></td></tr>}</Fragment>})}
      </tbody></table></div><TablePagination page={pagination.page} pageSize={pagination.pageSize} pageCount={pagination.pageCount} total={suppliers.length} onPageChange={(page) => { navigate({ pathname: location.pathname, hash: '' }, { replace: true }); pagination.setPage(page) }} />
    </section>}
    {target && !readonly && <EvidenceEditor key={`${target.supplier.quote_id}:${target.control}:${target.record?.evidence_id ?? 'new'}:${target.revision}`} task={task.data} target={target} onClose={() => setTarget(null)} onSaved={refresh} />}
    {amountRequirements.length > 0 && <section className="card compliance-amount-card"><div className="section-heading"><div><h3>Amount conditions and approval records</h3></div><span>{amountRequirements.length} conditions</span></div><div className="compliance-table-scroll"><table aria-label="Amount conditions and next actions" className="supplier-data-table compliance-amount-table"><thead><tr><th>Supplier</th><th>Execution stage</th><th>Current amount / threshold</th><th>Assessment</th><th>Next action</th></tr></thead><tbody>{amountRequirements.map((item, index) => {
      const supplier = suppliers.find((candidate) => candidate.quote_id === item.quote_id)
      const supplierName = item.supplier_name ?? supplier?.supplier_name ?? 'Supplier identity requires review'
      const record = item.quote_id ? currentEvidence(item.quote_id, 'AMOUNT_APPROVAL') : undefined
      const deferred = item.execution_stage === 'AFTER_SELECTION'
      const outcome = item.approval_confirmed ? 'Verified' : item.triggered === true ? (deferred ? 'Triggered' : 'Review') : item.triggered === false ? 'Clear' : 'Pending'
      return <tr key={`${item.quote_id}:${item.execution_stage}:${item.threshold}:${item.action}:${index}`}><td><strong>{supplierName}</strong></td><td>{executionStageLabel(item.execution_stage)}</td><td>{item.amount && item.currency ? `${item.currency} ${item.amount}` : 'Amount pending confirmation'}{item.threshold && <small>Threshold: {item.currency ?? ''} {item.threshold}</small>}</td><td><StateBadge status={item.approval_confirmed ? 'PASS' : item.triggered === true ? 'REVIEW_REQUIRED' : item.triggered === false ? 'PASS' : 'NOT_EVALUATED'} label={outcome} />{item.triggered !== false && (item.reason_codes ?? []).map((code) => <small key={code}>{complianceReasonLabel(code)}</small>)}</td><td>{item.triggered ? item.action ?? 'Follow policy procedure' : '—'}{!readonly && errors.length === 0 && item.triggered === true && supplier && <button className="button button-secondary compliance-add" type="button" onClick={() => setTarget({ supplier, control: 'AMOUNT_APPROVAL', record, revision: data.task_revision, runAfterSave: true })}>{record ? 'Replace' : 'Add'}</button>}</td></tr>
    })}</tbody></table></div></section>}
    {!readonly && data.assessment && <Confirmation key={`${data.task_revision}:${data.stage.assessment_id}`} workspace={data} onRefresh={refresh} />}
  </div>
}
