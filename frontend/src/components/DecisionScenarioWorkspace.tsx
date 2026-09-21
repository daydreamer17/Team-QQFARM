import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  api,
  ApiClientError,
  createIdempotencyKey,
  decisionConversationEventsUrl,
} from '../api/client'
import type {
  ComparisonResultResponse,
  DecisionChanges,
  DecisionMessage,
  RankingCriterion,
  DecisionScenario,
  TaskDetail,
} from '../api/types'
import { RankingCriterionSelect } from './RankingCriterionSelect'
import { rankingCriterionLabel } from '../lib/rankingCriteria'

const changeLabels: Record<string, string> = {
  budget_amount: '预算',
  delivery_deadline: '最晚到货日',
  primary_criterion: '主排序指标',
  secondary_criterion: '次排序指标',
  excluded_supplier_ids: '排除供应商',
  cost_tolerance_amount: '成本容差',
}

function mutationError(error: unknown) {
  return error instanceof ApiClientError ? error.message : '操作失败，请稍后重试。'
}

function changeValue(key: string, value: unknown, currency: string) {
  if (value === null) return '清除此设置'
  if (Array.isArray(value)) return value.length > 0 ? value.join('、') : '清空排除列表'
  if ((key === 'primary_criterion' || key === 'secondary_criterion') && typeof value === 'string') {
    return rankingCriterionLabel(value)
  }
  if ((key === 'budget_amount' || key === 'cost_tolerance_amount') && value !== undefined) {
    return `${currency} ${String(value)}`
  }
  return String(value)
}

function Changes({ changes, currency }: { changes: DecisionChanges; currency: string }) {
  return (
    <dl className="scenario-change-list">
      {Object.entries(changes).map(([key, value]) => (
        <div key={key}>
          <dt>{changeLabels[key] ?? key}</dt>
          <dd>{changeValue(key, value, currency)}</dd>
        </div>
      ))}
    </dl>
  )
}

interface DisplayCitation {
  id: string
  number: number
}

function citationTitle(id: string) {
  if (id.startsWith('REQUIREMENT:')) return '已确认采购需求与偏好'
  if (id.startsWith('RESULT:')) return '当前决策结果'
  if (id.startsWith('QUOTE:')) return '供应商报价'
  if (id.startsWith('POLICY:')) return '制度证据'
  if (id.startsWith('COMPLIANCE:')) return '合规检查状态'
  if (id.startsWith('INVESTIGATION:')) return 'Agent 调查记录'
  return '来源证据'
}

function citationPresentation(content: string, referenceIds: string[]) {
  const ids = [...new Set(referenceIds)]
  const citations: DisplayCitation[] = ids.map((id, index) => ({ id, number: index + 1 }))
  let displayContent = content
  const replacements = citations.flatMap((citation) => {
    const shortId = citation.id.includes(':') ? citation.id.slice(citation.id.indexOf(':') + 1) : citation.id
    return [...new Set([citation.id, shortId])].map((token) => ({
      token,
      marker: `[${citation.number}]`,
    }))
  }).sort((left, right) => right.token.length - left.token.length)

  for (const replacement of replacements) {
    const escaped = replacement.token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    displayContent = displayContent
      .replace(new RegExp(`[（(]\\s*${escaped}\\s*[）)]`, 'g'), replacement.marker)
      .replace(new RegExp(escaped, 'g'), replacement.marker)
  }
  return { displayContent, citations }
}

function CitedText({ text }: { text: string }) {
  return text.split(/(\[\d+\])/g).map((part, index) => (
    /^\[\d+\]$/.test(part)
      ? <span className="chat-citation-marker" key={`${part}-${index}`}>{part}</span>
      : part
  ))
}

