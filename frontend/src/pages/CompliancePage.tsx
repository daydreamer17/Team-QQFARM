import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
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
  const compliance = latestResult?.policy_compliance
  const assessmentByQuote = new Map(compliance?.assessments.map((item) => [item.quote_id, item]) ?? [])
  const okCount = retrievals.filter((retrieval) => retrieval.status === 'OK').length
  const allEvidenceReady = retrievals.length > 0 && okCount === retrievals.length

  return (
    <div className="page-stack compliance-page">
      <TaskWorkspaceHeader taskId={data.task_id} scenarioId={data.scenario_id} title={data.task_name} subtitle={`${data.requirement.required_quantity} ${data.requirement.quantity_unit} · ${data.quotes.length} 份报价`} status={data.status} revision={data.task_revision} resultId={data.current_result_id} quoteCount={data.quotes.length} summaryComplete={data.summary_completed} progress={data.progress} reviewBlocked={Boolean(data.current_issue)} policyReviewBlocked={policyIssue} active="compliance" />

      <section className="review-workspace-lead">
        <div><h2>制度检查</h2>{data.policy_binding && <p>查看各供应商的制度核验结果和依据。</p>}</div>
        {data.policy_binding && <span className={`status-pill ${allEvidenceReady ? 'status-pending' : 'status-offline'}`}>{!currentResultId ? '等待分析' : allEvidenceReady ? '依据已准备' : '依据待补充'}</span>}
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
          {currentResultId && currentResult.isPending && <section className="card loading-panel">正在整理逐供应商制度结论…</section>}
          {currentResult.isError && <section className="card error-panel" role="alert">{errorMessage(currentResult.error)}</section>}
          {!currentResultId && (
            <section className="card compliance-empty compact">
              <div>
                <h2>{['QUEUED', 'RUNNING'].includes(data.status) ? '正在生成当前版本结果' : '当前版本没有有效决策结果'}</h2>
                <p>历史结果不会作为当前制度结论显示。完成本次分析后，这里会自动更新。</p>
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

      {data.policy_binding && <p className="result-boundary">制度检查结果仅供核验，不代表采购批准。</p>}
    </div>
  )
}
