import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { api, ApiClientError, complianceEvidenceUrl, createIdempotencyKey } from '../api/client'
import type { ComplianceEvidenceFacts, ComplianceEvidenceRecord, ComplianceWorkspace, PolicyComplianceSupplierAssessment, TaskDetail } from '../api/types'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'
import { TablePagination } from '../components/TablePagination'
import { useTablePagination } from '../hooks/useTablePagination'
import { controlLabel } from '../lib/presentation'
import { aggregateControlStatus, checkStatusLabel, complianceAnchorId, complianceReasonLabel, complianceStatusLabel, executionStageLabel } from '../lib/compliance'

function errorMessage(error: unknown) {
  if (error instanceof ApiClientError && (error.status === 409 || error.code.includes('revision'))) return '任务或检查版本已变化，请刷新后重新核对。未保存的填写仍保留。'
  return error instanceof Error ? error.message : '制度检查操作失败。'
}
function errorText(error: string | Record<string, unknown>) {
  return typeof error === 'string' ? complianceReasonLabel(error) : String(error.message ?? error.code ?? '制度依据不完整，请联系制度管理员。')
}
type EvidenceTarget = { supplier: PolicyComplianceSupplierAssessment; control: ComplianceEvidenceFacts['control_code']; record?: ComplianceEvidenceRecord; revision: number }

