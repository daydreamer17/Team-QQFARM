import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, type ReactNode } from 'react'
import { useParams } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import type { ResultReason } from '../api/types'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'
import { cleanSummaryText, summaryStatusLabel } from '../lib/presentation'

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
  ['03', '报价与成本', 'summary-cost'],
  ['04', '交付与风险', 'summary-risk'],
  ['05', '制度与合规', 'summary-policy'],
  ['06', '建议与下一步', 'summary-next'],
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
  const supplierNames = new Map(suppliers.map((item) => [item.quote_id, item.supplier_name]))
  const narrativeText = (value: string) => cleanSummaryText(value, supplierNames)
  const narrative = current?.narrative
  const policySections = narrative?.sections.filter((section) =>
    /制度|政策|合规|审批|RoHS/i.test(section.heading + section.text)) ?? []
  const commercialSections = narrative?.sections.filter((section) =>
    !policySections.includes(section)) ?? []
  const feasibleCount = suppliers.filter((item) => item.status === 'FEASIBLE').length
  const pendingCount = suppliers.filter((item) => item.status === 'PENDING').length
  const infeasibleCount = suppliers.filter((item) => item.status === 'INFEASIBLE').length
  const recommendedIds = new Set(
    current?.facts.recommended_quote_ids ?? result.data?.result.recommended_quote_ids ?? [],
  )
  const recommended = suppliers.find((item) => recommendedIds.has(item.quote_id))
  const chartRows = suppliers.filter((item) => item.total_cost !== null)
  const budget = Number(data.requirement.budget_amount)
  const chartMax = Math.max(
    Number.isFinite(budget) ? budget : 0,
    ...chartRows.map((item) => Number(item.total_cost) || 0),
    1,
  )
  const referenceCount = new Set(
    narrative?.sections.flatMap((section) => section.reference_ids) ?? [],
  ).size
  const policyCounts = result.data?.policy_compliance.counts
  const isExportable = current?.status === 'SUCCEEDED' && Boolean(narrative)
  const generatedAt = current ? displayDate(current.updated_at) : '—'

  const exportPdf = () => {
    if (!isExportable || !current) return
    const previousTitle = document.title
    document.title = `${data.scenario_id ?? data.requirement.manufacturer_part_number}_采购总结_第${current.task_revision}版`
    window.addEventListener('afterprint', () => { document.title = previousTitle }, { once: true })
    window.print()
  }

  return (
    <div className="page-stack summary-page">
      <TaskWorkspaceHeader
        taskId={data.task_id}
        scenarioId={data.scenario_id}
        title={data.requirement.manufacturer_part_number}
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
          <strong>采购分析报告</strong>
          <span>{data.scenario_id ?? data.requirement.manufacturer_part_number} · 第 {current?.task_revision ?? data.task_revision} 版 · {generatedAt}</span>
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
          <button
            className="button button-secondary summary-print-button"
            type="button"
            disabled={!isExportable}
            onClick={exportPdf}
          >打印 / 导出 PDF</button>
        </div>
      </section>

      {(generate.isError || retry.isError) && (
        <div className="form-error" role="alert">{errorMessage(generate.error ?? retry.error)}</div>
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
          <span>报告</span>
          <div>
            <h2>{current ? '采购总结正在生成' : '尚未生成采购总结'}</h2>
            <p>{current ? 'Worker 完成后会自动刷新为可阅读、可导出的报告。' : '完成决策比较后，可生成基于冻结事实的采购报告。'}</p>
          </div>
        </section>
      )}

      {narrative && current && (
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
                <p>PROCUREMENT BRIEF</p>
                <h1>{data.requirement.manufacturer_part_number} 采购总结</h1>
                <span>{data.requirement.required_quantity} {data.requirement.quantity_unit} · {data.requirement.package} · {data.requirement.manufacturer_part_number}</span>
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
                    ? `确认总成本 ${data.requirement.currency} ${recommended.total_cost ?? '待确认'} · 预计到货 ${recommended.estimated_arrival_date ?? '待确认'}`
                    : '请先处理待确认信息，再重新生成采购结论。'}</p>
                </div>
                <small>初步建议，不代表最终采购批准。</small>
              </div>
            </ReportSection>

            <ReportSection number="02" title="采购需求" id="summary-requirement">
              <div className="summary-requirement-layout">
                <p>
                  本次采购对象为 {data.requirement.manufacturer} 的 {data.requirement.manufacturer_part_number}，
                  需求数量为 {data.requirement.required_quantity} {data.requirement.quantity_unit}，
                  预算上限为 {data.requirement.currency} {data.requirement.budget_amount}，
                  并要求在 {data.requirement.delivery_deadline} 前完成交付。
                </p>
                <dl>
                  <div><dt>数量</dt><dd>{data.requirement.required_quantity} {data.requirement.quantity_unit}</dd></div>
                  <div><dt>封装</dt><dd>{data.requirement.package}</dd></div>
                  <div><dt>预算</dt><dd>{data.requirement.currency} {data.requirement.budget_amount}</dd></div>
                  <div><dt>交付截止</dt><dd>{data.requirement.delivery_deadline}</dd></div>
                </dl>
              </div>
            </ReportSection>

            <ReportSection number="03" title="报价与成本" id="summary-cost">
              <div className="summary-cost-layout">
                <div className="summary-narrative-list">
                  {commercialSections.map((section, index) => (
                    <section key={section.heading + index}>
                      <h3>{narrativeText(section.heading)}</h3>
                      <p>{narrativeText(section.text)}</p>
                    </section>
                  ))}
                  {commercialSections.length === 0 && (
                    <p>本次共纳入 {suppliers.length} 份正式报价，其中 {feasibleCount} 份满足当前采购要求。</p>
                  )}
                </div>
                <figure className="summary-cost-chart">
                  <figcaption>已确认报价总成本</figcaption>
                  <div className="summary-cost-chart-body">
                    {chartRows.map((supplier) => (
                      <div className="summary-cost-row" key={supplier.quote_id}>
                        <span>{supplier.supplier_name}</span>
                        <div><i style={{ width: `${Math.max(7, ((Number(supplier.total_cost) || 0) / chartMax) * 100)}%` }} /></div>
                        <strong>{data.requirement.currency} {supplier.total_cost}</strong>
                      </div>
                    ))}
                  </div>
                  <small>预算上限：{data.requirement.currency} {data.requirement.budget_amount}</small>
                </figure>
              </div>
            </ReportSection>

            <ReportSection number="04" title="交付与风险" id="summary-risk">
              <p>
                从当前冻结结果看，共有 {feasibleCount} 份报价可行、{pendingCount} 份报价等待确认、
                {infeasibleCount} 份报价不符合要求。交付日期、成本完整性和未解决字段仍应在正式采购前复核。
              </p>
              <div className="summary-risk-strip">
                <div><span>交付风险</span><strong className={pendingCount > 0 ? 'risk-review' : 'risk-low'}>{pendingCount > 0 ? '需复核' : '低'}</strong></div>
                <div><span>数据完整性</span><strong className={pendingCount > 0 ? 'risk-medium' : 'risk-low'}>{pendingCount > 0 ? '中' : '良好'}</strong></div>
                <div><span>不可行报价</span><strong className={infeasibleCount > 0 ? 'risk-high' : 'risk-low'}>{infeasibleCount} 份</strong></div>
              </div>
            </ReportSection>

            <ReportSection number="05" title="制度与合规" id="summary-policy">
              <p>
                {data.policy_binding
                  ? '当前采购任务已绑定制度版本。制度检索与引用用于提示需要核验的控制项，不构成最终合规审批。'
                  : '当前采购任务未绑定制度，正式采购前需要补充适用制度并完成合规核验。'}
              </p>
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

            <ReportSection number="06" title="建议与下一步" id="summary-next">
              <ComparisonReasons reasons={result.data?.result.comparison_reasons ?? []} />
              <div className="summary-next-step">
                <strong>建议操作</strong>
                <p>在发起正式采购前，复核报价原件、待确认字段和制度证据，并由具备权限的人员完成最终批准。</p>
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
              <div><dt>引用证据</dt><dd>{data.quotes.length} 份报价 · {referenceCount} 条引用</dd></div>
              <div><dt>版本</dt><dd>第 {current.task_revision} 版</dd></div>
              <div><dt>生成时间</dt><dd>{generatedAt}</dd></div>
            </dl>
            <p>导出的 PDF 包含章节正文、图表、使用边界、版本和生成时间。</p>
            <button
              className="button button-submit"
              type="button"
              disabled={!isExportable}
              onClick={exportPdf}
            >导出 PDF</button>
            <small>将在系统打印窗口中选择“另存为 PDF”。</small>
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
