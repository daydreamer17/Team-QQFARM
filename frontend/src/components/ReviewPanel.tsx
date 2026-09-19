import { useMutation, useQueries, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useMemo, useState } from 'react'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import type {
  FieldCorrectionInput,
  QuoteField,
  QuoteFieldsResponse,
  ReviewFinding,
  TaskDetail,
  TaskQuote,
} from '../api/types'

interface ReviewPanelProps {
  task: TaskDetail
  onRefresh: () => void
}

interface EditorState {
  quote: TaskQuote
  finding: ReviewFinding
  field: QuoteField
  rawValue: string
  normalizedValue: string
  unit: string
  reason: string
  knownAmountAvailable: boolean
}

const feeStatusFields = new Set([
  'shipping_fee_status',
  'other_fees_status',
])

const feeAmountFields: Record<string, string> = {
  shipping_fee_status: 'shipping_fee_amount',
  other_fees_status: 'other_fees_amount',
}

const resolvingFeeStatuses = [
  ['FREE', '免费'],
  ['INCLUDED', '已包含在报价中'],
  ['NOT_APPLICABLE', '明确不适用／无此费用'],
  ['KNOWN_AMOUNT', '已有单独费用金额'],
] as const

const fieldLabels: Record<string, string> = {
  unit_price: '单价',
  moq_quantity: '最低订购量（MOQ）',
  shipping_fee_status: '运费状态',
  shipping_fee_amount: '运费金额',
  other_fees_status: '其他费用状态',
  other_fees_amount: '其他费用金额',
  tax_mode: '税费方式',
  manufacturer_part_number: '制造商料号',
  manufacturer_revision: '物料版本',
  package: '封装',
  condition: '物料状态',
  currency: '币种',
  packaging_type: '包装类型',
  units_per_pack: '每包装数量',
  order_multiple_units: '订购倍数',
  lead_time_days: '交期天数',
  day_basis: '交期日历口径',
  delivery_semantics: '交付语义',
  start_event: '交期起算事件',
  start_date: '交期起算日期',
  delivery_date: '明确交付日期',
  payment_terms: '付款条件',
  quote_date: '报价日期',
  valid_until: '报价有效期',
}

const findingCodeLabels: Record<string, string> = {
  OCR_CRITICAL_CONFIDENCE_LOW: '文字识别可信度不足',
  OCR_CRITICAL_CONFIDENCE_UNAVAILABLE: '文字识别可信度未知',
  OCR_CRITICAL_CONFUSABLE_TOKEN: '料号字符可能混淆',
  CRITICAL_FIELD_MISSING: '缺少必填信息',
  CRITICAL_FIELD_CONFLICT: '报价内容相互矛盾',
  FEE_STATUS_UNKNOWN: '费用状态尚未确认',
  MONEY_VALUE_INVALID: '金额格式不正确',
  NORMALIZED_ENUM_INVALID: '填写值不在允许范围内',
  NORMALIZED_TYPE_INVALID: '填写格式不正确',
  SOURCE_SEMANTIC_MISMATCH: '引用的原文与字段含义不一致',
  NORMALIZED_PRICE_NOT_IN_EVIDENCE: '填写的价格与原文证据不一致',
  CORRECTION_EVENT_INVALID: '修正记录与当前报价版本不一致',
  CORRECTION_AUDIT_MISSING: '缺少人工修正记录',
}

const findingActionMessages: Record<string, string> = {
  OCR_CRITICAL_CONFIDENCE_LOW: '请对照原 PDF 确认实际值。',
  OCR_CRITICAL_CONFIDENCE_UNAVAILABLE: '请对照原 PDF 确认实际值。',
  OCR_CRITICAL_CONFUSABLE_TOKEN: '请逐字核对原 PDF 中的制造商料号。',
  CRITICAL_FIELD_MISSING: '请根据原报价或供应商确认补充实际值。',
  CRITICAL_FIELD_CONFLICT: '请核对原报价并填写最终确认值。',
  FEE_STATUS_UNKNOWN: '请确认该费用是免费、已包含、不适用，还是另有金额。',
  MONEY_VALUE_INVALID: '请填写有效的十进制金额。',
  NORMALIZED_ENUM_INVALID: '请从系统允许的选项中选择。',
  NORMALIZED_TYPE_INVALID: '请按字段要求重新填写。',
  SOURCE_SEMANTIC_MISMATCH: '请核对引用原文与填写字段是否一致。',
  NORMALIZED_PRICE_NOT_IN_EVIDENCE: '请按原报价填写价格，不要换算或猜测。',
  CORRECTION_EVENT_INVALID: '请刷新任务后重新提交修正。',
  CORRECTION_AUDIT_MISSING: '请通过字段修正表单提交，不要直接改数据。',
}

