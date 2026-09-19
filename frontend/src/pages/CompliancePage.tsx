import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api, ApiClientError } from '../api/client'
import type { PolicyCitation, PolicyRetrievalResult } from '../api/types'
import { IssuePanel } from '../components/IssuePanel'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'

const statusLabels: Record<string, string> = {
  OK: '找到制度证据',
  NO_EVIDENCE: '没有匹配证据',
  CONFLICT: '制度证据冲突',
  ERROR: '制度检索失败',
}

function errorMessage(error: unknown) {
  return error instanceof ApiClientError ? error.message : '制度证据读取失败。'
}

function statusClass(status: string) {
  if (status === 'OK') return 'policy-status-ok'
  if (status === 'NO_EVIDENCE') return 'policy-status-missing'
  return 'policy-status-error'
}

function controlCodes(retrieval: PolicyRetrievalResult) {
  const codes = [...retrieval.covered_control_codes, ...retrieval.missing_control_codes]
  return [...new Set(codes)]
}

function latencyTotal(retrieval: PolicyRetrievalResult) {
  return retrieval.latency_ms.total ?? Object.entries(retrieval.latency_ms)
    .filter(([stage]) => stage !== 'total')
    .reduce((total, [, value]) => total + value, 0)
}

function CitationCard({ citation }: { citation: PolicyCitation }) {
  return (
    <article className="policy-citation-card">
      <header>
        <div>
          <span>{citation.control_code}</span>
          <strong>{citation.section}</strong>
        </div>
        <span>排名 #{citation.rerank_rank}</span>
      </header>
      <blockquote>{citation.text}</blockquote>
      <dl>
        <div><dt>Policy</dt><dd>{citation.policy_id} · {citation.policy_set_version}</dd></div>
        <div><dt>文档</dt><dd>{citation.document_id} · {citation.document_version}</dd></div>
        <div><dt>条款</dt><dd>{citation.clause_id}</dd></div>
        <div><dt>内容哈希</dt><dd title={citation.content_sha256}>{citation.content_sha256.slice(0, 12)}…</dd></div>
      </dl>
      <details>
        <summary>查看高级检索分数</summary>
        <div className="policy-score-grid">
          <span>BM25：{citation.bm25_rank === null ? '—' : `#${citation.bm25_rank} / ${citation.bm25_score?.toFixed(4) ?? '—'}`}</span>
          <span>Vector：{citation.vector_rank === null ? '—' : `#${citation.vector_rank} / ${citation.vector_score?.toFixed(4) ?? '—'}`}</span>
          <span>Fusion：#{citation.fusion_rank} / {citation.fusion_score.toFixed(4)}</span>
          <span>Rerank：#{citation.rerank_rank} / {citation.rerank_score.toFixed(4)}</span>
        </div>
      </details>
    </article>
  )
}

function RetrievalCard({ retrieval, index }: { retrieval: PolicyRetrievalResult; index: number }) {
  const codes = controlCodes(retrieval)
  return (
    <article className={`card policy-retrieval-card ${statusClass(retrieval.status)}`}>
      <header>
        <div>
          <p className="eyebrow">CONTROL CHECK {String(index + 1).padStart(2, '0')}</p>
          <h3>{codes.join('、') || '未返回控制项'}</h3>
        </div>
        <span className={`policy-retrieval-status ${statusClass(retrieval.status)}`}>{statusLabels[retrieval.status] ?? retrieval.status}</span>
      </header>

      <div className="policy-retrieval-summary">
        <div><span>已覆盖</span><strong>{retrieval.covered_control_codes.join('、') || '—'}</strong></div>
        <div><span>缺失</span><strong>{retrieval.missing_control_codes.join('、') || '—'}</strong></div>
        <div><span>检索耗时</span><strong>{Math.round(latencyTotal(retrieval))} ms</strong></div>
        <div><span>错误代码</span><strong>{retrieval.error_code ?? '—'}</strong></div>
      </div>

      <div className="policy-model-line">
        <span>Embedding：{retrieval.embedding_model}</span>
        <span>Rerank：{retrieval.rerank_model}</span>
        <span>候选：{retrieval.candidates.length}</span>
      </div>

      {retrieval.citations.length > 0 ? (
        <div className="policy-citation-list">
          {retrieval.citations.map((citation) => <CitationCard citation={citation} key={citation.citation_id} />)}
        </div>
      ) : (
        <p className="policy-no-citation">此控制项没有可展示的制度引用。</p>
      )}
    </article>
  )
}

