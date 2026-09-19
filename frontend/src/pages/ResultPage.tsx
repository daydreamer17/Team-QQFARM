import { useQueries, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiClientError } from '../api/client'
import type {
  FieldEvidence,
  QuoteFieldsResponse,
  ResultReason,
  SupplierComparisonResult,
} from '../api/types'
import { DecisionScenarioWorkspace } from '../components/DecisionScenarioWorkspace'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'

const statusLabels: Record<string, string> = {
  FEASIBLE: '符合要求',
  INFEASIBLE: '不符合要求',
  PENDING: '等待确认',
}

function errorMessage(error: unknown) {
  return error instanceof ApiClientError ? error.message : '结果读取失败。'
}

function valueText(value: unknown) {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
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
  if (supplier.failed_reasons.length > 0) return supplier.failed_reasons[0].message
  if (supplier.pending_reasons.length > 0) return supplier.pending_reasons[0].message
  return supplier.status === 'FEASIBLE' ? '可进入比较' : statusLabels[supplier.status] ?? supplier.status
}

function Reasons({ title, reasons }: { title: string; reasons: ResultReason[] }) {
  if (reasons.length === 0) return null
  return (
    <section className="drawer-reasons">
      <h3>{title}</h3>
      <ul>
        {reasons.map((reason, index) => (
          <li key={reason.code + index}>
            <strong>{reason.code}</strong>
            <span>{reason.message}</span>
            {reason.fields.length > 0 && <small>{reason.fields.join('、')}</small>}
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
            <p className="eyebrow">QUOTE EVIDENCE</p>
            <h2>{supplier.supplier_name}</h2>
            <span>{supplier.quote_id} · Version {supplier.quote_version}</span>
          </div>
          <button className="drawer-close" type="button" onClick={onClose} aria-label="关闭">×</button>
        </header>

        <div className="drawer-summary">
          <div><span>可行性</span><strong>{statusLabels[supplier.status] ?? supplier.status}</strong></div>
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
                  <code>{field.field_name}</code>
                  <span className={'field-status field-status-' + field.validation_status.toLowerCase()}>
                    {field.validation_status}
                  </span>
                </div>
                <dl>
                  <div><dt>原始表达</dt><dd>{valueText(field.raw_value)}</dd></div>
                  <div><dt>标准化值</dt><dd>{valueText(field.normalized_value)} {valueText(field.unit) === '—' ? '' : field.unit}</dd></div>
                  <div><dt>来源类型</dt><dd>{valueText(field.origin)}</dd></div>
                </dl>
                {field.evidence.length > 0 ? (
                  <div className="drawer-evidence-list">
                    {field.evidence.map((evidence, index) => (
                      <blockquote key={(evidence.source_id ?? 'source') + index}>
                        <span>{evidenceLocation(evidence)} · {evidence.source_id ?? '无来源 ID'}</span>
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
  const [selectedSupplierIndex, setSelectedSupplierIndex] = useState<number | null>(null)
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
  const suppliers = resultQuery.data?.result.supplier_results ?? []
  const fieldQueries = useQueries({
    queries: suppliers.map((supplier) => ({
      queryKey: ['tasks', taskId, 'quotes', supplier.quote_id, 'fields'],
      queryFn: () => api.getQuoteFields(taskId, supplier.quote_id),
    })),
  })

  if (resultQuery.isPending) {
    return <section className="card loading-panel">正在读取解析与比较结果…</section>
  }
  if (resultQuery.isError) {
    return (
      <section className="card error-panel" role="alert">
        <p className="eyebrow">RESULT ERROR</p>
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
  const feasibleCount = suppliers.filter((supplier) => supplier.status === 'FEASIBLE').length
  const pendingCount = suppliers.filter((supplier) => supplier.status === 'PENDING').length
  const loadedEvidenceCount = fieldQueries.filter((query) => Boolean(query.data)).length
  const task = taskQuery.data
  const selectedSupplier = selectedSupplierIndex === null ? null : suppliers[selectedSupplierIndex]
  const selectedFieldsQuery = selectedSupplierIndex === null ? null : fieldQueries[selectedSupplierIndex]
  const currency = task?.requirement.currency
  const successfulPolicyRetrievals = policyRetrievals.filter((item) => item.status === 'OK').length
  let policyState = '未绑定 Policy'
  if (taskQuery.isPending) policyState = '正在读取 Policy 绑定'
  else if (taskQuery.isError) policyState = 'Policy 绑定读取失败'
  else if (task?.policy_binding && policyRetrievals.length === 0) policyState = '未执行制度检索'
  else if (task?.policy_binding && successfulPolicyRetrievals === policyRetrievals.length) {
    policyState = `${successfulPolicyRetrievals} / ${policyRetrievals.length} 找到证据`
  } else if (task?.policy_binding) {
    policyState = `${policyRetrievals.length - successfulPolicyRetrievals} 项需复核`
  }

  return (
    <div className="page-stack result-page">
      {task ? (
        <TaskWorkspaceHeader
          taskId={task.task_id}
          scenarioId={task.scenario_id}
          title={task.requirement.manufacturer_part_number}
          subtitle={`${task.requirement.required_quantity} ${task.requirement.quantity_unit} · ${task.requirement.currency} · 最晚交付 ${task.requirement.delivery_deadline}`}
          status={task.status}
          revision={task.task_revision}
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

      <section className="decision-section-lead">
        <div>
          <p className="eyebrow">DECISION COMPARISON</p>
          <h2>决策比较</h2>
          <p>确定性计算产出比较与初步推荐；前端不重新计算金额或硬约束。</p>
        </div>
        <span className={`status-pill ${resultQuery.data.is_current ? 'status-ready' : 'status-muted'}`}>
          {resultQuery.data.is_current ? '当前结果有效' : '历史结果'}
        </span>
      </section>

      <section className="decision-state-ribbon" aria-label="分层状态">
        <article><span>任务</span><strong>{task?.status ?? '已生成结果'}</strong></article>
        <article><span>字段证据</span><strong>{loadedEvidenceCount} / {suppliers.length} 已读取</strong></article>
        <article><span>可行性</span><strong>{feasibleCount} 可行 · {pendingCount} 待确认</strong></article>
        <article><span>制度证据</span><strong>{policyState}</strong></article>
      </section>

      {task && (
        <section className="decision-context" aria-label="本次比较口径">
          <span className="decision-preference">排序口径：{task.requirement.ranking_preference}</span>
          <span>✓ {task.requirement.condition}</span>
          <span>✓ {task.requirement.manufacturer_part_number} / {task.requirement.package}</span>
          <span>✓ {task.requirement.allow_substitutes ? '允许替代料' : '禁止替代料'}</span>
          <span>✓ 截止 {task.requirement.delivery_deadline}</span>
        </section>
      )}

      <section className="result-policy-summary decision-impact-summary">
        <div>
          <p className="eyebrow">DECISION IMPACT</p>
          <h2>未知项对当前选择的影响</h2>
          <p>{decisionImpact ? decisionImpact.scope : '该历史结果没有保存决策影响证明。'}</p>
        </div>
        {decisionImpact && (
          <div className="impact-list">
            {decisionImpact.quote_impacts.map((impact) => (
              <article key={impact.quote_id}>
                <div><strong>{impact.quote_id}</strong><span className={`status-pill impact-${impact.status.toLowerCase().replaceAll('_', '-')}`}>{impact.status}</span></div>
                <p>{impact.message}</p>
                {impact.unknown_fields.length > 0 && <small>未知字段：{impact.unknown_fields.join('、')}</small>}
                {impact.cost_lower_bound !== null && <small>成本下界：{currency ?? ''} {impact.cost_lower_bound}</small>}
              </article>
            ))}
          </div>
        )}
        {task && <Link className="button button-secondary" to={`/tasks/${task.task_id}/gaps`}>查看入选差距</Link>}
      </section>

      <section className="result-policy-summary">
        <div>
          <p className="eyebrow">POLICY EVIDENCE</p>
          <h2>制度证据摘要</h2>
          <p>{policyState}。制度检索结果仅证明找到或未找到可引用条款，不代表最终合规审批。</p>
        </div>
        <div className="result-policy-checks">
          {policyRetrievals.length > 0 ? policyRetrievals.map((retrieval) => {
            const codes = [...new Set([...retrieval.covered_control_codes, ...retrieval.missing_control_codes])]
            return (
              <span className={`policy-retrieval-status policy-status-${retrieval.status.toLowerCase().replace('_', '-')}`} key={retrieval.retrieval_id}>
                {codes.join('、') || '控制项'} · {retrieval.status}
              </span>
            )
          }) : <span className="status-pill status-muted">无制度检索记录</span>}
        </div>
        {task && <Link className="button button-secondary" to={`/tasks/${task.task_id}/compliance`}>查看完整制度证据</Link>}
      </section>

      <section className="decision-grid">
        <div className="comparison-table-wrap">
          <table className="comparison-table">
            <thead>
              <tr>
                <th>供应商</th>
                <th>可行性</th>
                <th>确认总成本</th>
                <th>预计到货</th>
                <th>证据</th>
                <th>当前结论</th>
              </tr>
            </thead>
            <tbody>
              {suppliers.map((supplier, index) => {
                const isRecommended = recommended.has(supplier.quote_id)
                const fieldsQuery = fieldQueries[index]
                return (
                  <tr className={isRecommended ? 'comparison-row-recommended' : ''} key={supplier.quote_id}>
                    <td>
                      <button className="supplier-link" type="button" onClick={() => setSelectedSupplierIndex(index)}>
                        {supplier.supplier_name}
                      </button>
                      <small>{supplier.quote_id} · v{supplier.quote_version}</small>
                    </td>
                    <td><span className={'supplier-status supplier-status-' + supplier.status.toLowerCase()}>{statusLabels[supplier.status] ?? supplier.status}</span></td>
                    <td className="comparison-cost">{supplier.total_cost === null ? '—' : `${currency ?? ''} ${supplier.total_cost}`.trim()}</td>
                    <td>{valueText(supplier.estimated_arrival_date)}</td>
                    <td>
                      <button className="evidence-action" type="button" onClick={() => setSelectedSupplierIndex(index)}>
                        {fieldsQuery?.isPending ? '读取中…' : `${evidenceCount(fieldsQuery?.data)} 个来源`}
                      </button>
                    </td>
                    <td className="comparison-conclusion">{supplierConclusion(supplier, isRecommended)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        <aside className="recommendation-panel">
          <span className="recommendation-kicker">当前排序场景</span>
          <h2>{recommendedNames.length > 0 ? `初步推荐 ${recommendedNames.join('、')}` : '暂无可发布推荐'}</h2>
          <p className="recommendation-disposition">{payload.disposition}</p>
          {payload.comparison_reasons.length > 0 ? (
            <ul>
              {payload.comparison_reasons.map((reason, index) => (
                <li key={reason.code + index}>{reason.message}</li>
              ))}
            </ul>
          ) : (
            <p className="recommendation-empty">后端未返回额外比较说明。</p>
          )}
          <div className="recommendation-meta">
            <span>{payload.rule_version}</span>
            <span>{displayDate(payload.evaluated_at)}</span>
          </div>
          <div className="gate-warning">
            制度证据不等于最终合规审批，审批功能仍为 Target；此处只能作为初步推荐，不能视为中标或采购批准。
          </div>
          <div className="recommendation-actions">
            {suppliers.length > 0 && (
              <button
                className="button button-secondary"
                type="button"
                onClick={() => setSelectedSupplierIndex(Math.max(0, suppliers.findIndex((supplier) => recommended.has(supplier.quote_id))))}
              >
                查看关键证据
              </button>
            )}
            {task && <Link className="button button-submit" to={`/tasks/${task.task_id}/compliance`}>查看制度证据</Link>}
          </div>
        </aside>
      </section>

      {task && (
        <DecisionScenarioWorkspace task={task} result={resultQuery.data} />
      )}

      <p className="result-boundary">
        金额、数量、可行性和推荐均来自后端冻结结果；结果 ID {resultQuery.data.result_id}，基于 Task Rev {resultQuery.data.task_revision}。
      </p>

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