function EvidenceEditor({ task, target, onClose, onSaved }: {
  task: TaskDetail; target: EvidenceTarget; onClose: () => void; onSaved: () => Promise<void>
}) {
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
    manufacturer: task.requirement.manufacturer, manufacturer_part_number: task.requirement.manufacturer_part_number,
    material_number: '', outcome: 'PASS', effective_from: null, expires_on: null, permanent: false, source_refs: [], note: '',
    ...target.record?.facts, coverage_confirmed: false,
  }))
  const [file, setFile] = useState<File | null>(null)
  const [localError, setLocalError] = useState('')
  const [idempotencyKey, setIdempotencyKey] = useState(createIdempotencyKey)
  const save = useMutation({ mutationFn: () => api.saveComplianceEvidence(task.task_id,
    { expectedTaskRevision: target.revision, facts: { ...facts, source_refs: facts.source_refs.map((ref) => ref.trim()).filter(Boolean) }, file, evidenceId: target.record?.evidence_id }, idempotencyKey),
    onSuccess: async () => { await onSaved(); onClose() },
    onError: async (error) => { if (error instanceof ApiClientError && error.status === 409) await onSaved() },
  })
  const stale = task.task_revision !== target.revision
  const ready = facts.coverage_confirmed && facts.material_number.trim() && facts.supplier_id.trim()
    && (file || facts.source_refs.some((ref) => ref.trim()))
    && (facts.control_code !== 'ROHS_COMPLIANCE' || (facts.manufacturer?.trim() && facts.manufacturer_part_number?.trim()))
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
    <div className="section-heading"><div><h3 id="compliance-editor-title">{target.record ? '替换材料并保留旧版本' : '补充材料'} · {target.supplier.supplier_name}</h3><p>{controlLabel(target.control)} · 第 {target.supplier.quote_version} 版报价</p></div><button type="button" className="button button-secondary" disabled={save.isPending} onClick={onClose}>取消</button></div>
    <p id="compliance-editor-description">请按材料原文核对供应商和产品范围；下方预填信息需要确认，日期留空表示未说明。</p>
    <form onSubmit={submit}><fieldset disabled={save.isPending || stale} className="compliance-form-grid">
      <label className="field">材料编号<input required value={facts.material_number} onChange={(e) => change('material_number', e.target.value)} /></label>
      <label className="field">材料供应商编号<input required value={facts.supplier_id} onChange={(e) => change('supplier_id', e.target.value)} /></label>
      <label className="field">材料制造商<input required={target.control === 'ROHS_COMPLIANCE'} value={facts.manufacturer ?? ''} onChange={(e) => change('manufacturer', e.target.value)} /></label>
      <label className="field">材料制造商料号<input required={target.control === 'ROHS_COMPLIANCE'} value={facts.manufacturer_part_number ?? ''} onChange={(e) => change('manufacturer_part_number', e.target.value)} /></label>
      <label className="field">材料结论<select value={facts.outcome} onChange={(e) => change('outcome', e.target.value as 'PASS' | 'FAIL')}><option value="PASS">材料表明符合</option><option value="FAIL">材料表明不符合</option></select></label>
      <label className="field">生效日期<input type="date" value={facts.effective_from ?? ''} onChange={(e) => change('effective_from', e.target.value || null)} /></label>
      <label className="field">失效日期<input type="date" disabled={facts.permanent} value={facts.expires_on ?? ''} onChange={(e) => change('expires_on', e.target.value || null)} /></label>
      <label className="compliance-checkbox"><input type="checkbox" checked={facts.permanent} onChange={(e) => { setFacts((current) => ({ ...current, permanent: e.target.checked, expires_on: e.target.checked ? null : current.expires_on })); setIdempotencyKey(createIdempotencyKey()); setLocalError('') }} />材料明确注明永久有效</label>
      <label className="field compliance-wide">来源引用<textarea rows={2} placeholder="原始记录编号或来源链接，每行一条；也可上传附件" value={facts.source_refs.join('\n')} onChange={(e) => change('source_refs', e.target.value.split('\n'))} /></label>
      <label className="field compliance-wide">材料附件（PDF、TXT、MD，最多 10 MiB）<input type="file" accept=".pdf,.txt,.md" onChange={(e) => {
        const selected = e.target.files?.[0] ?? null
        if (selected && (!/\.(pdf|txt|md)$/i.test(selected.name) || selected.size > 10 * 1024 * 1024 || selected.size === 0)) {
          setLocalError('请选择非空 PDF、TXT 或 MD，文件大小不超过 10 MiB。'); setFile(null); return
        }
        setLocalError(''); setFile(selected); setIdempotencyKey(createIdempotencyKey())
      }} /></label>
      <label className="field compliance-wide">核对说明<textarea rows={2} value={facts.note} onChange={(e) => change('note', e.target.value)} /></label>
      <label className="compliance-checkbox compliance-wide"><input type="checkbox" checked={facts.coverage_confirmed} onChange={(e) => change('coverage_confirmed', e.target.checked)} />我已核对材料原文，并确认所填写的供应商、产品范围和结论</label>
      <button className="button button-submit" disabled={!ready || Boolean(localError)} type="submit">{save.isPending ? '正在保存…' : '保存材料并重新检查'}</button>
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
  const labels = new Map(assessment?.assessments.flatMap((supplier) => supplier.checks.map((check) => [check.item_id,
    `${supplier.supplier_name ?? supplier.supplier_id ?? supplier.quote_id} · ${controlLabel(check.control_code)}`])) ?? [])
  const confirm = useMutation({ mutationFn: () => api.confirmCompliance(workspace.task_id, {
    expected_task_revision: workspace.task_revision, expected_assessment_id: workspace.stage.assessment_id ?? assessment!.assessment_id!,
    acknowledged_missing_item_ids: acknowledged, acknowledge_no_policy: noPolicy,
  }, idempotencyKey), onSuccess: async (response) => {
    await onRefresh(); navigate(`/tasks/${workspace.task_id}/decision`, { state: { expectedRevision: response.task_revision } })
  }, onError: async (error) => { if (error instanceof ApiClientError && error.status === 409) await onRefresh() } })
  const errors = [...(workspace.plan?.policy_errors ?? []), ...(assessment?.policy_errors ?? [])]
  const disabled = !assessment || !workspace.stage.can_confirm || errors.length > 0
    || missing.some((id) => !acknowledged.includes(id)) || (!assessment.policy_enabled && !noPolicy)
  if (workspace.stage.can_compare) return <section className="card compliance-confirmation"><h3>制度检查已处理</h3><p>已保存本次处理记录。补充或替换材料后需要重新检查与确认。</p><Link className="button button-submit" to={`/tasks/${workspace.task_id}/decision`}>继续决策比较</Link></section>
  return <section className="card compliance-confirmation"><h3>确认本次处理结果</h3>
    <p>材料尚未补齐的供应商保持“未核验”；系统优先比较已核验候选。此处确认不代表采购批准。</p>
    {missing.map((id) => <label className="compliance-checkbox" key={id}><input type="checkbox" disabled={confirm.isPending} checked={acknowledged.includes(id)} onChange={(e) => { setAcknowledged((current) => e.target.checked ? [...current, id] : current.filter((item) => item !== id)); setIdempotencyKey(createIdempotencyKey()) }} />暂不补充：{labels.get(id) ?? id}</label>)}
    {assessment && !assessment.policy_enabled && <label className="compliance-checkbox"><input type="checkbox" disabled={confirm.isPending} checked={noPolicy} onChange={(e) => { setNoPolicy(e.target.checked); setIdempotencyKey(createIdempotencyKey()) }} />确认本任务不启用制度检查，后续结果仅用于采购比较</label>}
    <button className="button button-submit" type="button" disabled={disabled || confirm.isPending} onClick={() => confirm.mutate()}>{confirm.isPending ? '正在确认…' : '确认处理结果并进入决策'}</button>
    {confirm.isError && <p role="alert" className="form-error">{errorMessage(confirm.error)}</p>}
  </section>
}