function MessageBubble({
  message,
  currency,
  readOnly,
  confirmed,
  confirming,
  onConfirm,
  onOpenCitation,
}: {
  message: DecisionMessage
  currency: string
  readOnly: boolean
  confirmed: boolean
  confirming: boolean
  onConfirm: (intentId: string) => void
  onOpenCitation: (referenceId: string) => void
}) {
  const citation = citationPresentation(message.content ?? '', message.reference_ids)
  return (
    <article className={`decision-chat-message chat-role-${message.role.toLowerCase()}`}>
      <header>
        <strong>{message.role === 'USER' ? '你' : 'AI 决策助手'}</strong>
        <span>{message.status}</span>
      </header>
      {message.content && <p><CitedText text={citation.displayContent} /></p>}
      {message.status === 'FAILED' && (
        <p className="chat-message-error">生成失败：{message.error_message ?? message.error_code ?? '未知错误'}</p>
      )}
      {citation.citations.length > 0 && (
        <footer className="chat-citations">
          <strong>引用</strong>
          <ol>
            {citation.citations.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className="chat-citation-link"
                  aria-label={`查看引用 [${item.number}] ${citationTitle(item.id)}`}
                  onClick={() => onOpenCitation(item.id)}
                >
                  <span>[{item.number}]</span>
                  <div><small>{citationTitle(item.id)}</small><code>{item.id}</code></div>
                </button>
              </li>
            ))}
          </ol>
        </footer>
      )}
      {message.proposed_changes && (
        <section className="chat-proposal">
          <strong>建议生成以下决策情景</strong>
          <Changes changes={message.proposed_changes} currency={currency} />
          {message.decision_intent_id && (
            <button
              className="button button-submit"
              type="button"
              disabled={readOnly || confirmed || confirming}
              onClick={() => onConfirm(message.decision_intent_id as string)}
            >
              {confirmed ? '已生成 Scenario' : confirming ? '确认中…' : '确认并生成 Scenario'}
            </button>
          )}
        </section>
      )}
    </article>
  )
}

function ScenarioCard({
  scenario,
  currency,
  readOnly,
  applying,
  onApply,
}: {
  scenario: DecisionScenario
  currency: string
  readOnly: boolean
  applying: boolean
  onApply: (scenario: DecisionScenario) => void
}) {
  const changed = scenario.delta.recommendation_changed
  return (
    <article className={`scenario-card scenario-status-${scenario.status.toLowerCase()}`}>
      <header>
        <div>
          <strong>{changed ? '推荐发生变化' : '推荐保持不变'}</strong>
          <small>{scenario.decision_scenario_id}</small>
        </div>
        <span className="status-pill">{scenario.status}</span>
      </header>
      <Changes changes={scenario.changes} currency={currency} />
      <div className="scenario-recommendation-delta">
        <span>Baseline：{scenario.delta.baseline_recommended_quote_ids.join('、') || '无推荐'}</span>
        <span>模拟后：{scenario.delta.simulated_recommended_quote_ids.join('、') || '无推荐'}</span>
      </div>
      {scenario.delta.supplier_deltas.some((row) => row.excluded || row.total_cost_delta) && (
        <details>
          <summary>查看供应商差异</summary>
          <ul>
            {scenario.delta.supplier_deltas.map((row) => (
              <li key={row.quote_id}>
                <strong>{row.quote_id}</strong>
                {row.excluded
                  ? ' · 已排除'
                  : ` · ${row.baseline_status ?? '—'} → ${row.simulated_status ?? '—'} · 成本变化 ${row.total_cost_delta ?? '0'}`}
              </li>
            ))}
          </ul>
        </details>
      )}
      {scenario.status === 'READY' && (
        <button
          className="button button-submit"
          type="button"
          disabled={readOnly || applying}
          onClick={() => onApply(scenario)}
        >
          {applying ? '应用中…' : '应用并全量重算'}
        </button>
      )}
    </article>
  )
}

