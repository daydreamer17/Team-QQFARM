import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import type {
  FieldEvidence,
  PolicyComplianceSupplierAssessment,
  QuoteDecisionImpact,
  QuoteFieldsResponse,
  ResultReason,
  SupplierComparisonResult,
  TaskDetail,
} from '../api/types'
import { DecisionScenarioWorkspace } from '../components/DecisionScenarioWorkspace'
import { MatrixPaymentTerm, MatrixSupplierPerformance } from '../components/SupplierMatrixDetails'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'
import { ComplianceAssessmentDetails } from '../components/ComplianceAssessmentDetails'
import { OverlayPortal } from '../components/OverlayPortal'
import { rankingCriterionLabel } from '../lib/rankingCriteria'
import { supplierSelectionExplanation, withPolicyAssessment } from '../lib/resultComparison'
import {
  fieldLabel,
  originLabel,
  quoteStatusLabel,
  reasonText,
  validationStatusLabel,
} from '../lib/presentation'

function errorMessage(error: unknown) {
  return error instanceof ApiClientError ? error.message : 'Failed to load result.'
}

function reanalysisErrorMessage(error: unknown) {
  if (error instanceof ApiClientError) {
    if (error.code === 'task_revision_conflict') return 'The task has changed. Refresh and try again.'
    if (error.code === 'graph_run_active') return 'An analysis is already running. Return to the decision page to view its status.'
    return error.message
  }
  return 'Failed to start reanalysis.'
}

function valueText(value: unknown) {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  return String(value)
}

