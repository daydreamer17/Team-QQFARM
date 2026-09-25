import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { ComplianceAssessmentDetails } from './ComplianceAssessmentDetails'
import { EnglishDateInput } from './EnglishDateInput'
import { OverlayPortal } from './OverlayPortal'
import {
  api,
  ApiClientError,
  createIdempotencyKey,
  decisionConversationEventsUrl,
} from '../api/client'
import type {
  ComparisonResultResponse,
  DecisionChanges,
  DecisionConversation,
  DecisionMessage,
  InvestigationCase,
  RankingCriterion,
  DecisionScenario,
  TaskDetail,
} from '../api/types'
import { RankingCriterionSelect } from './RankingCriterionSelect'
import { rankingCriterionLabel } from '../lib/rankingCriteria'

const changeLabels: Record<string, string> = {
  budget_amount: 'Budget',
  delivery_deadline: 'Latest delivery date',
  primary_criterion: 'Primary Ranking Criterion',
  secondary_criterion: 'Secondary Ranking Criterion',
  excluded_supplier_ids: 'Excluded Suppliers',
  cost_tolerance_amount: 'Cost Tolerance',
}

const investigationToolLabels: Record<string, string> = {
  read_decision_overview: 'Review current recommendation',
  compare_alternatives: 'Compare alternatives',
  inspect_quote_evidence: 'Review quotation evidence',
  inspect_supplier_history: 'Review supplier history',
  inspect_policy_evidence: 'Review policy evidence',
  compile_decision_brief: 'Compile investigation findings',
}

function conversationStatusLabel(status?: string) {
  if (!status) return 'Not Started'
  if (status === 'ACTIVE') return 'Current version'
  if (status === 'STALE') return 'Historical version'
  if (status === 'CLOSED') return 'Closed'
  return status
}

function conversationTitle(title: string) {
  return /^\u51b3\u7b56\u8ba8\u8bba\s+\d{4}[/-]/.test(title.trim()) ? 'Decision discussion' : title
}

function conversationOptionLabel(item: DecisionConversation, currentResultId: string) {
  const version = item.status === 'ACTIVE' && item.base_result_id === currentResultId
    ? 'Current version'
    : `Revision ${item.base_task_revision}`
  const history = item.status === 'STALE' ? ' · Historical' : item.status === 'CLOSED' ? ' · Closed' : ''
  return `${version} · ${conversationTitle(item.title)}${history}`
}

function investigationObservationText(observation: InvestigationCase['observations'][number]) {
  const data = observation.result.data
  if (observation.result.status === 'NOT_FOUND') return 'No usable evidence was found. This does not support the opposite conclusion.'
  if (observation.result.status !== 'OK') return 'This step did not produce a usable result.'
  if (observation.result.tool_name === 'read_decision_overview') {
    return `Reviewed ${Array.isArray(data.suppliers) ? data.suppliers.length : 0} suppliers and the current ranking basis.`
  }
  if (observation.result.tool_name === 'compare_alternatives') {
    return `Compared cost, delivery, and blocking differences for ${Array.isArray(data.gaps) ? data.gaps.length : 0} suppliers.`
  }
  if (observation.result.tool_name === 'inspect_quote_evidence') {
    const focus = { COST: 'cost', DELIVERY: 'delivery', TERMS: 'commercial terms', ALL: 'key' }[String(data.focus)] ?? 'key'
    const supplier = String(data.supplier_name || data.quote_id || 'this supplier')
    return `Reviewed ${Array.isArray(data.fields) ? data.fields.length : 0} ${focus} quotation evidence items for ${supplier}.`
  }
  if (observation.result.tool_name === 'inspect_supplier_history') {
    return `Reviewed historical performance and data availability for ${String(data.supplier_name || data.quote_id || 'this supplier')}.`
  }
  if (observation.result.tool_name === 'inspect_policy_evidence') return 'Reviewed the policy retrieval and compliance status frozen with this result.'
  if (observation.result.tool_name === 'compile_decision_brief') {
    const pending = Array.isArray(data.unresolved_items) ? data.unresolved_items.length : 0
    const risks = Array.isArray(data.verified_risks) ? data.verified_risks.length : 0
    if (pending > 0) return `Compiled the findings, retaining ${risks} verified risks and ${pending} follow-up items.`
    return risks > 0 ? `Compiled the findings and retained ${risks} verified risks; there are no current follow-up items.` : 'Compiled the facts and conclusions from this investigation.'
  }
  return 'Compiled the facts, limitations, and follow-up items from this investigation.'
}

