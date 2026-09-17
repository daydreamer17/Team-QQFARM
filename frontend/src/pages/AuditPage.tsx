import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api, ApiClientError } from '../api/client'
import type { IssueHistoryItem, ResultHistoryItem } from '../api/types'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'

function displayDate(value: string | null | undefined) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)
}

function displayAnswer(answer: Record<string, unknown> | null) {
  if (!answer) return '—'
  if (answer.answer_type === 'SHIPPING_AMOUNT') {
    return `${String(answer.currency ?? '')} ${String(answer.amount ?? '')}`.trim()
  }
  if (answer.answer_type === 'CONFIRM_MISSING') return '已确认缺失'
  if (answer.answer_type === 'RETRY_POLICY_RETRIEVAL') return '修复后重新检索'
  return JSON.stringify(answer)
}

function issueLabel(issue: IssueHistoryItem) {
  const labels: Record<string, string> = {
    CONFIRM_MISSING: '确认缺失字段',
    SHIPPING_AMOUNT: '补充运费金额',
    POLICY_EVIDENCE_REVIEW: '制度证据待复核',
  }
  return labels[issue.issue_type] ?? issue.issue_type
}

function retrievalSummary(result: ResultHistoryItem) {
  if (result.policy_retrievals.length === 0) return '未执行 / 无记录'
  const statuses = result.policy_retrievals.reduce<Record<string, number>>((counts, retrieval) => {
    counts[retrieval.status] = (counts[retrieval.status] ?? 0) + 1
    return counts
  }, {})
  return Object.entries(statuses).map(([status, count]) => `${status} ${count}`).join(' · ')
}

function errorMessage(error: unknown) {
  return error instanceof ApiClientError ? error.message : '审计数据读取失败。'
}

