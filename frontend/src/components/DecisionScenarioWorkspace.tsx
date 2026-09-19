import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useEffect, useMemo, useState } from 'react'
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
  DecisionRankingMode,
  DecisionScenario,
  TaskDetail,
} from '../api/types'

const rankingLabels: Record<DecisionRankingMode, string> = {
  LOWEST_CONFIRMED_TOTAL_COST: '确认总成本最低',
  FASTEST_CONFIRMED_DELIVERY: '确认到货最快',
  LOWEST_COST_THEN_FASTEST_DELIVERY: '成本优先，其次交期',
  FASTEST_DELIVERY_THEN_LOWEST_COST: '交期优先，其次成本',
}

const changeLabels: Record<string, string> = {
  budget_amount: '预算',
  delivery_deadline: '最晚到货日',
  ranking_mode: '排序方式',
  excluded_supplier_ids: '排除供应商',
  cost_tolerance_amount: '成本容差',
}

function mutationError(error: unknown) {
  return error instanceof ApiClientError ? error.message : '操作失败，请稍后重试。'
}

function changeValue(key: string, value: unknown, currency: string) {
  if (value === null) return '清除此设置'
  if (Array.isArray(value)) return value.length > 0 ? value.join('、') : '清空排除列表'
  if (key === 'ranking_mode' && typeof value === 'string' && value in rankingLabels) {
    return rankingLabels[value as DecisionRankingMode]
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

function MessageBubble({
  message,
  currency,
  readOnly,
  confirmed,
  confirming,
  onConfirm,
}: {
  message: DecisionMessage
  currency: string
  readOnly: boolean
  confirmed: boolean
  confirming: boolean
  onConfirm: (intentId: string) => void
}) {
  return (
    <article className={`decision-chat-message chat-role-${message.role.toLowerCase()}`}>
      <header>
        <strong>{message.role === 'USER' ? '你' : 'AI 决策助手'}</strong>
        <span>{message.status}</span>
      </header>
      {message.content && <p>{message.content}</p>}
      {message.status === 'FAILED' && (
        <p className="chat-message-error">生成失败：{message.error_message ?? message.error_code ?? '未知错误'}</p>
      )}
      {message.reference_ids.length > 0 && (
        <small>引用：{message.reference_ids.join('、')}</small>
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
}: {
  task: TaskDetail
  result: ComparisonResultResponse
}) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [activeConversationId, setActiveConversationId] = useState('')
  const [message, setMessage] = useState('')
  const [streamingText, setStreamingText] = useState('')
  const [streamError, setStreamError] = useState('')
  const [queuedTurn, setQueuedTurn] = useState<{ conversationId: string; messageId: string } | null>(null)
  const [confirmedIntents, setConfirmedIntents] = useState<Set<string>>(() => new Set())
  const [rankingMode, setRankingMode] = useState('')
  const [budgetAmount, setBudgetAmount] = useState('')
  const [deliveryDeadline, setDeliveryDeadline] = useState('')
  const [tolerance, setTolerance] = useState('')
  const [clearTolerance, setClearTolerance] = useState(false)
  const [excludedSuppliers, setExcludedSuppliers] = useState('')
  const [clearExclusions, setClearExclusions] = useState(false)
  const [formError, setFormError] = useState('')

  const readOnly = !result.is_current || task.status === 'ABANDONED'
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

  const defaultConversation = conversations.data?.items.find((item) => item.status === 'ACTIVE')
    ?? conversations.data?.items[0]
  const selectedConversationId = activeConversationId || defaultConversation?.conversation_id || ''

  const activeConversation = useMemo(
    () => conversations.data?.items.find((item) => item.conversation_id === selectedConversationId),
    [selectedConversationId, conversations.data],
  )
  const lastMessage = activeConversation?.messages.at(-1)
  const pendingReplyTo = queuedTurn?.conversationId === selectedConversationId
    ? queuedTurn.messageId
    : lastMessage?.role === 'USER' ? lastMessage.message_id : null

  useEffect(() => {
    if (!selectedConversationId || !pendingReplyTo || readOnly) return
    let turnStarted = false
    const source = new EventSource(
      decisionConversationEventsUrl(task.task_id, selectedConversationId),
    )
    const parse = (event: Event) => JSON.parse((event as MessageEvent<string>).data) as Record<string, unknown>
    const started = (event: Event) => {
      const payload = parse(event)
      if (payload.reply_to_message_id === pendingReplyTo) {
        turnStarted = true
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
      setStreamingText('')
      setQueuedTurn(null)
      void queryClient.invalidateQueries({ queryKey: ['tasks', task.task_id, 'decision-conversations'] })
    }
    const failed = (event: Event) => {
      const payload = parse(event)
      const assistant = payload.message as { reply_to_message_id?: string; error_message?: string } | undefined
      if (assistant?.reply_to_message_id !== pendingReplyTo) return
      source.close()
      setStreamError(assistant.error_message ?? 'AI 回复生成失败。')
      setQueuedTurn(null)
      void queryClient.invalidateQueries({ queryKey: ['tasks', task.task_id, 'decision-conversations'] })
    }
    source.addEventListener('assistant.started', started)
    source.addEventListener('assistant.delta', delta)
    source.addEventListener('assistant.completed', completed)
    source.addEventListener('assistant.failed', failed)
    return () => source.close()
  }, [pendingReplyTo, queryClient, readOnly, selectedConversationId, task.task_id])

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
      void queryClient.invalidateQueries({ queryKey: ['tasks', task.task_id, 'decision-conversations'] })
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
      void queryClient.invalidateQueries({ queryKey: ['tasks', task.task_id, 'decision-conversations'] })
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
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['tasks', task.task_id] })
      navigate(`/tasks/${task.task_id}/decision`)
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
    if (rankingMode) changes.ranking_mode = rankingMode as DecisionRankingMode
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

  const activeTurn = Boolean(pendingReplyTo || sendMessage.isPending)
  const supplierIds = [...new Set(task.quotes.map((quote) => quote.supplier_id))]
  const persistedConfirmedIntents = new Set(
    intents.data?.items
      .filter((intent) => intent.status === 'CONFIRMED')
      .map((intent) => intent.decision_intent_id) ?? [],
  )
  const operationError = createConversation.error ?? sendMessage.error ?? confirmIntent.error
    ?? createScenario.error ?? applyScenario.error

  return (
    <section className="decision-assistant-workspace">
      <header className="decision-assistant-heading">
        <div>
          <p className="eyebrow">DECISION SCENARIO LAB</p>
          <h2>自然语言决策分析</h2>
          <p>AI 只负责解释和提取变更意图；金额、可行性、推荐与应用操作仍由后端确定性执行。</p>
        </div>
        <div className="decision-profile-summary">
          <span>Profile v{task.decision_profile.profile_version}</span>
          <strong>{task.decision_profile.preferences.ranking_mode
            ? rankingLabels[task.decision_profile.preferences.ranking_mode]
            : task.requirement.ranking_preference}</strong>
          <small>
            成本容差：{task.decision_profile.preferences.cost_tolerance_amount === null
              ? '未设置'
              : `${task.requirement.currency} ${task.decision_profile.preferences.cost_tolerance_amount}`}
            {' · '}排除：{task.decision_profile.preferences.excluded_supplier_ids.join('、') || '无'}
          </small>
        </div>
      </header>

      {readOnly && (
        <div className="run-notice">当前是历史结果或任务已废弃，对话、确认和应用操作已禁用。</div>
      )}
      {operationError && <div className="form-error" role="alert">{mutationError(operationError)}</div>}

      <div className="decision-assistant-grid">
        <article className="decision-chat-panel">
          <header className="decision-chat-toolbar">
            <div>
              <strong>对话</strong>
              <span>{activeConversation?.status ?? '尚未开始'}</span>
            </div>
            <div>
              {conversations.data && conversations.data.items.length > 0 && (
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
                  {conversations.data.items.map((item) => (
                    <option key={item.conversation_id} value={item.conversation_id}>
                      {item.title} · {item.status}
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

          <div className="decision-chat-transcript" aria-live="polite">
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
                currency={task.requirement.currency}
                readOnly={readOnly}
                confirmed={Boolean(item.decision_intent_id && (
                  confirmedIntents.has(item.decision_intent_id)
                  || persistedConfirmedIntents.has(item.decision_intent_id)
                ))}
                confirming={confirmIntent.isPending}
                onConfirm={(intentId) => confirmIntent.mutate(intentId)}
              />
            ))}
            {(streamingText || activeTurn) && (
              <article className="decision-chat-message chat-role-assistant chat-streaming">
                <header><strong>AI 决策助手</strong><span>生成中</span></header>
                <p>{streamingText || '正在读取冻结事实并生成经过校验的回复…'}</p>
              </article>
            )}
            {streamError && <div className="chat-message-error">{streamError}</div>}
          </div>

          <form className="decision-chat-composer" onSubmit={submitMessage}>
            <textarea
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              placeholder="用自然语言询问或描述你希望模拟的条件…"
              maxLength={4000}
              rows={3}
              disabled={readOnly || !activeConversation || activeConversation.status !== 'ACTIVE' || activeTurn}
            />
            <div>
              <small>{message.length} / 4000</small>
              <button
                className="button button-submit"
                type="submit"
                disabled={readOnly || !message.trim() || !activeConversation || activeTurn}
              >发送</button>
            </div>
          </form>
        </article>

        <aside className="scenario-workbench">
          <details className="scenario-builder">
            <summary>结构化创建 Scenario</summary>
            <form onSubmit={submitScenario}>
              <label><span>排序方式</span>
                <select value={rankingMode} onChange={(event) => setRankingMode(event.target.value)}>
                  <option value="">保持当前设置</option>
                  {Object.entries(rankingLabels).map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
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
            <div><strong>Scenario</strong><span>{scenarios.data?.items.length ?? 0} 个</span></div>
            <button className="text-button" type="button" onClick={() => void scenarios.refetch()}>刷新</button>
          </div>
          <div className="scenario-list">
            {scenarios.isPending && <p className="scenario-empty">正在读取 Scenario…</p>}
            {scenarios.isError && <p className="chat-message-error">{mutationError(scenarios.error)}</p>}
            {scenarios.data?.items.map((scenario) => (
              <ScenarioCard
                key={scenario.decision_scenario_id}
                scenario={scenario}
                currency={task.requirement.currency}
                readOnly={readOnly}
                applying={applyScenario.isPending}
                onApply={requestApply}
              />
            ))}
            {scenarios.data?.items.length === 0 && (
              <p className="scenario-empty">还没有 Scenario。通过对话提出变更，或使用上方结构化表单。</p>
            )}
          </div>
        </aside>
      </div>
    </section>
  )
}
