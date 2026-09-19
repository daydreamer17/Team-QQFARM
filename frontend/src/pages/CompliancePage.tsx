import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { api, ApiClientError } from '../api/client'
import type { PolicyCitation, PolicyComplianceSupplierAssessment, PolicyRetrievalResult, SupplierComparisonResult } from '../api/types'
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
          ? `已找到 ${retrieval.citations.length} 条可引用依据。找到条款不等于供应商已经通过核验。`
          : '当前制度依据不足，不能据此给出通过结论。'}
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

function policyConclusion(assessment: PolicyComplianceSupplierAssessment | undefined) {
  if (assessment?.status === 'COMPLIANT') return { tone: 'passed', icon: '✓', label: '制度核验通过' }
  if (assessment?.status === 'NON_COMPLIANT') return { tone: 'failed', icon: '×', label: '制度核验不通过' }
  if (assessment?.status === 'REVIEW_REQUIRED') return { tone: 'pending', icon: '!', label: '缺少供应商证明' }
  if (assessment?.status === 'NOT_EVALUATED') return { tone: 'unknown', icon: '—', label: '未进入制度核验' }
  return { tone: 'unknown', icon: '—', label: '尚无核验结果' }
}

function checkStatusLabel(status: string) {
  if (status === 'PASS') return '通过'
  if (status === 'FAIL') return '不通过'
  if (status === 'REVIEW_REQUIRED') return '缺少证据'
  return '未核验'
}

function SupplierPolicyRow({
  supplier,
  assessment,
}: {
  supplier: SupplierComparisonResult
  assessment: PolicyComplianceSupplierAssessment | undefined
}) {
  const conclusion = policyConclusion(assessment)
  const quoteTone = supplier.status === 'FEASIBLE' ? 'passed' : supplier.status === 'INFEASIBLE' ? 'failed' : 'pending'
  return (
    <article className="supplier-policy-row">
      <div className="supplier-policy-name"><strong>{supplier.supplier_name}</strong><span>第 {supplier.quote_version} 版报价</span></div>
      <div className={`supplier-policy-result result-${quoteTone}`}>
        <span className="supplier-policy-icon">{supplier.status === 'FEASIBLE' ? '✓' : supplier.status === 'INFEASIBLE' ? '×' : '!'}</span>
        <div><small>采购要求</small><strong>{quoteStatusLabel(supplier.status)}</strong></div>
      </div>
      <div className={`supplier-policy-result result-${conclusion.tone}`}>
        <span className="supplier-policy-icon">{conclusion.icon}</span>
        <div><small>制度结论</small><strong>{conclusion.label}</strong>{assessment?.status !== 'NOT_EVALUATED' && <ul className="policy-check-summary">{assessment?.checks.map((check) => <li key={check.control_code}><span>{controlLabel(check.control_code)}</span><b>{checkStatusLabel(check.status)}</b></li>)}</ul>}</div>
      </div>
    </article>
  )
}