export function DecisionScenarioWorkspace({
  task,
  result,
  compact = false,
  onOpenQuoteEvidence,
  onReanalyze,
  reanalyzing = false,
}: {
  task: TaskDetail
  result: ComparisonResultResponse
  compact?: boolean
  onOpenQuoteEvidence?: (quoteId: string) => void
  onReanalyze?: () => void
  reanalyzing?: boolean
}) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [activeConversationId, setActiveConversationId] = useState('')
  const [message, setMessage] = useState('')
  const [streamingText, setStreamingText] = useState('')
  const [streamError, setStreamError] = useState('')
  const [queuedTurn, setQueuedTurn] = useState<{ conversationId: string; messageId: string } | null>(null)
  const [confirmedIntents, setConfirmedIntents] = useState<Set<string>>(() => new Set())
  const [primaryCriterion, setPrimaryCriterion] = useState('')
  const [secondaryCriterion, setSecondaryCriterion] = useState('')
  const [budgetAmount, setBudgetAmount] = useState('')
  const [deliveryDeadline, setDeliveryDeadline] = useState('')
  const [tolerance, setTolerance] = useState('')
  const [clearTolerance, setClearTolerance] = useState(false)
  const [excludedSuppliers, setExcludedSuppliers] = useState('')
  const [clearExclusions, setClearExclusions] = useState(false)
  const [formError, setFormError] = useState('')
  const [selectedCitationId, setSelectedCitationId] = useState<string | null>(null)
  const transcriptRef = useRef<HTMLDivElement>(null)

  const readOnly = !result.is_current || task.status === 'ABANDONED'
  const viewRequirement = result.input_snapshot?.requirement ?? task.requirement
  const viewDecisionProfile = result.input_snapshot?.decision_profile ?? task.decision_profile
  const viewCurrency = viewRequirement?.currency ?? task.requirement.currency
  const conversations = useQuery({
    queryKey: ['tasks', task.task_id, 'decision-conversations'],
    queryFn: () => api.listDecisionConversations(task.task_id),
    refetchInterval: (query) => {
      const pending = query.state.data?.items.some((item) => {
        const last = item.messages.at(-1)
        return item.status === 'ACTIVE' && last?.role === 'USER'
      })
      return pending ? 1_500 : false
    },
  })
  const scenarios = useQuery({
    queryKey: ['tasks', task.task_id, 'decision-scenarios'],
    queryFn: () => api.listDecisionScenarios(task.task_id),
  })
  const intents = useQuery({
    queryKey: ['tasks', task.task_id, 'decision-intents'],
    queryFn: () => api.listDecisionIntents(task.task_id),
  })

  const allConversations = conversations.data?.items ?? []
  const resultConversations = allConversations.filter(
    (item) => item.base_result_id === result.result_id,
  )
  const resultScenarios = scenarios.data?.items.filter(
    (item) => item.base_result_id === result.result_id,
  ) ?? []
  const resultIntents = intents.data?.items.filter(
    (item) => item.base_result_id === result.result_id,
  ) ?? []
  const policyCitations = useMemo(
    () => new Map(result.policy_retrievals.flatMap((retrieval) => (
      retrieval.citations.map((citation) => [citation.citation_id, citation] as const)
    ))),
    [result.policy_retrievals],
  )
  const defaultConversation = resultConversations.find((item) => item.status === 'ACTIVE')
    ?? resultConversations[0]
    ?? allConversations[0]
  const selectedConversationId = activeConversationId || defaultConversation?.conversation_id || ''

  const activeConversation = allConversations.find(
    (item) => item.conversation_id === selectedConversationId,
  )
  const conversationReadOnly = readOnly
    || activeConversation?.status !== 'ACTIVE'
    || activeConversation.base_result_id !== result.result_id
  const activeMessageCount = activeConversation?.messages.length ?? 0
  const lastMessage = activeConversation?.messages.at(-1)
  const pendingReplyTo = queuedTurn?.conversationId === selectedConversationId
    ? queuedTurn.messageId
    : lastMessage?.role === 'USER' ? lastMessage.message_id : null

  useEffect(() => {
    const transcript = transcriptRef.current
    if (!transcript) return
    transcript.scrollTo({ top: transcript.scrollHeight, behavior: streamingText ? 'auto' : 'smooth' })
  }, [activeMessageCount, selectedConversationId, streamingText])

  useEffect(() => {
    if (!selectedConversationId || !pendingReplyTo || conversationReadOnly) return
    let turnStarted = false
    let waitWarning = window.setTimeout(() => {
      setStreamError('等待时间较长，请确认 Worker 正在运行；任务会保留在队列中。')
    }, 45_000)
    const source = new EventSource(
      decisionConversationEventsUrl(task.task_id, selectedConversationId),
    )
    const parse = (event: Event) => JSON.parse((event as MessageEvent<string>).data) as Record<string, unknown>
    const started = (event: Event) => {
      const payload = parse(event)
      if (payload.reply_to_message_id === pendingReplyTo) {
        turnStarted = true
        window.clearTimeout(waitWarning)
        waitWarning = window.setTimeout(() => {
          setStreamError('模型处理时间较长，系统仍在等待经过事实校验的完整回答。')
        }, 130_000)
        setStreamingText('')
        setStreamError('')
      }
    }
    const delta = (event: Event) => {
      if (!turnStarted) return
      const payload = parse(event)
      if (typeof payload.delta === 'string') setStreamingText((current) => current + payload.delta)
    }
    const completed = (event: Event) => {
      const payload = parse(event)
      const assistant = payload.message as { reply_to_message_id?: string } | undefined
      if (assistant?.reply_to_message_id !== pendingReplyTo) return
      source.close()
      window.clearTimeout(waitWarning)
      setStreamingText('')
      setQueuedTurn(null)
      void queryClient.invalidateQueries({
        queryKey: ['tasks', task.task_id, 'decision-conversations'],
      })
    }
    const failed = (event: Event) => {
      const payload = parse(event)
      const assistant = payload.message as { reply_to_message_id?: string; error_message?: string } | undefined
      if (assistant?.reply_to_message_id !== pendingReplyTo) return
      source.close()
      window.clearTimeout(waitWarning)
      setStreamError('')
      setQueuedTurn(null)
      void queryClient.invalidateQueries({
        queryKey: ['tasks', task.task_id, 'decision-conversations'],
      })
    }
    source.addEventListener('assistant.started', started)
    source.addEventListener('assistant.delta', delta)
    source.addEventListener('assistant.completed', completed)
    source.addEventListener('assistant.failed', failed)
    return () => {
      window.clearTimeout(waitWarning)
      source.close()
    }
  }, [conversationReadOnly, pendingReplyTo, queryClient, selectedConversationId, task.task_id])

  const createConversation = useMutation({
    mutationFn: () => api.createDecisionConversation(
      task.task_id,
      task.task_revision,
      `决策讨论 ${new Date().toLocaleString('zh-CN')}`,
      createIdempotencyKey(),
    ),
    onSuccess: (created) => {
      setActiveConversationId(created.conversation_id)
      setStreamingText('')
      setStreamError('')
      setQueuedTurn(null)
      void queryClient.invalidateQueries({
        queryKey: ['tasks', task.task_id, 'decision-conversations'],
      })
    },
  })
  const sendMessage = useMutation({
    mutationFn: ({ conversationId, content }: { conversationId: string; content: string }) =>
      api.sendDecisionMessage(
        task.task_id,
        conversationId,
        task.task_revision,
        content,
        createIdempotencyKey(),
      ),
    onSuccess: (response) => {
      setMessage('')
      setStreamingText('')
      setStreamError('')
      setQueuedTurn({
        conversationId: response.conversation_id,
        messageId: response.message.message_id,
      })
      void queryClient.invalidateQueries({
        queryKey: ['tasks', task.task_id, 'decision-conversations'],
      })
    },
  })
  const confirmIntent = useMutation({
    mutationFn: (intentId: string) => api.confirmDecisionIntent(
      task.task_id,
      intentId,
      task.task_revision,
      createIdempotencyKey(),
    ),
    onSuccess: (response) => {
      setConfirmedIntents((current) => new Set(current).add(response.decision_intent_id))
      void queryClient.invalidateQueries({ queryKey: ['tasks', task.task_id, 'decision-scenarios'] })
      void queryClient.invalidateQueries({ queryKey: ['tasks', task.task_id, 'decision-intents'] })
    },
  })
  const createScenario = useMutation({
    mutationFn: (changes: DecisionChanges) => api.createDecisionScenario(
      task.task_id,
      task.task_revision,
      changes,
      createIdempotencyKey(),
    ),
    onSuccess: () => {
      setFormError('')
      void queryClient.invalidateQueries({ queryKey: ['tasks', task.task_id, 'decision-scenarios'] })
    },
  })
  const applyScenario = useMutation({
    mutationFn: (scenarioId: string) => api.applyDecisionScenario(
      task.task_id,
      scenarioId,
      task.task_revision,
      createIdempotencyKey(),
    ),
    onSuccess: async (response) => {
      await queryClient.cancelQueries({ queryKey: ['tasks', task.task_id] })
      queryClient.setQueryData<TaskDetail>(['tasks', task.task_id], (current) => (
        current
          ? {
              ...current,
              task_revision: response.task_revision,
              status: response.status,
              current_result_id: null,
              current_snapshot_id: null,
            }
          : current
      ))
      navigate(`/tasks/${task.task_id}/decision`, {
        replace: true,
        state: { expectedRevision: response.task_revision },
      })
      void queryClient.invalidateQueries({ queryKey: ['tasks', task.task_id] })
    },
  })

  const submitMessage = (event: FormEvent) => {
    event.preventDefault()
    const content = message.trim()
    if (!content || !selectedConversationId) return
    sendMessage.mutate({ conversationId: selectedConversationId, content })
  }

  const submitScenario = (event: FormEvent) => {
    event.preventDefault()
    const changes: DecisionChanges = {}
    if (primaryCriterion) changes.primary_criterion = primaryCriterion as RankingCriterion
    if (secondaryCriterion) changes.secondary_criterion = secondaryCriterion as RankingCriterion
    if (budgetAmount.trim()) changes.budget_amount = budgetAmount.trim()
    if (deliveryDeadline) changes.delivery_deadline = deliveryDeadline
    if (clearTolerance) changes.cost_tolerance_amount = null
    else if (tolerance.trim()) changes.cost_tolerance_amount = tolerance.trim()
    if (clearExclusions) changes.excluded_supplier_ids = []
    else if (excludedSuppliers.trim()) {
      changes.excluded_supplier_ids = [...new Set(
        excludedSuppliers.split(',').map((item) => item.trim()).filter(Boolean),
      )]
    }
    if (Object.keys(changes).length === 0) {
      setFormError('请至少填写一项模拟条件。')
      return
    }
    setFormError('')
    createScenario.mutate(changes)
  }

  const requestApply = (scenario: DecisionScenario) => {
    if (!window.confirm('应用后会推进 Task Revision、失效当前结果并触发全量重算，是否继续？')) return
    applyScenario.mutate(scenario.decision_scenario_id)
  }

  const openCitation = (referenceId: string) => {
    if (
      activeConversation
      && activeConversation.base_result_id !== result.result_id
    ) {
      navigate(
        `/tasks/${task.task_id}/results/${activeConversation.base_result_id}`,
        { state: { conversationId: activeConversation.conversation_id } },
      )
      return
    }
    if (referenceId.startsWith('QUOTE:') && onOpenQuoteEvidence) {
      onOpenQuoteEvidence(referenceId.slice('QUOTE:'.length))
      return
    }
    setSelectedCitationId(referenceId)
  }

  const activeTurn = Boolean(pendingReplyTo || sendMessage.isPending)
  const supplierIds = [...new Set(task.quotes.map((quote) => quote.supplier_id))]
  const persistedConfirmedIntents = new Set(
    resultIntents
      .filter((intent) => intent.status === 'CONFIRMED')
      .map((intent) => intent.decision_intent_id) ?? [],
  )
  const operationError = createConversation.error ?? sendMessage.error ?? confirmIntent.error
    ?? createScenario.error ?? applyScenario.error
  const requiresReanalysis = operationError instanceof ApiClientError
    && operationError.code === 'scenario_baseline_stale'

  return (
    <section className={`decision-assistant-workspace${compact ? ' decision-assistant-compact' : ''}`}>
      {compact ? (
        <header className="decision-compact-chat-heading">
          <span className="decision-chat-spark" aria-hidden="true">✦</span>
          <div><h2>Ask QuoteWise</h2><p>基于当前冻结结果回答</p></div>
          <span className="decision-chat-state">{activeConversation?.status ?? 'READY'}</span>
        </header>
      ) : (
        <header className="decision-assistant-heading">
          <div>
            <p className="eyebrow">DECISION SCENARIO LAB</p>
            <h2>自然语言决策分析</h2>
            <p>AI 只负责解释和提取变更意图；金额、可行性、推荐与应用操作仍由后端确定性执行。</p>
          </div>
          <div className="decision-profile-summary">
            <span>Profile v{viewDecisionProfile?.profile_version ?? '—'}</span>
            <strong>{rankingCriterionLabel(
              viewDecisionProfile?.preferences.primary_criterion ?? viewRequirement?.ranking_preference,
            )}</strong>
            <small>次指标：{rankingCriterionLabel(
              viewDecisionProfile?.preferences.secondary_criterion ?? viewRequirement?.secondary_preference,
            )}</small>
            <small>
              成本容差：{viewDecisionProfile?.preferences.cost_tolerance_amount == null
                ? '未设置'
                : `${viewCurrency} ${viewDecisionProfile.preferences.cost_tolerance_amount}`}
              {' · '}排除：{viewDecisionProfile?.preferences.excluded_supplier_ids.join('、') || '无'}
            </small>
          </div>
        </header>
      )}

      {readOnly && (
        <div className="run-notice">当前是历史结果或任务已废弃，对话、确认和应用操作已禁用。</div>
      )}
      {operationError && (
        <div className="form-error" role="alert">
          <span>{mutationError(operationError)}</span>
          {requiresReanalysis && onReanalyze && (
            <button
              className="button button-secondary"
              type="button"
              disabled={reanalyzing}
              onClick={onReanalyze}
            >
              {reanalyzing ? '正在启动…' : '按当前代码重新分析'}
            </button>
          )}
        </div>
      )}

      {compact && (
        <div className="decision-compact-chat-context">
          <span>{task.scenario_id}</span>
          <span>{result.result.supplier_results.length} 家供应商</span>
          <span>Revision {result.task_revision}</span>
        </div>
      )}

      <div className="decision-assistant-grid">
        <article className="decision-chat-panel">
          <header className="decision-chat-toolbar">
            <div>
              <strong>对话</strong>
              <span>{activeConversation?.status ?? '尚未开始'}</span>
            </div>
            <div>
              {allConversations.length > 0 && (
                <select
                  aria-label="选择历史对话"
                  value={selectedConversationId}
                  onChange={(event) => {
                    setActiveConversationId(event.target.value)
                    setStreamingText('')
                    setStreamError('')
                    setQueuedTurn(null)
                  }}
                >
                  {allConversations.map((item) => (
                    <option key={item.conversation_id} value={item.conversation_id}>
                      Rev {item.base_task_revision} · {item.title} · {item.status}
                    </option>
                  ))}
                </select>
              )}
              <button
                className="button button-secondary"
                type="button"
                disabled={readOnly || createConversation.isPending}
                onClick={() => createConversation.mutate()}
              >
                {createConversation.isPending ? '创建中…' : '新对话'}
              </button>
            </div>
          </header>

          <div className="decision-chat-transcript" aria-live="polite" ref={transcriptRef}>
            {activeConversation && activeConversation.base_result_id !== result.result_id && (
              <div className="run-notice">
                这是 Revision {activeConversation.base_task_revision} 的历史对话，仅作背景；新回复会基于当前冻结结果重新核验。
              </div>
            )}
            {!activeConversation && (
              <div className="decision-chat-empty">
                <strong>和当前冻结结果对话</strong>
                <p>可以询问推荐原因，也可以说“排除 SUP-024、允许成本高 300 新币并优先交期”。</p>
                <button
                  className="button button-submit"
                  type="button"
                  disabled={readOnly || createConversation.isPending}
                  onClick={() => createConversation.mutate()}
                >开始对话</button>
              </div>
            )}
            {activeConversation?.messages.map((item) => (
              <MessageBubble
                key={item.message_id}
                message={item}
                currency={viewCurrency}
                readOnly={conversationReadOnly}
                confirmed={Boolean(item.decision_intent_id && (
                  confirmedIntents.has(item.decision_intent_id)
                  || persistedConfirmedIntents.has(item.decision_intent_id)
                ))}
                confirming={confirmIntent.isPending}
                onConfirm={(intentId) => confirmIntent.mutate(intentId)}
                onOpenCitation={openCitation}
              />
            ))}
            {(streamingText || activeTurn) && (
              <article className="decision-chat-message chat-role-assistant chat-streaming">
                <header><strong>AI 决策助手</strong><span>生成并校验中</span></header>
                <p>{streamingText || '正在读取冻结事实；完整回答通过事实与引用校验后才会显示。'}</p>
              </article>
            )}
            {streamError && <div className="chat-message-error">{streamError}</div>}
          </div>

          {compact && activeConversation && (
            <details className="decision-chat-prompt-menu">
              <summary><span>示例问题</span><small>3 个</small></summary>
              <div className="decision-chat-prompts" aria-label="快捷问题">
                <button type="button" onClick={() => setMessage('为什么推荐当前供应商？')}>为什么这样推荐？</button>
                <button type="button" onClick={() => setMessage('如果优先交期，推荐会变化吗？')}>如果优先交期？</button>
                <button type="button" onClick={() => setMessage('请解释当前关键风险及来源。')}>查看关键风险</button>
              </div>
            </details>
          )}

          <form className="decision-chat-composer" onSubmit={submitMessage}>
            <textarea
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              placeholder="用自然语言询问或描述你希望模拟的条件…"
              maxLength={4000}
              rows={compact ? 2 : 3}
              disabled={conversationReadOnly || !activeConversation || activeTurn}
            />
            <div>
              <small>{message.length} / 4000</small>
              <button
                className="button button-submit"
                type="submit"
                disabled={conversationReadOnly || !message.trim() || !activeConversation || activeTurn}
              >发送</button>
            </div>
          </form>
        </article>

        <details className={`decision-scenario-manager${compact ? ' decision-scenario-manager-compact' : ''}`} open={compact ? undefined : true}>
          <summary>Scenario 管理 · {resultScenarios.length} 个</summary>
          <aside className="scenario-workbench">
          <details className="scenario-builder">
            <summary>结构化创建 Scenario</summary>
            <form onSubmit={submitScenario}>
              <label><span>主排序指标</span>
                <RankingCriterionSelect historyApplicable={task.supplier_history_binding?.binding_status === 'AVAILABLE'} value={primaryCriterion} exclude={secondaryCriterion} onChange={(next) => {
                  setPrimaryCriterion(next)
                  if (next === secondaryCriterion) setSecondaryCriterion('')
                }} />
              </label>
              <label><span>次排序指标 <small>仅在主指标并列时使用</small></span>
                <RankingCriterionSelect allowEmpty historyApplicable={task.supplier_history_binding?.binding_status === 'AVAILABLE'} value={secondaryCriterion} exclude={primaryCriterion} onChange={setSecondaryCriterion} />
              </label>
              <div className="scenario-form-pair">
                <label><span>预算（{task.requirement.currency}）</span>
                  <input type="number" min="0" step="0.01" value={budgetAmount} onChange={(event) => setBudgetAmount(event.target.value)} />
                </label>
                <label><span>最晚到货日</span>
                  <input type="date" value={deliveryDeadline} onChange={(event) => setDeliveryDeadline(event.target.value)} />
                </label>
              </div>
              <label><span>成本容差（{task.requirement.currency}）</span>
                <input type="number" min="0" step="0.01" value={tolerance} disabled={clearTolerance} onChange={(event) => setTolerance(event.target.value)} />
              </label>
              <label className="scenario-inline-check"><input type="checkbox" checked={clearTolerance} onChange={(event) => setClearTolerance(event.target.checked)} />清除成本容差</label>
              <label><span>排除供应商 ID（逗号分隔）</span>
                <input value={excludedSuppliers} disabled={clearExclusions} onChange={(event) => setExcludedSuppliers(event.target.value)} placeholder={supplierIds.join(', ')} />
              </label>
              <label className="scenario-inline-check"><input type="checkbox" checked={clearExclusions} onChange={(event) => setClearExclusions(event.target.checked)} />清空排除列表</label>
              {formError && <div className="form-error">{formError}</div>}
              <button className="button button-secondary" type="submit" disabled={readOnly || createScenario.isPending}>
                {createScenario.isPending ? '计算中…' : '生成 baseline / delta'}
              </button>
            </form>
          </details>

          <div className="scenario-list-heading">
            <div><strong>Scenario</strong><span>{resultScenarios.length} 个</span></div>
            <button className="text-button" type="button" onClick={() => void scenarios.refetch()}>刷新</button>
          </div>
          <div className="scenario-list">
            {scenarios.isPending && <p className="scenario-empty">正在读取 Scenario…</p>}
            {scenarios.isError && <p className="chat-message-error">{mutationError(scenarios.error)}</p>}
            {resultScenarios.map((scenario) => (
              <ScenarioCard
                key={scenario.decision_scenario_id}
                scenario={scenario}
                currency={task.requirement.currency}
                readOnly={readOnly}
                applying={applyScenario.isPending}
                onApply={requestApply}
              />
            ))}
            {resultScenarios.length === 0 && (
              <p className="scenario-empty">还没有 Scenario。通过对话提出变更，或使用上方结构化表单。</p>
            )}
          </div>
          </aside>
        </details>
      </div>

      {selectedCitationId && (() => {
        const policyCitation = selectedCitationId.startsWith('POLICY:')
          ? policyCitations.get(selectedCitationId.slice('POLICY:'.length))
          : undefined
        const dialogLabel = policyCitation ? '制度引用详情' : '引用详情'
        return (
          <div className="evidence-drawer-layer" role="presentation">
            <button
              className="evidence-drawer-backdrop"
              type="button"
              aria-label="关闭引用详情"
              onClick={() => setSelectedCitationId(null)}
            />
            <aside className="evidence-drawer chat-reference-drawer" role="dialog" aria-modal="true" aria-label={dialogLabel}>
              <header>
                <div><p className="eyebrow">可核查引用</p><h2>{citationTitle(selectedCitationId)}</h2></div>
                <button className="drawer-close" type="button" onClick={() => setSelectedCitationId(null)} aria-label="关闭引用详情">×</button>
              </header>
              {policyCitation ? (
                <section className="drawer-fields">
                  <div className="drawer-section-title"><h3>{policyCitation.section}</h3></div>
                  <dl>
                    <div><dt>控制项</dt><dd>{policyCitation.control_code}</dd></div>
                    <div><dt>制度版本</dt><dd>{policyCitation.policy_set_version}</dd></div>
                    <div><dt>条款 ID</dt><dd>{policyCitation.clause_id}</dd></div>
                  </dl>
                  <blockquote><p>{policyCitation.text}</p></blockquote>
                  <small>内容哈希：{policyCitation.content_sha256}</small>
                </section>
              ) : selectedCitationId.startsWith('RESULT:') ? (
                <section className="drawer-fields">
                  <h3>冻结结果</h3>
                  <p>Result ID：{result.result_id}</p>
                  <p>Task Revision：{result.task_revision}</p>
                  <p>生成时间：{result.result.evaluated_at}</p>
                </section>
              ) : selectedCitationId.startsWith('REQUIREMENT:') ? (
                <section className="drawer-fields">
                  <h3>本结果冻结的采购要求</h3>
                  <p>任务版本：{result.task_revision}</p>
                  <p>预算：{viewCurrency} {viewRequirement.budget_amount}</p>
                  <p>最晚到货日：{viewRequirement.delivery_deadline}</p>
                  <p>主排序：{rankingCriterionLabel(viewDecisionProfile?.preferences.primary_criterion ?? viewRequirement.ranking_preference)}</p>
                </section>
              ) : selectedCitationId.startsWith('COMPLIANCE:') ? (
                <section className="drawer-fields">
                  <h3>合规检查状态</h3>
                  <p>{result.policy_compliance.disposition}</p>
                  <p>{result.policy_compliance.recommendation_scope === 'COMPLIANCE_VERIFIED'
                    ? '供应商合规状态已核验。'
                    : '当前结果仅用于采购比较，仍需人工核验供应商合规。'}</p>
                </section>
              ) : (
                <section className="drawer-fields">
                  <h3>{citationTitle(selectedCitationId)}</h3>
                  <p>{selectedCitationId}</p>
                  <p>可在任务的调查详情或版本记录中核查该来源。</p>
                </section>
              )}
            </aside>
          </div>
        )
      })()}
    </section>
  )
}