function mutationError(error: unknown) {
  return error instanceof ApiClientError ? error.message : 'Operation failed. Try again later.'
}

function changeValue(key: string, value: unknown, currency: string) {
  if (value === null) return 'Clear this setting'
  if (Array.isArray(value)) return value.length > 0 ? value.join(', ') : 'Clear exclusion list'
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
  if (id.startsWith('SIMULATION:')) return 'Deterministic simulation for these conditions (not applied)'
  if (id.startsWith('REQUIREMENT:')) return 'Confirmed procurement requirements and preferences'
  if (id.startsWith('RESULT:')) return 'CurrentDecision results'
  if (id.startsWith('QUOTE:')) return 'Supplier quotation'
  if (id.startsWith('POLICY:')) return 'Policy evidence'
  if (id.startsWith('COMPLIANCE:')) return 'Compliance review status'
  if (id.startsWith('INVESTIGATION:')) return 'Agent Investigation Log'
  return 'SourceEvidence'
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
  return text.split(/(\[\d+\]|\*\*[^*]+\*\*)/g).map((part, index) => (
    /^\[\d+\]$/.test(part)
      ? <span className="chat-citation-marker" key={`${part}-${index}`}>{part}</span>
      : part.startsWith('**') && part.endsWith('**')
        ? <strong key={`emphasis-${index}`}>{part.slice(2, -2)}</strong>
      : part
  ))
}

