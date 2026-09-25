import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Fragment, type FormEvent, useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { api, ApiClientError, complianceEvidenceUrl, createIdempotencyKey } from '../api/client'
import type { ComplianceClause, ComplianceEvidenceFacts, ComplianceEvidenceRecord, ComplianceWorkspace, PolicyComplianceSupplierAssessment, TaskDetail } from '../api/types'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'
import { TablePagination } from '../components/TablePagination'
import { useTablePagination } from '../hooks/useTablePagination'
import { controlLabel } from '../lib/presentation'
import { aggregateControlStatus, checkStatusLabel, complianceAnchorId, complianceReasonLabel, complianceStatusLabel, executionStageLabel, groupComplianceChecks } from '../lib/compliance'

function errorMessage(error: unknown) {
  if (error instanceof ApiClientError && (error.status === 409 || error.code.includes('revision'))) return '任务或检查版本已变化，请刷新后重新核对。未保存的填写仍保留。'
  return error instanceof Error ? error.message : '制度检查操作失败。'
}
function errorText(error: string | Record<string, unknown>) {
  return typeof error === 'string' ? complianceReasonLabel(error) : String(error.message ?? error.code ?? '制度依据不完整，请联系制度管理员。')
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
  return control === 'APPROVED_SUPPLIER' ? '供应商准入记录'
    : control === 'ROHS_COMPLIANCE' ? 'RoHS 证明'
      : '金额审批记录'
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
    <div className="compliance-detail-heading"><div><strong>检查与材料</strong><span>{groups.length} 类检查 · {supplier.checks.length} 条制度要求 · {evidence.length} 份材料</span></div><StateBadge status={supplier.status} label={complianceStatusLabel(supplier.status)} /></div>
    <div className="compliance-control-grid">
      {groups.map(([control, checks]) => {
        const status = aggregateControlStatus(checks, control)
        const reasons = [...new Set(checks.flatMap((check) => check.reason_codes ?? [check.reason_code ?? '']).filter(Boolean))]
        const records = evidence.filter((record) => record.control_code === control)
        const currentRecord = records.find((record) => !record.superseded)
        const linked = checks.some((check) => check.clause_id === linkedCheck)
        return <article className={`compliance-control-card compliance-control-${stateTone(status)}`} key={control}>
          <header><span className="compliance-control-icon" aria-hidden="true">{checkIcon(status)}</span><div><small>制度检查</small><h4>{controlLabel(control)}</h4></div><StateBadge status={status} label={checkStatusLabel(status)} /></header>
          {reasons.length > 0 && <ul className="compliance-control-reasons">{reasons.map((reason) => <li key={reason}>{complianceReasonLabel(reason)}</li>)}</ul>}
          <details className="compliance-source-details" open={linked || undefined}><summary>查看制度依据（{checks.length}）</summary>
            <div className="compliance-source-list">{checks.map((check, index) => {
              const matchingClauses = clauses.filter((clause) => clause.clause_id === check.clause_id)
              return <div className="compliance-source-row" id={complianceAnchorId(supplier.quote_id, check.clause_id)} tabIndex={-1} key={check.clause_id ?? index}>
                <div><strong>{checkStatusLabel(check.status)}</strong>{check.execution_stage && <small>{executionStageLabel(check.execution_stage)}</small>}</div>
                {matchingClauses.length > 0 ? matchingClauses.map((clause) => <details key={clause.clause_id}><summary>{clause.title ?? clause.section ?? '制度原文'} · 版本 {clause.document_version}</summary><blockquote>{clause.text}</blockquote><small>{clause.document_id} · {clause.clause_id}</small></details>) : <span>未关联可展示的制度原文</span>}
              </div>
            })}</div>
          </details>
          {records.length > 0 && <details className="compliance-source-details"><summary>查看已提交材料（{records.length}）</summary><div className="compliance-material-list">{records.map((record) => <article key={record.evidence_id}><strong>{record.facts.material_number} · 第 {record.version} 版{record.superseded ? '（已替换）' : ''}</strong><p>{record.facts.supplier_id} · {record.facts.manufacturer} · {record.facts.manufacturer_part_number}</p><p>有效期：{record.facts.effective_from ?? '未说明'} 至 {record.facts.permanent ? '明确永久有效' : record.facts.expires_on ?? '未说明'}</p><p>核对人：{record.confirmed_by} · {record.confirmed_at}</p>{record.facts.source_refs.map((ref) => <p key={ref}>{ref}</p>)}{record.files.map((file) => <a className="compliance-file" key={file.file_id} href={complianceEvidenceUrl(taskId, record.evidence_id, file.file_id)}>{file.original_filename}</a>)}</article>)}</div></details>}
          {!readonly && ['APPROVED_SUPPLIER', 'ROHS_COMPLIANCE'].includes(control) && <div className="compliance-control-action"><button className="button button-secondary" type="button" onClick={() => onEdit(control as ComplianceEvidenceFacts['control_code'], currentRecord)}>{currentRecord ? '替换材料' : '补充材料'}</button></div>}
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
        setParseMessage(`文件类型不匹配：这是${response.detected_control_code ? evidenceMaterialLabel(response.detected_control_code) : '其他类型材料'}，请关闭后从对应入口上传。`)
        return
      }
      setFacts((current) => ({ ...current, ...response.facts, coverage_confirmed: false }))
      const parsedSupplier = typeof response.facts.supplier_id === 'string' ? response.facts.supplier_id : null
      const expectedSupplier = target.supplier.supplier_id ?? null
      const parsedPart = typeof response.facts.manufacturer_part_number === 'string' ? response.facts.manufacturer_part_number : null
      const messages = [response.status === 'FOUND'
        ? `已自动解析并回填 ${response.parsed_fields.length} 个字段。`
        : `已回填 ${response.parsed_fields.length} 个字段；其余字段请按原文核对。`]
      if (parsedSupplier && expectedSupplier && parsedSupplier !== expectedSupplier) {
        messages.push(`注意：文件供应商编号 ${parsedSupplier} 与当前报价 ${expectedSupplier} 不一致。`)
      }
      if (parsedPart && task.requirement.manufacturer_part_number && parsedPart !== task.requirement.manufacturer_part_number) {
        messages.push(`注意：文件料号 ${parsedPart} 与采购料号 ${task.requirement.manufacturer_part_number} 不一致。`)
      }
      setParseMessage(messages.join(' '))
      setIdempotencyKey(createIdempotencyKey())
    },
    onError: (error) => setParseMessage(`未能自动解析：${errorMessage(error)} 你仍可手动填写后保存。`),
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
    if (facts.effective_from && facts.expires_on && facts.effective_from >= facts.expires_on) return setLocalError('失效日期须晚于生效日期。')
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
    <div className="section-heading"><div><h3 id="compliance-editor-title">{isAmountApproval ? target.record ? '替换金额审批记录并保留旧版本' : '补充金额审批记录' : target.record ? `替换材料：${evidenceMaterialLabel(target.control)}` : `补充材料：${evidenceMaterialLabel(target.control)}`} · {target.supplier.supplier_name}</h3><p>{evidenceMaterialLabel(target.control)} · 第 {target.supplier.quote_version} 版报价</p></div><button type="button" className="button button-secondary" disabled={save.isPending} onClick={onClose}>取消</button></div>
    <p id="compliance-editor-description">{target.runAfterSave
      ? isAmountApproval ? '请按审批原件核对审批对象、批准金额、币种和有效期；保存后将重新检查。' : '请按材料原文核对供应商和产品范围；保存后将重新检查。'
      : '保存后可继续上传其他已有材料；系统会在你点击“开始制度检查”后统一核验。'}</p>
    <form onSubmit={submit}><fieldset disabled={save.isPending || stale} className="compliance-form-grid">
      <label className="field">{isAmountApproval ? '审批记录编号' : '材料编号'}<input required value={facts.material_number} onChange={(e) => change('material_number', e.target.value)} /></label>
      <label className="field">{isAmountApproval ? '审批对象供应商编号' : '材料供应商编号'}<input required value={facts.supplier_id} onChange={(e) => change('supplier_id', e.target.value)} /></label>
      {!isAmountApproval && <label className="field">材料制造商<input required={target.control === 'ROHS_COMPLIANCE'} value={facts.manufacturer ?? ''} onChange={(e) => change('manufacturer', e.target.value)} /></label>}
      {!isAmountApproval && <label className="field">材料制造商料号<input required={target.control === 'ROHS_COMPLIANCE'} value={facts.manufacturer_part_number ?? ''} onChange={(e) => change('manufacturer_part_number', e.target.value)} /></label>}
      {isAmountApproval && <label className="field">批准金额<input required inputMode="decimal" value={facts.approval_amount ?? ''} onChange={(e) => change('approval_amount', e.target.value || null)} /></label>}
      {isAmountApproval && <label className="field">币种<input required maxLength={3} value={facts.currency ?? ''} onChange={(e) => change('currency', e.target.value.toUpperCase())} /></label>}
      <label className="field">{isAmountApproval ? '审批结论' : '材料结论'}<select value={facts.outcome} onChange={(e) => change('outcome', e.target.value as 'PASS' | 'FAIL')}><option value="PASS">{isAmountApproval ? '已批准' : '材料表明符合'}</option><option value="FAIL">{isAmountApproval ? '未批准/已拒绝' : '材料表明不符合'}</option></select></label>
      <label className="field">生效日期<input type="date" value={facts.effective_from ?? ''} onChange={(e) => change('effective_from', e.target.value || null)} /></label>
      <label className="field">失效日期<input type="date" disabled={facts.permanent} value={facts.expires_on ?? ''} onChange={(e) => change('expires_on', e.target.value || null)} /></label>
      <label className="compliance-checkbox"><input type="checkbox" checked={facts.permanent} onChange={(e) => { setFacts((current) => ({ ...current, permanent: e.target.checked, expires_on: e.target.checked ? null : current.expires_on })); setIdempotencyKey(createIdempotencyKey()); setLocalError('') }} />材料明确注明永久有效</label>
      <label className="field compliance-wide">来源引用<textarea rows={2} placeholder="原始记录编号或来源链接，每行一条；也可上传附件" value={facts.source_refs.join('\n')} onChange={(e) => change('source_refs', e.target.value.split('\n'))} /></label>
      <label className="field compliance-wide">材料附件（PDF、TXT、MD，最多 10 MiB）<input type="file" accept=".pdf,.txt,.md" onChange={(e) => {
        const selected = e.target.files?.[0] ?? null
        if (selected && (!/\.(pdf|txt|md)$/i.test(selected.name) || selected.size > 10 * 1024 * 1024 || selected.size === 0)) {
          setLocalError('请选择非空 PDF、TXT 或 MD，文件大小不超过 10 MiB。'); setFile(null); return
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
      }} /></label>
      {(parseFile.isPending || parseMessage) && <p className="compliance-wide compliance-parse-status" role="status" aria-live="polite">{parseFile.isPending ? '正在自动解析证明文件…' : parseMessage}</p>}
      <label className="field compliance-wide">核对说明<textarea rows={2} value={facts.note} onChange={(e) => change('note', e.target.value)} /></label>
      <label className="compliance-checkbox compliance-wide"><input type="checkbox" checked={facts.coverage_confirmed} onChange={(e) => change('coverage_confirmed', e.target.checked)} />{isAmountApproval ? '我已核对审批原件，并确认审批对象、金额、币种、有效期和结论' : '我已核对材料原文，并确认所填写的供应商、产品范围和结论'}</label>
      <button className="button button-submit" disabled={!ready || Boolean(localError) || parseFile.isPending} type="submit">{save.isPending ? '正在保存…' : target.runAfterSave
        ? isAmountApproval ? '保存审批记录并重新检查' : '保存材料并重新检查'
        : isAmountApproval ? '保存审批记录' : '保存材料'}</button>
    </fieldset></form>
    {(stale || localError || save.isError) && <p role="alert" className="form-error">{stale ? '任务版本已变化，请关闭表单后重新打开。' : localError || errorMessage(save.error)}</p>}
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
    key: id, supplier: '当前任务', control: '待确认事项', status: 'REVIEW_REQUIRED', itemIds: [id],
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
  if (workspace.stage.can_compare) return <section className="card compliance-confirmation"><h3>制度检查已处理</h3><p>已保存本次处理记录。补充或替换材料后需要重新检查与确认。</p><Link className="button button-submit" to={`/tasks/${workspace.task_id}/decision`}>继续决策比较</Link></section>
  return <section className="card compliance-confirmation"><div className="section-heading"><div><h3>确认本次处理结果</h3><p>缺少材料的供应商仍保持“未核验”。</p></div><span>{groupedMissing.length} 类待处理</span></div>
    {groupedMissing.length > 0 && <div className="compliance-table-scroll"><table aria-label="待补充事项" className="supplier-data-table compliance-pending-table"><thead><tr><th><label className="compliance-select-all"><input aria-label="全选待补充事项" type="checkbox" disabled={confirm.isPending} checked={allAcknowledged} ref={(element) => { if (element) element.indeterminate = partlyAcknowledged }} onChange={(event) => {
      setAcknowledged(event.target.checked ? [...missing] : [])
      setIdempotencyKey(createIdempotencyKey())
    }} />全选</label></th><th>供应商</th><th>待补充事项</th><th>当前状态</th><th>处理方式</th></tr></thead><tbody>
      {groupedMissing.map((row) => {
        const checked = row.itemIds.every((id) => acknowledged.includes(id))
        return <tr key={row.key}><td><input aria-label={`暂不补充 ${row.supplier} ${controlLabel(row.control)}`} type="checkbox" disabled={confirm.isPending} checked={checked} onChange={(event) => {
          setAcknowledged((current) => event.target.checked
            ? [...new Set([...current, ...row.itemIds])]
            : current.filter((id) => !row.itemIds.includes(id)))
          setIdempotencyKey(createIdempotencyKey())
        }} /></td><td><strong>{row.supplier}</strong></td><td>{controlLabel(row.control)}{row.itemIds.length > 1 && <small>合并 {row.itemIds.length} 条同类制度要求</small>}</td><td><StateBadge status={row.status} label={checkStatusLabel(row.status as Parameters<typeof checkStatusLabel>[0])} /></td><td><span className="compliance-defer-label">暂不补充，保留未核验状态</span>{row.stage && <small>{executionStageLabel(row.stage)}</small>}</td></tr>
      })}
    </tbody></table></div>}
    {assessment && !assessment.policy_enabled && <label className="compliance-checkbox compliance-no-policy"><input type="checkbox" disabled={confirm.isPending} checked={noPolicy} onChange={(e) => { setNoPolicy(e.target.checked); setIdempotencyKey(createIdempotencyKey()) }} />确认本任务不启用制度检查，后续结果仅用于采购比较</label>}
    <div className="compliance-confirm-actions"><button className="button button-submit" type="button" disabled={disabled || confirm.isPending} onClick={() => confirm.mutate()}>{confirm.isPending ? '正在确认…' : '确认处理结果并进入决策'}</button></div>
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
  if (task.isPending || workspace.isPending) return <ComplianceLoading label="正在读取制度检查" detail="正在获取当前任务、制度版本和已有核验结果…" />
  if (task.isError || workspace.isError) return <section className="card error-panel" role="alert">{errorMessage(task.error ?? workspace.error)}<button className="button button-secondary" onClick={() => void refresh()}>刷新重试</button></section>
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
  const policyCategory = data.policy_binding?.category === 'Electronics' ? '电子产品采购' : data.policy_binding?.category
  const policyRegion = data.policy_binding?.region === 'SG' ? '新加坡' : data.policy_binding?.region
  const policySummary = data.policy_binding
    ? [data.policy_binding.policy_set_version && `规则版本 v${data.policy_binding.policy_set_version}`, policyCategory, policyRegion].filter(Boolean).join(' · ')
    : '本任务未启用制度检查'
  return <div className="page-stack compliance-page">
    <TaskWorkspaceHeader taskId={taskId} scenarioId={task.data.scenario_id} title={task.data.task_name} subtitle={`${task.data.requirement.required_quantity} ${task.data.requirement.quantity_unit} · ${task.data.quotes.length} 份报价`} status={task.data.status} revision={data.task_revision} resultId={task.data.current_result_id} quoteCount={task.data.quotes.length} summaryComplete={task.data.summary_completed} progress={{ ...task.data.progress, compliance: data.stage }} active="compliance" />
    <section className="card compliance-context workspace-page-lead">
      <div className="compliance-context-heading workspace-page-lead-copy"><h2>制度检查</h2><p>{policySummary}</p></div>
      {data.assessment && <div className="compliance-kpis" aria-label="制度检查概览">
        <div><strong>{counts?.COMPLIANT ?? 0}</strong><span>已核验候选</span></div>
        <div><strong>{counts?.REVIEW_REQUIRED ?? 0}</strong><span>待补充或复核</span></div>
        <div><strong>{counts?.NON_COMPLIANT ?? 0}</strong><span>制度排除</span></div>
        <div><strong>{counts?.NOT_EVALUATED ?? 0}</strong><span>尚未核验</span></div>
      </div>}
      <div className="compliance-context-tags"><StateBadge status={data.stage.status} label={complianceStatusLabel(data.stage.status)} />{Boolean(data.stage.pending_count) && <span>{data.stage.pending_count} 个检查项</span>}</div>
    </section>
    {data.legacy_result && <p className="run-notice">这是旧流程任务，历史结果尚未经过本阶段确认。<Link to={`/tasks/${taskId}/audit`}>查看历史记录</Link></p>}
    {!data.policy_binding && <section className="card"><h3>本任务未启用制度检查</h3><p>请在下方明确确认后继续。系统不会将未启用解释为供应商合规。</p></section>}
    {errors.length > 0 && <section className="card error-panel" role="alert"><h3>制度依据需要处理</h3><ul>{errors.map((error, index) => <li key={index}>{errorText(error)}</li>)}</ul><p>请先完成制度条款或检索依据复核，当前不能确认。</p><Link to="/resources">查看制度资源</Link></section>}
    {data.assessment?.publication_blocked && <p className="run-notice">仍有发布前条件未满足。确认处理记录不会解除这些条件，决策结果会保留相应限制。</p>}
    {!data.assessment && data.stage.status === 'PROCESSING' && <ComplianceLoading label="正在进行制度检查" detail="系统正在检索适用条款，并核对当前版本的供应商材料和金额条件…" />}
    {!data.assessment && data.stage.status !== 'PROCESSING' && (failed || !task.data.progress.quote_review_completed) && <section className={`card ${failed ? 'error-panel' : ''}`} role={failed ? 'alert' : undefined}><h3>{failed ? '制度检查未完成' : '等待报价处理完成'}</h3><p>{failed ? task.data.current_job?.error_message ?? '后台处理失败，请重新开始制度检查。' : '请先完成报价审核，再准备证明材料。'}</p><Link to={`/tasks/${taskId}/quotes/new`}>查看报价与审核</Link></section>}
    {!readonly && data.policy_binding && !data.assessment && data.stage.status === 'NOT_STARTED' && task.data.progress.quote_review_completed && <section className="card compliance-precheck-card">
      <div className="section-heading"><div><p className="eyebrow">BEFORE CHECK</p><h3>检查前准备证明材料</h3><p>按供应商准备已有材料；没有的可先跳过，检查后系统会指明缺口。</p></div><span>已准备 {activeEvidence.length} / {precheckSuppliers.length * PRECHECK_CONTROLS.length} 份</span></div>
      <div className="compliance-precheck-list">{precheckSuppliers.map((supplier) => {
        const records = PRECHECK_CONTROLS.map((control) => ({ control, record: currentEvidence(supplier.quote_id, control) }))
        const completed = records.filter(({ record }) => Boolean(record)).length
        const expanded = expandedPrecheckQuote === supplier.quote_id
        const panelId = `precheck-${supplier.quote_id}`
        return <article key={supplier.quote_id} className={`compliance-precheck-supplier${expanded ? ' is-expanded' : ''}`}>
          <button className="compliance-precheck-summary" type="button" aria-expanded={expanded} aria-controls={panelId} aria-label={`${expanded ? '收起' : '准备'} ${supplier.supplier_name} 材料`} onClick={() => setExpandedPrecheckQuote(expanded ? null : supplier.quote_id)}>
            <span className="compliance-precheck-name"><strong>{supplier.supplier_name}</strong><small>第 {supplier.quote_version} 版报价</small></span>
            <span className="compliance-precheck-chips" aria-label={`${supplier.supplier_name} 材料状态`}>{records.map(({ control, record }) => <span className={record ? 'is-ready' : ''} key={control}>{control === 'APPROVED_SUPPLIER' ? '准入' : control === 'ROHS_COMPLIANCE' ? 'RoHS' : '审批'} {record ? '✓' : '—'}</span>)}</span>
            <span className="compliance-precheck-progress"><strong>{completed}/{PRECHECK_CONTROLS.length}</strong><small>已准备</small></span>
            <span className="compliance-precheck-action">{expanded ? '收起' : '准备材料'}</span>
          </button>
          {expanded && <div className="compliance-precheck-controls" id={panelId}>{records.map(({ control, record }) => <div className="compliance-precheck-control" key={control}>
            <span><strong>{evidenceMaterialLabel(control)}</strong><small>{record ? `已上传：${record.facts.material_number}` : '尚未上传'}</small></span>
            <button className="button button-secondary" type="button" aria-label={`${record ? '替换' : '上传'}${evidenceMaterialLabel(control)} · ${supplier.supplier_name}`} onClick={() => setTarget({ supplier, control, record, revision: data.task_revision, runAfterSave: false })}>{record ? '替换' : '上传'}</button>
          </div>)}</div>}
        </article>
      })}</div>
    </section>}
    {!readonly && data.stage.status === 'NOT_STARTED' && task.data.progress.quote_review_completed && <section className="card compliance-start-card"><div><h3>开始制度检查</h3><p>{activeEvidence.length > 0 ? `将使用已上传的 ${activeEvidence.length} 份材料检索适用规则并统一核验。` : '当前没有已上传材料；仍可开始检查，随后按结果补充缺口。'}</p></div><button className="button button-submit" disabled={start.isPending || ['QUEUED', 'RUNNING', 'PROCESSING'].includes(task.data.status)} onClick={() => start.mutate()}>{start.isPending ? '正在启动…' : failed ? '重新开始制度检查' : '开始制度检查'}</button>{start.isError && <p role="alert">{errorMessage(start.error)}</p>}</section>}
    {suppliers.length > 0 && <section className="card compliance-results-card"><div className="section-heading"><div><h3>供应商检查结果</h3></div><span>{suppliers.length} 家供应商</span></div>
      <div className="compliance-table-scroll"><table aria-label="供应商检查结果" className="supplier-data-table compliance-table"><thead><tr><th>供应商</th><th>供应商准入</th><th>RoHS</th><th>制度结论</th><th>推荐资格</th><th>材料与依据</th></tr></thead><tbody>
        {pagination.pageItems.map((supplier) => { const admission = aggregateControlStatus(supplier.checks, 'APPROVED_SUPPLIER'); const rohs = aggregateControlStatus(supplier.checks, 'ROHS_COMPLIANCE'); const supplierName = supplier.supplier_name ?? supplier.supplier_id ?? supplier.quote_id; const expanded = supplier.quote_id === linkedQuote || expandedQuotes.includes(supplier.quote_id); const supplierEvidence = data.evidence.filter((record) => record.quote_id === supplier.quote_id); return <Fragment key={supplier.quote_id}><tr id={complianceAnchorId(supplier.quote_id)} tabIndex={-1}><td><strong>{supplierName}</strong><small>第 {supplier.quote_version} 版报价</small></td><td><StateBadge status={admission} label={checkStatusLabel(admission)} /></td><td><StateBadge status={rohs} label={checkStatusLabel(rohs)} /></td><td><StateBadge status={supplier.status} label={complianceStatusLabel(supplier.status)} /></td><td><StateBadge status={supplier.eligibility ?? 'UNVERIFIED'} label={complianceStatusLabel(supplier.eligibility ?? 'UNVERIFIED')} /></td><td>
          {!readonly && errors.length > 0 && <small>制度规则需管理员处理，当前补充材料无法解除阻塞。</small>}
          <button type="button" className="compliance-details-toggle" aria-expanded={expanded} aria-label={`${expanded ? '收起' : '展开'} ${supplierName} 检查与材料`} onClick={() => {
            navigate({ pathname: location.pathname, hash: '' }, { replace: true })
            setExpandedQuotes((current) => expanded ? current.filter((quoteId) => quoteId !== supplier.quote_id) : [...current, supplier.quote_id])
          }}>{expanded ? '收起' : '展开'}检查与材料（{supplier.checks.length + supplierEvidence.length}）</button>
        </td></tr>{expanded && <tr className="compliance-detail-row"><td colSpan={6}><SupplierCheckDetails supplier={supplier} clauses={clauses} evidence={supplierEvidence} taskId={taskId} linkedCheck={linkedCheck} readonly={readonly} onEdit={(control, record) => setTarget({ supplier, control, record, revision: data.task_revision, runAfterSave: true })} /></td></tr>}</Fragment>})}
      </tbody></table></div><TablePagination page={pagination.page} pageSize={pagination.pageSize} pageCount={pagination.pageCount} total={suppliers.length} onPageChange={(page) => { navigate({ pathname: location.pathname, hash: '' }, { replace: true }); pagination.setPage(page) }} />
    </section>}
    {target && !readonly && <EvidenceEditor key={`${target.supplier.quote_id}:${target.control}:${target.record?.evidence_id ?? 'new'}:${target.revision}`} task={task.data} target={target} onClose={() => setTarget(null)} onSaved={refresh} />}
    {amountRequirements.length > 0 && <section className="card compliance-amount-card"><div className="section-heading"><div><h3>金额条件与审批记录</h3></div><span>{amountRequirements.length} 条条件</span></div><div className="compliance-table-scroll"><table aria-label="金额条件与后续动作" className="supplier-data-table compliance-amount-table"><thead><tr><th>供应商</th><th>执行阶段</th><th>当前金额 / 门槛</th><th>判断</th><th>后续动作</th></tr></thead><tbody>{amountRequirements.map((item, index) => {
      const supplier = suppliers.find((candidate) => candidate.quote_id === item.quote_id)
      const supplierName = item.supplier_name ?? supplier?.supplier_name ?? '供应商身份待核对'
      const record = item.quote_id ? currentEvidence(item.quote_id, 'AMOUNT_APPROVAL') : undefined
      const deferred = item.execution_stage === 'AFTER_SELECTION'
      const outcome = item.approval_confirmed ? '审批记录已核对' : item.triggered === true ? (deferred ? '选择后将触发' : '待核对审批') : item.triggered === false ? '未触发' : '待检查'
      return <tr key={`${item.quote_id}:${item.execution_stage}:${item.threshold}:${item.action}:${index}`}><td><strong>{supplierName}</strong></td><td>{executionStageLabel(item.execution_stage)}</td><td>{item.amount && item.currency ? `${item.currency} ${item.amount}` : '金额待确认'}{item.threshold && <small>门槛：{item.currency ?? ''} {item.threshold}</small>}</td><td><StateBadge status={item.approval_confirmed ? 'PASS' : item.triggered === true ? 'REVIEW_REQUIRED' : item.triggered === false ? 'PASS' : 'NOT_EVALUATED'} label={outcome} />{item.triggered !== false && (item.reason_codes ?? []).map((code) => <small key={code}>{complianceReasonLabel(code)}</small>)}</td><td>{item.triggered ? item.action ?? '按制度办理' : '—'}{!readonly && errors.length === 0 && item.triggered === true && supplier && <button className="button button-secondary compliance-add" type="button" onClick={() => setTarget({ supplier, control: 'AMOUNT_APPROVAL', record, revision: data.task_revision, runAfterSave: true })}>{record ? '替换金额审批记录' : '补充金额审批记录'}</button>}</td></tr>
    })}</tbody></table></div></section>}
    {!readonly && data.assessment && <Confirmation key={`${data.task_revision}:${data.stage.assessment_id}`} workspace={data} onRefresh={refresh} />}
  </div>
}