export function AuditPage() {
  const { taskId = '' } = useParams()
  const task = useQuery({
    queryKey: ['tasks', taskId],
    queryFn: () => api.getTask(taskId),
    enabled: Boolean(taskId),
  })
  const quotes = useQuery({
    queryKey: ['tasks', taskId, 'quotes'],
    queryFn: () => api.listQuotes(taskId),
    enabled: Boolean(taskId),
  })
  const issues = useQuery({
    queryKey: ['tasks', taskId, 'issues'],
    queryFn: () => api.listIssues(taskId),
    enabled: Boolean(taskId),
  })
  const results = useQuery({
    queryKey: ['tasks', taskId, 'results'],
    queryFn: () => api.listResults(taskId),
    enabled: Boolean(taskId),
  })

  if (task.isPending) return <section className="card loading-panel">正在读取任务审计信息…</section>
  if (task.isError) return <section className="card error-panel" role="alert">{errorMessage(task.error)}</section>

  const data = task.data
  const childError = quotes.error ?? issues.error ?? results.error

  return (
    <div className="page-stack audit-page">
      <TaskWorkspaceHeader
        taskId={data.task_id}
        scenarioId={data.scenario_id}
        title={data.requirement.manufacturer_part_number}
        subtitle={`${data.requirement.required_quantity} ${data.requirement.quantity_unit} · ${data.quotes.length} 份报价`}
        status={data.status}
        revision={data.task_revision}
        resultId={data.current_result_id}
        quoteCount={data.quotes.length}
        reviewBlocked={Boolean(data.current_issue)}
        policyReviewBlocked={data.current_issue?.issue_type === 'POLICY_EVIDENCE_REVIEW'}
        active="audit"
      />

      <section className="review-workspace-lead">
        <div>
          <p className="eyebrow">VERSION &amp; AUDIT</p>
          <h2>版本 / 审计</h2>
          <p>按后端保存顺序查看报价、人工问题和结果版本；不拼接不存在的统一事件时间线。</p>
        </div>
        <span className="status-pill status-ready">Task Rev {data.task_revision}</span>
      </section>

      <section className="audit-overview">
        <article><span>当前任务版本</span><strong>Revision {data.task_revision}</strong><small>{data.status}</small></article>
        <article><span>当前结果</span><strong>{data.current_result_id ?? '尚未生成'}</strong><small>{data.current_graph_run_id ?? '无 Graph Run'}</small></article>
        <article><span>Policy 绑定</span><strong>{data.policy_binding?.policy_set_version ?? '未绑定'}</strong><small>{data.policy_binding?.policy_index_version ?? '未执行制度检索'}</small></article>
      </section>

      {childError && <section className="card error-panel" role="alert">部分审计数据读取失败：{errorMessage(childError)}</section>}

      <section className="audit-section">
        <div className="section-heading">
          <div><p className="eyebrow">QUOTE VERSIONS</p><h2>报价与文档版本</h2></div>
          <span>{quotes.data?.items.length ?? 0} 个报价</span>
        </div>
        {quotes.isPending && <div className="card loading-panel">正在读取报价版本…</div>}
        {quotes.data?.items.length === 0 && <div className="card audit-empty">暂无报价版本。</div>}
        <div className="audit-list">
          {quotes.data?.items.map((quote) => (
            <article className="card audit-record" key={quote.quote_id}>
              <header>
                <div><strong>{quote.supplier_id}</strong><span>{quote.quote_id}</span></div>
                <span className={`status-pill ${quote.active ? 'status-ready' : 'status-muted'}`}>{quote.active ? `当前 v${quote.current_version}` : '非当前报价'}</span>
              </header>
              <div className="audit-version-list">
                {quote.versions.map((version) => (
                  <div className={version.is_current ? 'audit-version audit-version-current' : 'audit-version'} key={`${quote.quote_id}-${version.quote_version}`}>
                    <strong>Quote v{version.quote_version}</strong>
                    <span>{version.original_filename}</span>
                    <span>Document {version.document_id} · v{version.document_version}</span>
                    <span>{displayDate(version.created_at)}</span>
                    <small title={version.document_sha256}>{version.document_sha256.slice(0, 12)}… · {version.media_type}</small>
                    {version.is_current && <b>当前版本</b>}
                  </div>
                ))}
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="audit-section">
        <div className="section-heading">
          <div><p className="eyebrow">ISSUE HISTORY</p><h2>问题历史</h2></div>
          <span>{issues.data?.length ?? 0} 条记录</span>
        </div>
        {issues.isPending && <div className="card loading-panel">正在读取问题历史…</div>}
        {issues.data?.length === 0 && <div className="card audit-empty">暂无人工问题。</div>}
        <div className="audit-table-wrap">
          {(issues.data?.length ?? 0) > 0 && (
            <table className="audit-table">
              <thead><tr><th>问题</th><th>状态</th><th>Revision</th><th>回答</th><th>处理信息</th></tr></thead>
              <tbody>
                {issues.data?.map((issue) => (
                  <tr key={issue.issue_id}>
                    <td><strong>{issueLabel(issue)}</strong><span>{issue.question}</span><small>{issue.field_name ?? issue.issue_id}</small></td>
                    <td><span className={`status-pill ${issue.status === 'RESOLVED' ? 'status-ready' : 'status-pending'}`}>{issue.status}</span></td>
                    <td>创建 {issue.created_revision}<br />解决 {issue.resolved_revision ?? '—'}</td>
                    <td>{displayAnswer(issue.answer)}</td>
                    <td>{issue.answered_by ?? '—'}<br /><small>{displayDate(issue.answered_at)}</small></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>

      <section className="audit-section">
        <div className="section-heading">
          <div><p className="eyebrow">RESULT HISTORY</p><h2>结果历史</h2></div>
          <span>{results.data?.length ?? 0} 个结果</span>
        </div>
        {results.isPending && <div className="card loading-panel">正在读取结果历史…</div>}
        {results.data?.length === 0 && <div className="card audit-empty">暂无比较结果。</div>}
        <div className="audit-result-list">
          {results.data?.map((result) => (
            <article className={`card audit-result ${result.is_current ? 'audit-result-current' : ''}`} key={result.result_id}>
              <div>
                <span className="audit-result-revision">Task Rev {result.task_revision}</span>
                <strong>{result.result_id}</strong>
                <small>Graph Run {result.graph_run_id}</small>
              </div>
              <dl>
                <div><dt>规则版本</dt><dd>{result.result.rule_version}</dd></div>
                <div><dt>评估时间</dt><dd>{displayDate(result.result.evaluated_at)}</dd></div>
                <div><dt>Policy 检索</dt><dd>{retrievalSummary(result)}</dd></div>
                <div><dt>结果状态</dt><dd>{result.is_current ? '当前结果' : '历史结果'}</dd></div>
              </dl>
              <Link className="button button-secondary" to={`/tasks/${taskId}/results/${result.result_id}`}>
                {result.is_current ? '查看当前结果' : '查看历史结果'}
              </Link>
            </article>
          ))}
        </div>
      </section>
    </div>
  )
}
