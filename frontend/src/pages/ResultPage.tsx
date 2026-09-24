import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import type {
  FieldEvidence,
  QuoteDecisionImpact,
  QuoteFieldsResponse,
  ResultReason,
  SupplierComparisonResult,
  TaskDetail,
} from '../api/types'
import { DecisionScenarioWorkspace } from '../components/DecisionScenarioWorkspace'
import { MatrixPaymentTerm, MatrixSupplierPerformance } from '../components/SupplierMatrixDetails'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'
import { rankingCriterionLabel } from '../lib/rankingCriteria'
import { supplierSelectionExplanation } from '../lib/resultComparison'
import {
  fieldLabel,
  originLabel,
  quoteStatusLabel,
  reasonText,
  validationStatusLabel,
} from '../lib/presentation'

function errorMessage(error: unknown) {
  return error instanceof ApiClientError ? error.message : '结果读取失败。'
}

function reanalysisErrorMessage(error: unknown) {
  if (error instanceof ApiClientError) {
    if (error.code === 'task_revision_conflict') return '任务内容已变化，请刷新后重试。'
    if (error.code === 'graph_run_active') return '当前已有分析正在运行，请返回决策页查看状态。'
    return error.message
  }
  return '重新分析启动失败。'
}

function valueText(value: unknown) {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
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
  const unitLabels: Record<string, string> = { piece: '件', pieces: '件', unit: '件', units: '件' }
  return `${new Intl.NumberFormat('zh-CN').format(value)} ${unitLabels[unit ?? ''] ?? unit ?? ''}`.trim()
}

function displayDate(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)
}

function evidenceLocation(evidence: FieldEvidence) {
  const parts: string[] = []
  if (evidence.page_number !== null) parts.push('第 ' + evidence.page_number + ' 页')
  if (evidence.row_number !== null) parts.push('第 ' + evidence.row_number + ' 行')
  if (evidence.column_name) parts.push('列 ' + evidence.column_name)
  return parts.join(' · ') || evidence.kind || '来源位置'
}

function recommendationNarrative(
  primary: SupplierComparisonResult | undefined,
  currency: string | undefined,
  ranking: string | undefined,
) {
  if (!primary) return '当前结果没有可发布的推荐方案，请先处理阻塞项或补齐待确认信息。'
  const rankingText = rankingCriterionLabel(ranking) || '当前排序规则'
  const arrival = primary.estimated_arrival_date
    ? `，预计于 ${primary.estimated_arrival_date} 到货`
    : ''
  return `按照“${rankingText}”，${primary.supplier_name} 满足当前报价比较条件，以 ${moneyText(currency, primary.total_cost)} 的已确认总成本成为首选${arrival}。`
}

function impactMessage(status: string, fallback: string) {
  if (status === 'NON_BLOCKING') return '该报价已有确定的不符合项，因此这些未知字段目前不会改变推荐结果。'
  if (status === 'REQUIRES_INVESTIGATION') return '这些未知字段可能影响报价的可行性或排序，需要确认后才能稳定当前推荐。'
  if (status === 'UNDETERMINED') return '现有信息不足以判断这些未知字段是否会改变推荐，需要人工确认。'
  return fallback
}

