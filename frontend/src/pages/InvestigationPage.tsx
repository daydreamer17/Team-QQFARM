import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api, ApiClientError } from '../api/client'
import type { InvestigationObservation } from '../api/types'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'
import { fieldLabel, impactStatusLabel } from '../lib/presentation'

function errorMessage(error: unknown) {
  return error instanceof ApiClientError ? error.message : '调查记录读取失败。'
}

const toolLabels: Record<string, string> = {
  get_task_context: '读取当前任务信息',
  analyze_decision_impact: '核对决策影响',
  get_comparison_result: '读取供应商比较结果',
  get_cost_breakdown: '读取成本明细',
  locate_quote_source: '定位报价原文',
  get_confirmed_quote_records: '查询历史人工确认',
  request_clarification: '请求人工补充信息',
  retrieve_policy: '检索适用制度',
  analyze_selection_gap: '分析入选差距',
  draft_clarification: '生成供应商澄清草稿',
  simulate_requirement_change: '模拟需求变化',
  get_policy_retrieval_status: '诊断制度检索状态',
  retry_policy_retrieval: '重试制度检索',
  read_decision_overview: '核对当前推荐',
  compare_alternatives: '比较备选方案',
  inspect_quote_evidence: '核对报价原文',
  inspect_supplier_history: '核对供应商历史',
  inspect_policy_evidence: '核对制度依据',
  compile_decision_brief: '整理核查结论',
}

const stopReasonLabels: Record<string, string> = {
  EVIDENCE_CONFIRMED: '证据已确认',
  REQUEST_COMPLETED: '调查目标已完成',
  NO_DECISION_IMPACT: '不影响当前决策',
  SOURCES_EXHAUSTED: '现有来源不足，等待补充',
  CONFLICT_UNRESOLVED: '证据冲突，等待人工处理',
  BUDGET_EXHAUSTED: '已达到本轮调查上限',
  INPUT_CHANGED: '任务数据已变化，本记录已失效',
  EVIDENCE_INSUFFICIENT: '证据不足',
  MODEL_UNAVAILABLE: 'Agent 暂时不可用',
}

function observationSummary(observation: InvestigationObservation) {
  const data = observation.result.data
  if (observation.result.tool_name === 'read_decision_overview') {
    return `已读取 ${Array.isArray(data.suppliers) ? data.suppliers.length : 0} 家供应商及当前排序依据。`
  }
  if (observation.result.tool_name === 'compare_alternatives') {
    return `已比较 ${Array.isArray(data.gaps) ? data.gaps.length : 0} 家供应商的成本、交期和阻碍差异。`
  }
  if (observation.result.tool_name === 'inspect_quote_evidence') {
    const focus = { COST: '成本', DELIVERY: '交期', TERMS: '商务条款', ALL: '关键' }[String(data.focus)] ?? '关键'
    return `已核对 ${String(data.supplier_name || data.quote_id || '该供应商')} 的${focus}报价证据（${Array.isArray(data.fields) ? data.fields.length : 0} 项）。`
  }
  if (observation.result.tool_name === 'inspect_supplier_history') {
    return `已核对 ${String(data.supplier_name || data.quote_id || '该供应商')} 的历史表现及数据可用性。`
  }
  if (observation.result.tool_name === 'inspect_policy_evidence') return '已核对本次结果冻结的制度检索与合规状态。'
  if (observation.result.tool_name === 'compile_decision_brief') {
    const pending = Array.isArray(data.unresolved_items) ? data.unresolved_items.length : 0
    return pending > 0 ? `已汇总结论，并列出 ${pending} 项待补证明或审批记录。` : '已整理本轮核查事实与结论。'
  }
  if (observation.result.tool_name === 'draft_clarification' && typeof data.text === 'string') {
    return data.text
  }
  if (observation.result.tool_name === 'analyze_selection_gap') {
    const details = [
      typeof data.cost_difference_vs_other === 'string' ? `成本差额 ${data.cost_difference_vs_other}` : null,
      typeof data.delivery_days_late === 'number' ? `交付差 ${data.delivery_days_late} 天` : null,
    ].filter(Boolean)
    return details.join('；') || '已形成成本、交付和阻塞项分析。'
  }
  if (observation.result.tool_name === 'simulate_requirement_change') {
    const comparison = data.comparison as Record<string, unknown> | undefined
    const rows = Array.isArray(comparison?.supplier_results) ? comparison.supplier_results as Record<string, unknown>[] : []
    const ids = Array.isArray(comparison?.recommended_quote_ids) ? comparison.recommended_quote_ids : []
    const recommended = rows.filter((row) => ids.includes(row.quote_id)).map((row) => String(row.supplier_name))
    const changes = data.changes as Record<string, unknown> | undefined
    const conditions = [changes?.budget_amount ? `预算 ${changes.budget_amount}` : null,
      changes?.delivery_deadline ? `交期 ${changes.delivery_deadline}` : null].filter(Boolean).join('、')
    return `假设条件：${conditions || '已授权条件'}；试算推荐：${recommended.join('、') || '暂无'}。正式采购需求与推荐未改变。`
  }
  if (observation.result.sources.length > 0) {
    return `找到 ${observation.result.sources.length} 条可追溯来源。`
  }
  if (observation.result.status === 'NOT_FOUND') return '当前范围内未找到可用记录。'
  if (observation.result.status === 'NEEDS_INPUT') return '需要用户或管理员补充信息。'
  if (observation.result.status === 'DENIED') return '该调用不在本次调查授权范围内。'
  return observation.reason || '检查结果已保存。'
}

