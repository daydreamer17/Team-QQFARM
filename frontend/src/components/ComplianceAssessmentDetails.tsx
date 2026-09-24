import { Link } from 'react-router-dom'
import { complianceEvidenceUrl } from '../api/client'
import type { PolicyComplianceResult, PolicyComplianceSupplierAssessment } from '../api/types'
import { checkStatusLabel, complianceCheckHref, complianceReasonLabel, complianceStatusLabel, executionStageLabel } from '../lib/compliance'
import { controlLabel } from '../lib/presentation'

export function ComplianceAssessmentDetails({ assessment, assessments, taskId, resultId, historical = false, legacy = false }: {
  assessment?: PolicyComplianceResult | null; assessments?: PolicyComplianceSupplierAssessment[];
  taskId: string; resultId?: string; historical?: boolean; legacy?: boolean
}) {
  const rows = assessments ?? assessment?.assessments ?? []
  return <details className="compliance-frozen-details">
    <summary>本结果的制度核验记录{assessment?.task_revision ? ` · 第 ${assessment.task_revision} 版` : ''}</summary>
    {(legacy || assessment?.legacy_compliance) && <p className="run-notice">旧流程结果：未记录完整的制度材料确认，不能视为已完成当前制度检查。</p>}
    {assessment?.policy_enabled === false && <p>本结果未启用制度检查，仅用于采购比较。</p>}
    {assessment?.strategy === 'VERIFIED_FIRST' && <p>推荐策略：已核验候选优先；制度排除与报价可行性分别记录。</p>}
    {rows.length === 0 ? <p>本结果没有逐供应商核验记录。</p> : rows.map((row) => <article className="compliance-check" key={row.quote_id}>
      <strong>{row.supplier_name ?? row.supplier_id ?? row.quote_id} · {complianceStatusLabel(row.status)}</strong>
      {!historical && <p><Link to={complianceCheckHref(taskId, row.quote_id)}>查看此供应商材料</Link></p>}
      {row.eligibility && <p>{complianceStatusLabel(row.eligibility)}</p>}
      <ul>{row.checks.map((check, index) => <li key={check.clause_id ?? index}>{controlLabel(check.control_code)}：{checkStatusLabel(check.status)}
        {(check.reason_codes ?? []).length > 0 && <span> · {check.reason_codes!.map(complianceReasonLabel).join('；')}</span>}
        {check.source_refs?.length ? <small>材料来源：{check.source_refs.join('、')}</small> : null}
        {!historical && <small><Link to={complianceCheckHref(taskId, row.quote_id, check.clause_id)}>定位此检查</Link></small>}
        {assessment?.citations?.filter((citation) => check.citation_ids.includes(citation.citation_id)).map((citation) => <details key={citation.citation_id}><summary>{citation.section || '制度原文'} · 版本 {citation.document_version}</summary><blockquote>{citation.text}</blockquote></details>)}
      </li>)}</ul>
      {assessment?.evidence?.filter((evidence) => evidence.quote_id === row.quote_id).map((evidence) => <div key={evidence.evidence_id}><small>{evidence.facts.material_number} · 材料第 {evidence.version} 版</small>{evidence.files.map((file) => <a className="compliance-file" key={file.file_id} href={complianceEvidenceUrl(taskId, evidence.evidence_id, file.file_id)}>{file.original_filename}</a>)}</div>)}
    </article>)}
    {(assessment?.amount_requirements?.length ?? 0) > 0 && <section><h4>金额条件与后续动作</h4>{assessment!.amount_requirements!.map((item, index) => <p key={index}>{executionStageLabel(item.execution_stage)} · {item.triggered ? `已触发：${item.action}` : item.triggered === false ? '未触发' : '尚待检查'}</p>)}<p>以上为制度要求，不代表采购获批。</p></section>}
    {historical ? <p>正在展示冻结的历史记录。{resultId && <Link to={`/tasks/${taskId}/results/${resultId}`}>查看对应历史结果</Link>}</p> : <Link to={`/tasks/${taskId}/compliance`}>查看当前制度检查与材料</Link>}
  </details>
}
