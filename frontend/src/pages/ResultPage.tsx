import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import type {
  FieldEvidence,
  QuoteDecisionImpact,
  QuoteFieldsResponse,
  ResultReason,
  SelectionGap,
  SupplierComparisonResult,
} from '../api/types'
import { DecisionScenarioWorkspace } from '../components/DecisionScenarioWorkspace'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'
import {
  fieldLabel,
  originLabel,
  quoteStatusLabel,
  reasonText,
  validationStatusLabel,
} from '../lib/presentation'

const rankingLabels: Record<string, string> = {
  LOWEST_CONFIRMED_TOTAL_COST: '确认总成本最低',
  FASTEST_CONFIRMED_DELIVERY: '确认到货最快',
  LOWEST_COST_THEN_FASTEST_DELIVERY: '成本优先，其次交期',
  FASTEST_DELIVERY_THEN_LOWEST_COST: '交期优先，其次成本',
}

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

function evidenceCount(fields: QuoteFieldsResponse | undefined) {
  return fields?.fields.reduce((count, field) => count + field.evidence.length, 0) ?? 0
}

function supplierConclusion(supplier: SupplierComparisonResult, recommended: boolean) {
  if (recommended) return '初步推荐'
  if (supplier.failed_reasons.length > 0) return reasonText(supplier.failed_reasons[0])
  if (supplier.pending_reasons.length > 0) return reasonText(supplier.pending_reasons[0])
  return supplier.status === 'FEASIBLE' ? '可进入比较' : quoteStatusLabel(supplier.status)
}

function recommendationNarrative(
  primary: SupplierComparisonResult | undefined,
  suppliers: SupplierComparisonResult[],
  currency: string | undefined,
  ranking: string | undefined,
) {
  if (!primary) return '当前结果没有可发布的推荐方案，请先处理阻塞项或补齐待确认信息。'
  const rankingText = rankingLabels[ranking ?? ''] ?? ranking ?? '当前排序规则'
  const arrival = primary.estimated_arrival_date
    ? `，预计于 ${primary.estimated_arrival_date} 到货`
    : ''
  const first = `按照“${rankingText}”，${primary.supplier_name} 满足当前采购要求，以 ${moneyText(currency, primary.total_cost)} 的已确认总成本成为首选${arrival}。`
  const alternatives = suppliers.filter((supplier) => supplier.quote_id !== primary.quote_id)
  if (alternatives.length === 0) return first
  const second = alternatives.slice(0, 2).map((supplier) => (
    `${supplier.supplier_name}${supplier.status === 'FEASIBLE'
      ? '仍可作为备选'
      : `未入选，原因是${supplierConclusion(supplier, false).replace(/[。；，,.]+$/, '')}`}`
  )).join('；')
  return `${first} ${second}。`
}

function impactMessage(status: string, fallback: string) {
  if (status === 'NON_BLOCKING') return '该报价已有确定的不符合项，因此这些未知字段目前不会改变推荐结果。'
  if (status === 'REQUIRES_INVESTIGATION') return '这些未知字段可能影响报价的可行性或排序，需要确认后才能稳定当前推荐。'
  if (status === 'UNDETERMINED') return '现有信息不足以判断这些未知字段是否会改变推荐，需要人工确认。'
  return fallback
}

