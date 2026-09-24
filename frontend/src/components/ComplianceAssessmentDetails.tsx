import { Link } from 'react-router-dom'
import { complianceEvidenceUrl } from '../api/client'
import type { PolicyComplianceCheckStatus, PolicyComplianceResult, PolicyComplianceSupplierAssessment } from '../api/types'
import { aggregateControlStatus, checkStatusLabel, complianceCheckHref, complianceReasonLabel, complianceStatusLabel, executionStageLabel, groupComplianceChecks } from '../lib/compliance'
import { controlLabel } from '../lib/presentation'

function tone(status: string) {
  if (['PASS', 'COMPLIANT', 'VERIFIED'].includes(status)) return 'success'
  if (['FAIL', 'NON_COMPLIANT', 'EXCLUDED'].includes(status)) return 'danger'
  if (['REVIEW_REQUIRED', 'UNVERIFIED'].includes(status)) return 'warning'
  return 'neutral'
}

function Badge({ status, children }: { status: string; children: string }) {
  return <span className={`compliance-state compliance-state-${tone(status)}`}>{children}</span>
}

function statusIcon(status: PolicyComplianceCheckStatus) {
  return status === 'PASS' ? '✓' : status === 'FAIL' ? '×' : status === 'REVIEW_REQUIRED' ? '!' : '—'
}