function moneyText(currency: string | undefined, value: string | null | undefined) {
  if (value === null || value === undefined) return '—'
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

function displayDate(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('en-SG', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)
}

function evidenceLocation(evidence: FieldEvidence) {
  const parts: string[] = []
  if (evidence.page_number !== null) parts.push('Page ' + evidence.page_number)
  if (evidence.row_number !== null) parts.push('Row ' + evidence.row_number)
  if (evidence.column_name) parts.push('Column ' + evidence.column_name)
  return parts.join(' · ') || evidence.kind || 'Source location'
}

function recommendationNarrative(
  primary: SupplierComparisonResult | undefined,
  currency: string | undefined,
  ranking: string | undefined,
) {
  if (!primary) return 'The current result has no publishable recommendation. Resolve blocking items or complete pending information first.'
  const rankingText = rankingCriterionLabel(ranking) || 'current ranking rules'
  const arrival = primary.estimated_arrival_date
    ? `, with expected arrival on ${primary.estimated_arrival_date}`
    : ''
  return `Under “${rankingText}”, ${primary.supplier_name} meets the current quotation-comparison conditions and is preferred with a confirmed total cost of ${moneyText(currency, primary.total_cost)}${arrival}.`
}

function impactMessage(status: string, fallback: string) {
  if (status === 'NON_BLOCKING') return 'This quotation already has a confirmed non-compliance issue, so these unknown fields do not currently change the recommendation.'
  if (status === 'REQUIRES_INVESTIGATION') return 'These unknown fields may affect quotation feasibility or ranking. Follow-up is required before the recommendation can be considered stable.'
  if (status === 'UNDETERMINED') return 'There is not enough information to determine whether these unknown fields would change the recommendation. Manual confirmation is required.'
  return fallback
}

function matrixSelectionSummary(
  supplier: SupplierComparisonResult,
  primary: SupplierComparisonResult | undefined,
  ranking: string | undefined,
  currency: string | undefined,
  impact: QuoteDecisionImpact | undefined,
  recommended: boolean,
  policyAssessment: PolicyComplianceSupplierAssessment | undefined,
) {
  const commercial = impact?.unknown_fields.length && impact.status !== 'NON_BLOCKING'
    ? {
      label: `${impact.unknown_fields.length} pending confirmation`,
      detail: impactMessage(impact.status, impact.message),
      tone: 'warning' as const,
    }
    : supplierSelectionExplanation(supplier, primary, ranking, currency, recommended)
  return withPolicyAssessment(commercial, policyAssessment)
}

function Reasons({ title, reasons }: { title: string; reasons: ResultReason[] }) {
  if (reasons.length === 0) return null
  return (
    <section className="drawer-reasons">
      <h3>{title}</h3>
      <ul>
        {reasons.map((reason, index) => (
          <li key={reason.code + index}>
            <span>{reasonText(reason)}</span>
            {reason.fields.length > 0 && <small>Related information: {reason.fields.map(fieldLabel).join(', ')}</small>}
          </li>
        ))}
      </ul>
    </section>
  )
}

function EvidenceDrawer({
  supplier,
  fields,
  pending,
  error,
  onClose,
}: {
  supplier: SupplierComparisonResult
  fields: QuoteFieldsResponse | undefined
  pending: boolean
  error: unknown
  onClose: () => void
}) {
  return (
    <OverlayPortal>
      <div className="evidence-drawer-layer" role="presentation">
        <button className="evidence-drawer-backdrop" type="button" aria-label="Close evidence drawer" onClick={onClose} />
        <aside className="evidence-drawer" role="dialog" aria-modal="true" aria-label={`${supplier.supplier_name} Field Evidence`}>
        <header>
          <div>
            <p className="eyebrow">Source Quotation</p>
            <h2>{supplier.supplier_name}</h2>
            <span>Quotation revision {supplier.quote_version}</span>
          </div>
          <button className="drawer-close" type="button" onClick={onClose} aria-label="Close">×</button>
        </header>

        <div className="drawer-summary">
          <div><span>Feasibility</span><strong>{quoteStatusLabel(supplier.status)}</strong></div>
          <div><span>Confirmed total cost</span><strong>{valueText(supplier.total_cost)}</strong></div>
          <div><span>Expected Delivery Date</span><strong>{valueText(supplier.estimated_arrival_date)}</strong></div>
        </div>

        <Reasons title="Non-compliant items" reasons={supplier.failed_reasons} />
        <Reasons title="Pending confirmation" reasons={supplier.pending_reasons} />

        {pending && <div className="fields-loading">Loading fields and source evidence…</div>}
        {error !== null && (
          <div className="form-error compact-error">
            <strong>Failed to load field results</strong><p>{errorMessage(error)}</p>
          </div>
        )}
        {fields && (
          <section className="drawer-fields">
            <div className="drawer-section-title">
            <div><h3>Extracted fields and source text</h3></div>
              <span>{fields.fields.length} fields</span>
            </div>
            {fields.fields.map((field) => (
              <article className="drawer-field" key={field.field_name}>
                <div className="drawer-field-heading">
                  <strong>{fieldLabel(field.field_name)}</strong>
                  <span className={'field-status field-status-' + field.validation_status.toLowerCase()}>
                    {validationStatusLabel(field.validation_status)}
                  </span>
                </div>
                <dl>
                  <div><dt>Source wording</dt><dd>{valueText(field.raw_value)}</dd></div>
                  <div><dt>Normalised Value</dt><dd>{valueText(field.normalized_value)} {valueText(field.unit) === '—' ? '' : field.unit}</dd></div>
                  <div><dt>Source type</dt><dd>{originLabel(field.origin)}</dd></div>
                </dl>
                {field.evidence.length > 0 ? (
                  <div className="drawer-evidence-list">
                    {field.evidence.map((evidence, index) => (
                      <blockquote key={(evidence.source_id ?? 'source') + index}>
                        <span>{evidenceLocation(evidence)}</span>
                        <p>{evidence.quoted_text ?? 'No citation excerpt'}</p>
                      </blockquote>
                    ))}
                  </div>
                ) : <p className="drawer-no-evidence">No source evidence is available for this field.</p>}
              </article>
            ))}
          </section>
        )}
        </aside>
      </div>
    </OverlayPortal>
  )
}

export function ResultPage() {
  const { taskId = '', resultId = '' } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [assistantExpanded, setAssistantExpanded] = useState(true)
  const [selectedSupplierIndex, setSelectedSupplierIndex] = useState<number | null>(null)
  const [expandedGapQuoteId, setExpandedGapQuoteId] = useState<string | null>(null)
  const resultQuery = useQuery({
    queryKey: ['tasks', taskId, 'results', resultId],
    queryFn: () => api.getResult(taskId, resultId),
    enabled: Boolean(taskId && resultId),
  })
  const taskQuery = useQuery({
    queryKey: ['tasks', taskId],
    queryFn: () => api.getTask(taskId),
    enabled: Boolean(taskId),
  })
  const reanalysis = useMutation({
    mutationFn: () => {
      const currentTask = taskQuery.data
      if (!currentTask) throw new Error('The task has not finished loading.')
      return api.startRun(
        currentTask.task_id,
        currentTask.task_revision,
        createIdempotencyKey(),
      )
    },
    onSuccess: async (response) => {
      await queryClient.cancelQueries({ queryKey: ['tasks', taskId] })
      queryClient.setQueryData<TaskDetail>(['tasks', taskId], (current) => (
        current
          ? {
              ...current,
              status: 'QUEUED',
              current_graph_run_id: response.graph_run_id,
            }
          : current
      ))
      navigate(`/tasks/${taskId}/decision`, {
        state: {
          expectedGraphRunId: response.graph_run_id,
          previousResultId: resultId,
        },
      })
      void queryClient.invalidateQueries({ queryKey: ['tasks', taskId] })
    },
  })
  const suppliers = resultQuery.data?.result.supplier_results ?? []
  const fieldQueries = useQueries({
    queries: suppliers.map((supplier) => ({
      queryKey: ['tasks', taskId, 'results', resultId, 'quotes', supplier.quote_id, 'fields'],
      queryFn: () => api.getQuoteFields(taskId, supplier.quote_id, resultId),
    })),
  })
  const selectionGapsQuery = useQuery({
    queryKey: ['tasks', taskId, 'selection-gaps', taskQuery.data?.task_revision, resultId],
    queryFn: () => api.getSelectionGaps(taskId, taskQuery.data!.task_revision, resultQuery.data?.result_id),
    enabled: Boolean(taskId && taskQuery.data?.current_result_id && resultQuery.data?.is_current),
    retry: false,
  })

  if (resultQuery.isPending) {
    return <section className="card loading-panel">Loading extraction and comparison results…</section>
  }
  if (resultQuery.isError) {
    return (
      <section className="card error-panel" role="alert">
        <p className="eyebrow">Failed to load result</p>
        <h1>Unable to load result</h1>
        <p>{errorMessage(resultQuery.error)}</p>
        <Link className="button button-secondary" to={'/tasks/' + taskId}>Back to task</Link>
      </section>
    )
  }

  const payload = resultQuery.data.result
  if (resultQuery.data.is_current && taskQuery.data?.workflow_contract_version === 'compliance/2.0'
      && !taskQuery.data.progress.compliance?.can_compare) {
    return <section className="card"><h2>Complete the compliance review for the current revision first</h2><Link className="button button-submit" to={`/tasks/${taskId}/compliance`}>Go to compliance review</Link></section>
  }
  const decisionImpact = resultQuery.data.decision_impact
  const policyRetrievals = resultQuery.data.policy_retrievals
  const recommended = new Set(payload.recommended_quote_ids)
  const recommendedNames = suppliers
    .filter((supplier) => recommended.has(supplier.quote_id))
    .map((supplier) => supplier.supplier_name)
  const primaryRecommendation = suppliers.find((supplier) => recommended.has(supplier.quote_id))
  const feasibleCount = suppliers.filter((supplier) => supplier.status === 'FEASIBLE').length
  const pendingCount = suppliers.filter((supplier) => supplier.status === 'PENDING').length
  const infeasibleCount = suppliers.filter((supplier) => supplier.status === 'INFEASIBLE').length
  const task = taskQuery.data
  const canReanalyze = Boolean(task && task.quotes.length > 0 && task.task_revision > 1 && (
    (
      task.current_result_id === null
      && (task.status === 'DRAFT' || task.status === 'FAILED' || task.status === 'NEEDS_INPUT')
    )
    || (
      resultQuery.data.is_current
      && task.current_result_id === resultId
      && task.status === 'COMPLETED'
    )
  ))
  const selectedSupplier = selectedSupplierIndex === null ? null : suppliers[selectedSupplierIndex]
  const selectedFieldsQuery = selectedSupplierIndex === null ? null : fieldQueries[selectedSupplierIndex]
  const frozenRequirement = resultQuery.data.input_snapshot?.requirement
    ?? (resultQuery.data.is_current ? task?.requirement : undefined)
  const frozenDecisionProfile = resultQuery.data.input_snapshot?.decision_profile
    ?? (resultQuery.data.is_current ? task?.decision_profile : undefined)
  const excludedSupplierIds = frozenDecisionProfile?.preferences.excluded_supplier_ids ?? []
  const excludedActiveQuoteCount = resultQuery.data.is_current && task
    ? task.quotes.filter((quote) => excludedSupplierIds.includes(quote.supplier_id)).length
    : excludedSupplierIds.length
  const currency = frozenRequirement?.currency
  const successfulPolicyRetrievals = policyRetrievals.filter((item) => item.status === 'OK').length
  const policyReviewCount = resultQuery.data.policy_compliance.counts.REVIEW_REQUIRED ?? 0
  const hasPolicyBinding = Boolean(
    resultQuery.data.input_snapshot?.policy_set_version
    ?? (resultQuery.data.is_current ? task?.policy_binding?.policy_set_version : null),
  )
  const currentRanking = frozenDecisionProfile?.preferences.primary_criterion
    ?? frozenRequirement?.ranking_preference
  const currentSecondaryRanking = frozenDecisionProfile?.preferences.secondary_criterion
    ?? frozenRequirement?.secondary_preference
  const suppliersByQuote = new Map(suppliers.map((supplier) => [supplier.quote_id, supplier]))
  const quoteImpactsByQuote = new Map(decisionImpact?.quote_impacts.map((impact) => [impact.quote_id, impact]) ?? [])
  const policyAssessmentsByQuote = new Map(
    resultQuery.data.policy_compliance.assessments.map((assessment) => [assessment.quote_id, assessment]),
  )
  const selectionGapsByQuote = new Map(selectionGapsQuery.data?.gaps.map((gap) => [gap.quote_id, gap]) ?? [])
  const clarificationDraftsByQuote = new Map(
    selectionGapsQuery.data?.clarification_drafts.map((draft) => [draft.quote_id, draft]) ?? [],
  )
  const activeGapQuoteId = expandedGapQuoteId
  const activeGap = activeGapQuoteId ? selectionGapsByQuote.get(activeGapQuoteId) : undefined
  const activeGapSupplier = activeGapQuoteId ? suppliersByQuote.get(activeGapQuoteId) : undefined
  const activeGapImpact = activeGapQuoteId ? quoteImpactsByQuote.get(activeGapQuoteId) : undefined
  const activeGapDraft = activeGapQuoteId ? clarificationDraftsByQuote.get(activeGapQuoteId) : undefined
  const activeGapReasons = activeGap
    ? [...activeGap.failed_reasons, ...activeGap.pending_reasons]
    : activeGapSupplier ? [...activeGapSupplier.failed_reasons, ...activeGapSupplier.pending_reasons] : []
  const activeAdditionalReasons = activeGapReasons.slice(1)
  const activeUnknownFields = activeGapImpact?.unknown_fields ?? []
  const showCommunicationAdvice = Boolean(activeGapDraft && (activeGapReasons.length > 0 || activeUnknownFields.length > 0))
  const activeHasSupplement = activeAdditionalReasons.length > 0
    || activeUnknownFields.length > 0
    || showCommunicationAdvice
  const headerProgress = resultQuery.data.is_current || !task
    ? task?.progress
    : {
        requirement_completed: true,
        quote_review_completed: true,
        decision_completed: true,
        summary_completed: false,
        compliance: resultQuery.data.input_snapshot?.compliance_assessment_id ? {
          status: resultQuery.data.policy_compliance.policy_enabled === false ? 'DISABLED' as const : 'PROCESSED' as const,
          confirmed: true, can_compare: true,
        } : undefined,
      }
  let policyState = 'Not boundPolicy'
  if (taskQuery.isPending) policyState = 'Loading policy binding'
  else if (taskQuery.isError) policyState = 'Failed to load policy binding'
  else if (resultQuery.data.input_snapshot?.policy_set_version && policyRetrievals.length === 0) policyState = 'Policy retrieval not run'
  else if (resultQuery.data.input_snapshot?.policy_set_version && successfulPolicyRetrievals === policyRetrievals.length) {
    policyState = `Evidence found for ${successfulPolicyRetrievals} / ${policyRetrievals.length}`
  } else if (resultQuery.data.input_snapshot?.policy_set_version) {
    policyState = `${policyRetrievals.length - successfulPolicyRetrievals} require review`
  }

  return (
    <div className="page-stack result-page">
      {task ? (
        <TaskWorkspaceHeader
          taskId={task.task_id}
          scenarioId={task.scenario_id}
          title={task.task_name}
          subtitle={frozenRequirement
            ? `${frozenRequirement.required_quantity} ${frozenRequirement.quantity_unit} · ${frozenRequirement.currency} · Latest delivery ${frozenRequirement.delivery_deadline}`
            : 'This historical result has no displayable frozen procurement requirements'}
          status={resultQuery.data.is_current ? task.status : 'COMPLETED'}
          revision={resultQuery.data.task_revision}
          revisionContext={resultQuery.data.is_current ? 'current' : 'historical'}
          resultId={resultQuery.data.result_id}
          quoteCount={suppliers.length}
          summaryComplete={resultQuery.data.is_current ? task.summary_completed : false}
          progress={headerProgress ?? task.progress}
          reviewBlocked={Boolean(task.current_issue)}
          policyReviewBlocked={task.current_issue?.issue_type === 'POLICY_EVIDENCE_REVIEW'}
          active="decision"
        />
      ) : (
        <section className="result-header">
          <div><p className="eyebrow">AUDITABLE RESULT</p><h1>Quotation analysis and recommendation</h1></div>
          <Link className="button button-secondary" to={'/tasks/' + taskId}>Back to task</Link>
        </section>
      )}

      {!resultQuery.data.is_current && (
        <div className="run-notice">This is a historical result and does not represent the current task revision.</div>
      )}

      {resultQuery.data.policy_compliance.recommendation_scope !== 'COMPLIANCE_VERIFIED' && (
        <div className="run-notice">This result is limited to procurement comparison. Supplier compliance must be verified separately.</div>
      )}
      {resultQuery.data.legacy_compliance && <p className="run-notice">Legacy workflow result: the full evidence-confirmation stage was not run at the time.</p>}
      <ComplianceAssessmentDetails assessment={resultQuery.data.policy_compliance} taskId={taskId} resultId={resultId} historical={!resultQuery.data.is_current} legacy={resultQuery.data.legacy_compliance} />

      {reanalysis.isError && (
        <div className="form-error compact-error" role="alert">
          <div>
            <strong>Reanalysis did not start</strong>
            <p>{reanalysisErrorMessage(reanalysis.error)}</p>
          </div>
        </div>
      )}

      <section className="decision-ready-banner">
        <div>
          <strong>{resultQuery.data.is_current ? 'Quotation review complete; ready for comparison' : 'Viewing historical decision result'}</strong>
          <span>
            Currently using {suppliers.length} formally submitted and reviewed quotations.
            {infeasibleCount > 0 ? ' Non-compliant quotations remain in the matrix but are excluded from ranking.' : ''}
            {excludedSupplierIds.length > 0
              ? ` ${resultQuery.data.is_current && task ? `Of the ${task.quotes.length} currently active quotations, ` : ''}${suppliers.length} are compared and ${excludedActiveQuoteCount} are excluded by supplier (${excludedSupplierIds.join(', ')}).`
              : ''}
          </span>
        </div>
        <div>
          <span>
            {feasibleCount} feasible
            {pendingCount > 0 ? ` · ${pendingCount} pending confirmation` : ''}
            {infeasibleCount > 0 ? ` · ${infeasibleCount} non-compliant` : ''}
            {excludedSupplierIds.length > 0 ? ` · ${excludedSupplierIds.length} excluded` : ''}
          </span>
          <strong>{resultQuery.data.is_current ? 'Analysis complete' : 'Historical revision'}</strong>
          {canReanalyze && (
            <button
              className="button button-secondary"
              type="button"
              onClick={() => reanalysis.mutate()}
              disabled={reanalysis.isPending}
            >
              {reanalysis.isPending ? 'Starting…' : 'Reanalyse'}
            </button>
          )}
        </div>
      </section>

      {task && (
        <div className="decision-assistant-toggle-bar">
          <button className="button button-secondary" type="button"
            aria-expanded={assistantExpanded} aria-controls="decision-assistant-panel"
            onClick={() => setAssistantExpanded((expanded) => !expanded)}>
            {assistantExpanded ? 'Collapse AI Decision Assistant' : 'Expand AI Decision Assistant'}
          </button>
        </div>
      )}
      <div className={`decision-workspace-layout${!assistantExpanded || !task ? ' decision-assistant-collapsed' : ''}`}>
        <main className="decision-workspace-main">
          <section className="decision-summary-grid">
            <article className="decision-recommendation-hero">
              <div>
                <h2>{recommendedNames.length > 0 ? `Recommended supplier: ${recommendedNames.join(', ')}` : 'No publishable recommendation'}</h2>
                <div className="decision-hero-cost">
                  <strong>{moneyText(currency, primaryRecommendation?.total_cost)}</strong>
                  <span>Confirmed Total Cost</span>
                </div>
                <p>
                  {recommendationNarrative(primaryRecommendation, currency, currentRanking)}
                </p>
                {frozenRequirement && (
                  <div className="decision-hero-context">
                    <span>{rankingCriterionLabel(currentRanking)}</span>
                    <span>Deadline {frozenRequirement.delivery_deadline}</span>
                    <span>{frozenRequirement.allow_substitutes ? 'Substitutes allowed' : 'Substitutes prohibited'}</span>
                  </div>
                )}
              </div>
            </article>

            <aside className="decision-compact-signals" aria-label="Current decision settings and policy evidence summary">
              <article className="decision-settings-signal">
                <div>
                  <strong>Current Decision Settings</strong>
                  <span className="signal-badge">Current revision {resultQuery.data.task_revision}</span>
                </div>
                <dl>
                  <div><dt>Primary criterion</dt><dd>{rankingCriterionLabel(currentRanking)}</dd></div>
                  <div><dt>Secondary criterion</dt><dd>{rankingCriterionLabel(currentSecondaryRanking)}</dd></div>
                  <div><dt>Cost Tolerance</dt><dd>{!frozenDecisionProfile || frozenDecisionProfile.preferences.cost_tolerance_amount === null ? 'Not set' : moneyText(currency, frozenDecisionProfile.preferences.cost_tolerance_amount)}</dd></div>
                  <div><dt>Excluded Suppliers</dt><dd>{frozenDecisionProfile?.preferences.excluded_supplier_ids.join(', ') || 'None'}</dd></div>
                </dl>
              </article>
              {hasPolicyBinding ? (
                <article>
                  <div><strong>Compliance Review</strong><span className="signal-badge">{successfulPolicyRetrievals} / {policyRetrievals.length}</span></div>
                  <p>{policyState}{policyReviewCount > 0 ? `; ${policyReviewCount} suppliers still require manual verification.` : '.'}</p>
                  {task && resultQuery.data.is_current && <Link to={`/tasks/${task.task_id}/compliance`}>View policy evidence</Link>}
                </article>
              ) : (
                <article>
                  <div><strong>Compliance Review</strong></div>
                  <p>No policy is bound to this task.</p>
                </article>
              )}
            </aside>
          </section>

          <section className="comparison-matrix-panel">
            <header>
              <div><h2>Supplier Comparison</h2></div>
              <div>
                {task && <Link className="button button-secondary" to={`/tasks/${task.task_id}/quotes/new`}>View all source quotations</Link>}
              </div>
            </header>
            <div className="comparison-matrix-scroll">
              <table className="comparison-matrix-table" style={{ minWidth: Math.max(720, 128 + suppliers.length * 190) }}>
                <thead>
                  <tr>
                    <th>Criterion</th>
                    {suppliers.map((supplier, index) => (
                      <th className={recommended.has(supplier.quote_id) ? 'matrix-recommended' : ''} key={supplier.quote_id}>
                        <button className="supplier-link" type="button" onClick={() => setSelectedSupplierIndex(index)}>{supplier.supplier_name}</button>
                        {supplier.quote_version > 1 && <small>Revision {supplier.quote_version}</small>}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <tr><th>Confirmed Total Cost</th>{suppliers.map((supplier) => <td className={recommended.has(supplier.quote_id) ? 'matrix-recommended matrix-best' : ''} key={supplier.quote_id}>{moneyText(currency, supplier.total_cost)}{recommended.has(supplier.quote_id) && <span className="matrix-tag">Recommendation</span>}</td>)}</tr>
                  <tr><th>Expected Delivery Date</th>{suppliers.map((supplier) => <td className={recommended.has(supplier.quote_id) ? 'matrix-recommended matrix-best' : ''} key={supplier.quote_id}>{valueText(supplier.estimated_arrival_date)}</td>)}</tr>
                  <tr><th>Actual Order Quantity</th>{suppliers.map((supplier) => <td className={recommended.has(supplier.quote_id) ? 'matrix-recommended' : ''} key={supplier.quote_id}>{quantityText(supplier.actual_quantity, frozenRequirement?.quantity_unit)}</td>)}</tr>
                  <tr><th scope="row">Payment Term</th>{suppliers.map((supplier) => <td className={recommended.has(supplier.quote_id) ? 'matrix-recommended' : ''} key={supplier.quote_id}><MatrixPaymentTerm supplier={supplier} /></td>)}</tr>
                  <tr><th scope="row">Supplier Performance</th>{suppliers.map((supplier) => <td className={recommended.has(supplier.quote_id) ? 'matrix-recommended' : ''} key={supplier.quote_id}><MatrixSupplierPerformance supplier={supplier} /></td>)}</tr>
                  {suppliers.some((supplier) => supplier.status !== 'FEASIBLE') && <tr><th>Feasibility</th>{suppliers.map((supplier) => <td className={recommended.has(supplier.quote_id) ? 'matrix-recommended' : ''} key={supplier.quote_id}><span className={'supplier-status supplier-status-' + supplier.status.toLowerCase()}>{quoteStatusLabel(supplier.status)}</span></td>)}</tr>}
                  <tr><th>Source Quotation</th>{suppliers.map((supplier, index) => <td className={recommended.has(supplier.quote_id) ? 'matrix-recommended' : ''} key={supplier.quote_id}><button className="evidence-action" type="button" onClick={() => setSelectedSupplierIndex(index)}>{fieldQueries[index]?.isPending ? 'Loading…' : 'View source quotation'}</button></td>)}</tr>
                  <tr>
                    <th>Recommendation and reasons not selected</th>
                    {suppliers.map((supplier) => {
                      const gap = selectionGapsByQuote.get(supplier.quote_id)
                      const reasons = gap
                        ? [...gap.failed_reasons, ...gap.pending_reasons]
                        : [...supplier.failed_reasons, ...supplier.pending_reasons]
                      const impact = quoteImpactsByQuote.get(supplier.quote_id)
                      const unknownFields = impact?.unknown_fields ?? []
                      const draft = clarificationDraftsByQuote.get(supplier.quote_id)
                      const hasCommunicationAdvice = Boolean(draft && (reasons.length > 0 || unknownFields.length > 0))
                      const hasSupplement = reasons.length > 1
                        || unknownFields.length > 0
                        || hasCommunicationAdvice
                      const summary = matrixSelectionSummary(
                        supplier,
                        primaryRecommendation,
                        currentRanking,
                        currency,
                        impact,
                        recommended.has(supplier.quote_id),
                        policyAssessmentsByQuote.get(supplier.quote_id),
                      )
                      return (
                        <td className={recommended.has(supplier.quote_id) ? 'matrix-recommended' : ''} key={supplier.quote_id}>
                          <div className="matrix-cell-summary">
                            <span className={`matrix-gap-tag matrix-gap-${summary.tone}`}>{summary.label}</span>
                            <small>{summary.detail}</small>
                            {hasSupplement && (
                              <button
                                className="matrix-detail-toggle"
                                type="button"
                                aria-expanded={activeGapQuoteId === supplier.quote_id}
                                onClick={() => setExpandedGapQuoteId((current) => {
                                  return current === supplier.quote_id ? null : supplier.quote_id
                                })}
                              >{activeGapQuoteId === supplier.quote_id ? 'Hide additional information' : 'View additional information'}</button>
                            )}
                          </div>
                        </td>
                      )
                    })}
                  </tr>
                  {activeGapSupplier && activeHasSupplement && (
                    <tr className="matrix-expanded-row">
                      <td colSpan={suppliers.length + 1}>
                        <div className="matrix-expanded-detail">
                          <header>
                            <div><strong>{activeGapSupplier.supplier_name} · Additional information</strong>{activeGapSupplier.quote_version > 1 && <small>Quotation revision {activeGapSupplier.quote_version}</small>}</div>
                            <span className={`supplier-status supplier-status-${activeGapSupplier.status.toLowerCase()}`}>{quoteStatusLabel(activeGapSupplier.status)}</span>
                          </header>
                          <div className="matrix-expanded-grid">
                            {activeAdditionalReasons.length > 0 && (
                              <section>
                                <h3>Other requirement failures or pending items</h3>
                                <ul>{activeAdditionalReasons.map((reason, index) => <li key={`${reason.code}-${index}`}>{reasonText(reason)}</li>)}</ul>
                              </section>
                            )}
                            {activeUnknownFields.length > 0 && (
                              <section>
                                <h3>Pending information</h3>
                                <p>{activeGapImpact && impactMessage(activeGapImpact.status, activeGapImpact.message)}</p>
                                <div className="decision-impact-fields">
                                  {activeUnknownFields.map((field) => <span key={field}>{fieldLabel(field)}</span>)}
                                </div>
                              </section>
                            )}
                            {showCommunicationAdvice && (
                              <section>
                                <h3>Suggested Communication</h3>
                                <p>{activeGapDraft?.text}</p>
                              </section>
                            )}
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <p className="result-boundary">
            Result updated {displayDate(payload.evaluated_at)}. Detailed calculation records are available in revision history.
          </p>
        </main>

        {task && (
          <aside className="decision-chat-rail" id="decision-assistant-panel" hidden={!assistantExpanded}>
            <DecisionScenarioWorkspace
              key={resultQuery.data.result_id}
              task={task}
              result={resultQuery.data}
              compact
              onReanalyze={canReanalyze ? () => reanalysis.mutate() : undefined}
              reanalyzing={reanalysis.isPending}
              onOpenQuoteEvidence={(quoteId) => {
                const index = suppliers.findIndex((supplier) => supplier.quote_id === quoteId)
                if (index >= 0) setSelectedSupplierIndex(index)
              }}
            />
          </aside>
        )}
      </div>

      {selectedSupplier && selectedFieldsQuery && (
        <EvidenceDrawer
          supplier={selectedSupplier}
          fields={selectedFieldsQuery.data}
          pending={selectedFieldsQuery.isPending}
          error={selectedFieldsQuery.isError ? selectedFieldsQuery.error : null}
          onClose={() => setSelectedSupplierIndex(null)}
        />
      )}
    </div>
  )
}
