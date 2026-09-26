import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import type { PolicyComplianceSupplierAssessment, ResultReason, SupplierComparisonResult } from '../api/types'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'
import { ComplianceAssessmentDetails } from '../components/ComplianceAssessmentDetails'
import { rankingCriterionLabel } from '../lib/rankingCriteria'
import { policyAwareSupplierSelectionExplanation } from '../lib/resultComparison'
import { cleanSummaryText, controlLabel, quoteStatusLabel, reasonText, summaryStatusLabel } from '../lib/presentation'

function errorMessage(error: unknown) {
  return error instanceof ApiClientError ? error.message : 'Failed to load the procurement decision brief.'
}

function displayDate(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('en-SG', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function moneyText(currency: string | undefined, value: string | null | undefined) {
  if (value === null || value === undefined || value === '') return '—'
  const parsed = Number(value)
  const amount = Number.isFinite(parsed)
    ? new Intl.NumberFormat('en-SG', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(parsed)
    : value
  return `${currency ?? ''} ${amount}`.trim()
}

function quantityText(value: number | null, unit: string | undefined) {
  if (value === null) return '—'
  const unitLabels: Record<string, string> = { piece: 'Piece', pieces: 'Piece', unit: 'Piece', units: 'Piece' }
  return `${new Intl.NumberFormat('en-SG').format(value)} ${unitLabels[unit ?? ''] ?? unit ?? ''}`.trim()
}

function paymentText(supplier: SupplierComparisonResult) {
  return supplier.payment_term?.normalized_text
    ?? supplier.payment_term?.raw_text
    ?? (supplier.payment_term?.net_days !== null && supplier.payment_term?.net_days !== undefined
      ? `${supplier.payment_term.net_days} days`
      : '—')
}

function costDeltaText(
  supplier: SupplierComparisonResult,
  recommended: SupplierComparisonResult | undefined,
  currency: string | undefined,
) {
  if (!recommended || supplier.total_cost === null || recommended.total_cost === null) return '—'
  const delta = Number(supplier.total_cost) - Number(recommended.total_cost)
  if (!Number.isFinite(delta)) return '—'
  if (delta === 0) return 'Baseline'
  return `${delta > 0 ? '+' : '−'}${moneyText(currency, String(Math.abs(delta)))}`
}

function policyIssues(assessment: PolicyComplianceSupplierAssessment | undefined) {
  if (!assessment) return []
  return [...new Set(assessment.checks
    .filter((check) => ['FAIL', 'REVIEW_REQUIRED', 'NOT_EVALUATED'].includes(check.status))
    .map((check) => controlLabel(check.control_code)))]
}

function policyEligibility(assessment: PolicyComplianceSupplierAssessment | undefined) {
  if (!assessment) return { label: 'Not recorded', tone: 'neutral' }
  if (assessment.eligibility === 'EXCLUDED' || assessment.status === 'NON_COMPLIANT') {
    return { label: 'Excluded by Policy', tone: 'danger' }
  }
  if (assessment.eligibility === 'UNVERIFIED'
      || assessment.status === 'REVIEW_REQUIRED'
      || assessment.status === 'NOT_EVALUATED') {
    return { label: 'Evidence or Review Required', tone: 'warning' }
  }
  return { label: 'Verified Candidate', tone: 'good' }
}

function communicationGoal(
  supplier: SupplierComparisonResult,
  isRecommended: boolean,
  assessment: PolicyComplianceSupplierAssessment | undefined,
) {
  if (assessment?.eligibility === 'EXCLUDED' || assessment?.status === 'NON_COMPLIANT') {
    return 'Correct the policy evidence and reassess'
  }
  if (assessment?.eligibility === 'UNVERIFIED'
      || assessment?.status === 'REVIEW_REQUIRED'
      || assessment?.status === 'NOT_EVALUATED') {
    return 'Complete the policy evidence and review'
  }
  if (isRecommended) return 'Secure the current quotation and delivery commitment'
  if (supplier.status === 'PENDING') return 'Complete pending information and restore comparability'
  if (supplier.status === 'INFEASIBLE') return 'Confirm whether non-compliant items can be corrected'
  return 'Narrow the gap with the preferred option'
}

function fallbackCommunicationDraft(
  supplier: SupplierComparisonResult,
  isRecommended: boolean,
  recommendation: SupplierComparisonResult | undefined,
  currency: string | undefined,
  assessment: PolicyComplianceSupplierAssessment | undefined,
) {
  const issues = policyIssues(assessment)
  const issueText = issues.length > 0 ? issues.join(', ') : 'policy requirements'
  if (assessment?.eligibility === 'EXCLUDED' || assessment?.status === 'NON_COMPLIANT') {
    return `This supplier failed the ${issueText} check and cannot be recommended. Submit or correct the relevant evidence, complete the review, and then rerun the analysis.`
  }
  if (assessment?.eligibility === 'UNVERIFIED'
      || assessment?.status === 'REVIEW_REQUIRED'
      || assessment?.status === 'NOT_EVALUATED') {
    return `${issueText} verification is incomplete. Add valid evidence or confirmation records and complete the review before comparing price and delivery.`
  }
  if (isRecommended) {
    return `Please confirm in writing that the total cost of ${moneyText(currency, supplier.total_cost)}, the ${supplier.estimated_arrival_date ?? 'current delivery date'}, and the payment terms remain valid through the quotation validity period, together with the supply commitment.`
  }
  const firstIssue = supplier.failed_reasons[0] ?? supplier.pending_reasons[0]
  if (firstIssue) {
    return `Please clarify “${reasonText(firstIssue)}” in writing and provide a revised quotation, delivery date, and validity period that meet the procurement requirements.`
  }
  const delta = costDeltaText(supplier, recommendation, currency)
  return `The cost difference from the preferred option is ${delta}. Please confirm whether price, delivery, or payment terms can be improved and submit a complete updated quotation.`
}

function ReportSection({
  number,
  title,
  id,
  children,
}: {
  number: string
  title: string
  id: string
  children: ReactNode
}) {
  return (
    <section className="summary-report-section" id={id}>
      <header>
        <span>{number}</span>
        <h2>{title}</h2>
      </header>
      <div className="summary-report-section-body">{children}</div>
    </section>
  )
}

function ComparisonReasons({ reasons }: { reasons: ResultReason[] }) {
  if (reasons.length === 0) return null
  return (
    <ul className="summary-reason-list">
      {reasons.map((reason, index) => <li key={reason.code + index}>{reason.message}</li>)}
    </ul>
  )
}

const reportOutline = [
  ['01', 'Executive Summary', 'summary-executive'],
  ['02', 'Procurement Requirements', 'summary-requirement'],
  ['03', 'Quotations and Trade-offs', 'summary-cost'],
  ['04', 'Selection and communication', 'summary-communication'],
  ['05', 'Risk and policy', 'summary-risk'],
  ['06', 'Actions and Records', 'summary-next'],
] as const

export function SummaryPage() {
  const { taskId = '' } = useParams()
  const queryClient = useQueryClient()
  const task = useQuery({
    queryKey: ['tasks', taskId],
    queryFn: () => api.getTask(taskId),
    enabled: Boolean(taskId),
  })
  const summaries = useQuery({
    queryKey: ['tasks', taskId, 'summaries'],
    queryFn: () => api.listSummaries(taskId),
    enabled: Boolean(taskId),
    refetchInterval: (query) => query.state.data?.items.some((item) =>
      ['PENDING', 'RUNNING'].includes(item.status)) ? 1_500 : false,
  })
  const displayedSummary = summaries.data?.items.find((item) => item.is_current)
    ?? summaries.data?.items[0]
  const displayedResultId = displayedSummary?.result_id ?? task.data?.current_result_id
  const result = useQuery({
    queryKey: ['tasks', taskId, 'results', displayedResultId],
    queryFn: () => api.getResult(taskId, displayedResultId!),
    enabled: Boolean(taskId && displayedResultId),
  })
  const selectionGaps = useQuery({
    queryKey: ['tasks', taskId, 'selection-gaps', task.data?.task_revision, displayedResultId, 'summary'],
    queryFn: () => api.getSelectionGaps(taskId, task.data!.task_revision, displayedResultId!),
    enabled: Boolean(taskId && displayedSummary?.is_current && displayedResultId && task.data?.task_revision),
    retry: false,
  })

  useEffect(() => {
    document.body.classList.add('summary-report-route')
    return () => document.body.classList.remove('summary-report-route')
  }, [])

  const refresh = async () => queryClient.invalidateQueries({
    queryKey: ['tasks', taskId, 'summaries'],
  })
  const generate = useMutation({
    mutationFn: () => api.createSummary(
      taskId,
      task.data!.task_revision,
      task.data!.current_result_id!,
      createIdempotencyKey(),
    ),
    onSuccess: refresh,
  })
  const retry = useMutation({
    mutationFn: (summaryId: string) => api.retrySummary(
      taskId,
      summaryId,
      task.data!.task_revision,
      createIdempotencyKey(),
    ),
    onSuccess: refresh,
  })
  const exportDocument = useMutation({
    mutationFn: ({ summaryId, format }: { summaryId: string; format: 'md' | 'docx' }) =>
      api.exportSummary(taskId, summaryId, format),
    onSuccess: ({ blob, filename }) => {
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = filename
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(url)
    },
  })

  if (task.isPending || summaries.isPending) {
    return <section className="card loading-panel">Loading procurement brief…</section>
  }
  if (task.isError || summaries.isError) {
    return <section className="card error-panel" role="alert">{errorMessage(task.error ?? summaries.error)}</section>
  }

  const data = task.data
  const current = displayedSummary
  const retryBudgetExhausted = Boolean(
    current?.status === 'FAILED' && current.calls_used >= current.max_calls,
  )
  const mayGenerate = Boolean(
    data.current_result_id
    && data.status !== 'ABANDONED'
    && (!current || !current.is_current || retryBudgetExhausted),
  )
  const suppliers = result.data?.result.supplier_results ?? []
  const reportRequirement = current?.facts.requirement
    ?? result.data?.input_snapshot?.requirement
    ?? (result.data?.is_current ? data.requirement : null)
  const reportPolicyBinding = current?.facts.policy_binding
    ?? (result.data?.input_snapshot?.policy_set_version
      ? {
          policy_set_version: result.data.input_snapshot.policy_set_version,
          policy_index_version: result.data.input_snapshot.policy_index_version ?? '',
          category: result.data.input_snapshot.policy_category ?? '',
          region: result.data.input_snapshot.policy_region ?? '',
        }
      : result.data?.is_current ? data.policy_binding : null)
  const supplierNames = new Map(suppliers.map((item) => [item.quote_id, item.supplier_name]))
  const narrativeText = (value: string, fallback = 'Regenerate this report to create an updated English narrative for this section.') =>
    /[\u3400-\u9fff]/.test(value) ? fallback : cleanSummaryText(value, supplierNames)
  const narrative = current?.narrative
  const policySections = narrative?.sections.filter((section) =>
    /\u5236\u5ea6|\u653f\u7b56|\u5408\u89c4|\u5ba1\u6279|RoHS/i.test(section.heading + section.text)) ?? []
  const communicationSections = narrative?.sections.filter((section) =>
    /\u6c9f\u901a|\u8be2\u4ef7|\u8c08\u5224|\u6f84\u6e05|\u4e0b\u4e00\u6b65|\u884c\u52a8/i.test(section.heading + section.text)) ?? []
  const riskSections = narrative?.sections.filter((section) =>
    /\u98ce\u9669|\u4ea4\u4ed8|\u672a\u77e5|\u5f85\u786e\u8ba4|\u7f3a\u5931/i.test(section.heading + section.text)) ?? []
  const commercialSections = narrative?.sections.filter((section) =>
    !policySections.includes(section)
    && !communicationSections.includes(section)
    && !riskSections.includes(section)) ?? []
  const feasibleCount = suppliers.filter((item) => item.status === 'FEASIBLE').length
  const pendingCount = suppliers.filter((item) => item.status === 'PENDING').length
  const infeasibleCount = suppliers.filter((item) => item.status === 'INFEASIBLE').length
  const recommendedIds = new Set(
    current?.facts.recommended_quote_ids ?? result.data?.result.recommended_quote_ids ?? [],
  )
  const recommended = suppliers.find((item) => recommendedIds.has(item.quote_id))
  const overviewFallback = reportRequirement
    ? `This brief covers the procurement of ${reportRequirement.manufacturer} ${reportRequirement.manufacturer_part_number} (${reportRequirement.package}), with a required quantity of ${reportRequirement.required_quantity} ${reportRequirement.quantity_unit}, a budget of ${reportRequirement.currency} ${reportRequirement.budget_amount}, and delivery required by ${reportRequirement.delivery_deadline}. It evaluates ${suppliers.length} submitted ${suppliers.length === 1 ? 'quotation' : 'quotations'}${recommended ? ` and identifies ${recommended.supplier_name} as the preliminary recommended supplier at a confirmed total cost of ${moneyText(reportRequirement.currency, recommended.total_cost)}` : '; no publishable recommendation is currently available'}.`
    : 'The frozen procurement facts are unavailable. Regenerate this report to create an English executive summary.'
  const policyAssessments = new Map(
    (result.data?.policy_compliance.assessments ?? []).map((assessment) => [assessment.quote_id, assessment]),
  )
  const reportDecisionProfile = current?.facts.decision_profile
    ?? result.data?.input_snapshot?.decision_profile
    ?? (result.data?.is_current ? data.decision_profile : undefined)
  const currentRanking = reportDecisionProfile?.preferences.primary_criterion
    ?? reportRequirement?.ranking_preference
  const communicationDrafts = new Map(
    selectionGaps.data?.clarification_drafts.map((draft) => [draft.quote_id, draft.text]) ?? [],
  )
  const selectionSummaries = new Map(suppliers.map((supplier) => [
    supplier.quote_id,
    policyAwareSupplierSelectionExplanation(
      supplier,
      recommended,
      currentRanking,
      reportRequirement?.currency,
      recommendedIds.has(supplier.quote_id),
      policyAssessments.get(supplier.quote_id),
    ),
  ]))
  const chartRows = suppliers.filter((item) => item.total_cost !== null)
  const budget = Number(reportRequirement?.budget_amount)
  const chartMax = Math.max(
    Number.isFinite(budget) ? budget : 0,
    ...chartRows.map((item) => Number(item.total_cost) || 0),
    1,
  )
  const tradeoffRows = chartRows.filter((item) => {
    const arrival = item.estimated_arrival_date ? Date.parse(`${item.estimated_arrival_date}T00:00:00Z`) : Number.NaN
    return Number.isFinite(arrival) && Number.isFinite(Number(item.total_cost))
  })
  const tradeoffCosts = tradeoffRows.map((item) => Number(item.total_cost))
  const tradeoffDates = tradeoffRows.map((item) => Date.parse(`${item.estimated_arrival_date}T00:00:00Z`))
  const minTradeoffCost = tradeoffCosts.length > 0 ? Math.min(...tradeoffCosts) : 0
  const maxTradeoffCost = tradeoffCosts.length > 0 ? Math.max(...tradeoffCosts) : 1
  const minTradeoffDate = tradeoffDates.length > 0 ? Math.min(...tradeoffDates) : 0
  const maxTradeoffDate = tradeoffDates.length > 0 ? Math.max(...tradeoffDates) : 1
  const referenceCount = new Set(
    narrative?.sections.flatMap((section) => section.reference_ids) ?? [],
  ).size
  const policyCounts = result.data?.policy_compliance.counts
  const isExportable = current?.status === 'SUCCEEDED' && Boolean(narrative && reportRequirement)
  const generatedAt = current ? displayDate(current.updated_at) : '—'

  const exportPdf = () => {
    if (!isExportable || !current) return
    const previousTitle = document.title
    document.title = `${data.task_name}_Procurement_Decision_Brief_Revision_${current.task_revision}`
    window.addEventListener('afterprint', () => { document.title = previousTitle }, { once: true })
    window.print()
  }

  return (
    <div className="page-stack summary-page">
      <TaskWorkspaceHeader
        taskId={data.task_id}
        scenarioId={data.scenario_id}
        title={data.task_name}
        subtitle={`${data.requirement.required_quantity} ${data.requirement.quantity_unit} · ${data.quotes.length} submitted quotations`}
        status={data.status}
        revision={data.task_revision}
        resultId={data.current_result_id}
        quoteCount={data.quotes.length}
        summaryComplete={data.summary_completed}
        progress={data.progress}
        active="summary"
      />

      <section className="summary-report-toolbar card workspace-page-lead">
        <div className="workspace-page-lead-copy">
          <h2>Summary</h2>
          {current && <span>{data.task_name} · Revision {current.task_revision} · {generatedAt}</span>}
        </div>
        <div className="summary-report-actions">
          {mayGenerate && (
            <button
              className="button button-submit"
              type="button"
              disabled={generate.isPending}
              onClick={() => generate.mutate()}
            >{generate.isPending
                ? 'Creating…'
                : retryBudgetExhausted ? 'Generate current version' : 'Generate'}</button>
          )}
          {current?.status === 'FAILED'
            && current.is_current
            && !retryBudgetExhausted
            && data.status !== 'ABANDONED' && (
            <button
              className="button button-secondary"
              type="button"
              disabled={retry.isPending}
              onClick={() => retry.mutate(current.summary_id)}
            >Retry generation</button>
          )}
        </div>
      </section>

      {(generate.isError || retry.isError || exportDocument.isError) && (
        <div className="form-error" role="alert">{errorMessage(generate.error ?? retry.error ?? exportDocument.error)}</div>
      )}
      {current && !current.is_current && (
        <div className="run-notice">This is the historical procurement summary for revision {current.task_revision}. Requirements, quotations, and policy information are shown as frozen at that time.</div>
      )}
      {current?.status === 'FAILED' && (
        <section className="card error-panel">
          <strong>Brief generation failed</strong>
          <p>{current.error_message}</p>
          <small>{current.calls_used} attempts made</small>
        </section>
      )}

      {!narrative && current?.status !== 'FAILED' && (
        <section className="card summary-report-empty">
          <div>
            <h2>{current ? 'Generating procurement decision brief' : 'Procurement decision brief not yet generated'}</h2>
            <p>{current ? 'The page will update automatically when generation is complete.' : 'It can be generated after decision comparison is complete.'}</p>
            {!current && !data.current_result_id && <Link className="button button-secondary" to={`/tasks/${taskId}/decision`}>Go to decision comparison</Link>}
          </div>
        </section>
      )}

      {narrative && current && !reportRequirement && (
        <section className="card error-panel" role="alert">
          <strong>Historical procurement requirements are unavailable</strong>
          <p>The brief does not contain the frozen requirements for its revision. Current task data will not be substituted.</p>
        </section>
      )}

      {narrative && current && reportRequirement && (
        <div className="summary-report-layout">
          <nav className="summary-report-outline" aria-label="Report contents">
            <strong>Report contents</strong>
            {reportOutline.map(([number, label, id], index) => (
              <a className={index === 0 ? 'is-active' : ''} href={`#${id}`} key={id}>
                <span>{number}</span>{label}
              </a>
            ))}
          </nav>

          <article className="summary-report-paper">
            <header className="summary-report-cover">
              <div>
                <p>PROCUREMENT DECISION BRIEF</p>
                <h1>{reportRequirement.manufacturer_part_number} Procurement Summary</h1>
                <span>{reportRequirement.required_quantity} {reportRequirement.quantity_unit} · {reportRequirement.package} · {reportRequirement.manufacturer_part_number}</span>
              </div>
              <div className="summary-report-stamp">
                <strong>{recommended ? 'Recommended' : 'Analysed'}</strong>
                <small>Task revision {current.task_revision}</small>
              </div>
            </header>

            <ReportSection number="01" title="Executive Summary" id="summary-executive">
              <p>{narrativeText(narrative.overview, overviewFallback)}</p>
              <div className={`summary-recommendation-callout${recommended ? '' : ' no-recommendation'}`}>
                <span aria-hidden="true">{recommended ? '✓' : '!'}</span>
                <div>
                  <strong>{recommended ? `Proceed with recommended supplier ${recommended.supplier_name}` : 'No clear recommendation can currently be made'}</strong>
                  <p>{recommended
                    ? `Confirmed total cost ${moneyText(reportRequirement.currency, recommended.total_cost)} · Expected delivery date ${recommended.estimated_arrival_date ?? 'Pending confirmation'} · Ranking basis ${rankingCriterionLabel(currentRanking) || 'Current decision settings'}`
                    : 'Resolve pending information before regenerating the procurement conclusion.'}</p>
                </div>
                <small>Preliminary recommendation; not final procurement approval.</small>
              </div>
            </ReportSection>

            <ReportSection number="02" title="Procurement Requirements" id="summary-requirement">
              <div className="summary-requirement-layout">
                <p>
                  This procurement is for {reportRequirement.manufacturer} {reportRequirement.manufacturer_part_number},
                  with a required quantity of {reportRequirement.required_quantity} {reportRequirement.quantity_unit},
                  a budget ceiling of {reportRequirement.currency} {reportRequirement.budget_amount},
                  and delivery required by {reportRequirement.delivery_deadline}.
                </p>
                <dl>
                  <div><dt>Quantity</dt><dd>{reportRequirement.required_quantity} {reportRequirement.quantity_unit}</dd></div>
                  <div><dt>Package</dt><dd>{reportRequirement.package}</dd></div>
                  <div><dt>Budget</dt><dd>{reportRequirement.currency} {reportRequirement.budget_amount}</dd></div>
                  <div><dt>Delivery deadline</dt><dd>{reportRequirement.delivery_deadline}</dd></div>
                </dl>
              </div>
            </ReportSection>

            <ReportSection number="03" title="Quotations and Trade-offs" id="summary-cost">
              <p className="summary-section-intro">
                Policy eligibility is checked before cost, delivery, and feasibility are compared. Natural-language text only explains the frozen result; it does not recalculate or alter the recommendation.
              </p>
              <div className="summary-comparison-scroll">
                <table className="summary-comparison-table" style={{ minWidth: Math.max(700, 142 + suppliers.length * 175) }}>
                  <thead><tr><th>Criterion</th>{suppliers.map((supplier) => (
                    <th className={recommendedIds.has(supplier.quote_id) ? 'is-recommended' : ''} key={supplier.quote_id}>
                      {supplier.supplier_name}
                      {recommendedIds.has(supplier.quote_id) && <span>Recommendation</span>}
                    </th>
                  ))}</tr></thead>
                  <tbody>
                    <tr><th>Policy eligibility</th>{suppliers.map((supplier) => {
                      const eligibility = policyEligibility(policyAssessments.get(supplier.quote_id))
                      return <td className={recommendedIds.has(supplier.quote_id) ? 'is-recommended' : ''} key={supplier.quote_id}><span className={`summary-tradeoff summary-tradeoff-${eligibility.tone}`}>{eligibility.label}</span></td>
                    })}</tr>
                    <tr><th>Confirmed total cost</th>{suppliers.map((supplier) => <td className={recommendedIds.has(supplier.quote_id) ? 'is-recommended is-best' : ''} key={supplier.quote_id}>{moneyText(reportRequirement.currency, supplier.total_cost)}</td>)}</tr>
                    <tr><th>Difference from preferred option</th>{suppliers.map((supplier) => <td className={recommendedIds.has(supplier.quote_id) ? 'is-recommended' : ''} key={supplier.quote_id}>{costDeltaText(supplier, recommended, reportRequirement.currency)}</td>)}</tr>
                    <tr><th>Actual Order Quantity</th>{suppliers.map((supplier) => <td className={recommendedIds.has(supplier.quote_id) ? 'is-recommended' : ''} key={supplier.quote_id}>{quantityText(supplier.actual_quantity, reportRequirement.quantity_unit)}</td>)}</tr>
                    <tr><th>Expected Delivery Date</th>{suppliers.map((supplier) => <td className={recommendedIds.has(supplier.quote_id) ? 'is-recommended' : ''} key={supplier.quote_id}>{supplier.estimated_arrival_date ?? '—'}</td>)}</tr>
                    <tr><th>Payment terms</th>{suppliers.map((supplier) => <td className={recommendedIds.has(supplier.quote_id) ? 'is-recommended' : ''} key={supplier.quote_id}>{paymentText(supplier)}</td>)}</tr>
                    <tr><th>Feasibility</th>{suppliers.map((supplier) => <td className={recommendedIds.has(supplier.quote_id) ? 'is-recommended' : ''} key={supplier.quote_id}><span className={`summary-status summary-status-${supplier.status.toLowerCase()}`}>{quoteStatusLabel(supplier.status)}</span></td>)}</tr>
                    <tr><th>Trade-off</th>{suppliers.map((supplier) => {
                      const selection = selectionSummaries.get(supplier.quote_id)
                      return <td className={recommendedIds.has(supplier.quote_id) ? 'is-recommended' : ''} key={supplier.quote_id}><strong className={`summary-tradeoff summary-tradeoff-${selection?.tone ?? 'neutral'}`}>{selection?.label}</strong><small>{selection?.detail}</small></td>
                    })}</tr>
                  </tbody>
                </table>
              </div>
              <div className="summary-cost-insight-grid">
                <figure className="summary-cost-chart">
                  <figcaption>Confirmed total cost relative to budget</figcaption>
                  <div className="summary-cost-chart-body">
                    {chartRows.map((supplier) => (
                      <div className="summary-cost-row" key={supplier.quote_id}>
                        <span>{supplier.supplier_name}</span>
                        <div><i className={recommendedIds.has(supplier.quote_id) ? 'is-recommended' : ''} style={{ width: `${Math.max(7, ((Number(supplier.total_cost) || 0) / chartMax) * 100)}%` }} /></div>
                        <strong>{moneyText(reportRequirement.currency, supplier.total_cost)}</strong>
                      </div>
                    ))}
                  </div>
                  <small>Budget ceiling: {moneyText(reportRequirement.currency, reportRequirement.budget_amount)}</small>
                </figure>
                <figure className="summary-tradeoff-chart">
                  <figcaption>Cost vs expected delivery</figcaption>
                  <div className="summary-tradeoff-plot">
                    <span className="summary-axis-y">Lower cost is better</span>
                    <span className="summary-axis-x">Expected delivery date → later</span>
                    {tradeoffRows.map((supplier) => {
                      const costRange = maxTradeoffCost - minTradeoffCost
                      const dateRange = maxTradeoffDate - minTradeoffDate
                      const cost = Number(supplier.total_cost)
                      const arrival = Date.parse(`${supplier.estimated_arrival_date}T00:00:00Z`)
                      const left = dateRange > 0 ? 12 + ((arrival - minTradeoffDate) / dateRange) * 70 : 46
                      const top = costRange > 0 ? 12 + ((cost - minTradeoffCost) / costRange) * 62 : 40
                      return <span className={`summary-tradeoff-point${recommendedIds.has(supplier.quote_id) ? ' is-recommended' : ''}`} style={{ left: `${left}%`, top: `${top}%` }} key={supplier.quote_id}><i /><small>{supplier.supplier_name}</small></span>
                    })}
                    {tradeoffRows.length === 0 && <p>No quotation currently has both cost and delivery confirmed.</p>}
                  </div>
                </figure>
              </div>
              <div className="summary-ai-brief">
                <header><span>AI</span><div><strong>Analysis Notes</strong><small>Generated from frozen facts and not used in calculations</small></div></header>
                {commercialSections.slice(0, 3).map((section, index) => (
                  <section key={section.heading + index}><h3>{narrativeText(section.heading)}</h3><p>{narrativeText(section.text)}</p></section>
                ))}
                {commercialSections.length === 0 && <p>{narrativeText(narrative.overview, overviewFallback)}</p>}
              </div>
            </ReportSection>

            <ReportSection number="04" title="Selection Rationale and Suggested Communication" id="summary-communication">
              <p className="summary-section-intro">Explain the recommendation or non-selection reasons for each supplier and provide communication wording for procurement review.</p>
              <div className="summary-communication-report">
                {suppliers.map((supplier) => {
                  const isRecommended = recommendedIds.has(supplier.quote_id)
                  const assessment = policyAssessments.get(supplier.quote_id)
                  const needsPolicyAction = assessment?.eligibility === 'EXCLUDED'
                    || assessment?.eligibility === 'UNVERIFIED'
                    || assessment?.status === 'NON_COMPLIANT'
                    || assessment?.status === 'REVIEW_REQUIRED'
                    || assessment?.status === 'NOT_EVALUATED'
                  const draft = (!needsPolicyAction ? communicationDrafts.get(supplier.quote_id) : undefined)
                    ?? fallbackCommunicationDraft(supplier, isRecommended, recommended, reportRequirement.currency, assessment)
                  return (
                    <article className={isRecommended ? 'is-recommended' : ''} key={supplier.quote_id}>
                      <h3>{supplier.supplier_name}</h3>
                      <p className="summary-selection-line"><strong>{isRecommended ? 'Recommendation basis' : 'Trade-off'}: </strong>{selectionSummaries.get(supplier.quote_id)?.detail}</p>
                      <small>Communication objective: {communicationGoal(supplier, isRecommended, assessment)}</small>
                      <p><strong>Suggested message: </strong>{draft}</p>
                    </article>
                  )
                })}
              </div>
              {communicationSections.length > 0 && <div className="summary-communication-ai"><strong>AI communication suggestions</strong><p>{narrativeText(communicationSections[0].text)}</p></div>}
            </ReportSection>

            <ReportSection number="05" title="Risks, Policy, and Decision Gates" id="summary-risk">
              <ComplianceAssessmentDetails assessment={result.data?.policy_compliance} taskId={taskId} resultId={result.data?.result_id} historical={result.data?.is_current === false} legacy={result.data?.legacy_compliance} />
              <p>
                There are currently {feasibleCount} feasible quotations, {pendingCount} awaiting confirmation, and {infeasibleCount} that do not meet requirements.
                The following gates determine readiness for formal approval. Unknown values are never treated as zero or automatically compliant.
              </p>
              <div className="summary-risk-table-wrap">
                <table className="summary-risk-table">
                  <tbody>
                    <tr><th>Quotation completeness</th><td><strong className={pendingCount > 0 ? 'risk-review' : 'risk-low'}>{pendingCount > 0 ? `${pendingCount} pending confirmation` : 'Frozen'}</strong></td><td>{pendingCount > 0 ? 'Complete fields that may affect feasibility or ranking.' : 'Cost, delivery, and quantity all come from the current frozen result.'}</td></tr>
                    <tr><th>Policy evidence</th><td><strong className={(policyCounts?.REVIEW_REQUIRED ?? 0) > 0 ? 'risk-review' : 'risk-low'}>{reportPolicyBinding ? `${policyCounts?.REVIEW_REQUIRED ?? 0} require review` : 'No policy bound'}</strong></td><td>{reportPolicyBinding ? `Currently bound to policy revision ${reportPolicyBinding.policy_set_version}.` : 'An applicable policy must be added before formal procurement.'}</td></tr>
                    <tr><th>Recommendation Stability</th><td><strong className={pendingCount > 0 ? 'risk-review' : 'risk-low'}>{pendingCount > 0 ? 'Attention required' : 'Currently stable'}</strong></td><td>A new version and recalculation are required whenever the inputs change.</td></tr>
                    <tr><th>Approval Status</th><td><strong className="risk-review">Not approved</strong></td><td>This report provides decision support and does not replace approval by an authorised procurement representative.</td></tr>
                  </tbody>
                </table>
              </div>
              {riskSections.slice(0, 2).map((section, index) => <div className="summary-risk-note" key={section.heading + index}><strong>{narrativeText(section.heading)}</strong><p>{narrativeText(section.text)}</p></div>)}
              {policySections.map((section, index) => (
                <div className="summary-policy-note" key={section.heading + index}>
                  <strong>{narrativeText(section.heading)}</strong>
                  <p>{narrativeText(section.text)}</p>
                </div>
              ))}
              <div className="summary-policy-status">
                <span>Compliance confirmed <strong>{policyCounts?.COMPLIANT ?? 0}</strong></span>
                <span>Human confirmation required <strong>{policyCounts?.REVIEW_REQUIRED ?? feasibleCount}</strong></span>
                <span>Not included in assessment <strong>{policyCounts?.NOT_EVALUATED ?? infeasibleCount}</strong></span>
              </div>
            </ReportSection>

            <ReportSection number="06" title="Actions and Records" id="summary-next">
              <ComparisonReasons reasons={result.data?.result.comparison_reasons ?? []} />
              <div className="summary-action-list">
                <div><span>1</span><p><strong>Complete supplier communication</strong>Confirm price, delivery, fees, and outstanding information from the communication playbook.</p></div>
                <div><span>2</span><p><strong>Close policy review items</strong>Review policy evidence and exception conditions, retaining the manual confirmation record.</p></div>
                <div><span>3</span><p><strong>Submit for formal approval</strong>Attach the source quotations, comparison result, and procurement summary for an authorised decision-maker.</p></div>
              </div>
              <p className="summary-disclaimer">{narrativeText(narrative.disclaimer, 'This report provides decision support and does not constitute final procurement approval.')}</p>
            </ReportSection>

            <footer className="summary-report-footer">
              <span>Procurement summary · Revision {current.task_revision}</span>
              <span>Generated {generatedAt}</span>
            </footer>
          </article>

          <aside className="summary-report-rail">
            <h2>Report Information</h2>
            <dl>
              <div><dt>Status</dt><dd className={isExportable ? 'report-ready' : ''}>{isExportable ? 'Ready to export' : summaryStatusLabel(current.status)}</dd></div>
              <div><dt>Report coverage</dt><dd>6 sections</dd></div>
              <div><dt>Supporting evidence</dt><dd>{suppliers.length} quotations · {referenceCount} references</dd></div>
              <div><dt>Revision</dt><dd>Revision {current.task_revision}</dd></div>
              <div><dt>Generated At</dt><dd>{generatedAt}</dd></div>
            </dl>
            <p>Every export is bound to the current Summary, Result and Task Revision and includes report text, charts, usage boundaries and version information.</p>
            <div className="summary-export-options">
              <button className="button button-submit" type="button" disabled={!isExportable} onClick={exportPdf}>PDF</button>
              <button className="button button-secondary" type="button" disabled={!isExportable || exportDocument.isPending} onClick={() => exportDocument.mutate({ summaryId: current.summary_id, format: 'md' })}>Markdown</button>
              <button className="button button-secondary" type="button" disabled={!isExportable || exportDocument.isPending} onClick={() => exportDocument.mutate({ summaryId: current.summary_id, format: 'docx' })}>Word</button>
            </div>
            <small>Use PDF for formal submission, Markdown for collaboration and Word for further editing.</small>
          </aside>
        </div>
      )}

      {summaries.data.items.length > 1 && (
        <section className="summary-history-section">
          <div className="section-heading"><h2>Historical procurement brief</h2><span>{summaries.data.items.length}</span></div>
          <div className="summary-history-list">
            {summaries.data.items.map((item) => (
              <article className="card summary-history-record" key={item.summary_id}>
                <strong>{summaryStatusLabel(item.status)}</strong>
                <span>Procurement task revision {item.task_revision}</span>
                <small>{displayDate(item.created_at)}</small>
              </article>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