export function CompliancePage() {
  const { taskId = '' } = useParams()
  const task = useQuery({
    queryKey: ['tasks', taskId],
    queryFn: () => api.getTask(taskId),
    enabled: Boolean(taskId),
  })
  const results = useQuery({
    queryKey: ['tasks', taskId, 'results'],
    queryFn: () => api.listResults(taskId),
    enabled: Boolean(taskId),
  })

  if (task.isPending) return <section className="card loading-panel">正在读取任务制度绑定…</section>
  if (task.isError) return <section className="card error-panel" role="alert">{errorMessage(task.error)}</section>

  const data = task.data
  const latestResult = results.data?.[0]
  const retrievals = latestResult?.policy_retrievals ?? []
  const policyIssue = data.current_issue?.issue_type === 'POLICY_EVIDENCE_REVIEW'
  const okCount = retrievals.filter((retrieval) => retrieval.status === 'OK').length

  return (
    <div className="page-stack compliance-page">
      <TaskWorkspaceHeader
        taskId={data.task_id}
        scenarioId={data.scenario_id}
        title={data.requirement.manufacturer_part_number}
        subtitle={`${data.requirement.required_quantity} ${data.requirement.quantity_unit} · ${data.quotes.length} 份报价`}
        status={data.status}
        revision={data.task_revision}
        resultId={data.current_result_id}
        quoteCount={data.quotes.length}
        summaryComplete={data.summary_completed}
        progress={data.progress}
        reviewBlocked={Boolean(data.current_issue)}
        policyReviewBlocked={policyIssue}
        active="compliance"
      />

      <section className="review-workspace-lead">
        <div>
          <p className="eyebrow">POLICY EVIDENCE</p>
          <h2>合规 / 制度证据</h2>
          <p>展示后端检索到的制度条款及检索状态，不代替人工合规审批。</p>
        </div>
        <span className={`status-pill ${retrievals.length > 0 && okCount === retrievals.length ? 'status-ready' : 'status-pending'}`}>
          {retrievals.length > 0 ? `${okCount} / ${retrievals.length} 项有证据` : '尚无检索结果'}
        </span>
      </section>

      {!data.policy_binding ? (
        <section className="card compliance-empty">
          <span className="compliance-empty-mark">—</span>
          <div>
            <h2>未绑定 Policy，未执行制度检索</h2>
            <p>这不代表任务已经通过合规检查。策略目录接口完成后，新建任务才能选择已发布的 Policy。</p>
          </div>
        </section>
      ) : (
        <>
          <section className="policy-binding-card">
            <div><span>策略集版本</span><strong>{data.policy_binding.policy_set_version}</strong></div>
            <div><span>索引版本</span><strong>{data.policy_binding.policy_index_version}</strong></div>
            <div><span>分类</span><strong>{data.policy_binding.category}</strong></div>
            <div><span>地区</span><strong>{data.policy_binding.region}</strong></div>
          </section>

          {policyIssue && (
            <IssuePanel task={data} onRefresh={() => void Promise.all([task.refetch(), results.refetch()])} />
          )}

          {results.isPending && <section className="card loading-panel">正在读取结果历史中的制度检索…</section>}
          {results.isError && <section className="card error-panel" role="alert">{errorMessage(results.error)}</section>}
          {!results.isPending && !results.isError && !latestResult && (
            <section className="card compliance-empty compact">
              <div><h2>尚未生成比较结果</h2><p>任务完成分析后，这里会展示随结果冻结的制度检索记录。</p></div>
            </section>
          )}
          {latestResult && retrievals.length === 0 && (
            <section className="card compliance-empty compact">
              <div><h2>当前结果未包含制度检索</h2><p>该结果可能来自旧版本流程，或制度检查尚未执行。不能将其解释为合规通过。</p></div>
            </section>
          )}

          {latestResult && (
            <div className="policy-result-context">
              <span>展示最新结果：<Link to={`/tasks/${taskId}/results/${latestResult.result_id}`}>{latestResult.result_id}</Link></span>
              <span>Task Rev {latestResult.task_revision}</span>
              {!latestResult.is_current && <strong>尚未发布为当前结果</strong>}
            </div>
          )}

          <section className="policy-retrieval-list" aria-label="制度检索结果">
            {retrievals.map((retrieval, index) => (
              <RetrievalCard retrieval={retrieval} index={index} key={retrieval.retrieval_id} />
            ))}
          </section>
        </>
      )}

      <p className="result-boundary">“找到制度证据”只表示检索链路返回了可引用条款，不代表最终合规审批或采购授权。</p>
    </div>
  )
}