function findingCodeLabel(code: string) {
  return findingCodeLabels[code] ?? code
}

function findingDisplayMessage(finding: ReviewFinding) {
  const firstKnownCode = finding.codes.find((code) => findingActionMessages[code])
  return firstKnownCode ? findingActionMessages[firstKnownCode] : finding.message
}

function blockingFindingSummary(
  details: Record<string, unknown>,
  quotes: TaskQuote[],
) {
  const groups = details.blocking_findings
  if (!groups || typeof groups !== 'object' || Array.isArray(groups)) return ''
  const filenames = new Map(
    quotes.map((quote) => [quote.quote_id, quote.original_filename]),
  )

  return Object.entries(groups)
    .flatMap(([quoteId, group]) =>
      (Array.isArray(group) ? group : []).map((item) => ({ quoteId, item })),
    )
    .map(({ quoteId, item }) => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) return null
      const finding = item as Record<string, unknown>
      const fieldName =
        typeof finding.field_name === 'string' ? finding.field_name : '未知字段'
      const codes = Array.isArray(finding.codes)
        ? finding.codes.filter((code): code is string => typeof code === 'string')
        : []
      const filename = filenames.get(quoteId) ?? quoteId
      const fieldLabel = fieldLabels[fieldName] ?? fieldName
      const reason = codes.length > 0
        ? codes.map(findingCodeLabel).join('、')
        : '仍未通过审核'
      return `${filename} 的“${fieldLabel}”：${reason}`
    })
    .filter((item): item is string => item !== null)
    .join('；')
}

function errorMessage(error: unknown, quotes: TaskQuote[] = []) {
  if (
    error instanceof ApiClientError &&
    error.code === 'field_correction_batch_invalid'
  ) {
    const summary = blockingFindingSummary(error.details, quotes)
    return `提交失败：${summary || '字段版本或修正内容已经变化'}。请刷新后重新核对。`
  }
  return error instanceof ApiClientError ? error.message : '字段修正失败。'
}

function editableValue(value: unknown) {
  if (value === null || value === undefined) return ''
  return String(value)
}

function displayFieldValue(value: unknown) {
  if (value === null || value === undefined || value === '') return '未提供'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function preserveValueType(value: string, original: unknown) {
  if (typeof original === 'boolean') return value.trim().toLowerCase() === 'true'
  if (typeof original === 'number') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : value
  }
  return value
}

function actionableFindings(response: QuoteFieldsResponse) {
  return response.review_findings.filter(
    (finding) =>
      !finding.resolved &&
      (finding.decision === 'REVIEW_REQUIRED' ||
        finding.decision === 'REJECTED' ||
        finding.decision === 'WARNING'),
  )
}

function groupFindingsByField(findings: ReviewFinding[]) {
  const groups = new Map<string, ReviewFinding[]>()
  findings.forEach((finding) => {
    groups.set(finding.field_name, [...(groups.get(finding.field_name) ?? []), finding])
  })
  return Array.from(groups.values())
}

function isDeferredShippingFinding(
  finding: ReviewFinding,
  response: QuoteFieldsResponse,
) {
  if (finding.field_name !== 'shipping_fee_status') return false
  const field = response.fields.find((item) => item.field_name === finding.field_name)
  return field?.normalized_value === null ||
    field?.normalized_value === undefined ||
    field?.normalized_value === 'UNKNOWN'
}

