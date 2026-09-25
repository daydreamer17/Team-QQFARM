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
    <summary className="compliance-frozen-summary"><span><strong>Compliance records for this result</strong><small>{assessment?.task_revision ? `Revision ${assessment.task_revision} · ` : ''}{rows.length} suppliers</small></span><span className="compliance-frozen-summary-counts"><b>{compliant} verified</b>{review > 0 && <b>{review} require attention</b>}{excluded > 0 && <b>{excluded} excluded</b>}</span></summary>
    <div className="compliance-frozen-body">
      {(legacy || assessment?.legacy_compliance) && <p className="run-notice">Legacy result: complete policy evidence confirmation was not recorded and cannot be treated as a completed compliance review.</p>}
      {assessment?.policy_enabled === false && <p className="compliance-frozen-note">Compliance review was not enabled for this result. It is limited to procurement comparison.</p>}
      {assessment?.strategy === 'VERIFIED_FIRST' && <p className="compliance-frozen-note"><strong>Recommendation strategy: </strong>Verified candidates are prioritised. Policy exclusion and quotation feasibility are recorded separately.</p>}
      {rows.length === 0 ? <p className="audit-empty">This result has no supplier-level compliance records.</p> : <div className="compliance-frozen-suppliers">
        {rows.map((row) => {
          const groups = groupComplianceChecks(row.checks)
          const pending = groups.filter(([control, checks]) => aggregateControlStatus(checks, control) !== 'PASS').length
          const evidence = assessment?.evidence?.filter((item) => item.quote_id === row.quote_id) ?? []
          return <details className="compliance-frozen-supplier" key={row.quote_id}>
            <summary><span className="compliance-frozen-supplier-name"><strong>{row.supplier_name ?? row.supplier_id ?? row.quote_id}</strong><small>Quotation revision {row.quote_version}</small></span><span><small>Compliance conclusion</small><Badge status={row.status}>{complianceStatusLabel(row.status)}</Badge></span><span><small>Recommendation eligibility</small><Badge status={row.eligibility ?? 'UNVERIFIED'}>{complianceStatusLabel(row.eligibility ?? 'UNVERIFIED')}</Badge></span><span className="compliance-frozen-issue-count">{pending > 0 ? `${pending} areas require attention` : 'All checks passed'}</span></summary>
            <div className="compliance-frozen-supplier-body">
              <div className="compliance-frozen-actions">{!historical && <Link to={complianceCheckHref(taskId, row.quote_id)}>View supplier evidence</Link>}{historical && <span>Frozen historical record</span>}</div>
              <div className="compliance-frozen-control-grid">{groups.map(([control, checks]) => {
                const status = aggregateControlStatus(checks, control)
                const reasons = [...new Set(checks.flatMap((check) => check.reason_codes ?? [check.reason_code ?? '']).filter(Boolean))]
                return <article className={`compliance-frozen-control compliance-frozen-control-${tone(status)}`} key={control}>
                  <header><span className="compliance-frozen-icon" aria-hidden="true">{statusIcon(status)}</span><div><strong>{controlLabel(control)}</strong><small>{checks.length} policy requirements</small></div><Badge status={status}>{checkStatusLabel(status)}</Badge></header>
                  {reasons.length > 0 && <p>{reasons.map(complianceReasonLabel).join('; ')}</p>}
                  <details><summary>View evidence and location</summary><div className="compliance-frozen-sources">{checks.map((check, index) => <div key={check.clause_id ?? index}>{!historical && <Link to={complianceCheckHref(taskId, row.quote_id, check.clause_id)}>Open this check</Link>}{check.source_refs?.length ? <small>Evidence sources: {check.source_refs.join(', ')}</small> : null}{assessment?.citations?.filter((citation) => check.citation_ids.includes(citation.citation_id)).map((citation) => <details key={citation.citation_id}><summary>{citation.section || 'Policy source text'} · Revision {citation.document_version}</summary><blockquote>{citation.text}</blockquote></details>)}</div>)}</div></details>
                </article>
              })}</div>
              {evidence.length > 0 && <details className="compliance-frozen-materials"><summary>Submitted evidence ({evidence.length})</summary><div>{evidence.map((item) => <article key={item.evidence_id}><strong>{item.facts.material_number} · {item.control_code === 'AMOUNT_APPROVAL' ? `Amount approval ${item.facts.currency} ${item.facts.approval_amount}` : controlLabel(item.control_code)} · Revision {item.version}</strong>{item.files.map((file) => <a className="compliance-file" key={file.file_id} href={complianceEvidenceUrl(taskId, item.evidence_id, file.file_id)}>{file.original_filename}</a>)}</article>)}</div></details>}
            </div>
          </details>
        })}
      </div>}
      {amountRequirements.length > 0 && <details className="compliance-frozen-amounts"><summary>Amount conditions and next actions ({amountRequirements.length})</summary><div className="compliance-table-scroll"><table className="supplier-data-table"><thead><tr><th>Supplier</th><th>Current amount</th><th>Execution stage</th><th>Assessment</th><th>Next action</th></tr></thead><tbody>{amountRequirements.map((item, index) => <tr key={`${item.quote_id}:${item.execution_stage}:${index}`}><td>{item.supplier_name ?? 'Candidate supplier'}</td><td>{item.currency} {item.amount}</td><td>{executionStageLabel(item.execution_stage)}</td><td>{item.approval_confirmed ? 'Approval record verified' : item.triggered ? 'Threshold reached; approval review required' : item.triggered === false ? 'Threshold not reached' : 'Not yet checked'}</td><td>{item.triggered ? item.action ?? 'Follow policy procedure' : '—'}</td></tr>)}</tbody></table></div><p>These are policy requirements. The system verifies approval records but does not grant procurement approval.</p></details>}
      <footer>{historical ? <>A frozen historical record is displayed. {resultId && <Link to={`/tasks/${taskId}/results/${resultId}`}>View the corresponding historical result</Link>}</> : <Link to={`/tasks/${taskId}/compliance`}>View current compliance review and evidence</Link>}</footer>
    </div>
  </details>
}