function matrixSelectionSummary(
  supplier: SupplierComparisonResult,
  primary: SupplierComparisonResult | undefined,
  ranking: string | undefined,
  currency: string | undefined,
  impact: QuoteDecisionImpact | undefined,
  recommended: boolean,
) {
  if (impact?.unknown_fields.length && impact.status !== 'NON_BLOCKING') {
    return {
      label: `${impact.unknown_fields.length} 项待确认`,
      detail: impactMessage(impact.status, impact.message),
      tone: 'warning',
    }
  }
  return supplierSelectionExplanation(supplier, primary, ranking, currency, recommended)
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
            {reason.fields.length > 0 && <small>相关信息：{reason.fields.map(fieldLabel).join('、')}</small>}
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
    <div className="evidence-drawer-layer" role="presentation">
      <button className="evidence-drawer-backdrop" type="button" aria-label="关闭证据抽屉" onClick={onClose} />
      <aside className="evidence-drawer" role="dialog" aria-modal="true" aria-label={`${supplier.supplier_name} 字段证据`}>
        <header>
          <div>
            <p className="eyebrow">报价原文</p>
            <h2>{supplier.supplier_name}</h2>
            <span>第 {supplier.quote_version} 版报价</span>
          </div>
          <button className="drawer-close" type="button" onClick={onClose} aria-label="关闭">×</button>
        </header>

        <div className="drawer-summary">
          <div><span>可行性</span><strong>{quoteStatusLabel(supplier.status)}</strong></div>
          <div><span>确认总成本</span><strong>{valueText(supplier.total_cost)}</strong></div>
          <div><span>预计到货</span><strong>{valueText(supplier.estimated_arrival_date)}</strong></div>
        </div>

        <Reasons title="不符合项" reasons={supplier.failed_reasons} />
        <Reasons title="待确认项" reasons={supplier.pending_reasons} />

        {pending && <div className="fields-loading">正在读取字段和来源证据…</div>}
        {error !== null && (
          <div className="form-error compact-error">
            <strong>字段结果读取失败</strong><p>{errorMessage(error)}</p>
          </div>
        )}
        {fields && (
          <section className="drawer-fields">
            <div className="drawer-section-title">
            <div><h3>解析字段与原文</h3></div>
              <span>{fields.fields.length} 个字段</span>
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
                  <div><dt>原始表达</dt><dd>{valueText(field.raw_value)}</dd></div>
                  <div><dt>标准化值</dt><dd>{valueText(field.normalized_value)} {valueText(field.unit) === '—' ? '' : field.unit}</dd></div>
                  <div><dt>来源类型</dt><dd>{originLabel(field.origin)}</dd></div>
                </dl>
                {field.evidence.length > 0 ? (
                  <div className="drawer-evidence-list">
                    {field.evidence.map((evidence, index) => (
                      <blockquote key={(evidence.source_id ?? 'source') + index}>
                        <span>{evidenceLocation(evidence)}</span>
                        <p>{evidence.quoted_text ?? '无引用片段'}</p>
                      </blockquote>
                    ))}
                  </div>
                ) : <p className="drawer-no-evidence">该字段没有来源证据。</p>}
              </article>
            ))}
          </section>
        )}
      </aside>
    </div>
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
      if (!currentTask) throw new Error('任务尚未读取完成。')
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
    return <section className="card loading-panel">正在读取解析与比较结果…</section>
  }
  if (resultQuery.isError) {
    return (
      <section className="card error-panel" role="alert">
        <p className="eyebrow">结果读取失败</p>
        <h1>无法读取结果</h1>
        <p>{errorMessage(resultQuery.error)}</p>
        <Link className="button button-secondary" to={'/tasks/' + taskId}>返回任务</Link>
      </section>
    )
  }

  const payload = resultQuery.data.result
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
      }
  let policyState = '未绑定制度'
  if (taskQuery.isPending) policyState = '正在读取制度绑定'
  else if (taskQuery.isError) policyState = '制度绑定读取失败'
  else if (resultQuery.data.input_snapshot?.policy_set_version && policyRetrievals.length === 0) policyState = '未执行制度检索'
  else if (resultQuery.data.input_snapshot?.policy_set_version && successfulPolicyRetrievals === policyRetrievals.length) {
    policyState = `${successfulPolicyRetrievals} / ${policyRetrievals.length} 找到证据`
  } else if (resultQuery.data.input_snapshot?.policy_set_version) {
    policyState = `${policyRetrievals.length - successfulPolicyRetrievals} 项需复核`
  }

  return (
    <div className="page-stack result-page">
      {task ? (
        <TaskWorkspaceHeader
          taskId={task.task_id}
          scenarioId={task.scenario_id}
          title={task.task_name}
          subtitle={frozenRequirement
            ? `${frozenRequirement.required_quantity} ${frozenRequirement.quantity_unit} · ${frozenRequirement.currency} · 最晚交付 ${frozenRequirement.delivery_deadline}`
            : '该历史结果缺少可展示的冻结采购需求'}
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
          <div><p className="eyebrow">AUDITABLE RESULT</p><h1>报价分析与推荐结果</h1></div>
          <Link className="button button-secondary" to={'/tasks/' + taskId}>返回任务</Link>
        </section>
      )}

      {!resultQuery.data.is_current && (
        <div className="run-notice">这是历史结果，不代表任务当前版本。</div>
      )}

      {resultQuery.data.policy_compliance.recommendation_scope !== 'COMPLIANCE_VERIFIED' && (
        <div className="run-notice">该结果仅用于采购比较；供应商合规仍需单独核验。</div>
      )}

      {reanalysis.isError && (
        <div className="form-error compact-error" role="alert">
          <div>
            <strong>重新分析未启动</strong>
            <p>{reanalysisErrorMessage(reanalysis.error)}</p>
          </div>
        </div>
      )}

      <section className="decision-ready-banner">
        <div>
          <strong>{resultQuery.data.is_current ? '报价审核已完成，可以比较' : '正在查看历史决策结果'}</strong>
          <span>
            当前使用 {suppliers.length} 份正式提交且审核通过的报价。
            {infeasibleCount > 0 ? ' 不符合项仍保留在矩阵中，但不参与排序。' : ''}
            {excludedSupplierIds.length > 0
              ? ` ${resultQuery.data.is_current && task ? `当前 ${task.quotes.length} 份有效报价中，` : ''}${suppliers.length} 份进入比较，${excludedActiveQuoteCount} 份按设置排除（${excludedSupplierIds.join('、')}）。`
              : ''}
          </span>
        </div>
        <div>
          <span>
            {feasibleCount} 家可行
            {pendingCount > 0 ? ` · ${pendingCount} 家待确认` : ''}
            {infeasibleCount > 0 ? ` · ${infeasibleCount} 家不符合` : ''}
            {excludedSupplierIds.length > 0 ? ` · ${excludedSupplierIds.length} 家已排除` : ''}
          </span>
          <strong>{resultQuery.data.is_current ? '分析完成' : '历史版本'}</strong>
          {canReanalyze && (
            <button
              className="button button-secondary"
              type="button"
              onClick={() => reanalysis.mutate()}
              disabled={reanalysis.isPending}
            >
              {reanalysis.isPending ? '正在启动…' : '重新分析'}
            </button>
          )}
        </div>
      </section>

      {task && (
        <div className="decision-assistant-toggle-bar">
          <button className="button button-secondary" type="button"
            aria-expanded={assistantExpanded} aria-controls="decision-assistant-panel"
            onClick={() => setAssistantExpanded((expanded) => !expanded)}>
            {assistantExpanded ? '收起 AI 决策助手' : '展开 AI 决策助手'}
          </button>
        </div>
      )}
      <div className={`decision-workspace-layout${!assistantExpanded || !task ? ' decision-assistant-collapsed' : ''}`}>
        <main className="decision-workspace-main">
          <section className="decision-summary-grid">
            <article className="decision-recommendation-hero">
              <div>
                <h2>{recommendedNames.length > 0 ? `建议优先：${recommendedNames.join('、')}` : '暂无可发布推荐'}</h2>
                <div className="decision-hero-cost">
                  <strong>{moneyText(currency, primaryRecommendation?.total_cost)}</strong>
                  <span>已确认总成本</span>
                </div>
                <p>
                  {recommendationNarrative(primaryRecommendation, currency, currentRanking)}
                </p>
                {frozenRequirement && (
                  <div className="decision-hero-context">
                    <span>{rankingCriterionLabel(currentRanking)}</span>
                    <span>截止 {frozenRequirement.delivery_deadline}</span>
                    <span>{frozenRequirement.allow_substitutes ? '允许替代料' : '禁止替代料'}</span>
                  </div>
                )}
              </div>
            </article>

            <aside className="decision-compact-signals" aria-label="当前决策设置和制度证据摘要">
              <article className="decision-settings-signal">
                <div>
                  <strong>当前决策设置</strong>
                  <span className="signal-badge">当前第 {resultQuery.data.task_revision} 版</span>
                </div>
                <dl>
                  <div><dt>主指标</dt><dd>{rankingCriterionLabel(currentRanking)}</dd></div>
                  <div><dt>次指标</dt><dd>{rankingCriterionLabel(currentSecondaryRanking)}</dd></div>
                  <div><dt>成本容差</dt><dd>{!frozenDecisionProfile || frozenDecisionProfile.preferences.cost_tolerance_amount === null ? '未设置' : moneyText(currency, frozenDecisionProfile.preferences.cost_tolerance_amount)}</dd></div>
                  <div><dt>排除供应商</dt><dd>{frozenDecisionProfile?.preferences.excluded_supplier_ids.join('、') || '无'}</dd></div>
                </dl>
              </article>
              {hasPolicyBinding ? (
                <article>
                  <div><strong>制度检查</strong><span className="signal-badge">{successfulPolicyRetrievals} / {policyRetrievals.length}</span></div>
                  <p>{policyState}{policyReviewCount > 0 ? `；${policyReviewCount} 家供应商仍需人工核验。` : '。'}</p>
                  {task && resultQuery.data.is_current && <Link to={`/tasks/${task.task_id}/compliance`}>查看制度依据</Link>}
                </article>
              ) : (
                <article>
                  <div><strong>制度检查</strong></div>
                  <p>当前未绑定制度。</p>
                </article>
              )}
            </aside>
          </section>

          <section className="comparison-matrix-panel">
            <header>
              <div><h2>供应商比较</h2></div>
              <div>
                {task && <Link className="button button-secondary" to={`/tasks/${task.task_id}/quotes/new`}>查看全部报价原文</Link>}
              </div>
            </header>
            <div className="comparison-matrix-scroll">
              <table className="comparison-matrix-table" style={{ minWidth: Math.max(720, 128 + suppliers.length * 190) }}>
                <thead>
                  <tr>
                    <th>指标</th>
                    {suppliers.map((supplier, index) => (
                      <th className={recommended.has(supplier.quote_id) ? 'matrix-recommended' : ''} key={supplier.quote_id}>
                        <button className="supplier-link" type="button" onClick={() => setSelectedSupplierIndex(index)}>{supplier.supplier_name}</button>
                        {supplier.quote_version > 1 && <small>第 {supplier.quote_version} 版</small>}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <tr><th>已确认总成本</th>{suppliers.map((supplier) => <td className={recommended.has(supplier.quote_id) ? 'matrix-recommended matrix-best' : ''} key={supplier.quote_id}>{moneyText(currency, supplier.total_cost)}{recommended.has(supplier.quote_id) && <span className="matrix-tag">推荐</span>}</td>)}</tr>
                  <tr><th>预计到货</th>{suppliers.map((supplier) => <td className={recommended.has(supplier.quote_id) ? 'matrix-recommended matrix-best' : ''} key={supplier.quote_id}>{valueText(supplier.estimated_arrival_date)}</td>)}</tr>
                  <tr><th>实际采购量</th>{suppliers.map((supplier) => <td className={recommended.has(supplier.quote_id) ? 'matrix-recommended' : ''} key={supplier.quote_id}>{quantityText(supplier.actual_quantity, frozenRequirement?.quantity_unit)}</td>)}</tr>
                  <tr><th scope="row">付款账期</th>{suppliers.map((supplier) => <td className={recommended.has(supplier.quote_id) ? 'matrix-recommended' : ''} key={supplier.quote_id}><MatrixPaymentTerm supplier={supplier} /></td>)}</tr>
                  <tr><th scope="row">供应商表现</th>{suppliers.map((supplier) => <td className={recommended.has(supplier.quote_id) ? 'matrix-recommended' : ''} key={supplier.quote_id}><MatrixSupplierPerformance supplier={supplier} /></td>)}</tr>
                  {suppliers.some((supplier) => supplier.status !== 'FEASIBLE') && <tr><th>可行性</th>{suppliers.map((supplier) => <td className={recommended.has(supplier.quote_id) ? 'matrix-recommended' : ''} key={supplier.quote_id}><span className={'supplier-status supplier-status-' + supplier.status.toLowerCase()}>{quoteStatusLabel(supplier.status)}</span></td>)}</tr>}
                  <tr><th>报价原文</th>{suppliers.map((supplier, index) => <td className={recommended.has(supplier.quote_id) ? 'matrix-recommended' : ''} key={supplier.quote_id}><button className="evidence-action" type="button" onClick={() => setSelectedSupplierIndex(index)}>{fieldQueries[index]?.isPending ? '读取中…' : '查看报价原文'}</button></td>)}</tr>
                  <tr>
                    <th>推荐与未选原因</th>
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
                              >{activeGapQuoteId === supplier.quote_id ? '收起补充信息' : '查看补充信息'}</button>
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
                            <div><strong>{activeGapSupplier.supplier_name} · 补充信息</strong>{activeGapSupplier.quote_version > 1 && <small>第 {activeGapSupplier.quote_version} 版报价</small>}</div>
                            <span className={`supplier-status supplier-status-${activeGapSupplier.status.toLowerCase()}`}>{quoteStatusLabel(activeGapSupplier.status)}</span>
                          </header>
                          <div className="matrix-expanded-grid">
                            {activeAdditionalReasons.length > 0 && (
                              <section>
                                <h3>其他不符合或待确认事项</h3>
                                <ul>{activeAdditionalReasons.map((reason, index) => <li key={`${reason.code}-${index}`}>{reasonText(reason)}</li>)}</ul>
                              </section>
                            )}
                            {activeUnknownFields.length > 0 && (
                              <section>
                                <h3>待确认信息</h3>
                                <p>{activeGapImpact && impactMessage(activeGapImpact.status, activeGapImpact.message)}</p>
                                <div className="decision-impact-fields">
                                  {activeUnknownFields.map((field) => <span key={field}>{fieldLabel(field)}</span>)}
                                </div>
                              </section>
                            )}
                            {showCommunicationAdvice && (
                              <section>
                                <h3>建议沟通内容</h3>
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
            结果更新于 {displayDate(payload.evaluated_at)}，详细计算记录可在版本记录中查看。
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