function matrixGapSummary(
  supplier: SupplierComparisonResult,
  gap: SelectionGap | undefined,
  impact: QuoteDecisionImpact | undefined,
  recommended: boolean,
  loading: boolean,
) {
  if (!gap && loading) return { label: '读取中…', detail: '正在整理差距与未知项', tone: 'neutral' }
  if (!gap && impact?.unknown_fields.length) {
    return {
      label: `${impact.unknown_fields.length} 个未知字段`,
      detail: impactMessage(impact.status, impact.message),
      tone: 'warning',
    }
  }
  if (!gap) return { label: '详情不可用', detail: '该结果未保存供应商差距数据', tone: 'neutral' }
  if (recommended) return { label: '无阻塞风险', detail: '满足当前硬性条件', tone: 'good' }
  if (supplier.status === 'INFEASIBLE') {
    return {
      label: impact?.unknown_fields.length ? `${impact.unknown_fields.length} 个未知字段` : '存在硬性差距',
      detail: impact?.unknown_fields.length ? '已有失败项，未知项当前不改变推荐' : supplierConclusion(supplier, false),
      tone: impact?.unknown_fields.length ? 'warning' : 'danger',
    }
  }
  if (supplier.status === 'PENDING') {
    return {
      label: impact?.unknown_fields.length ? `${impact.unknown_fields.length} 个待确认项` : '仍需确认',
      detail: '确认后可能改变当前选择',
      tone: 'warning',
    }
  }
  return {
    label: impact?.unknown_fields.length ? `${impact.unknown_fields.length} 个未知字段` : '无未知项',
    detail: gap.cost_difference_vs_other === null
      ? '满足要求，可作为备选'
      : `与更优方案相差 ${moneyText(gap.currency, gap.cost_difference_vs_other)}`,
    tone: impact?.unknown_fields.length ? 'warning' : 'good',
  }
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
            <p className="eyebrow">报价证据</p>
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
              <div><p className="eyebrow">NORMALIZED FIELDS</p><h3>解析字段与证据</h3></div>
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
  const [selectedSupplierIndex, setSelectedSupplierIndex] = useState<number | null>(null)
  const [expandedGapQuoteId, setExpandedGapQuoteId] = useState<string | null | undefined>(undefined)
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
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['tasks', taskId] })
      navigate(`/tasks/${taskId}/decision`)
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
    queryKey: ['tasks', taskId, 'selection-gaps', taskQuery.data?.task_revision],
    queryFn: () => api.getSelectionGaps(taskId, taskQuery.data!.task_revision),
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
  const loadedEvidenceCount = fieldQueries.filter((query) => Boolean(query.data)).length
  const task = taskQuery.data
  const canReanalyze = Boolean(
    task &&
    task.current_result_id === null &&
    task.quotes.length > 0 &&
    task.task_revision > 1 &&
    (task.status === 'DRAFT' || task.status === 'FAILED' || task.status === 'NEEDS_INPUT'),
  )
  const selectedSupplier = selectedSupplierIndex === null ? null : suppliers[selectedSupplierIndex]
  const selectedFieldsQuery = selectedSupplierIndex === null ? null : fieldQueries[selectedSupplierIndex]
  const frozenRequirement = resultQuery.data.input_snapshot?.requirement
    ?? (resultQuery.data.is_current ? task?.requirement : undefined)
  const frozenDecisionProfile = resultQuery.data.input_snapshot?.decision_profile
    ?? (resultQuery.data.is_current ? task?.decision_profile : undefined)
  const currency = frozenRequirement?.currency
  const successfulPolicyRetrievals = policyRetrievals.filter((item) => item.status === 'OK').length
  const policyReviewCount = resultQuery.data.policy_compliance.counts.REVIEW_REQUIRED ?? 0
  const currentRanking = frozenDecisionProfile?.preferences.ranking_mode
    ?? frozenRequirement?.ranking_preference
  const suppliersByQuote = new Map(suppliers.map((supplier) => [supplier.quote_id, supplier]))
  const quoteImpactsByQuote = new Map(decisionImpact?.quote_impacts.map((impact) => [impact.quote_id, impact]) ?? [])
  const selectionGapsByQuote = new Map(selectionGapsQuery.data?.gaps.map((gap) => [gap.quote_id, gap]) ?? [])
  const clarificationDraftsByQuote = new Map(
    selectionGapsQuery.data?.clarification_drafts.map((draft) => [draft.quote_id, draft]) ?? [],
  )
  const defaultExpandedGapQuoteId = selectionGapsQuery.data?.gaps.find((gap) => (
    gap.failed_reasons.length > 0 || gap.pending_reasons.length > 0
  ))?.quote_id ?? null
  const activeGapQuoteId = expandedGapQuoteId === undefined ? defaultExpandedGapQuoteId : expandedGapQuoteId
  const activeGap = activeGapQuoteId ? selectionGapsByQuote.get(activeGapQuoteId) : undefined
  const activeGapSupplier = activeGapQuoteId ? suppliersByQuote.get(activeGapQuoteId) : undefined
  const activeGapImpact = activeGapQuoteId ? quoteImpactsByQuote.get(activeGapQuoteId) : undefined
  const activeGapDraft = activeGapQuoteId ? clarificationDraftsByQuote.get(activeGapQuoteId) : undefined
  const activeGapReasons = activeGap ? [...activeGap.failed_reasons, ...activeGap.pending_reasons] : []
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
          title={frozenRequirement?.manufacturer_part_number ?? '历史采购结果'}
          subtitle={frozenRequirement
            ? `${frozenRequirement.required_quantity} ${frozenRequirement.quantity_unit} · ${frozenRequirement.currency} · 最晚交付 ${frozenRequirement.delivery_deadline}`
            : '该历史结果缺少可展示的冻结采购需求'}
          status={task.status}
          revision={resultQuery.data.task_revision}
          revisionContext={resultQuery.data.is_current ? 'current' : 'historical'}
          resultId={task.current_result_id}
          quoteCount={task.quotes.length}
          summaryComplete={task.summary_completed}
          progress={task.progress}
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
          <span>当前仅使用正式提交且字段审核通过的报价；字段证据 {loadedEvidenceCount} / {suppliers.length} 已读取。</span>
        </div>
        <div>
          <span>{feasibleCount} 家可行{pendingCount > 0 ? ` · ${pendingCount} 家待确认` : ''}</span>
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

      <div className="decision-workspace-layout">
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
                  {recommendationNarrative(primaryRecommendation, suppliers, currency, currentRanking)}
                </p>
                {frozenRequirement && (
                  <div className="decision-hero-context">
                    <span>{rankingLabels[currentRanking ?? ''] ?? currentRanking}</span>
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
                  <span className="signal-badge">Rev {resultQuery.data.task_revision}</span>
                </div>
                <dl>
                  <div><dt>排序</dt><dd>{rankingLabels[currentRanking ?? ''] ?? currentRanking ?? '未设置'}</dd></div>
                  <div><dt>成本容差</dt><dd>{!frozenDecisionProfile || frozenDecisionProfile.preferences.cost_tolerance_amount === null ? '未设置' : moneyText(currency, frozenDecisionProfile.preferences.cost_tolerance_amount)}</dd></div>
                  <div><dt>排除供应商</dt><dd>{frozenDecisionProfile?.preferences.excluded_supplier_ids.join('、') || '无'}</dd></div>
                </dl>
              </article>
              <article>
                <div><strong>制度证据</strong><span className="signal-badge">{successfulPolicyRetrievals} / {policyRetrievals.length}</span></div>
                <p>{policyState}；{policyReviewCount > 0 ? `${policyReviewCount} 家供应商仍需人工核验。` : '仍不等同于最终合规审批。'}</p>
                {task && resultQuery.data.is_current && <Link to={`/tasks/${task.task_id}/compliance`}>查看制度依据</Link>}
              </article>
            </aside>
          </section>

          <section className="comparison-matrix-panel">
            <header>
              <div><p className="eyebrow">确定性比较</p><h2>供应商比较矩阵</h2></div>
              <div>
                {primaryRecommendation && (
                  <button className="button button-secondary" type="button" onClick={() => setSelectedSupplierIndex(suppliers.indexOf(primaryRecommendation))}>解释推荐</button>
                )}
                {task && <Link className="button button-secondary" to={`/tasks/${task.task_id}/quotes/new`}>查看全部证据</Link>}
              </div>
            </header>
            <div className="comparison-matrix-scroll">
              <table className="comparison-matrix-table">
                <thead>
                  <tr>
                    <th>指标</th>
                    {suppliers.map((supplier, index) => (
                      <th className={recommended.has(supplier.quote_id) ? 'matrix-recommended' : ''} key={supplier.quote_id}>
                        <button className="supplier-link" type="button" onClick={() => setSelectedSupplierIndex(index)}>{supplier.supplier_name}</button>
                        <small>第 {supplier.quote_version} 版</small>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <tr><th>已确认总成本</th>{suppliers.map((supplier) => <td className={recommended.has(supplier.quote_id) ? 'matrix-recommended matrix-best' : ''} key={supplier.quote_id}>{moneyText(currency, supplier.total_cost)}{recommended.has(supplier.quote_id) && <span className="matrix-tag">推荐</span>}</td>)}</tr>
                  <tr><th>预计到货</th>{suppliers.map((supplier) => <td className={recommended.has(supplier.quote_id) ? 'matrix-recommended matrix-best' : ''} key={supplier.quote_id}>{valueText(supplier.estimated_arrival_date)}</td>)}</tr>
                  <tr><th>实际采购量</th>{suppliers.map((supplier) => <td className={recommended.has(supplier.quote_id) ? 'matrix-recommended' : ''} key={supplier.quote_id}>{quantityText(supplier.actual_quantity, frozenRequirement?.quantity_unit)}</td>)}</tr>
                  <tr><th>可行性</th>{suppliers.map((supplier) => <td className={recommended.has(supplier.quote_id) ? 'matrix-recommended' : ''} key={supplier.quote_id}><span className={'supplier-status supplier-status-' + supplier.status.toLowerCase()}>{quoteStatusLabel(supplier.status)}</span></td>)}</tr>
                  <tr><th>字段证据</th>{suppliers.map((supplier, index) => <td className={recommended.has(supplier.quote_id) ? 'matrix-recommended' : ''} key={supplier.quote_id}><button className="evidence-action" type="button" onClick={() => setSelectedSupplierIndex(index)}>{fieldQueries[index]?.isPending ? '读取中…' : `查看 ${evidenceCount(fieldQueries[index]?.data)} 个来源`}</button></td>)}</tr>
                  <tr><th>选择结论</th>{suppliers.map((supplier) => <td className={recommended.has(supplier.quote_id) ? 'matrix-recommended' : ''} key={supplier.quote_id}><div className="matrix-cell-summary"><strong>{supplierConclusion(supplier, recommended.has(supplier.quote_id))}</strong><small>{recommended.has(supplier.quote_id) ? '当前排序下优先' : supplier.status === 'FEASIBLE' ? '满足要求，可作为备选' : '未进入当前推荐'}</small></div></td>)}</tr>
                  <tr>
                    <th>差距与未知项</th>
                    {suppliers.map((supplier) => {
                      const summary = matrixGapSummary(
                        supplier,
                        selectionGapsByQuote.get(supplier.quote_id),
                        quoteImpactsByQuote.get(supplier.quote_id),
                        recommended.has(supplier.quote_id),
                        selectionGapsQuery.isFetching,
                      )
                      return (
                        <td className={recommended.has(supplier.quote_id) ? 'matrix-recommended' : ''} key={supplier.quote_id}>
                          <div className="matrix-cell-summary">
                            <span className={`matrix-gap-tag matrix-gap-${summary.tone}`}>{summary.label}</span>
                            <small>{summary.detail}</small>
                          </div>
                        </td>
                      )
                    })}
                  </tr>
                  <tr>
                    <th>解释与沟通</th>
                    {suppliers.map((supplier) => (
                      <td className={recommended.has(supplier.quote_id) ? 'matrix-recommended' : ''} key={supplier.quote_id}>
                        <button
                          className="matrix-detail-toggle"
                          type="button"
                          disabled={!selectionGapsQuery.data}
                          aria-expanded={activeGapQuoteId === supplier.quote_id}
                          onClick={() => setExpandedGapQuoteId((current) => {
                            const resolvedCurrent = current === undefined ? defaultExpandedGapQuoteId : current
                            return resolvedCurrent === supplier.quote_id ? null : supplier.quote_id
                          })}
                        >{activeGapQuoteId === supplier.quote_id ? '收起详情' : '展开详情'}</button>
                      </td>
                    ))}
                  </tr>
                  {activeGap && activeGapSupplier && (
                    <tr className="matrix-expanded-row">
                      <td colSpan={suppliers.length + 1}>
                        <div className="matrix-expanded-detail">
                          <header>
                            <div><strong>{activeGapSupplier.supplier_name} · 详细说明</strong><small>第 {activeGap.quote_version} 版报价</small></div>
                            <span className={`supplier-status supplier-status-${activeGap.status.toLowerCase()}`}>{quoteStatusLabel(activeGap.status)}</span>
                          </header>
                          <div className="matrix-expanded-grid">
                            <section>
                              <h3>关键结论与{activeGapReasons.length > 0 ? '未入选原因' : '选择依据'}</h3>
                              <p className="matrix-expanded-conclusion">{supplierConclusion(activeGapSupplier, recommended.has(activeGap.quote_id))}</p>
                              {activeGapReasons.length > 0 ? (
                                <ul>{activeGapReasons.map((reason, index) => <li key={`${reason.code}-${index}`}>{reasonText(reason)}</li>)}</ul>
                              ) : <p>该报价满足当前采购硬性要求，可以进入排序比较。</p>}
                            </section>
                            <section>
                              <h3>未知项对选择的影响</h3>
                              <p>{activeGapImpact
                                ? impactMessage(activeGapImpact.status, activeGapImpact.message)
                                : '当前没有保存会影响选择的未知项说明。'}</p>
                              {activeGapImpact && activeGapImpact.unknown_fields.length > 0 && (
                                <div className="decision-impact-fields">
                                  {activeGapImpact.unknown_fields.map((field) => <span key={field}>{fieldLabel(field)}</span>)}
                                </div>
                              )}
                            </section>
                            <section>
                              <h3>建议沟通策略</h3>
                              <p>{activeGapDraft?.text ?? '当前没有需要向供应商补充确认的沟通事项。'}</p>
                            </section>
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
            金额、数量、可行性和推荐来自 {displayDate(payload.evaluated_at)} 保存的确定性计算结果；AI 只解释事实并提出需确认的情景变更。
          </p>
        </main>

        {task && (
          <aside className="decision-chat-rail">
            <DecisionScenarioWorkspace
              key={resultQuery.data.result_id}
              task={task}
              result={resultQuery.data}
              compact
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