export function ReviewPanel({ task, onRefresh }: ReviewPanelProps) {
  const queryClient = useQueryClient()
  const quotes = useMemo(() => task.quotes ?? [], [task.quotes])
  const [editor, setEditor] = useState<EditorState | null>(null)
  const [drafts, setDrafts] = useState<Record<string, EditorState>>({})
  const fieldQueries = useQueries({
    queries: quotes.map((quote) => ({
      queryKey: ['tasks', task.task_id, 'quotes', quote.quote_id, 'fields'],
      queryFn: () => api.getQuoteFields(task.task_id, quote.quote_id),
    })),
  })

  const reviewRows = useMemo(
    () =>
      quotes.map((quote, index) => ({
        quote,
        query: fieldQueries[index],
        findings: fieldQueries[index]?.data
          ? actionableFindings(fieldQueries[index].data)
          : [],
      })),
    [fieldQueries, quotes],
  )

  const correction = useMutation({
    mutationFn: (states: EditorState[]) =>
      api.correctQuoteFields(
        task.task_id,
        task.task_revision,
        states.map<FieldCorrectionInput>((state) => ({
          quoteId: state.quote.quote_id,
          fieldName: state.field.field_name,
          expectedFieldVersion: state.field.field_version,
          rawValue: state.rawValue.trim(),
          normalizedValue: preserveValueType(
            state.normalizedValue.trim(),
            state.field.normalized_value,
          ),
          unit: state.unit.trim() || null,
          reason: state.reason.trim(),
        })),
        createIdempotencyKey(),
      ),
    onSuccess: async () => {
      setEditor(null)
      setDrafts({})
      await queryClient.invalidateQueries({ queryKey: ['tasks', task.task_id] })
      onRefresh()
    },
  })

  const blockingKeys = useMemo(
    () =>
      Array.from(
        new Set(
          reviewRows.flatMap(({ quote, query, findings }) =>
            findings
              .filter(
                (finding) =>
                  finding.severity === 'BLOCKING' &&
                  !(query.data && isDeferredShippingFinding(finding, query.data)),
              )
              .map((finding) => quote.quote_id + ':' + finding.field_name),
          ),
        ),
      ),
    [reviewRows],
  )
  const stagedBlockingCount = blockingKeys.filter((key) => drafts[key]).length
  const allBlockingStaged =
    blockingKeys.length === 0 || stagedBlockingCount === blockingKeys.length

  function openEditor(
    quote: TaskQuote,
    finding: ReviewFinding,
    response: QuoteFieldsResponse,
  ) {
    const field = response.fields.find((item) => item.field_name === finding.field_name)
    if (!field) return
    const key = quote.quote_id + ':' + field.field_name
    const unresolvedUnknownFee =
      feeStatusFields.has(field.field_name) && field.normalized_value === 'UNKNOWN'
    const amountFieldName = feeAmountFields[field.field_name]
    const amountField = amountFieldName
      ? response.fields.find((item) => item.field_name === amountFieldName)
      : undefined
    setEditor(drafts[key] ?? {
      quote,
      finding,
      field,
      rawValue: unresolvedUnknownFee ? '' : editableValue(field.raw_value),
      normalizedValue: unresolvedUnknownFee
        ? ''
        : editableValue(field.normalized_value),
      unit: field.unit ?? '',
      reason: '人工核对原始报价后修正',
      knownAmountAvailable:
        amountField?.normalized_value !== null &&
        amountField?.normalized_value !== undefined,
    })
    correction.reset()
  }

  function submitCorrection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!editor) return
    const key = editor.quote.quote_id + ':' + editor.field.field_name
    setDrafts((current) => ({ ...current, [key]: editor }))
    setEditor(null)
  }

  function confirmCurrentValue(
    quote: TaskQuote,
    finding: ReviewFinding,
    response: QuoteFieldsResponse,
  ) {
    const field = response.fields.find((item) => item.field_name === finding.field_name)
    if (
      !field || field.raw_value === null || field.raw_value === undefined ||
      field.normalized_value === null || field.normalized_value === undefined
    ) return
    const key = quote.quote_id + ':' + field.field_name
    const amountName = feeAmountFields[field.field_name]
    const amount = amountName
      ? response.fields.find((item) => item.field_name === amountName)
      : undefined
    setDrafts((current) => ({
      ...current,
      [key]: {
        quote,
        finding,
        field,
        rawValue: editableValue(field.raw_value),
        normalizedValue: editableValue(field.normalized_value),
        unit: field.unit ?? '',
        reason: '人工核对原报价，确认系统提取值正确',
        knownAmountAvailable:
          amount?.normalized_value !== null && amount?.normalized_value !== undefined,
      },
    }))
    setEditor(null)
  }

  function submitAllCorrections() {
    const states = Object.values(drafts)
    if (states.length > 0 && allBlockingStaged) correction.mutate(states)
  }

  return (
    <section className="card review-panel">
      <div className="section-heading compact-heading">
        <div>
          <p className="eyebrow">MANUAL REVIEW</p>
          <h2>待审核字段与原因</h2>
        </div>
        <span>按原文或已确认信息逐项修正；不要猜测缺失值</span>
      </div>

      <div className="review-quote-list">
        {reviewRows.map(({ quote, query, findings }) => (
          <article className="review-quote" key={quote.quote_id}>
            <header>
              <div>
                <strong>{quote.supplier_id}</strong>
                <span>{quote.original_filename}</span>
              </div>
              <span className="status-pill">
                {query.data?.review_status ?? (query.isPending ? '读取中' : '未审查')}
              </span>
            </header>

            {query.isError && <p className="form-error">{errorMessage(query.error)}</p>}
            {query.data && findings.length === 0 && (
              <p className="review-empty">当前保存的审核记录没有未解决 finding。</p>
            )}
            {query.data && findings.length > 0 && (
              <ul className="review-finding-list">
                {groupFindingsByField(findings).map((findingGroup) => {
                  const finding = findingGroup[0]
                  const key = quote.quote_id + ':' + finding.field_name
                  const field = query.data.fields.find(
                    (item) => item.field_name === finding.field_name,
                  )
                  const staged = drafts[key]
                  const isEditing = editor?.quote.quote_id === quote.quote_id &&
                    editor.field.field_name === finding.field_name
                  const canConfirm =
                    field?.raw_value !== null && field?.raw_value !== undefined &&
                    field?.normalized_value !== null && field?.normalized_value !== undefined
                  const severity = findingGroup.some((item) => item.severity === 'BLOCKING')
                    ? 'BLOCKING'
                    : findingGroup.some((item) => item.severity === 'WARNING')
                      ? 'WARNING'
                      : finding.severity
                  const reasonCodes = Array.from(new Set(findingGroup.flatMap((item) => item.codes)))
                  const messages = Array.from(new Set(findingGroup.map(findingDisplayMessage)))

                  return (
                    <li className={isEditing ? 'review-finding review-finding-editing' : 'review-finding'} key={finding.field_name}>
                      <div className="review-finding-title">
                        <strong>{fieldLabels[finding.field_name] ?? finding.field_name}</strong>
                        <code>{finding.field_name}</code>
                        <span className={'review-severity review-severity-' + severity.toLowerCase()}>
                          {severity}
                        </span>
                        {staged && <span className="review-staged">已暂存</span>}
                      </div>

                      <div className="review-current-values" aria-label="当前字段值">
                        <div>
                          <span>当前原文值</span>
                          <strong>{displayFieldValue(field?.raw_value)}</strong>
                        </div>
                        <div>
                          <span>当前标准化值</span>
                          <strong>
                            {displayFieldValue(field?.normalized_value)}
                            {field?.unit ? ` ${field.unit}` : ''}
                          </strong>
                        </div>
                      </div>

                      <div className="review-finding-reason">
                        <span>待审核原因</span>
                        <strong>{reasonCodes.map(findingCodeLabel).join(' / ')}</strong>
                        {messages.map((message) => <p key={message}>{message}</p>)}
                      </div>

                      {staged && (
                        <div className="review-staged-value">
                          暂存为：{displayFieldValue(staged.normalizedValue)}{staged.unit ? ` ${staged.unit}` : ''}
                        </div>
                      )}

                      <div className="review-finding-actions">
                        {canConfirm && (
                        <button
                          className='button button-secondary button-small'
                          type='button'
                          onClick={() => confirmCurrentValue(quote, finding, query.data)}
                        >
                          确认当前值正确
                        </button>
                        )}
                        <button
                          className="button button-secondary button-small"
                          type="button"
                          disabled={!field}
                          onClick={() => openEditor(quote, finding, query.data)}
                        >
                          {staged ? '修改暂存内容' : '核对并修正字段'}
                        </button>
                      </div>

                      {isEditing && editor && (
                        <form className="review-editor review-editor-inline" onSubmit={submitCorrection}>
                          <div className="section-heading compact-heading">
                            <div>
                              <p className="eyebrow">FIELD CORRECTION</p>
                              <h3>核对：{fieldLabels[editor.field.field_name] ?? editor.field.field_name}</h3>
                            </div>
                            <button className="button button-secondary button-small" type="button" onClick={() => setEditor(null)}>
                              取消
                            </button>
                          </div>
                          <p className="review-editor-warning">
                            此处只暂存。请填写从原报价或供应商确认得到的值，不要猜测。
                          </p>
                          <div className="review-editor-grid">
                            <label>
                              原文或人工确认依据
                              <input
                                required
                                placeholder={
                                  feeStatusFields.has(editor.field.field_name)
                                    ? '例如：供应商确认运费免费'
                                    : undefined
                                }
                                value={editor.rawValue}
                                onChange={(event) => setEditor({ ...editor, rawValue: event.target.value })}
                              />
                            </label>
                            <label>
                              标准化值
                              {feeStatusFields.has(editor.field.field_name) ? (
                                <>
                                  <select
                                    required
                                    value={editor.normalizedValue}
                                    onChange={(event) => setEditor({ ...editor, normalizedValue: event.target.value })}
                                  >
                                    <option value="">请选择已经人工确认的实际状态</option>
                                    {resolvingFeeStatuses
                                      .filter(([value]) => value !== 'KNOWN_AMOUNT' || editor.knownAmountAvailable)
                                      .map(([value, label]) => (
                                        <option key={value} value={value}>{value} — {label}</option>
                                      ))}
                                  </select>
                                  <small>
                                    UNKNOWN 表示仍然未知，不能解除成本计算阻塞。
                                    {!editor.knownAmountAvailable && ' 当前报价没有对应费用金额，因此不能选择 KNOWN_AMOUNT。'}
                                  </small>
                                </>
                              ) : (
                                <input
                                  required
                                  value={editor.normalizedValue}
                                  onChange={(event) => setEditor({ ...editor, normalizedValue: event.target.value })}
                                />
                              )}
                            </label>
                            <label>
                              单位（没有可留空）
                              <input value={editor.unit} onChange={(event) => setEditor({ ...editor, unit: event.target.value })} />
                            </label>
                            <label>
                              修正原因
                              <input
                                required
                                minLength={3}
                                value={editor.reason}
                                onChange={(event) => setEditor({ ...editor, reason: event.target.value })}
                              />
                            </label>
                          </div>
                          <button className="button button-submit" type="submit">加入待提交清单</button>
                        </form>
                      )}
                    </li>
                  )
                })}
              </ul>
            )}
          </article>
        ))}
      </div>

      <div className="review-batch-actions">
        <div>
          <strong>批量修正</strong>
          <p>
            已暂存 {stagedBlockingCount} / {blockingKeys.length} 个阻塞字段。
            全部填完后只创建一个 revision 和一个 Job。
          </p>
        </div>
        <button
          className="button button-submit"
          type="button"
          disabled={
            correction.isPending ||
            Object.keys(drafts).length === 0 ||
            !allBlockingStaged
          }
          onClick={submitAllCorrections}
        >
          {correction.isPending ? '正在提交全部修正…' : '提交全部修正并继续'}
        </button>
      </div>
      {correction.isError && (
        <p className="form-error" role="alert">{errorMessage(correction.error, quotes)}</p>
      )}
    </section>
  )
}