export function ComplianceAssessmentDetails({ assessment, assessments, taskId, resultId, historical = false, legacy = false }: {
  assessment?: PolicyComplianceResult | null; assessments?: PolicyComplianceSupplierAssessment[];
  taskId: string; resultId?: string; historical?: boolean; legacy?: boolean
}) {
  const rows = assessments ?? assessment?.assessments ?? []
  const compliant = rows.filter((row) => row.status === 'COMPLIANT').length
  const review = rows.filter((row) => row.status === 'REVIEW_REQUIRED').length
  const excluded = rows.filter((row) => row.status === 'NON_COMPLIANT').length
  const amountRequirements = (assessment?.amount_requirements ?? []).filter((item, index, items) => items.findIndex((candidate) =>
    [candidate.quote_id, candidate.execution_stage, candidate.threshold, candidate.triggered, candidate.action].join('|') ===
    [item.quote_id, item.execution_stage, item.threshold, item.triggered, item.action].join('|')) === index)

  return <details className="compliance-frozen-details">
    <summary className="compliance-frozen-summary"><span><strong>本结果的制度核验记录</strong><small>{assessment?.task_revision ? `第 ${assessment.task_revision} 版 · ` : ''}{rows.length} 家供应商</small></span><span className="compliance-frozen-summary-counts"><b>{compliant} 已核验</b>{review > 0 && <b>{review} 待处理</b>}{excluded > 0 && <b>{excluded} 已排除</b>}</span></summary>
    <div className="compliance-frozen-body">
      {(legacy || assessment?.legacy_compliance) && <p className="run-notice">旧流程结果：未记录完整的制度材料确认，不能视为已完成当前制度检查。</p>}
      {assessment?.policy_enabled === false && <p className="compliance-frozen-note">本结果未启用制度检查，仅用于采购比较。</p>}
      {assessment?.strategy === 'VERIFIED_FIRST' && <p className="compliance-frozen-note"><strong>推荐策略：</strong>已核验候选优先；制度排除与报价可行性分别记录。</p>}
      {rows.length === 0 ? <p className="audit-empty">本结果没有逐供应商核验记录。</p> : <div className="compliance-frozen-suppliers">
        {rows.map((row) => {
          const groups = groupComplianceChecks(row.checks)
          const pending = groups.filter(([control, checks]) => aggregateControlStatus(checks, control) !== 'PASS').length
          const evidence = assessment?.evidence?.filter((item) => item.quote_id === row.quote_id) ?? []
          return <details className="compliance-frozen-supplier" key={row.quote_id}>
            <summary><span className="compliance-frozen-supplier-name"><strong>{row.supplier_name ?? row.supplier_id ?? row.quote_id}</strong><small>第 {row.quote_version} 版报价</small></span><span><small>制度结论</small><Badge status={row.status}>{complianceStatusLabel(row.status)}</Badge></span><span><small>推荐资格</small><Badge status={row.eligibility ?? 'UNVERIFIED'}>{complianceStatusLabel(row.eligibility ?? 'UNVERIFIED')}</Badge></span><span className="compliance-frozen-issue-count">{pending > 0 ? `${pending} 类待处理` : '检查均已通过'}</span></summary>
            <div className="compliance-frozen-supplier-body">
              <div className="compliance-frozen-actions">{!historical && <Link to={complianceCheckHref(taskId, row.quote_id)}>查看此供应商材料</Link>}{historical && <span>冻结历史记录</span>}</div>
              <div className="compliance-frozen-control-grid">{groups.map(([control, checks]) => {
                const status = aggregateControlStatus(checks, control)
                const reasons = [...new Set(checks.flatMap((check) => check.reason_codes ?? [check.reason_code ?? '']).filter(Boolean))]
                return <article className={`compliance-frozen-control compliance-frozen-control-${tone(status)}`} key={control}>
                  <header><span className="compliance-frozen-icon" aria-hidden="true">{statusIcon(status)}</span><div><strong>{controlLabel(control)}</strong><small>{checks.length} 条制度要求</small></div><Badge status={status}>{checkStatusLabel(status)}</Badge></header>
                  {reasons.length > 0 && <p>{reasons.map(complianceReasonLabel).join('；')}</p>}
                  <details><summary>查看依据与定位</summary><div className="compliance-frozen-sources">{checks.map((check, index) => <div key={check.clause_id ?? index}>{!historical && <Link to={complianceCheckHref(taskId, row.quote_id, check.clause_id)}>定位此检查</Link>}{check.source_refs?.length ? <small>材料来源：{check.source_refs.join('、')}</small> : null}{assessment?.citations?.filter((citation) => check.citation_ids.includes(citation.citation_id)).map((citation) => <details key={citation.citation_id}><summary>{citation.section || '制度原文'} · 版本 {citation.document_version}</summary><blockquote>{citation.text}</blockquote></details>)}</div>)}</div></details>
                </article>
              })}</div>
              {evidence.length > 0 && <details className="compliance-frozen-materials"><summary>已提交材料（{evidence.length}）</summary><div>{evidence.map((item) => <article key={item.evidence_id}><strong>{item.facts.material_number} · {item.control_code === 'AMOUNT_APPROVAL' ? `金额审批 ${item.facts.currency} ${item.facts.approval_amount}` : controlLabel(item.control_code)} · 第 {item.version} 版</strong>{item.files.map((file) => <a className="compliance-file" key={file.file_id} href={complianceEvidenceUrl(taskId, item.evidence_id, file.file_id)}>{file.original_filename}</a>)}</article>)}</div></details>}
            </div>
          </details>
        })}
      </div>}
      {amountRequirements.length > 0 && <details className="compliance-frozen-amounts"><summary>金额条件与后续动作（{amountRequirements.length}）</summary><div className="compliance-table-scroll"><table className="supplier-data-table"><thead><tr><th>供应商</th><th>当前金额</th><th>执行阶段</th><th>判断</th><th>后续动作</th></tr></thead><tbody>{amountRequirements.map((item, index) => <tr key={`${item.quote_id}:${item.execution_stage}:${index}`}><td>{item.supplier_name ?? '候选供应商'}</td><td>{item.currency} {item.amount}</td><td>{executionStageLabel(item.execution_stage)}</td><td>{item.approval_confirmed ? '审批记录已核对' : item.triggered ? '达到条件，待核对审批' : item.triggered === false ? '未达到条件' : '尚待检查'}</td><td>{item.triggered ? item.action ?? '按制度办理' : '—'}</td></tr>)}</tbody></table></div><p>以上为制度要求，系统仅核对审批记录，不代表采购获批。</p></details>}
      <footer>{historical ? <>正在展示冻结的历史记录。{resultId && <Link to={`/tasks/${taskId}/results/${resultId}`}>查看对应历史结果</Link>}</> : <Link to={`/tasks/${taskId}/compliance`}>查看当前制度检查与材料</Link>}</footer>
    </div>
  </details>
}