export function CompliancePage() {
  const { taskId = '' } = useParams()
  const location = useLocation()
  const navigate = useNavigate()
  const client = useQueryClient()
  const [target, setTarget] = useState<EvidenceTarget | null>(null)
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
  if (task.isPending || workspace.isPending) return <section className="card loading-panel">正在读取制度检查…</section>
  if (task.isError || workspace.isError) return <section className="card error-panel" role="alert">{errorMessage(task.error ?? workspace.error)}<button className="button button-secondary" onClick={() => void refresh()}>刷新重试</button></section>
  const data = workspace.data
  const clauses = data.plan?.clauses ?? []
  const errors = [...(data.plan?.policy_errors ?? []), ...(data.assessment?.policy_errors ?? [])]
  const readonly = task.data.status === 'ABANDONED'
  return <div className="page-stack compliance-page">
    <TaskWorkspaceHeader taskId={taskId} scenarioId={task.data.scenario_id} title={task.data.task_name} subtitle={`${task.data.requirement.required_quantity} ${task.data.requirement.quantity_unit} · ${task.data.quotes.length} 份报价`} status={task.data.status} revision={data.task_revision} resultId={task.data.current_result_id} quoteCount={task.data.quotes.length} summaryComplete={task.data.summary_completed} progress={{ ...task.data.progress, compliance: data.stage }} active="compliance" />
    <section className="review-workspace-lead"><div><h2>制度检查</h2><p>先核对条款与供应商材料，再进入决策比较。</p></div><span className="status-pill">{complianceStatusLabel(data.stage.status)}</span></section>
    {data.legacy_result && <p className="run-notice">这是旧流程任务，历史结果尚未经过本阶段确认。<Link to={`/tasks/${taskId}/audit`}>查看历史记录</Link></p>}
    {!data.policy_binding && <section className="card"><h3>本任务未启用制度检查</h3><p>请在下方明确确认后继续。系统不会将未启用解释为供应商合规。</p></section>}
    {data.policy_binding && <section className="policy-binding-card policy-binding-friendly" aria-label="已绑定制度">
      <div><span>制度名称</span><strong>{[...new Set(clauses.map((clause) => clause.title).filter(Boolean))].join('、') || '等待载入制度名称'}</strong></div>
      <div><span>制度版本</span><strong>{data.policy_binding.policy_set_version}</strong></div>
      <div><span>适用品类</span><strong>{data.policy_binding.category === 'Electronics' ? '电子产品采购' : data.policy_binding.category}</strong></div>
      <div><span>适用地区</span><strong>{data.policy_binding.region === 'SG' ? '新加坡' : data.policy_binding.region}</strong></div>
    </section>}
    {errors.length > 0 && <section className="card error-panel" role="alert"><h3>制度依据需要处理</h3><ul>{errors.map((error, index) => <li key={index}>{errorText(error)}</li>)}</ul><p>请先完成制度条款或检索依据复核，当前不能确认。</p><Link to="/resources">查看制度资源</Link></section>}
    {data.assessment?.publication_blocked && <p className="run-notice">仍有发布前条件未满足。确认处理记录不会解除这些条件，决策结果会保留相应限制。</p>}
    {!data.assessment && <section className="card"><h3>{data.stage.status === 'PROCESSING' ? '正在检查本次报价' : '等待报价处理完成'}</h3><p>这里仅展示当前版本的检查，历史结果不会作为当前制度结论显示。</p><Link to={`/tasks/${taskId}/quotes/new`}>查看报价与审核</Link></section>}
    {!readonly && data.stage.status === 'NOT_STARTED' && task.data.progress.quote_review_completed && <section className="card"><button className="button button-submit" disabled={start.isPending || ['QUEUED', 'RUNNING', 'PROCESSING'].includes(task.data.status)} onClick={() => start.mutate()}>{start.isPending ? '正在启动…' : '开始制度检查'}</button>{start.isError && <p role="alert">{errorMessage(start.error)}</p>}</section>}
    {suppliers.length > 0 && <section className="card"><div className="section-heading"><div><h3>供应商检查结果</h3><p>已核验候选优先；明确不符合的候选不参与推荐。</p></div><span>{suppliers.length} 家</span></div>
      <div className="compliance-table-scroll"><table className="supplier-data-table compliance-table"><thead><tr><th>供应商</th><th>供应商准入</th><th>RoHS</th><th>制度结论</th><th>推荐资格</th><th>材料与依据</th></tr></thead><tbody>
        {pagination.pageItems.map((supplier) => <tr key={supplier.quote_id} id={complianceAnchorId(supplier.quote_id)} tabIndex={-1}><td><strong>{supplier.supplier_name ?? supplier.supplier_id ?? supplier.quote_id}</strong><small>第 {supplier.quote_version} 版报价</small></td><td>{checkStatusLabel(aggregateControlStatus(supplier.checks, 'APPROVED_SUPPLIER'))}</td><td>{checkStatusLabel(aggregateControlStatus(supplier.checks, 'ROHS_COMPLIANCE'))}</td><td>{complianceStatusLabel(supplier.status)}</td><td>{complianceStatusLabel(supplier.eligibility ?? 'UNVERIFIED')}</td><td>
          {!readonly && [...new Set(supplier.checks.filter((check) => ['APPROVED_SUPPLIER', 'ROHS_COMPLIANCE'].includes(check.control_code)).map((check) => check.control_code))].map((control) => <button key={control} className="button button-secondary compliance-add" type="button" onClick={() => setTarget({ supplier, control: control as EvidenceTarget['control'], revision: data.task_revision })}>补充材料 · {controlLabel(control)}</button>)}
          <details open={supplier.quote_id === linkedQuote || undefined}><summary>展开检查与材料（{supplier.checks.length}）</summary>
            {supplier.checks.map((check, index) => <article className="compliance-check" id={complianceAnchorId(supplier.quote_id, check.clause_id)} tabIndex={-1} key={check.clause_id ?? index}><strong>{controlLabel(check.control_code)} · {checkStatusLabel(check.status)}</strong><ul>{(check.reason_codes ?? [check.reason_code ?? '']).filter(Boolean).map((code) => <li key={code}>{complianceReasonLabel(code)}</li>)}</ul>
              {clauses.filter((clause) => clause.clause_id === check.clause_id).map((clause) => <details key={clause.clause_id}><summary>{clause.title ?? clause.section ?? '制度原文'} · 版本 {clause.document_version}</summary><blockquote>{clause.text}</blockquote><small>{clause.document_id} · {clause.clause_id}</small></details>)}
            </article>)}
            {data.evidence.filter((record) => record.quote_id === supplier.quote_id).map((record) => <article className="compliance-check" key={record.evidence_id}><strong>{record.facts.material_number} · 材料第 {record.version} 版{record.superseded ? '（已替换）' : ''}</strong><p>{record.facts.supplier_id} · {record.facts.manufacturer} · {record.facts.manufacturer_part_number}</p><p>有效期：{record.facts.effective_from ?? '未说明'} 至 {record.facts.permanent ? '明确永久有效' : record.facts.expires_on ?? '未说明'}</p><p>核对人：{record.confirmed_by} · {record.confirmed_at}</p>{record.facts.source_refs.map((ref) => <p key={ref}>{ref}</p>)}{record.files.map((file) => <a className="compliance-file" key={file.file_id} href={complianceEvidenceUrl(taskId, record.evidence_id, file.file_id)}>{file.original_filename}</a>)}{!readonly && !record.superseded && <button className="button button-secondary" onClick={() => setTarget({ supplier, control: record.control_code, record, revision: data.task_revision })}>替换材料</button>}</article>)}
          </details>
        </td></tr>)}
      </tbody></table></div><TablePagination page={pagination.page} pageSize={pagination.pageSize} pageCount={pagination.pageCount} total={suppliers.length} onPageChange={(page) => { navigate({ pathname: location.pathname, hash: '' }, { replace: true }); pagination.setPage(page) }} />
    </section>}
    {target && !readonly && <EvidenceEditor key={`${target.supplier.quote_id}:${target.control}:${target.record?.evidence_id ?? 'new'}:${target.revision}`} task={task.data} target={target} onClose={() => setTarget(null)} onSaved={refresh} />}
    {(data.assessment?.amount_requirements?.length ?? 0) > 0 && <section className="card"><h3>金额条件与后续动作</h3><p>金额条件单独列示，不代表采购已经获批。</p>{data.assessment!.amount_requirements!.map((item, index) => <p key={index}>{item.supplier_name ?? suppliers.find((supplier) => supplier.quote_id === item.quote_id)?.supplier_name ?? '供应商身份待核对'} · {executionStageLabel(item.execution_stage)} · {item.triggered === true ? `已触发：${item.action ?? '按制度办理'}` : item.triggered === false ? '未触发' : '待相应阶段检查'}</p>)}</section>}
    {!readonly && <Confirmation key={`${data.task_revision}:${data.stage.assessment_id}`} workspace={data} onRefresh={refresh} />}
  </div>
}