export function InvestigationPage() {
  const { taskId = '' } = useParams()
  const task = useQuery({ queryKey: ['tasks', taskId], queryFn: () => api.getTask(taskId), enabled: Boolean(taskId) })
  const investigations = useQuery({ queryKey: ['tasks', taskId, 'investigations'], queryFn: () => api.listInvestigations(taskId), enabled: Boolean(taskId) })
  if (task.isPending || investigations.isPending) return <section className="card loading-panel">正在读取调查记录…</section>
  if (task.isError || investigations.isError) return <section className="card error-panel">{errorMessage(task.error ?? investigations.error)}</section>
  const data = task.data
  return (
    <div className="page-stack investigation-page">
      <TaskWorkspaceHeader taskId={data.task_id} scenarioId={data.scenario_id} title={data.task_name} subtitle={`${investigations.data.length} 条只读调查记录`} status={data.status} revision={data.task_revision} resultId={data.current_result_id} quoteCount={data.quotes.length} summaryComplete={data.summary_completed} progress={data.progress} reviewBlocked={Boolean(data.current_issue)} policyReviewBlocked={data.current_issue?.issue_type === 'POLICY_EVIDENCE_REVIEW'} active="investigations" />
      <section className="review-workspace-lead"><div><p className="eyebrow">辅助调查</p><h2>核查记录</h2><p>这里保留系统为解决未知信息形成的只读核查；新核查请在“决策结果”中发起，预算和交期试算请使用决策助手。</p></div><span>{investigations.data.filter((item) => item.is_current).length} 条当前记录 · 共 {investigations.data.length} 条</span></section>
      {data.current_result_id && <Link className="button button-secondary" to={`/tasks/${taskId}/decision`}>返回决策结果</Link>}
      {investigations.data.length === 0 && <section className="card audit-empty">当前任务没有需要额外调查的信息，这是正常状态。</section>}
      <div className="investigation-list">
        {investigations.data.map((item) => (
          <article className="card investigation-card" key={item.case_id}>
            <header><div><strong>{item.kind === 'DECISION' ? '决策核查' : item.quote_id ? (data.quotes.find((quote) => quote.quote_id === item.quote_id)?.supplier_id ?? '报价调查') : '制度调查'}</strong><span>{item.kind === 'QUOTE' ? '报价信息核查' : item.kind === 'DECISION' ? '供应商比较核查' : '制度依据核查'}</span></div><span className={`status-pill ${item.is_current ? 'status-ready' : 'status-muted'}`}>{item.is_current ? '当前记录' : '历史记录'}</span></header>
            <p>{item.goal}</p>
            <dl className="detail-grid"><div><dt>对决策的影响</dt><dd>{impactStatusLabel(item.impact_status)}</dd></div><div><dt>调查结果</dt><dd>{item.stop_reason ? (stopReasonLabels[item.stop_reason] ?? item.stop_reason) : '检查已记录'}</dd></div><div><dt>模型决策</dt><dd>{item.model_calls > 0 ? `${item.model_calls} 次` : '未调用'}</dd></div></dl>
            {item.unknown_fields.length > 0 && <p><strong>待确认信息：</strong>{item.unknown_fields.map(fieldLabel).join('、')}</p>}
            {item.plan.length > 0 && <ol>{item.plan.map((step) => <li key={step}>{step}</li>)}</ol>}
            <div className="investigation-observations">
              {item.observations.map((observation) => <div key={observation.sequence}><strong>{observation.sequence}. {toolLabels[observation.result.tool_name] ?? observation.result.tool_name}</strong><span>{observation.result.status === 'OK' ? '完成' : observation.result.status}</span><p>{observationSummary(observation)}</p></div>)}
            </div>
            {item.clarification.length > 0 && <div className="run-notice">有 {item.clarification.length} 项信息需要人工确认，请前往“待处理事项”统一处理。</div>}
          </article>
        ))}
      </div>
    </div>
  )
}
