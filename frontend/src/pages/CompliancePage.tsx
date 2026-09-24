import { useMutation, useQuery } from '@tanstack/react-query'
import { useState, type ChangeEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiClientError } from '../api/client'
import type {
  PolicyCitation,
  PolicyComplianceSupplierAssessment,
  PolicyRetrievalResult,
  ProcurementRequirement,
  SupplierComparisonResult,
  SupplierComplianceEvidenceInput,
} from '../api/types'
import { IssuePanel } from '../components/IssuePanel'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'
import { controlLabel, policyStatusLabel, quoteStatusLabel } from '../lib/presentation'

function errorMessage(error: unknown) {
  return error instanceof ApiClientError ? error.message : '制度检查读取失败。'
}

function statusClass(status: string) {
  if (status === 'OK') return 'policy-status-ok'
  if (status === 'NO_EVIDENCE') return 'policy-status-missing'
  return 'policy-status-error'
}

function controlCodes(retrieval: PolicyRetrievalResult) {
  return [...new Set([...retrieval.covered_control_codes, ...retrieval.missing_control_codes])]
}

function CitationCard({ citation }: { citation: PolicyCitation }) {
  return (
    <article className="policy-citation-card">
      <header><strong>{citation.section || '制度条款'}</strong><span>引用依据</span></header>
      <blockquote>{citation.text}</blockquote>
    </article>
  )
}

function RetrievalCard({ retrieval }: { retrieval: PolicyRetrievalResult }) {
  const labels = controlCodes(retrieval).map(controlLabel)
  return (
    <article className={`card policy-retrieval-card ${statusClass(retrieval.status)}`}>
      <header>
        <div><p className="eyebrow">制度要求</p><h3>{labels.join('、') || '未识别的制度要求'}</h3></div>
        <span className={`policy-retrieval-status ${statusClass(retrieval.status)}`}>{policyStatusLabel(retrieval.status)}</span>
      </header>
      <p className="policy-control-explanation">
        {retrieval.status === 'OK'
          ? `已找到 ${retrieval.citations.length} 条可引用的制度依据。`
          : '当前制度依据不足，请检查任务绑定的制度版本和索引。'}
      </p>
      {retrieval.citations.length > 0 ? (
        <details className="policy-evidence-details">
          <summary>查看引用依据（{retrieval.citations.length}）</summary>
          <div className="policy-citation-list">
            {retrieval.citations.map((citation) => <CitationCard citation={citation} key={citation.citation_id} />)}
          </div>
        </details>
      ) : <p className="policy-no-citation">没有可展示的制度引用。</p>}
    </article>
  )
}

function complianceConclusion(assessment: PolicyComplianceSupplierAssessment | undefined) {
  if (assessment?.status === 'COMPLIANT') return { tone: 'passed', icon: '✓', label: '制度检查通过' }
  if (assessment?.status === 'NON_COMPLIANT') return { tone: 'failed', icon: '×', label: '不符合制度' }
  if (assessment?.status === 'REVIEW_REQUIRED') return { tone: 'pending', icon: '!', label: '需要补充数据' }
  if (assessment?.status === 'NOT_EVALUATED') return { tone: 'unknown', icon: '—', label: '未进入制度检查' }
  return { tone: 'unknown', icon: '—', label: '等待制度检查' }
}

