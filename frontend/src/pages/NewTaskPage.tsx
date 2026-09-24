import { useMutation, useQuery } from '@tanstack/react-query'
import { type FormEvent, useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import type { CreateTaskRequest, PolicySetSummary, RankingCriterion, RequirementDraftResponse } from '../api/types'
import { FilePreviewDialog, type PreviewFileSource } from '../components/FilePreviewDialog'
import { RequirementFields, type RequirementFormValues } from '../components/RequirementFields'
import { backendFieldErrors } from '../lib/apiErrors'
import { fieldLabel } from '../lib/presentation'

interface FormState extends RequirementFormValues {
  task_name: string
}

type FieldErrors = Partial<Record<keyof FormState, string>>

interface Submission {
  body: CreateTaskRequest
  idempotencyKey: string
}

interface RequirementFileMetadata {
  name: string
  mediaType: string
  sizeBytes: number
}

interface PersistedNewTaskState {
  version: 1
  form: FormState
  requirementDraft: RequirementDraftResponse | null
  requirementFileMetadata: RequirementFileMetadata | null
  autoFilledFields: (keyof RequirementFormValues)[]
  bindPolicy: boolean
  selectedPolicyKey: string
  policyCategory: string
  policyRegion: string
  taskNameEdited?: boolean
}

const initialForm: FormState = {
  task_name: '',
  manufacturer: '',
  manufacturer_part_number: '',
  package: '',
  revision: '',
  condition: '',
  allow_substitutes: false,
  base_unit: '',
  required_quantity: '',
  quantity_unit: '',
  budget_amount: '',
  currency: '',
  includes_shipping: false,
  tax_mode: '',
  other_fees_required: false,
  planned_order_date: '',
  delivery_deadline: '',
  delivery_location: '',
  ranking_preference: '',
  secondary_preference: '',
}

const NEW_TASK_STORAGE_KEY = 'quotewise.new-task.v1'
let inMemoryRequirementFile: File | null = null

function readPersistedNewTask(): PersistedNewTaskState | null {
  try {
    const raw = window.sessionStorage.getItem(NEW_TASK_STORAGE_KEY)
    if (!raw) return null
    const value = JSON.parse(raw) as Partial<PersistedNewTaskState>
    if (value.version !== 1 || !value.form || typeof value.form !== 'object') return null
    const restoredForm = { ...initialForm, ...value.form }
    if (!restoredForm.task_name && restoredForm.manufacturer_part_number) {
      restoredForm.task_name = defaultTaskName(restoredForm.manufacturer_part_number)
    }
    return {
      version: 1,
      form: restoredForm,
      requirementDraft: value.requirementDraft ?? null,
      requirementFileMetadata: value.requirementFileMetadata ?? null,
      autoFilledFields: Array.isArray(value.autoFilledFields) ? value.autoFilledFields : [],
      bindPolicy: Boolean(value.bindPolicy),
      selectedPolicyKey: typeof value.selectedPolicyKey === 'string' ? value.selectedPolicyKey : '',
      policyCategory: typeof value.policyCategory === 'string' ? value.policyCategory : '',
      policyRegion: typeof value.policyRegion === 'string' ? value.policyRegion : '',
      taskNameEdited: Boolean(value.taskNameEdited),
    }
  } catch {
    window.sessionStorage.removeItem(NEW_TASK_STORAGE_KEY)
    return null
  }
}

function hasFormProgress(form: FormState) {
  return (Object.keys(initialForm) as (keyof FormState)[]).some((field) => form[field] !== initialForm[field])
}

function persistNewTask(state: PersistedNewTaskState) {
  const hasProgress = hasFormProgress(state.form)
    || Boolean(state.requirementDraft)
    || Boolean(state.requirementFileMetadata)
    || state.bindPolicy
  if (!hasProgress) {
    window.sessionStorage.removeItem(NEW_TASK_STORAGE_KEY)
    return
  }
  const requirementDraft = state.requirementDraft
    ? { ...state.requirementDraft, parsed: null }
    : null
  try {
    window.sessionStorage.setItem(NEW_TASK_STORAGE_KEY, JSON.stringify({ ...state, requirementDraft }))
  } catch {
    // Storage can be unavailable in restricted browser contexts; the page remains usable in memory.
  }
}

function clearPersistedNewTask() {
  inMemoryRequirementFile = null
  window.sessionStorage.removeItem(NEW_TASK_STORAGE_KEY)
}

function errorMessage(error: unknown) {
  if (error instanceof ApiClientError) return error.message
  return '任务创建失败，请稍后重试。'
}

function localDate() {
  const now = new Date()
  const year = now.getFullYear()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function defaultTaskName(partNumber: string) {
  const normalized = partNumber.trim()
  return normalized ? `${normalized} · ${localDate()}` : ''
}

function validateForm(form: FormState): FieldErrors {
  const errors: FieldErrors = {}
  const requiredText: (keyof FormState)[] = [
    'task_name',
    'manufacturer', 'manufacturer_part_number', 'package', 'revision', 'condition',
    'quantity_unit', 'currency', 'tax_mode', 'delivery_deadline',
    'delivery_location', 'ranking_preference',
  ]
  for (const field of requiredText) {
    if (!String(form[field]).trim()) errors[field] = '此字段为必填项。'
  }
  const requiredQuantity = Number(form.required_quantity)
  if (!Number.isInteger(requiredQuantity) || requiredQuantity <= 0) {
    errors.required_quantity = '需求数量必须是大于 0 的整数。'
  }
  if (!/^\d+(\.\d{1,2})?$/.test(form.budget_amount)) {
    errors.budget_amount = '请输入非负金额，最多保留两位小数。'
  }
  if (form.planned_order_date && form.delivery_deadline && form.delivery_deadline < form.planned_order_date) {
    errors.delivery_deadline = '交付截止日期不能早于计划下单日期。'
  }
  if (form.secondary_preference && form.secondary_preference === form.ranking_preference) {
    errors.secondary_preference = '次要偏好不能与主要排序偏好相同。'
  }
  return errors
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  return `${(bytes / 1024).toFixed(1)} KiB`
}

function policyKey(policy: PolicySetSummary) {
  return JSON.stringify([
    policy.policy_set_id,
    policy.policy_set_version,
    policy.policy_index_version,
  ])
}

export function NewTaskPage() {
  const navigate = useNavigate()
  const fileInput = useRef<HTMLInputElement>(null)
  const [restoredState] = useState(readPersistedNewTask)
  const [form, setForm] = useState<FormState>(() => restoredState?.form ?? initialForm)
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const [localError, setLocalError] = useState('')
  const [lastSubmission, setLastSubmission] = useState<Submission | null>(null)
  const [requirementFile, setRequirementFile] = useState<File | null>(() => inMemoryRequirementFile)
  const [requirementFileMetadata, setRequirementFileMetadata] = useState<RequirementFileMetadata | null>(() => (
    inMemoryRequirementFile
      ? { name: inMemoryRequirementFile.name, mediaType: inMemoryRequirementFile.type, sizeBytes: inMemoryRequirementFile.size }
      : restoredState?.requirementFileMetadata ?? null
  ))
  const [requirementDraft, setRequirementDraft] = useState<RequirementDraftResponse | null>(() => restoredState?.requirementDraft ?? null)
  const [preview, setPreview] = useState<PreviewFileSource | null>(null)
  const [extractionNotice, setExtractionNotice] = useState(() => restoredState ? '已恢复未提交的采购任务，请继续检查或创建任务。' : '')
  const [autoFilledFields, setAutoFilledFields] = useState<Set<keyof RequirementFormValues>>(() => new Set(restoredState?.autoFilledFields ?? []))
  const [bindPolicy, setBindPolicy] = useState(() => restoredState?.bindPolicy ?? false)
  const [selectedPolicyKey, setSelectedPolicyKey] = useState(() => restoredState?.selectedPolicyKey ?? '')
  const [policyCategory, setPolicyCategory] = useState(() => restoredState?.policyCategory ?? '')
  const [policyRegion, setPolicyRegion] = useState(() => restoredState?.policyRegion ?? '')
  const [taskNameEdited, setTaskNameEdited] = useState(() => restoredState?.taskNameEdited ?? false)

  useEffect(() => {
    persistNewTask({
      version: 1,
      form,
      requirementDraft,
      requirementFileMetadata,
      autoFilledFields: [...autoFilledFields],
      bindPolicy,
      selectedPolicyKey,
      policyCategory,
      policyRegion,
      taskNameEdited,
    })
  }, [autoFilledFields, bindPolicy, form, policyCategory, policyRegion, requirementDraft, requirementFileMetadata, selectedPolicyKey, taskNameEdited])

  useEffect(() => {
    const draftId = restoredState?.requirementDraft?.requirement_draft_id
    if (!draftId) return
    let cancelled = false
    void api.getRequirementDraft(draftId).then((draft) => {
      if (cancelled) return
      if (draft.status === 'DISCARDED' || draft.status === 'USED') {
        setRequirementDraft(null)
        setRequirementFileMetadata(null)
        setExtractionNotice('此前的需求草稿已失效，表单内容仍已保留；如需文件证据请重新上传。')
        return
      }
      setRequirementDraft(draft)
    }).catch(() => {
      if (!cancelled) setExtractionNotice('已恢复表单内容，但暂时无法向后端校验需求草稿。')
    })
    return () => { cancelled = true }
  }, [restoredState])

  const policySets = useQuery({
    queryKey: ['policy-sets', 'list', 'new-task'],
    queryFn: () => api.listPolicySets({ limit: 100, offset: 0 }),
  })
  const selectedPolicy = policySets.data?.items.find(
    (policy) => policyKey(policy) === selectedPolicyKey,
  )
  const visibleRequirementCandidates = requirementDraft?.candidates.filter(
    (candidate) => !['scenario_id', 'base_unit'].includes(candidate.field_name),
  ) ?? []

  const createTask = useMutation({
    mutationFn: ({ body, idempotencyKey }: Submission) => api.createTask(body, idempotencyKey),
    onSuccess: (task) => {
      clearPersistedNewTask()
      void navigate(`/tasks/${task.task_id}`, { replace: true })
    },
    onError: (error) => {
      const errors = backendFieldErrors(error, Object.keys(initialForm)) as FieldErrors
      if (Object.keys(errors).length > 0) setFieldErrors(errors)
    },
  })

  const requirementExtraction = useMutation({
    mutationFn: async (file: File) => {
      let draft = await api.uploadRequirementDraft(file, createIdempotencyKey())
      setRequirementDraft(draft)
      while (draft.status === 'PROCESSING') {
        await new Promise((resolve) => window.setTimeout(resolve, 1_000))
        draft = await api.getRequirementDraft(draft.requirement_draft_id)
      }
      return draft
    },
    onSuccess: (draft) => {
      setRequirementDraft(draft)
      if (draft.status !== 'READY') return
      const nextFields = new Set<keyof RequirementFormValues>()
      setForm((current) => {
        const next = { ...current }
        for (const candidate of draft.candidates) {
          if (candidate.field_name === 'scenario_id' || candidate.field_name === 'base_unit' || !(candidate.field_name in next)) continue
          const field = candidate.field_name as keyof RequirementFormValues
          const original = next[field]
          const value = candidate.normalized_value
          if (typeof original === 'boolean' && typeof value === 'boolean') {
            ;(next[field] as boolean) = value
          } else if (typeof original === 'string' && value !== null && value !== undefined) {
            ;(next[field] as string) = String(value)
          }
          nextFields.add(field)
        }
        next.base_unit = next.quantity_unit
        if (!taskNameEdited) next.task_name = defaultTaskName(next.manufacturer_part_number)
        return next
      })
      setAutoFilledFields(nextFields)
      setFieldErrors({})
      setLocalError('')
      setExtractionNotice(`已自动填入 ${draft.candidates.length} 个字段，请检查后创建任务。`)
    },
  })

  function update(field: keyof FormState, value: FormState[keyof FormState]) {
    if (field === 'task_name') setTaskNameEdited(true)
    setForm((current) => ({
      ...current,
      [field]: value,
      ...(field === 'quantity_unit' ? { base_unit: String(value) } : {}),
      ...(field === 'manufacturer_part_number' && !taskNameEdited
        ? { task_name: defaultTaskName(String(value)) }
        : {}),
    }))
    setFieldErrors((current) => {
      const next = { ...current }
      delete next[field]
      return next
    })
    setAutoFilledFields((current) => {
      const next = new Set(current)
      if (field !== 'task_name') next.delete(field as keyof RequirementFormValues)
      return next
    })
    setLocalError('')
    createTask.reset()
  }

  function updateRankingPreference(value: string) {
    setForm((current) => ({
      ...current,
      ranking_preference: value,
      secondary_preference: current.secondary_preference === value ? '' : current.secondary_preference,
    }))
    setFieldErrors((current) => ({ ...current, ranking_preference: undefined, secondary_preference: undefined }))
    setLocalError('')
    createTask.reset()
  }

  function selectPolicy(value: string) {
    const policy = policySets.data?.items.find((item) => policyKey(item) === value)
    setSelectedPolicyKey(value)
    setPolicyCategory(policy?.categories.length === 1 ? policy.categories[0] : '')
    setPolicyRegion(policy?.regions.length === 1 ? policy.regions[0] : '')
    setLocalError('')
    createTask.reset()
  }

  function setPolicyMode(enabled: boolean) {
    setBindPolicy(enabled)
    if (!enabled) {
      setSelectedPolicyKey('')
      setPolicyCategory('')
      setPolicyRegion('')
    }
    setLocalError('')
    createTask.reset()
  }

  function handleRequirementFile(file: File | null) {
    setExtractionNotice('')
    setLocalError('')
    if (!file) {
      inMemoryRequirementFile = null
      setRequirementFile(null)
      setRequirementFileMetadata(null)
      setRequirementDraft(null)
      return
    }
    const extension = file.name.split('.').pop()?.toLowerCase()
    if (!['pdf', 'md', 'txt'].includes(extension ?? '')) {
      setLocalError('采购需求附件仅支持 PDF、Markdown 或 TXT。')
      if (fileInput.current) fileInput.current.value = ''
      return
    }
    if (file.size > 10 * 1024 * 1024) {
      setLocalError('采购需求附件不能超过 10 MiB。')
      if (fileInput.current) fileInput.current.value = ''
      return
    }
    inMemoryRequirementFile = file
    setRequirementFile(file)
    setRequirementFileMetadata({ name: file.name, mediaType: file.type, sizeBytes: file.size })
    setRequirementDraft(null)
  }

  function clearForm() {
    clearPersistedNewTask()
    setForm(initialForm)
    setFieldErrors({})
    setAutoFilledFields(new Set())
    setExtractionNotice('')
    setRequirementFile(null)
    setRequirementFileMetadata(null)
    setRequirementDraft(null)
    setBindPolicy(false)
    setSelectedPolicyKey('')
    setPolicyCategory('')
    setPolicyRegion('')
    setTaskNameEdited(false)
    if (fileInput.current) fileInput.current.value = ''
  }

  function runRequirementExtraction() {
    if (!requirementFile || requirementExtraction.isPending) return
    setExtractionNotice('')
    requirementExtraction.mutate(requirementFile)
  }

  function buildSubmission(): Submission | null {
    const errors = validateForm(form)
    setFieldErrors(errors)
    if (Object.keys(errors).length > 0) {
      setLocalError('部分字段未通过前端校验，请修改标红内容后重新提交。')
      return null
    }
    if (bindPolicy) {
      if (policySets.isError) {
        setLocalError('已发布制度列表读取失败。请重试，或改选“不绑定制度”。')
        return null
      }
      if (!selectedPolicyKey) {
        setLocalError('请选择一套已发布制度，或改选“不绑定制度”。')
        return null
      }
      if (!selectedPolicy) {
        setLocalError('已选制度不再存在于服务端目录中，请重新选择。')
        return null
      }
      if (!policyCategory || !selectedPolicy.categories.includes(policyCategory)) {
        setLocalError('请选择该制度支持的具体分类。')
        return null
      }
      if (!policyRegion || !selectedPolicy.regions.includes(policyRegion)) {
        setLocalError('请选择该制度支持的具体地区。')
        return null
      }
    }
    return {
      idempotencyKey: createIdempotencyKey(),
      body: {
        task_name: form.task_name.trim(),
        scenario_id: null,
        ...(bindPolicy && selectedPolicy ? {
          policy_binding: {
            policy_set_version: selectedPolicy.policy_set_version,
            policy_index_version: selectedPolicy.policy_index_version,
            category: policyCategory,
            region: policyRegion,
          },
        } : {}),
        ...(requirementDraft?.status === 'READY' ? {
          requirement_draft_id: requirementDraft.requirement_draft_id,
          expected_requirement_draft_revision: requirementDraft.draft_revision,
        } : {}),
        requirement: {
          manufacturer: form.manufacturer.trim(),
          manufacturer_part_number: form.manufacturer_part_number.trim(),
          package: form.package.trim(),
          revision: form.revision.trim(),
          condition: form.condition.trim(),
          allow_substitutes: form.allow_substitutes,
          base_unit: form.quantity_unit.trim(),
          required_quantity: Number(form.required_quantity),
          quantity_unit: form.quantity_unit.trim(),
          budget_amount: form.budget_amount,
          currency: form.currency,
          includes_shipping: form.includes_shipping,
          tax_mode: form.tax_mode,
          other_fees_required: form.other_fees_required,
          planned_order_date: form.planned_order_date || null,
          delivery_deadline: form.delivery_deadline,
          delivery_location: form.delivery_location.trim(),
          ranking_preference: form.ranking_preference as RankingCriterion,
          secondary_preference: (form.secondary_preference.trim() || null) as RankingCriterion | null,
        },
      },
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const submission = buildSubmission()
    if (!submission) return
    setLastSubmission(submission)
    createTask.mutate(submission)
  }

  return (
    <div className="page-stack new-task-page">
      <section className="page-heading">
        <div>
          <h1>新建任务</h1>
          <p>上传采购需求文件，或直接填写采购信息。</p>
        </div>
        <Link className="button button-secondary" to="/">返回工作台</Link>
      </section>

      <section className="requirement-source-panel task-name-panel">
        <label className={`field field-wide ${fieldErrors.task_name ? 'field-invalid' : ''}`}>
          <span>任务名称</span>
          <input
            form="new-task-form"
            required
            aria-invalid={Boolean(fieldErrors.task_name)}
            placeholder={`例如：QW-MCU9-DEMO · ${localDate()}`}
            value={form.task_name}
            onChange={(event) => update('task_name', event.target.value)}
          />
          {fieldErrors.task_name
            ? <small className="field-error-text">{fieldErrors.task_name}</small>
            : <small>默认按“制造商料号 · 创建日期”生成，也可以自行修改；同一用户下不可重名。</small>}
        </label>
      </section>

      <section className="requirement-source-panel">
        <div className="requirement-source-intro">
          <span className="source-step">01</span>
          <div>
            <h2>导入采购需求 <small>可选</small></h2>
          </div>
        </div>

        <div className="requirement-source-actions">
          <label className="source-file-picker">
            <span aria-hidden="true">↑</span>
            <strong>{requirementFileMetadata ? '更换需求文件' : '选择需求文件'}</strong>
            <small>PDF / MD / TXT · 最大 10 MiB</small>
            <input ref={fileInput} type="file" accept=".pdf,.md,.txt,application/pdf,text/markdown,text/plain" onChange={(event) => handleRequirementFile(event.target.files?.[0] ?? null)} />
          </label>

          {requirementFileMetadata ? (
            <div className="source-file-selected">
              <span className="source-file-icon">DOC</span>
              <div><strong>{requirementFileMetadata.name}</strong><small>{formatBytes(requirementFileMetadata.sizeBytes)} · {requirementDraft?.status === 'READY' ? '已解析' : requirementDraft?.status === 'PROCESSING' ? '正在解析' : '等待解析'}</small></div>
              {requirementFile && <button type="button" onClick={() => setPreview({ name: requirementFile.name, mediaType: requirementFile.type, sizeBytes: requirementFile.size, file: requirementFile })}>预览</button>}
              {requirementDraft?.status === 'READY'
                ? <span className="status-pill status-ready">解析完成</span>
                : requirementFile
                  ? <button className="button button-submit" type="button" onClick={runRequirementExtraction} disabled={requirementExtraction.isPending}>{requirementExtraction.isPending ? '正在解析…' : '解析并填入'}</button>
                  : <small>如需重新解析或预览，请重新选择原文件。</small>}
            </div>
          ) : null}
        </div>
        {requirementExtraction.isError && <div className="form-error compact-error"><strong>需求文件解析失败</strong><p>{errorMessage(requirementExtraction.error)}</p></div>}
        {requirementDraft?.status === 'FAILED' && <div className="form-error compact-error"><strong>需求文件解析失败</strong><p>{requirementDraft.error_message}</p></div>}
        {extractionNotice && <div className="extraction-notice" role="status">✓ {extractionNotice}</div>}
        {requirementDraft?.status === 'READY' && visibleRequirementCandidates.length > 0 && <details className="card requirement-evidence-list"><summary>查看自动填入字段的原文证据（{visibleRequirementCandidates.length}）</summary><div className="audit-list">{visibleRequirementCandidates.map((candidate) => <article key={candidate.field_name} className="audit-record"><strong>{fieldLabel(candidate.field_name)}：{String(candidate.normalized_value ?? candidate.raw_value)}</strong>{candidate.source_refs.map((source) => <small key={source.source_id}>{source.quoted_text}</small>)}</article>)}</div></details>}
      </section>

      <form id="new-task-form" className="requirement-form" noValidate onSubmit={handleSubmit}>
        <div className="form-title-row">
          <div><span className="source-step">02</span><div><h2>采购需求</h2></div></div>
          <button className="button button-secondary" type="button" onClick={clearForm}>清空表单</button>
        </div>

        <RequirementFields
          value={form}
          onChange={(field, value) => field === 'ranking_preference'
            ? updateRankingPreference(String(value))
            : update(field, value)}
          errors={fieldErrors}
          highlightedFields={autoFilledFields}
        />

        <fieldset className="form-section policy-binding-section">
          <legend>制度检查</legend>
          <label className="field checkbox-field policy-binding-toggle"><input type="checkbox" checked={bindPolicy} onChange={(event) => setPolicyMode(event.target.checked)} /><span>启用制度检查</span></label>

          {bindPolicy && (
            <div className="policy-binding-picker">
              {policySets.isPending && <div className="policy-picker-state">正在读取已发布制度…</div>}
              {policySets.isError && <div className="policy-picker-state policy-picker-error"><span>制度目录读取失败。仍可改为不绑定并继续创建。</span><button className="button button-secondary" type="button" onClick={() => void policySets.refetch()}>重试</button></div>}
              {policySets.data?.items.length === 0 && <div className="policy-picker-state"><span>当前没有已发布制度。请先到规则资源库完成发布，或选择不绑定。</span><Link to="/resources">前往规则资源库</Link></div>}
              {policySets.data && policySets.data.items.length > 0 && (
                <>
                  <label className="field policy-picker-wide"><span>已发布制度</span><select value={selectedPolicyKey} onChange={(event) => selectPolicy(event.target.value)}><option value="">请选择制度版本</option>{policySets.data.items.map((policy) => <option key={policyKey(policy)} value={policyKey(policy)}>{policy.policy_set_id} · 版本 {policy.policy_set_version}</option>)}</select></label>
                  {selectedPolicyKey && !selectedPolicy && <div className="policy-stale-warning" role="alert">已选制度已从服务端目录消失，请重新选择后再提交。</div>}
                  {selectedPolicy && (
                    <>
                      <div className="policy-binding-fields">
                        <label className="field"><span>适用分类</span><select value={policyCategory} onChange={(event) => { setPolicyCategory(event.target.value); setLocalError(''); createTask.reset() }}><option value="">请选择具体分类</option>{selectedPolicy.categories.map((category) => <option key={category} value={category}>{category}</option>)}</select></label>
                        <label className="field"><span>适用地区</span><select value={policyRegion} onChange={(event) => { setPolicyRegion(event.target.value); setLocalError(''); createTask.reset() }}><option value="">请选择具体地区</option>{selectedPolicy.regions.map((region) => <option key={region} value={region}>{region}</option>)}</select></label>
                      </div>
                      <dl className="policy-picker-summary"><div><dt>文件</dt><dd>{selectedPolicy.document_count}</dd></div><div><dt>制度条款</dt><dd>{selectedPolicy.clause_count}</dd></div><div><dt>发布时间</dt><dd>{selectedPolicy.published_at ? new Date(selectedPolicy.published_at).toLocaleString('zh-CN') : '—'}</dd></div></dl>
                    </>
                  )}
                  {policySets.data.total > policySets.data.items.length && <small className="policy-picker-limit">目录共有 {policySets.data.total} 个版本，当前显示最近的 {policySets.data.items.length} 个。</small>}
                </>
              )}
            </div>
          )}
        </fieldset>

        {(localError || createTask.isError) && (
          <div className="form-error" role="alert">
            <div>
              <strong>无法创建任务</strong>
              <p>{localError || errorMessage(createTask.error)}</p>
            </div>
            {!localError && lastSubmission && <button className="button button-secondary" type="button" onClick={() => createTask.mutate(lastSubmission)}>重试相同请求</button>}
          </div>
        )}

        <div className="form-actions form-actions-compact">
          <button className="button button-submit" type="submit" disabled={createTask.isPending}>{createTask.isPending ? '正在创建…' : '创建任务'}</button>
        </div>
      </form>

      {preview && <FilePreviewDialog source={preview} onClose={() => setPreview(null)} />}
    </div>
  )
}