export function CompliancePage() {
  const { taskId = '' } = useParams()
  const task = useQuery({ queryKey: ['tasks', taskId], queryFn: () => api.getTask(taskId), enabled: Boolean(taskId) })
  const results = useQuery({ queryKey: ['tasks', taskId, 'results'], queryFn: () => api.listResults(taskId), enabled: Boolean(taskId) })

  if (task.isPending) return <section className="card loading-panel">正在读取制度检查…</section>
  if (task.isError) return <section className="card error-panel" role="alert">{errorMessage(task.error)}</section>

  const data = task.data
  const latestResult = results.data?.find((item) => item.is_current) ?? results.data?.[0]
  const retrievals = latestResult?.policy_retrievals ?? []
  const suppliers = latestResult?.result.supplier_results ?? []
  const policyIssue = data.current_issue?.issue_type === 'POLICY_EVIDENCE_REVIEW'
  const compliance = latestResult?.policy_compliance
  const assessmentByQuote = new Map(compliance?.assessments.map((item) => [item.quote_id, item]) ?? [])
  const okCount = retrievals.filter((retrieval) => retrieval.status === 'OK').length
  const allEvidenceReady = retrievals.length > 0 && okCount === retrievals.length

  return (
    <div className="page-stack compliance-page">
      <TaskWorkspaceHeader taskId={data.task_id} scenarioId={data.scenario_id} title={data.requirement.manufacturer_part_number} subtitle={`${data.requirement.required_quantity} ${data.requirement.quantity_unit} · ${data.quotes.length} 份报价`} status={data.status} revision={data.task_revision} resultId={data.current_result_id} quoteCount={data.quotes.length} summaryComplete={data.summary_completed} progress={data.progress} reviewBlocked={Boolean(data.current_issue)} policyReviewBlocked={policyIssue} active="compliance" />

      <section className="review-workspace-lead">
        <div><p className="eyebrow">制度检查</p><h2>每份报价是否符合制度</h2><p>先看逐供应商结论，再按需展开制度依据。制度检索成功不等于供应商已经通过。</p></div>
        <span className={`status-pill ${allEvidenceReady ? 'status-pending' : 'status-offline'}`}>{allEvidenceReady ? '制度依据已准备' : '制度依据不完整'}</span>
      </section>

      {!data.policy_binding ? (
        <section className="card compliance-empty"><span className="compliance-empty-mark">—</span><div><h2>本任务未绑定制度</h2><p>系统没有执行制度检索，因此不能把任何报价标记为“制度通过”。</p></div></section>
      ) : (
        <>
          <section className="policy-binding-card policy-binding-friendly">
            <div><span>检查范围</span><strong>{data.policy_binding.category === 'Electronics' ? '电子产品采购' : data.policy_binding.category}</strong></div>
            <div><span>适用地区</span><strong>{data.policy_binding.region === 'SG' ? '新加坡' : data.policy_binding.region}</strong></div>
            <div><span>制度要求</span><strong>{retrievals.length} 项</strong></div>
            <div><span>已有依据</span><strong>{okCount} 项</strong></div>
          </section>

          {policyIssue && <IssuePanel task={data} onRefresh={() => void Promise.all([task.refetch(), results.refetch()])} />}
          {results.isPending && <section className="card loading-panel">正在整理逐供应商制度结论…</section>}
          {results.isError && <section className="card error-panel" role="alert">{errorMessage(results.error)}</section>}
          {!results.isPending && !latestResult && <section className="card compliance-empty compact"><div><h2>尚未生成决策结果</h2><p>完成报价分析后，这里会显示逐供应商状态和制度依据。</p></div></section>}

          {suppliers.length > 0 && (
            <section className="supplier-policy-section">
              <div className="section-heading"><div><h2>逐供应商检查结果</h2><p>图标分别表示采购要求和制度核验所处状态。</p></div><span>{suppliers.length} 份报价</span></div>
              {compliance && <div className={`policy-compliance-summary ${compliance.counts.COMPLIANT > 0 ? 'has-compliant' : ''}`}><strong>{compliance.counts.COMPLIANT > 0 ? `${compliance.counts.COMPLIANT} 家供应商已通过制度核验` : '暂无已核验合格供应商'}</strong><span>{compliance.counts.REVIEW_REQUIRED} 家缺少证明 · {compliance.counts.NON_COMPLIANT} 家不通过 · {compliance.counts.NOT_EVALUATED} 家未核验</span></div>}
              <div className="supplier-policy-list">{suppliers.map((supplier) => <SupplierPolicyRow supplier={supplier} assessment={assessmentByQuote.get(supplier.quote_id)} key={supplier.quote_id} />)}</div>
            </section>
          )}

          {latestResult && retrievals.length === 0 && <section className="card compliance-empty compact"><div><h2>当前结果没有制度检索记录</h2><p>不能将其解释为制度通过。</p></div></section>}
          {retrievals.length > 0 && <section className="policy-evidence-section"><div className="section-heading"><div><h2>制度依据</h2><p>仅在需要追溯时展开引用内容。</p></div><span>{okCount} / {retrievals.length} 项找到依据</span></div><div className="policy-retrieval-list">{retrievals.map((retrieval) => <RetrievalCard retrieval={retrieval} key={retrieval.retrieval_id} />)}</div></section>}
        </>
      )}

      <p className="result-boundary">本页只展示系统已保存的制度检查事实，不代表采购批准、签约或下单授权。</p>
    </div>
  )
}