function MessageBubble({
  taskId,
  message,
  currency,
  readOnly,
  confirmed,
  confirming,
  onConfirm,
  onOpenCitation,
  investigation,
  onRetry,
  retrying,
}: {
  taskId: string
  message: DecisionMessage
  currency: string
  readOnly: boolean
  confirmed: boolean
  confirming: boolean
  onConfirm: (intentId: string) => void
  onOpenCitation: (referenceId: string) => void
  investigation?: InvestigationCase
  onRetry?: () => void
  retrying: boolean
}) {
  const [copied, setCopied] = useState(false)
  const citation = citationPresentation(message.content ?? '', message.reference_ids)
  const statusLabel = message.role === 'USER'
    ? null
    : message.status === 'STALE'
      ? 'Historical response'
    : message.status === 'FAILED'
      ? 'Failed'
      : message.status === 'PENDING'
        ? 'Generating'
        : null
  return (
    <article className={`decision-chat-message chat-role-${message.role.toLowerCase()}`}>
      {statusLabel && <header className="chat-message-status"><span>{statusLabel}</span></header>}
      {message.content && <p><CitedText text={citation.displayContent} /></p>}
      {message.status === 'STALE' && <p className="run-notice">The underlying evidence has changed. This response is retained for history; please ask again using the current result.</p>}
      {message.status === 'FAILED' && (
        <p className="chat-message-error">Generation failed: {message.error_message ?? message.error_code ?? 'Unknown error'}</p>
      )}
      {citation.citations.length > 0 && (
        <details className="chat-citations">
          <summary>View {citation.citations.length} sources</summary>
          <ol>
            {citation.citations.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className="chat-citation-link"
                  aria-label={`View citation [${item.number}] ${citationTitle(item.id)}`}
                  onClick={() => onOpenCitation(item.id)}
                >
                  <span>[{item.number}]</span>
                  <small>{citationTitle(item.id)}</small>
                </button>
              </li>
            ))}
          </ol>
        </details>
      )}
      {investigation && (
        <details className="decision-investigation-trace">
          <summary>View investigation steps and tool results</summary>
          <p>Status: {investigation.status === 'RESOLVED' ? 'Completed' : 'Incomplete; findings are for reference only'}</p>
          <ol>{investigation.observations.map((observation) => (
            <li key={observation.sequence}>
              <strong>{investigationToolLabels[observation.result.tool_name] ?? observation.result.tool_name}</strong>
              <span>{investigationObservationText(observation)}</span>
              {observation.plan && observation.plan.length > 0 && <small>Public plan: {observation.plan.join(' → ')}</small>}
              {observation.reason && <small>Selection reason: {observation.reason}</small>}
            </li>
          ))}</ol>
        </details>
      )}
      {message.role === 'ASSISTANT' && (message.content || message.status === 'FAILED') && (
        <footer className="chat-message-actions">
          {message.content && (
            <button className="text-button" type="button" onClick={() => {
              if (!navigator.clipboard) return
              void navigator.clipboard.writeText(message.content as string)
                .then(() => setCopied(true))
                .catch(() => undefined)
            }}>{copied ? 'Copied' : 'Copy answer'}</button>
          )}
          {message.status === 'FAILED' && message.error_code === 'selection_review_required' ? (
            <Link className="button button-secondary" to={`/tasks/${taskId}/review#excluded-review`}>Go to action items</Link>
          ) : message.status === 'FAILED' && onRetry ? (
            <button className="button button-secondary" type="button" disabled={readOnly || retrying} onClick={onRetry}>
              {retrying ? 'Regenerating…' : 'Regenerate'}
            </button>
          ) : null}
        </footer>
      )}
      {message.proposed_changes && (
        <section className="chat-proposal">
          <strong>Suggested decision scenario</strong>
          <Changes changes={message.proposed_changes} currency={currency} />
          {message.decision_intent_id && (
            <button
              className="button button-submit"
              type="button"
              disabled={readOnly || message.status === 'STALE' || confirmed || confirming}
              onClick={() => onConfirm(message.decision_intent_id as string)}
            >
              {confirmed ? 'Scenario generated' : confirming ? 'Confirming…' : 'Confirm and generate scenario'}
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
          <strong>{changed ? 'Recommendation changed' : 'Recommendation unchanged'}</strong>
          <small>{scenario.decision_scenario_id}</small>
        </div>
        <span className="status-pill">{scenario.status}</span>
      </header>
      <Changes changes={scenario.changes} currency={currency} />
      <div className="scenario-recommendation-delta">
        <span>Baseline: {scenario.delta.baseline_recommended_quote_ids.join(', ') || 'No recommendation'}</span>
        <span>After simulation: {scenario.delta.simulated_recommended_quote_ids.join(', ') || 'No recommendation'}</span>
      </div>
      {scenario.delta.supplier_deltas.some((row) => row.excluded || row.total_cost_delta) && (
        <details>
          <summary>View supplier differences</summary>
          <ul>
            {scenario.delta.supplier_deltas.map((row) => (
              <li key={row.quote_id}>
                <strong>{row.quote_id}</strong>
                {row.excluded
                  ? ' · Excluded'
                  : ` · ${row.baseline_status ?? '—'} → ${row.simulated_status ?? '—'} · Cost change ${row.total_cost_delta ?? '0'}`}
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
          {applying ? 'Applying…' : 'Apply and rerun full analysis'}
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
  const location = useLocation()
  const queryClient = useQueryClient()
  const [activeConversationId, setActiveConversationId] = useState(() => (
    typeof location.state?.conversationId === 'string' ? location.state.conversationId : ''
  ))
  const [message, setMessage] = useState('')
  const [streamingText, setStreamingText] = useState('')
  const [streamError, setStreamError] = useState('')
  const [processingStage, setProcessingStage] = useState('Interpreting your question and preferences')
  const [toolProgress, setToolProgress] = useState<string[]>([])
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
  const [scenarioManagerOpen, setScenarioManagerOpen] = useState(!compact)
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
  const investigations = useQuery({
    queryKey: ['tasks', task.task_id, 'investigations'],
    queryFn: () => api.listInvestigations(task.task_id),
    enabled: Boolean(selectedCitationId?.startsWith('INVESTIGATION:') ||
      (conversations.data?.items ?? []).some((conversation) => conversation.messages.some((entry) =>
        entry.reference_ids.some((id) => id.startsWith('INVESTIGATION:'))))),
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
  const selectedConversationId = allConversations.some((item) => item.conversation_id === activeConversationId)
    ? activeConversationId : defaultConversation?.conversation_id || ''

  const activeConversation = allConversations.find(
    (item) => item.conversation_id === selectedConversationId,
  )
  const conversationReadOnly = readOnly
    || activeConversation?.status !== 'ACTIVE'
    || activeConversation.base_result_id !== result.result_id
  const activeMessageCount = activeConversation?.messages.length ?? 0
  const lastMessage = activeConversation?.messages.at(-1)
  const pendingReplyTo = queuedTurn && queuedTurn.conversationId === selectedConversationId
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
      setStreamError('This is taking longer than expected. Confirm that the worker is running; the task will remain queued.')
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
          setStreamError('Model processing is taking longer than expected. The system is still waiting for a complete, fact-checked answer.')
        }, 130_000)
        setStreamingText('')
        setStreamError('')
        setToolProgress([])
      }
    }
    const delta = (event: Event) => {
      if (!turnStarted) return
      const payload = parse(event)
      if (typeof payload.delta === 'string') setStreamingText((current) => current + payload.delta)
    }
    const stageChanged = (event: Event) => {
      const payload = parse(event)
      if (payload.reply_to_message_id !== pendingReplyTo) return
      const labels: Record<string, string> = {
        intent: 'Interpreting your question and preferences',
        investigation: 'Reviewing quotation, historical, or policy evidence',
        simulation: 'Running a deterministic simulation with the new conditions; the official result will not change',
        narration: 'Generating a factual explanation and validating citations',
        persist: 'Saving this response',
      }
      setProcessingStage(labels[String(payload.stage)] ?? 'Processing this request')
    }
    const toolObserved = (event: Event) => {
      const payload = parse(event)
      if (payload.reply_to_message_id !== pendingReplyTo || typeof payload.tool_name !== 'string') return
      const label = investigationToolLabels[payload.tool_name] ?? payload.tool_name
      const status = payload.status === 'OK' ? 'Completed' : payload.status === 'NOT_FOUND' ? 'No evidence found' : 'No result obtained'
      const reason = typeof payload.reason === 'string' && payload.reason ? `; ${payload.reason}` : ''
      setToolProgress((current) => [...current, `${label}: ${status}${reason}`])
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
      void queryClient.invalidateQueries({ queryKey: ['tasks', task.task_id, 'investigations'] })
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
    source.addEventListener('assistant.stage', stageChanged)
    source.addEventListener('assistant.tool', toolObserved)
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
      `Decision Discussion ${new Date().toLocaleString('en-SG')}`,
      createIdempotencyKey(),
    ),
    onSuccess: (created) => {
      setActiveConversationId(created.conversation_id)
      setStreamingText('')
      setStreamError('')
      setToolProgress([])
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
      setToolProgress([])
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
      setScenarioManagerOpen(true)
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
      setScenarioManagerOpen(true)
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
      setFormError('Enter at least one simulation condition.')
      return
    }
    setFormError('')
    createScenario.mutate(changes)
  }

  const requestApply = (scenario: DecisionScenario) => {
    if (!window.confirm('Applying this scenario will advance the task revision, invalidate the current result, and trigger a full recalculation. Continue?')) return
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
          <div className="decision-compact-chat-copy">
            <h2>Ask QuoteWise</h2>
            <p>Hello! I’m QuoteWise. I can explain the recommendation, review quotation and policy evidence, and simulate changes to the budget or delivery deadline.</p>
          </div>
          {activeConversation?.status && activeConversation.status !== 'ACTIVE' && (
            <span className="decision-chat-state">{conversationStatusLabel(activeConversation.status)}</span>
          )}
        </header>
      ) : (
        <header className="decision-assistant-heading">
          <div>
            <p className="eyebrow">DECISION SCENARIO LAB</p>
            <h2>Natural-language Decision Analysis</h2>
            <p>AI explains results and extracts change requests. Costs, feasibility, recommendations and apply operations remain deterministic backend functions.</p>
          </div>
          <div className="decision-profile-summary">
            <span>Profile v{viewDecisionProfile?.profile_version ?? '—'}</span>
            <strong>{rankingCriterionLabel(
              viewDecisionProfile?.preferences.primary_criterion ?? viewRequirement?.ranking_preference,
            )}</strong>
            <small>Secondary criterion: {rankingCriterionLabel(
              viewDecisionProfile?.preferences.secondary_criterion ?? viewRequirement?.secondary_preference,
            )}</small>
            <small>
              Cost tolerance: {viewDecisionProfile?.preferences.cost_tolerance_amount == null
                ? 'Not set'
                : `${viewCurrency} ${viewDecisionProfile.preferences.cost_tolerance_amount}`}
              {' · '}Excluded: {viewDecisionProfile?.preferences.excluded_supplier_ids.join(', ') || 'None'}
            </small>
          </div>
        </header>
      )}

      {readOnly && (
        <div className="run-notice">This is a historical result or an abandoned task. Conversation, confirmation and apply actions are disabled.</div>
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
              {reanalyzing ? 'Starting…' : 'Reanalyse with current code'}
            </button>
          )}
        </div>
      )}

      <div className="decision-assistant-grid">
        <article className="decision-chat-panel">
          <header className="decision-chat-toolbar">
            <div>
              <strong>Conversation</strong>
              <span>{conversationStatusLabel(activeConversation?.status)}</span>
            </div>
            <div>
              {allConversations.length > 0 && (
                <select
                  aria-label="Select a previous conversation"
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
                      {conversationOptionLabel(item, task.current_result_id ?? result.result_id)}
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
                {createConversation.isPending ? 'Creating…' : 'New conversation'}
              </button>
            </div>
          </header>

          <div className="decision-chat-transcript" aria-live="polite" ref={transcriptRef}>
            {!activeConversation && (
              <div className="decision-chat-empty">
                <strong>Discuss the current frozen result</strong>
                <p>Ask why a supplier is recommended, or say “Exclude SUP-024, allow a cost premium of SGD 300, and prioritise delivery.”</p>
                <button
                  className="button button-submit"
                  type="button"
                  disabled={readOnly || createConversation.isPending}
                  onClick={() => createConversation.mutate()}
                >Start a conversation</button>
              </div>
            )}
            {activeConversation?.messages.map((item) => (
              <MessageBubble
                taskId={task.task_id}
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
                investigation={investigations.data?.find((record) => item.reference_ids.includes(`INVESTIGATION:${record.artifact_id}`))}
                onRetry={item.status === 'FAILED' && item.reply_to_message_id
                  ? (() => {
                      const original = activeConversation.messages.find((entry) => entry.message_id === item.reply_to_message_id)
                      if (original?.content) sendMessage.mutate({ conversationId: activeConversation.conversation_id, content: original.content })
                    })
                  : undefined}
                retrying={sendMessage.isPending}
              />
            ))}
            {(streamingText || activeTurn) && (
              <article className="decision-chat-message chat-role-assistant chat-streaming">
                <header className="chat-message-status"><span>Generating and validating</span></header>
                <p>{streamingText || processingStage}</p>
                {toolProgress.length > 0 && <ol className="decision-chat-tool-progress">
                  {toolProgress.map((step, index) => <li key={`${index}-${step}`}>{step}</li>)}
                </ol>}
              </article>
            )}
            {streamError && <div className="chat-message-error">{streamError}</div>}
            {activeConversation && activeConversation.base_result_id !== result.result_id && (
              <div className="run-notice">
                This conversation belongs to revision {activeConversation.base_task_revision} and is shown only for context. New responses will be verified against the current result.
              </div>
            )}
          </div>

          {compact && activeConversation && (
            <details className="decision-chat-prompt-menu" open={activeMessageCount === 0 ? true : undefined}>
              <summary><span>Suggested Questions</span></summary>
              <div className="decision-chat-prompts" aria-label="Suggested questions">
                <button type="button" onClick={() => setMessage('Why is the current supplier recommended?')}>Why is this supplier recommended?</button>
                <button type="button" onClick={() => setMessage('Would the recommendation change if delivery were prioritised?')}>What if delivery is prioritised?</button>
                <button type="button" onClick={() => setMessage('Explain the current key risks and their sources.')}>View key risks</button>
              </div>
            </details>
          )}

          <form className="decision-chat-composer" onSubmit={submitMessage}>
            <textarea
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              placeholder="Ask about the recommendation, investigate risks, or simulate a change in conditions…"
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
              >Send</button>
            </div>
          </form>
        </article>

        <details
          className={`decision-scenario-manager${compact ? ' decision-scenario-manager-compact' : ''}`}
          open={scenarioManagerOpen}
          onToggle={(event) => setScenarioManagerOpen(event.currentTarget.open)}
        >
          <summary>Scenario management · {resultScenarios.length}</summary>
          <aside className="scenario-workbench">
          <details className="scenario-builder">
            <summary>Create scenario manually</summary>
            <form onSubmit={submitScenario}>
              <label><span>Primary Ranking Criterion</span>
                <RankingCriterionSelect historyApplicable={task.supplier_history_binding?.binding_status === 'AVAILABLE'} value={primaryCriterion} exclude={secondaryCriterion} onChange={(next) => {
                  setPrimaryCriterion(next)
                  if (next === secondaryCriterion) setSecondaryCriterion('')
                }} />
              </label>
              <label><span>Secondary Ranking Criterion <small>Used only when the primary criterion is tied</small></span>
                <RankingCriterionSelect allowEmpty historyApplicable={task.supplier_history_binding?.binding_status === 'AVAILABLE'} value={secondaryCriterion} exclude={primaryCriterion} onChange={setSecondaryCriterion} />
              </label>
              <div className="scenario-form-pair">
                <label><span>Budget ({task.requirement.currency})</span>
                  <input type="number" min="0" step="0.01" value={budgetAmount} onChange={(event) => setBudgetAmount(event.target.value)} />
                </label>
                <label><span>Latest delivery date</span>
                  <EnglishDateInput value={deliveryDeadline} onChange={setDeliveryDeadline} />
                </label>
              </div>
              <label><span>Cost tolerance ({task.requirement.currency})</span>
                <input type="number" min="0" step="0.01" value={tolerance} disabled={clearTolerance} onChange={(event) => setTolerance(event.target.value)} />
              </label>
              <label className="scenario-inline-check"><input type="checkbox" checked={clearTolerance} onChange={(event) => setClearTolerance(event.target.checked)} />Clear cost tolerance</label>
              <label><span>Excluded supplier IDs (comma-separated)</span>
                <input value={excludedSuppliers} disabled={clearExclusions} onChange={(event) => setExcludedSuppliers(event.target.value)} placeholder={supplierIds.join(', ')} />
              </label>
              <label className="scenario-inline-check"><input type="checkbox" checked={clearExclusions} onChange={(event) => setClearExclusions(event.target.checked)} />Clear exclusion list</label>
              {formError && <div className="form-error">{formError}</div>}
              <button className="button button-secondary" type="submit" disabled={readOnly || createScenario.isPending}>
                {createScenario.isPending ? 'Calculating…' : 'Generate baseline / delta'}
              </button>
            </form>
          </details>

          <div className="scenario-list-heading">
            <div><strong>Scenarios</strong><span>{resultScenarios.length}</span></div>
            <button className="text-button" type="button" onClick={() => void scenarios.refetch()}>Refresh</button>
          </div>
          <div className="scenario-list">
            {scenarios.isPending && <p className="scenario-empty">Loading scenarios…</p>}
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
              <p className="scenario-empty">No scenarios yet. Propose a change in the conversation or use the manual form above.</p>
            )}
          </div>
          </aside>
        </details>
      </div>

      {selectedCitationId && (() => {
        const policyCitation = selectedCitationId.startsWith('POLICY:')
          ? policyCitations.get(selectedCitationId.slice('POLICY:'.length))
          : undefined
        const dialogLabel = policyCitation ? 'Policy citation details' : 'Citation details'
        return (
          <OverlayPortal>
            <div className="evidence-drawer-layer" role="presentation">
            <button
              className="evidence-drawer-backdrop"
              type="button"
              aria-label="Close citation details"
              onClick={() => setSelectedCitationId(null)}
            />
            <aside className="evidence-drawer chat-reference-drawer" role="dialog" aria-modal="true" aria-label={dialogLabel}>
              <header>
                <div><p className="eyebrow">Verifiable citations</p><h2>{citationTitle(selectedCitationId)}</h2></div>
                <button className="drawer-close" type="button" onClick={() => setSelectedCitationId(null)} aria-label="Close citation details">×</button>
              </header>
              {policyCitation ? (
                <section className="drawer-fields">
                  <div className="drawer-section-title"><h3>{policyCitation.section}</h3></div>
                  <dl>
                    <div><dt>Control</dt><dd>{policyCitation.control_code}</dd></div>
                    <div><dt>Policy Version</dt><dd>{policyCitation.policy_set_version}</dd></div>
                    <div><dt>Clause ID</dt><dd>{policyCitation.clause_id}</dd></div>
                  </dl>
                  <blockquote><p>{policyCitation.text}</p></blockquote>
                  <small>Content hash: {policyCitation.content_sha256}</small>
                </section>
              ) : selectedCitationId.startsWith('INVESTIGATION:') ? (
                <section className="drawer-fields">
                  {(() => {
                    const record = investigations.data?.find((item) => `INVESTIGATION:${item.artifact_id}` === selectedCitationId)
                    if (!record) return <p>Loading this investigation record, or the record is no longer associated with the task currently being viewed.</p>
                    return <>
                      <h3>Investigation record</h3>
                      <p>{record.status === 'RESOLVED' ? 'Investigation completed; this does not constitute procurement approval.' : 'Investigation incomplete. Do not treat missing evidence as a pass.'}</p>
                      <ol>{record.observations.map((observation) => <li key={observation.sequence}>
                        <strong>{investigationToolLabels[observation.result.tool_name] ?? observation.result.tool_name}</strong>
                        <span>{investigationObservationText(observation)}</span>
                        {observation.plan && observation.plan.length > 0 && <small>Public plan: {observation.plan.join(' → ')}</small>}
                        {observation.reason && <small>Selection reason: {observation.reason}</small>}
                        <details><summary>View structured tool results</summary><pre>{JSON.stringify(observation.result.data, null, 2)}</pre></details>
                      </li>)}</ol>
                    </>
                  })()}
                </section>
              ) : selectedCitationId.startsWith('RESULT:') ? (
                <section className="drawer-fields">
                  <h3>Frozen Result</h3>
                  <p>Result ID: {result.result_id}</p>
                  <p>Task revision: {result.task_revision}</p>
                  <p>Generated at: {result.result.evaluated_at}</p>
                </section>
              ) : selectedCitationId.startsWith('REQUIREMENT:') ? (
                <section className="drawer-fields">
                  <h3>Procurement requirements frozen with this result</h3>
                  <p>Task revision: {result.task_revision}</p>
                  <p>Budget: {viewCurrency} {viewRequirement.budget_amount}</p>
                  <p>Latest arrival date: {viewRequirement.delivery_deadline}</p>
                  <p>Primary ranking criterion: {rankingCriterionLabel(viewDecisionProfile?.preferences.primary_criterion ?? viewRequirement.ranking_preference)}</p>
                </section>
              ) : selectedCitationId.startsWith('COMPLIANCE:') ? (
                <section className="drawer-fields">
                  <h3>Compliance review status</h3>
                  <ComplianceAssessmentDetails assessment={result.policy_compliance} taskId={task.task_id} resultId={result.result_id} historical={!result.is_current} legacy={result.legacy_compliance} />
                  <p>{result.policy_compliance.disposition}</p>
                  <p>{result.policy_compliance.recommendation_scope === 'COMPLIANCE_VERIFIED'
                    ? 'Supplier compliance status verified.'
                    : 'The current result is for procurement comparison only; supplier compliance still requires manual verification.'}</p>
                </section>
              ) : (
                <section className="drawer-fields">
                  <h3>{citationTitle(selectedCitationId)}</h3>
                  <p>{selectedCitationId}</p>
                  <p>Verify this source in the task investigation details or revision history.</p>
                </section>
              )}
            </aside>
            </div>
          </OverlayPortal>
        )
      })()}
    </section>
  )
}
