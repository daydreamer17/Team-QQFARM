import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import type { ResultReason, SupplierComparisonResult } from '../api/types'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'
import { rankingCriterionLabel } from '../lib/rankingCriteria'
import { supplierSelectionExplanation } from '../lib/resultComparison'
import { cleanSummaryText, quoteStatusLabel, reasonText, summaryStatusLabel } from '../lib/presentation'

function errorMessage(error: unknown) {
  return error instanceof ApiClientError ? error.message : '采购总结读取失败。'
}

function displayDate(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
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
  const unitLabels: Record<string, string> = { piece: '件', pieces: '件', unit: '件', units: '件' }
  return `${new Intl.NumberFormat('zh-CN').format(value)} ${unitLabels[unit ?? ''] ?? unit ?? ''}`.trim()
}

function paymentText(supplier: SupplierComparisonResult) {
  return supplier.payment_term?.normalized_text
    ?? supplier.payment_term?.raw_text
    ?? (supplier.payment_term?.net_days !== null && supplier.payment_term?.net_days !== undefined
      ? `${supplier.payment_term.net_days} 天`
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
  if (delta === 0) return '基准'
  return `${delta > 0 ? '+' : '−'}${moneyText(currency, String(Math.abs(delta)))}`
}

function communicationGoal(supplier: SupplierComparisonResult, isRecommended: boolean) {
  if (isRecommended) return '锁定当前报价与交付承诺'
  if (supplier.status === 'PENDING') return '补齐待确认信息，恢复可比较性'
  if (supplier.status === 'INFEASIBLE') return '确认能否修正不符合项'
  return '争取缩小与首选方案的差距'
}

function fallbackCommunicationDraft(
  supplier: SupplierComparisonResult,
  isRecommended: boolean,
  recommendation: SupplierComparisonResult | undefined,
  currency: string | undefined,
) {
  if (isRecommended) {
    return `请确认 ${moneyText(currency, supplier.total_cost)} 的总成本、${supplier.estimated_arrival_date ?? '当前交期'}及付款条件在报价有效期内保持不变，并书面回复供货承诺。`
  }
  const firstIssue = supplier.failed_reasons[0] ?? supplier.pending_reasons[0]
  if (firstIssue) {
    return `请书面澄清“${reasonText(firstIssue)}”，并提供可满足采购要求的修订报价、交付日期及有效期。`
  }
  const delta = costDeltaText(supplier, recommendation, currency)
  return `当前方案与首选方案的成本差额为 ${delta}。请确认是否可优化价格、交付或付款条件，并提交更新后的完整报价。`
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
  ['01', '执行摘要', 'summary-executive'],
  ['02', '采购需求', 'summary-requirement'],
  ['03', '报价与取舍', 'summary-cost'],
  ['04', '选择与沟通', 'summary-communication'],
  ['05', '风险与制度', 'summary-risk'],
  ['06', '行动与留档', 'summary-next'],
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
    return <section className="card loading-panel">正在读取采购总结…</section>
  }
  if (task.isError || summaries.isError) {
    return <section className="card error-panel" role="alert">{errorMessage(task.error ?? summaries.error)}</section>
  }

  const data = task.data
  const current = displayedSummary
  const mayGenerate = Boolean(
    data.current_result_id
    && data.status !== 'ABANDONED'
    && (!current || !current.is_current),
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
  const narrativeText = (value: string) => cleanSummaryText(value, supplierNames)
  const narrative = current?.narrative
  const policySections = narrative?.sections.filter((section) =>
    /制度|政策|合规|审批|RoHS/i.test(section.heading + section.text)) ?? []
  const communicationSections = narrative?.sections.filter((section) =>
    /沟通|询价|谈判|澄清|下一步|行动/i.test(section.heading + section.text)) ?? []
  const riskSections = narrative?.sections.filter((section) =>
    /风险|交付|未知|待确认|缺失/i.test(section.heading + section.text)) ?? []
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
    supplierSelectionExplanation(
      supplier,
      recommended,
      currentRanking,
      reportRequirement?.currency,
      recommendedIds.has(supplier.quote_id),
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
    document.title = `${data.task_name}_采购总结_第${current.task_revision}版`
    window.addEventListener('afterprint', () => { document.title = previousTitle }, { once: true })
    window.print()
  }

  return (
    <div className="page-stack summary-page">
      <TaskWorkspaceHeader
        taskId={data.task_id}
        scenarioId={data.scenario_id}
        title={data.task_name}
        subtitle={`${data.requirement.required_quantity} ${data.requirement.quantity_unit} · ${data.quotes.length} 份正式报价`}
        status={data.status}
        revision={data.task_revision}
        resultId={data.current_result_id}
        quoteCount={data.quotes.length}
        summaryComplete={data.summary_completed}
        progress={data.progress}
        active="summary"
      />

      <section className="summary-report-toolbar">
        <div>
          <strong>采购总结</strong>
          {current && <span>{data.task_name} · 第 {current.task_revision} 版 · {generatedAt}</span>}
        </div>
        <div className="summary-report-actions">
          {mayGenerate && (
            <button
              className="button button-submit"
              type="button"
              disabled={generate.isPending}
              onClick={() => generate.mutate()}
            >{generate.isPending ? '正在创建…' : '生成采购总结'}</button>
          )}
          {current?.status === 'FAILED' && current.is_current && data.status !== 'ABANDONED' && (
            <button
              className="button button-secondary"
              type="button"
              disabled={retry.isPending || current.calls_used >= current.max_calls}
              onClick={() => retry.mutate(current.summary_id)}
            >重试生成</button>
          )}
        </div>
      </section>

      {(generate.isError || retry.isError || exportDocument.isError) && (
        <div className="form-error" role="alert">{errorMessage(generate.error ?? retry.error ?? exportDocument.error)}</div>
      )}
      {current && !current.is_current && (
        <div className="run-notice">这是第 {current.task_revision} 版历史采购总结，需求、报价和制度信息均按当时冻结的数据展示。</div>
      )}
      {current?.status === 'FAILED' && (
        <section className="card error-panel">
          <strong>总结生成失败</strong>
          <p>{current.error_message}</p>
          <small>已尝试 {current.calls_used} 次</small>
        </section>
      )}

      {!narrative && current?.status !== 'FAILED' && (
        <section className="card summary-report-empty">
          <div>
            <h2>{current ? '采购总结正在生成' : '尚未生成采购总结'}</h2>
            <p>{current ? '生成完成后页面会自动更新。' : '完成决策比较后即可生成。'}</p>
            {!current && !data.current_result_id && <Link className="button button-secondary" to={`/tasks/${taskId}/decision`}>前往决策比较</Link>}
          </div>
        </section>
      )}

      {narrative && current && !reportRequirement && (
        <section className="card error-panel" role="alert">
          <strong>历史采购需求不可用</strong>
          <p>该总结缺少对应版本的冻结需求，系统不会使用当前任务数据替代。</p>
        </section>
      )}

      {narrative && current && reportRequirement && (
        <div className="summary-report-layout">
          <nav className="summary-report-outline" aria-label="报告目录">
            <strong>报告目录</strong>
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
                <h1>{reportRequirement.manufacturer_part_number} 采购总结</h1>
                <span>{reportRequirement.required_quantity} {reportRequirement.quantity_unit} · {reportRequirement.package} · {reportRequirement.manufacturer_part_number}</span>
              </div>
              <div className="summary-report-stamp">
                <strong>{recommended ? '初步推荐已形成' : '采购分析已完成'}</strong>
                <small>基于采购任务第 {current.task_revision} 版</small>
              </div>
            </header>

            <ReportSection number="01" title="执行摘要" id="summary-executive">
              <p>{narrativeText(narrative.overview)}</p>
              <div className={`summary-recommendation-callout${recommended ? '' : ' no-recommendation'}`}>
                <span aria-hidden="true">{recommended ? '✓' : '!'}</span>
                <div>
                  <strong>{recommended ? `建议优先推进 ${recommended.supplier_name}` : '当前不具备明确推荐条件'}</strong>
                  <p>{recommended
                    ? `确认总成本 ${moneyText(reportRequirement.currency, recommended.total_cost)} · 预计到货 ${recommended.estimated_arrival_date ?? '待确认'} · 排序依据 ${rankingCriterionLabel(currentRanking) || '当前决策设置'}`
                    : '请先处理待确认信息，再重新生成采购结论。'}</p>
                </div>
                <small>初步建议，不代表最终采购批准。</small>
              </div>
            </ReportSection>

            <ReportSection number="02" title="采购需求" id="summary-requirement">
              <div className="summary-requirement-layout">
                <p>
                  本次采购对象为 {reportRequirement.manufacturer} 的 {reportRequirement.manufacturer_part_number}，
                  需求数量为 {reportRequirement.required_quantity} {reportRequirement.quantity_unit}，
                  预算上限为 {reportRequirement.currency} {reportRequirement.budget_amount}，
                  并要求在 {reportRequirement.delivery_deadline} 前完成交付。
                </p>
                <dl>
                  <div><dt>数量</dt><dd>{reportRequirement.required_quantity} {reportRequirement.quantity_unit}</dd></div>
                  <div><dt>封装</dt><dd>{reportRequirement.package}</dd></div>
                  <div><dt>预算</dt><dd>{reportRequirement.currency} {reportRequirement.budget_amount}</dd></div>
                  <div><dt>交付截止</dt><dd>{reportRequirement.delivery_deadline}</dd></div>
                </dl>
              </div>
            </ReportSection>

            <ReportSection number="03" title="报价与取舍" id="summary-cost">
              <p className="summary-section-intro">
                以下表格使用冻结结果直接呈现成本、交付和可行性；自然语言只解释取舍，不重新计算或改变推荐结论。
              </p>
              <div className="summary-comparison-scroll">
                <table className="summary-comparison-table" style={{ minWidth: Math.max(700, 142 + suppliers.length * 175) }}>
                  <thead><tr><th>指标</th>{suppliers.map((supplier) => (
                    <th className={recommendedIds.has(supplier.quote_id) ? 'is-recommended' : ''} key={supplier.quote_id}>
                      {supplier.supplier_name}
                      {recommendedIds.has(supplier.quote_id) && <span>推荐</span>}
                    </th>
                  ))}</tr></thead>
                  <tbody>
                    <tr><th>确认总成本</th>{suppliers.map((supplier) => <td className={recommendedIds.has(supplier.quote_id) ? 'is-recommended is-best' : ''} key={supplier.quote_id}>{moneyText(reportRequirement.currency, supplier.total_cost)}</td>)}</tr>
                    <tr><th>相对首选差额</th>{suppliers.map((supplier) => <td className={recommendedIds.has(supplier.quote_id) ? 'is-recommended' : ''} key={supplier.quote_id}>{costDeltaText(supplier, recommended, reportRequirement.currency)}</td>)}</tr>
                    <tr><th>实际采购量</th>{suppliers.map((supplier) => <td className={recommendedIds.has(supplier.quote_id) ? 'is-recommended' : ''} key={supplier.quote_id}>{quantityText(supplier.actual_quantity, reportRequirement.quantity_unit)}</td>)}</tr>
                    <tr><th>预计到货</th>{suppliers.map((supplier) => <td className={recommendedIds.has(supplier.quote_id) ? 'is-recommended' : ''} key={supplier.quote_id}>{supplier.estimated_arrival_date ?? '—'}</td>)}</tr>
                    <tr><th>付款条件</th>{suppliers.map((supplier) => <td className={recommendedIds.has(supplier.quote_id) ? 'is-recommended' : ''} key={supplier.quote_id}>{paymentText(supplier)}</td>)}</tr>
                    <tr><th>可行性</th>{suppliers.map((supplier) => <td className={recommendedIds.has(supplier.quote_id) ? 'is-recommended' : ''} key={supplier.quote_id}><span className={`summary-status summary-status-${supplier.status.toLowerCase()}`}>{quoteStatusLabel(supplier.status)}</span></td>)}</tr>
                    <tr><th>本次取舍</th>{suppliers.map((supplier) => {
                      const selection = selectionSummaries.get(supplier.quote_id)
                      return <td className={recommendedIds.has(supplier.quote_id) ? 'is-recommended' : ''} key={supplier.quote_id}><strong className={`summary-tradeoff summary-tradeoff-${selection?.tone ?? 'neutral'}`}>{selection?.label}</strong><small>{selection?.detail}</small></td>
                    })}</tr>
                  </tbody>
                </table>
              </div>
              <div className="summary-cost-insight-grid">
                <figure className="summary-cost-chart">
                  <figcaption>已确认总成本与预算位置</figcaption>
                  <div className="summary-cost-chart-body">
                    {chartRows.map((supplier) => (
                      <div className="summary-cost-row" key={supplier.quote_id}>
                        <span>{supplier.supplier_name}</span>
                        <div><i className={recommendedIds.has(supplier.quote_id) ? 'is-recommended' : ''} style={{ width: `${Math.max(7, ((Number(supplier.total_cost) || 0) / chartMax) * 100)}%` }} /></div>
                        <strong>{moneyText(reportRequirement.currency, supplier.total_cost)}</strong>
                      </div>
                    ))}
                  </div>
                  <small>预算上限：{moneyText(reportRequirement.currency, reportRequirement.budget_amount)}</small>
                </figure>
                <figure className="summary-tradeoff-chart">
                  <figcaption>成本 × 预计到货位置</figcaption>
                  <div className="summary-tradeoff-plot">
                    <span className="summary-axis-y">成本越低越好</span>
                    <span className="summary-axis-x">预计到货日期 → 越晚</span>
                    {tradeoffRows.map((supplier) => {
                      const costRange = maxTradeoffCost - minTradeoffCost
                      const dateRange = maxTradeoffDate - minTradeoffDate
                      const cost = Number(supplier.total_cost)
                      const arrival = Date.parse(`${supplier.estimated_arrival_date}T00:00:00Z`)
                      const left = dateRange > 0 ? 12 + ((arrival - minTradeoffDate) / dateRange) * 70 : 46
                      const top = costRange > 0 ? 12 + ((cost - minTradeoffCost) / costRange) * 62 : 40
                      return <span className={`summary-tradeoff-point${recommendedIds.has(supplier.quote_id) ? ' is-recommended' : ''}`} style={{ left: `${left}%`, top: `${top}%` }} key={supplier.quote_id}><i /><small>{supplier.supplier_name}</small></span>
                    })}
                    {tradeoffRows.length === 0 && <p>暂无同时确认成本与交期的报价。</p>}
                  </div>
                </figure>
              </div>
              <div className="summary-ai-brief">
                <header><span>AI</span><div><strong>分析补充</strong><small>基于冻结事实生成，不参与计算</small></div></header>
                {commercialSections.slice(0, 3).map((section, index) => (
                  <section key={section.heading + index}><h3>{narrativeText(section.heading)}</h3><p>{narrativeText(section.text)}</p></section>
                ))}
                {commercialSections.length === 0 && <p>{narrativeText(narrative.overview)}</p>}
              </div>
            </ReportSection>

            <ReportSection number="04" title="选择说明与建议沟通内容" id="summary-communication">
              <p className="summary-section-intro">按供应商连续说明推荐或未选原因，并提供可由采购人员复核后使用的沟通措辞。</p>
              <div className="summary-communication-report">
                {suppliers.map((supplier) => {
                  const isRecommended = recommendedIds.has(supplier.quote_id)
                  const draft = communicationDrafts.get(supplier.quote_id)
                    ?? fallbackCommunicationDraft(supplier, isRecommended, recommended, reportRequirement.currency)
                  return (
                    <article className={isRecommended ? 'is-recommended' : ''} key={supplier.quote_id}>
                      <h3>{supplier.supplier_name}</h3>
                      <p className="summary-selection-line"><strong>{isRecommended ? '推荐原因' : '本次取舍'}：</strong>{selectionSummaries.get(supplier.quote_id)?.detail}</p>
                      <small>沟通目标：{communicationGoal(supplier, isRecommended)}</small>
                      <p><strong>建议沟通：</strong>{draft}</p>
                    </article>
                  )
                })}
              </div>
              {communicationSections.length > 0 && <div className="summary-communication-ai"><strong>AI 沟通提示</strong><p>{narrativeText(communicationSections[0].text)}</p></div>}
            </ReportSection>

            <ReportSection number="05" title="风险、制度与决策门槛" id="summary-risk">
              <p>
                当前共有 {feasibleCount} 份报价可行、{pendingCount} 份等待确认、{infeasibleCount} 份不符合要求。
                下列门槛用于判断能否进入正式批准，不会把未知值自动当作零或当作合格。
              </p>
              <div className="summary-risk-table-wrap">
                <table className="summary-risk-table">
                  <tbody>
                    <tr><th>报价完整性</th><td><strong className={pendingCount > 0 ? 'risk-review' : 'risk-low'}>{pendingCount > 0 ? `${pendingCount} 份待确认` : '已冻结'}</strong></td><td>{pendingCount > 0 ? '补齐可能影响可行性或排序的字段。' : '成本、交付和数量均来自当前冻结结果。'}</td></tr>
                    <tr><th>制度证据</th><td><strong className={(policyCounts?.REVIEW_REQUIRED ?? 0) > 0 ? 'risk-review' : 'risk-low'}>{reportPolicyBinding ? `${policyCounts?.REVIEW_REQUIRED ?? 0} 项待复核` : '未绑定制度'}</strong></td><td>{reportPolicyBinding ? `当前绑定制度版本 ${reportPolicyBinding.policy_set_version}。` : '正式采购前需补充适用制度。'}</td></tr>
                    <tr><th>推荐稳定性</th><td><strong className={pendingCount > 0 ? 'risk-review' : 'risk-low'}>{pendingCount > 0 ? '需要关注' : '当前稳定'}</strong></td><td>输入发生变化后必须创建新版本并重新计算。</td></tr>
                    <tr><th>批准状态</th><td><strong className="risk-review">尚未批准</strong></td><td>本报告是决策支持材料，不能替代有权限人员的采购批准。</td></tr>
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
                <span>已确认合规 <strong>{policyCounts?.COMPLIANT ?? 0}</strong></span>
                <span>需要人工确认 <strong>{policyCounts?.REVIEW_REQUIRED ?? feasibleCount}</strong></span>
                <span>未进入评估 <strong>{policyCounts?.NOT_EVALUATED ?? infeasibleCount}</strong></span>
              </div>
            </ReportSection>

            <ReportSection number="06" title="行动与留档" id="summary-next">
              <ComparisonReasons reasons={result.data?.result.comparison_reasons ?? []} />
              <div className="summary-action-list">
                <div><span>1</span><p><strong>完成供应商沟通</strong>确认沟通作战卡中的价格、交期、费用和待确认信息。</p></div>
                <div><span>2</span><p><strong>关闭制度复核项</strong>核对制度证据与例外条件，保留人工确认记录。</p></div>
                <div><span>3</span><p><strong>提交正式批准</strong>附上本版报价原件、比较结果和采购总结，由授权人员决定。</p></div>
              </div>
              <p className="summary-disclaimer">{narrativeText(narrative.disclaimer)}</p>
            </ReportSection>

            <footer className="summary-report-footer">
              <span>采购总结 · 第 {current.task_revision} 版</span>
              <span>生成时间 {generatedAt}</span>
            </footer>
          </article>

          <aside className="summary-report-rail">
            <h2>报告信息</h2>
            <dl>
              <div><dt>状态</dt><dd className={isExportable ? 'report-ready' : ''}>{isExportable ? '可导出' : summaryStatusLabel(current.status)}</dd></div>
              <div><dt>覆盖范围</dt><dd>6 个章节</dd></div>
              <div><dt>引用证据</dt><dd>{suppliers.length} 份报价 · {referenceCount} 条引用</dd></div>
              <div><dt>版本</dt><dd>第 {current.task_revision} 版</dd></div>
              <div><dt>生成时间</dt><dd>{generatedAt}</dd></div>
            </dl>
            <p>导出文件均绑定当前 Summary、Result 和 Task Revision，并包含正文、图表、使用边界与版本信息。</p>
            <div className="summary-export-options">
              <button className="button button-submit" type="button" disabled={!isExportable} onClick={exportPdf}>导出 PDF</button>
              <button className="button button-secondary" type="button" disabled={!isExportable || exportDocument.isPending} onClick={() => exportDocument.mutate({ summaryId: current.summary_id, format: 'md' })}>导出 Markdown</button>
              <button className="button button-secondary" type="button" disabled={!isExportable || exportDocument.isPending} onClick={() => exportDocument.mutate({ summaryId: current.summary_id, format: 'docx' })}>导出 Word</button>
            </div>
            <small>PDF 用于正式提交；Markdown 便于协作；Word 便于继续编辑。</small>
          </aside>
        </div>
      )}

      {summaries.data.items.length > 1 && (
        <section className="summary-history-section">
          <div className="section-heading"><h2>历史采购总结</h2><span>{summaries.data.items.length}</span></div>
          <div className="summary-history-list">
            {summaries.data.items.map((item) => (
              <article className="card summary-history-record" key={item.summary_id}>
                <strong>{summaryStatusLabel(item.status)}</strong>
                <span>采购任务第 {item.task_revision} 版</span>
                <small>{displayDate(item.created_at)}</small>
              </article>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