function SupplierRequirementRow({
  supplier,
  assessment,
  checked,
}: {
  supplier: SupplierComparisonResult
  assessment: PolicyComplianceSupplierAssessment | undefined
  checked: boolean
}) {
  const quoteTone = supplier.status === 'FEASIBLE'
    ? 'passed'
    : supplier.status === 'INFEASIBLE'
      ? 'failed'
      : 'pending'
  const conclusion = complianceConclusion(assessment)

  return (
    <article className={`supplier-policy-row supplier-requirement-row ${checked ? 'has-compliance-result' : ''}`}>
      <div className="supplier-policy-name">
        <strong>{supplier.supplier_name}</strong>
        <span>第 {supplier.quote_version} 版报价</span>
      </div>
      <div className={`supplier-policy-result result-${quoteTone}`}>
        <span className="supplier-policy-icon">
          {supplier.status === 'FEASIBLE' ? '✓' : supplier.status === 'INFEASIBLE' ? '×' : '!'}
        </span>
        <div><small>采购要求</small><strong>{quoteStatusLabel(supplier.status)}</strong></div>
      </div>
      {checked && (
        <div className={`supplier-policy-result result-${conclusion.tone}`}>
          <span className="supplier-policy-icon">{conclusion.icon}</span>
          <div>
            <small>制度检查</small>
            <strong>{conclusion.label}</strong>
            {assessment && (
              <ul className="policy-check-summary">
                {assessment.checks.map((check) => (
                  <li key={check.control_code}>
                    <span>{controlLabel(check.control_code)}</span>
                    <b>{check.message}</b>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </article>
  )
}

type EvidenceDraft = SupplierComplianceEvidenceInput & { rohs_enabled: boolean }

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function nullableString(value: unknown) {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function ComplianceEvidenceEditor({
  taskId,
  taskRevision,
  resultId,
  suppliers,
  supplierIds,
  requirement,
  onChecked,
}: {
  taskId: string
  taskRevision: number
  resultId: string
  suppliers: SupplierComparisonResult[]
  supplierIds: Record<string, string>
  requirement: ProcurementRequirement
  onChecked: () => void
}) {
  const saved = useQuery({
    queryKey: ['tasks', taskId, 'supplier-compliance-evidence', resultId],
    queryFn: () => api.getSupplierComplianceEvidence(taskId, resultId),
  })
  const [edits, setEdits] = useState<Record<string, EvidenceDraft>>({})
  const [formError, setFormError] = useState('')
  const [importMessage, setImportMessage] = useState('')
  const prior = new Map((saved.data?.evidence ?? []).map((item) => [item.supplier_id, item]))
  const drafts = Object.fromEntries(suppliers.flatMap((supplier) => {
    const supplierId = supplierIds[supplier.quote_id]
    if (!supplierId) return []
    const item = prior.get(supplierId)
    const base: EvidenceDraft = {
      supplier_id: supplierId,
      supplier_name: supplier.supplier_name,
      approved_supplier: item?.approved_supplier ?? false,
      supplier_registry_valid_until: item?.supplier_registry_valid_until ?? null,
      rohs_certificate_number: item?.rohs_certificate_number ?? null,
      rohs_part_number: item?.rohs_part_number ?? null,
      rohs_revision: item?.rohs_revision ?? null,
      rohs_valid_until: item?.rohs_valid_until ?? null,
      rohs_enabled: Boolean(item?.rohs_certificate_number),
    }
    return [[supplierId, edits[supplierId] ?? base]]
  })) as Record<string, EvidenceDraft>

  const check = useMutation({
    mutationFn: (evidence: SupplierComplianceEvidenceInput[]) => api.checkSupplierCompliance(
      taskId, taskRevision, resultId, evidence, crypto.randomUUID(),
    ),
    onSuccess: async () => {
      setFormError('')
      await saved.refetch()
      onChecked()
    },
  })

  function update(supplierId: string, changes: Partial<EvidenceDraft>) {
    setEdits((current) => ({
      ...current,
      [supplierId]: { ...drafts[supplierId], ...changes },
    }))
  }

  async function importEvidence(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    try {
      const payload: unknown = JSON.parse(await file.text())
      const rows = isRecord(payload) ? payload.evidence : undefined
      if (!Array.isArray(rows)) throw new Error('missing evidence array')
      const imported: Record<string, EvidenceDraft> = {}
      rows.forEach((row) => {
        if (!isRecord(row) || typeof row.supplier_id !== 'string' || !drafts[row.supplier_id]) return
        const supplierId = row.supplier_id
        const certificateNumber = nullableString(row.rohs_certificate_number)
        imported[supplierId] = {
          supplier_id: supplierId,
          supplier_name: nullableString(row.supplier_name) ?? drafts[supplierId].supplier_name,
          approved_supplier: row.approved_supplier === true,
          supplier_registry_valid_until: nullableString(row.supplier_registry_valid_until),
          rohs_certificate_number: certificateNumber,
          rohs_part_number: nullableString(row.rohs_part_number),
          rohs_revision: nullableString(row.rohs_revision),
          rohs_valid_until: nullableString(row.rohs_valid_until),
          rohs_enabled: Boolean(certificateNumber),
        }
      })
      const count = Object.keys(imported).length
      if (count === 0) throw new Error('no matching suppliers')
      setEdits((current) => ({ ...current, ...imported }))
      setFormError('')
      setImportMessage(`已从 ${file.name} 导入 ${count} 家供应商的数据，请确认后执行制度检查。`)
    } catch {
      setImportMessage('')
      setFormError('JSON 导入失败：请使用本演示生成的 supplier_compliance_evidence.json 文件。')
    }
  }

  function submit() {
    const evidence = Object.values(drafts).map(({ rohs_enabled, ...item }) => ({
      ...item,
      supplier_registry_valid_until: item.approved_supplier ? item.supplier_registry_valid_until : null,
      rohs_certificate_number: rohs_enabled ? item.rohs_certificate_number : null,
      rohs_part_number: rohs_enabled ? item.rohs_part_number : null,
      rohs_revision: rohs_enabled ? item.rohs_revision : null,
      rohs_valid_until: rohs_enabled ? item.rohs_valid_until : null,
    }))
    const incomplete = Object.values(drafts).some((item) => (
      (item.approved_supplier && !item.supplier_registry_valid_until)
      || (item.rohs_enabled && (!item.rohs_certificate_number || !item.rohs_part_number || !item.rohs_revision || !item.rohs_valid_until))
    ))
    if (incomplete) {
      setFormError('请补全已勾选的准入记录或 RoHS 证书信息。')
      return
    }
    check.mutate(evidence)
  }

  return (
    <section className="card compliance-evidence-editor">
      <div className="section-heading">
        <div><h2>供应商准入与 RoHS 数据</h2><p>录入结构化记录后，系统会结合当前 RAG 制度引用执行检查。</p></div>
        <span>{Object.keys(drafts).length} 家供应商</span>
      </div>
      {saved.isPending ? <p>正在读取已保存数据…</p> : (
        <div className="compliance-evidence-list">
          {suppliers.map((supplier) => {
            const supplierId = supplierIds[supplier.quote_id]
            const item = drafts[supplierId]
            if (!supplierId || !item) return null
            return (
              <article className="compliance-evidence-form" key={supplier.quote_id}>
                <header><strong>{supplier.supplier_name}</strong><span>{supplierId}</span></header>
                <div className="compliance-evidence-fields">
                  <label className="check-field">
                    <input type="checkbox" checked={item.approved_supplier} onChange={(event) => update(supplierId, { approved_supplier: event.target.checked })} />
                    在供应商准入名单中
                  </label>
                  <label>准入有效期至<input type="date" disabled={!item.approved_supplier} value={item.supplier_registry_valid_until ?? ''} onChange={(event) => update(supplierId, { supplier_registry_valid_until: event.target.value || null })} /></label>
                  <label className="check-field">
                    <input type="checkbox" checked={item.rohs_enabled} onChange={(event) => update(supplierId, {
                      rohs_enabled: event.target.checked,
                      rohs_part_number: event.target.checked ? requirement.manufacturer_part_number : null,
                      rohs_revision: event.target.checked ? requirement.revision : null,
                    })} />
                    已提供 RoHS 证书
                  </label>
                  <label>证书编号<input disabled={!item.rohs_enabled} value={item.rohs_certificate_number ?? ''} onChange={(event) => update(supplierId, { rohs_certificate_number: event.target.value || null })} /></label>
                  <label>证书料号<input disabled={!item.rohs_enabled} value={item.rohs_part_number ?? ''} onChange={(event) => update(supplierId, { rohs_part_number: event.target.value || null })} /></label>
                  <label>证书版本<input disabled={!item.rohs_enabled} value={item.rohs_revision ?? ''} onChange={(event) => update(supplierId, { rohs_revision: event.target.value || null })} /></label>
                  <label>RoHS 有效期至<input type="date" disabled={!item.rohs_enabled} value={item.rohs_valid_until ?? ''} onChange={(event) => update(supplierId, { rohs_valid_until: event.target.value || null })} /></label>
                </div>
              </article>
            )
          })}
        </div>
      )}
      {importMessage && <p className="form-success" role="status">{importMessage}</p>}
      {formError && <p className="form-error" role="alert">{formError}</p>}
      {check.isError && <p className="form-error" role="alert">{errorMessage(check.error)}</p>}
      <div className="compliance-evidence-actions">
        <label className="button button-secondary compliance-import-button">
          导入供应商数据 JSON
          <input type="file" accept="application/json,.json" onChange={importEvidence} />
        </label>
        <button className="button button-submit" type="button" disabled={saved.isPending || check.isPending} onClick={submit}>
          {check.isPending ? '正在执行制度检查…' : '执行制度检查'}
        </button>
      </div>
    </section>
  )
}

export function CompliancePage() {
  const { taskId = '' } = useParams()
  const task = useQuery({
    queryKey: ['tasks', taskId],
    queryFn: () => api.getTask(taskId),
    enabled: Boolean(taskId),
    refetchInterval: (query) => {
      const data = query.state.data
      return data && (
        ['QUEUED', 'RUNNING'].includes(data.status)
        || ['PENDING', 'RUNNING'].includes(data.current_job?.job_status ?? '')
      ) ? 1_500 : false
    },
  })
  const currentResultId = task.data?.current_result_id
  const currentResult = useQuery({
    queryKey: ['tasks', taskId, 'results', currentResultId],
    queryFn: () => api.getResult(taskId, currentResultId!),
    enabled: Boolean(taskId && currentResultId),
  })

  if (task.isPending) return <section className="card loading-panel">正在读取制度检查…</section>
  if (task.isError) return <section className="card error-panel" role="alert">{errorMessage(task.error)}</section>

  const data = task.data
  const latestResult = currentResult.data?.is_current ? currentResult.data : undefined
  const retrievals = latestResult?.policy_retrievals ?? []
  const suppliers = latestResult?.result.supplier_results ?? []
  const policyIssue = data.current_issue?.issue_type === 'POLICY_EVIDENCE_REVIEW'
  const okCount = retrievals.filter((retrieval) => retrieval.status === 'OK').length
  const allEvidenceReady = retrievals.length > 0 && okCount === retrievals.length
  const completedComplianceCheck = latestResult?.policy_compliance.schema_version === 'policy-compliance/2.0.0'
  const assessmentByQuote = new Map(
    (completedComplianceCheck ? latestResult?.policy_compliance.assessments : [])?.map((item) => [item.quote_id, item]) ?? [],
  )
  const supplierIds = Object.fromEntries(data.quotes.map((quote) => [quote.quote_id, quote.supplier_id]))

  return (
    <div className="page-stack compliance-page">
      <TaskWorkspaceHeader
        taskId={data.task_id}
        scenarioId={data.scenario_id}
        title={data.requirement.manufacturer_part_number}
        subtitle={`${data.requirement.required_quantity} ${data.requirement.quantity_unit} · ${data.quotes.length} 份报价`}
        status={data.status}
        revision={data.task_revision}
        resultId={data.current_result_id}
        quoteCount={data.quotes.length}
        summaryComplete={data.summary_completed}
        progress={data.progress}
        reviewBlocked={Boolean(data.current_issue)}
        policyReviewBlocked={policyIssue}
        active="compliance"
      />

      <section className="review-workspace-lead">
        <div><h2>制度检查</h2>{data.policy_binding && <p>查看当前任务适用的制度条款和引用依据。</p>}</div>
        {data.policy_binding && (
          <span className={`status-pill ${allEvidenceReady ? 'status-pending' : 'status-offline'}`}>
            {!currentResultId ? '等待分析' : allEvidenceReady ? '制度依据已就绪' : '制度依据待补充'}
          </span>
        )}
      </section>

      {!data.policy_binding ? (
        <section className="card compliance-empty"><div><h2>本任务未启用制度检查</h2></div></section>
      ) : (
        <>
          <section className="policy-binding-card policy-binding-friendly">
            <div><span>检查范围</span><strong>{data.policy_binding.category === 'Electronics' ? '电子产品采购' : data.policy_binding.category}</strong></div>
            <div><span>适用地区</span><strong>{data.policy_binding.region === 'SG' ? '新加坡' : data.policy_binding.region}</strong></div>
            <div><span>制度要求</span><strong>{retrievals.length} 项</strong></div>
            <div><span>已有依据</span><strong>{okCount} 项</strong></div>
          </section>

          {policyIssue && <IssuePanel task={data} onRefresh={() => {
            void task.refetch()
            if (currentResultId) void currentResult.refetch()
          }} />}
          {currentResultId && currentResult.isPending && <section className="card loading-panel">正在整理制度检索结果…</section>}
          {currentResult.isError && <section className="card error-panel" role="alert">{errorMessage(currentResult.error)}</section>}
          {!currentResultId && (
            <section className="card compliance-empty compact">
              <div>
                <h2>{['QUEUED', 'RUNNING'].includes(data.status) ? '正在生成当前版本结果' : '当前版本没有有效决策结果'}</h2>
                <p>历史结果不会作为当前制度依据显示。完成本次分析后，这里会自动更新。</p>
                <Link to={`/tasks/${taskId}/audit`}>查看历史结果</Link>
              </div>
            </section>
          )}
          {currentResultId && !currentResult.isPending && !currentResult.isError && !latestResult && (
            <section className="card compliance-empty compact">
              <div>
                <h2>当前结果已经失效</h2>
                <p>页面不会继续展示该历史结果，正在等待任务状态刷新。</p>
              </div>
            </section>
          )}

          {latestResult && suppliers.length > 0 && (
            <ComplianceEvidenceEditor
              taskId={taskId}
              taskRevision={data.task_revision}
              resultId={latestResult.result_id}
              suppliers={suppliers}
              supplierIds={supplierIds}
              requirement={data.requirement}
              onChecked={() => void currentResult.refetch()}
            />
          )}

          {suppliers.length > 0 && (
            <section className="supplier-policy-section">
              <div className="section-heading">
                <div><h2>逐供应商采购要求检查</h2><p>展示供应商报价是否符合当前采购要求。</p></div>
                <span>{suppliers.length} 份报价</span>
              </div>
              <div className="supplier-policy-list">
                {suppliers.map((supplier) => (
                  <SupplierRequirementRow
                    supplier={supplier}
                    assessment={assessmentByQuote.get(supplier.quote_id)}
                    checked={completedComplianceCheck}
                    key={supplier.quote_id}
                  />
                ))}
              </div>
            </section>
          )}

          {latestResult && retrievals.length === 0 && (
            <section className="card compliance-empty compact">
              <div><h2>当前结果没有制度检索记录</h2><p>请先完成任务分析，或检查任务绑定的制度版本。</p></div>
            </section>
          )}
          {retrievals.length > 0 && (
            <section className="policy-evidence-section">
              <div className="section-heading">
                <div><h2>制度依据</h2><p>需要追溯时可展开查看引用内容。</p></div>
                <span>{okCount} / {retrievals.length} 项找到依据</span>
              </div>
              <div className="policy-retrieval-list">
                {retrievals.map((retrieval) => <RetrievalCard retrieval={retrieval} key={retrieval.retrieval_id} />)}
              </div>
            </section>
          )}
        </>
      )}

      {data.policy_binding && <p className="result-boundary">制度检查基于当前任务的制度引用和人工录入的结构化供应商记录。</p>}
    </div>
  )
}
