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
  return 'Unable to create the task. Try again later.'
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
    if (!String(form[field]).trim()) errors[field] = 'This field is required.'
  }
  const requiredQuantity = Number(form.required_quantity)
  if (!Number.isInteger(requiredQuantity) || requiredQuantity <= 0) {
    errors.required_quantity = 'Required quantity must be an integer greater than zero.'
  }
  if (!/^\d+(\.\d{1,2})?$/.test(form.budget_amount)) {
    errors.budget_amount = 'Enter a non-negative amount with no more than two decimal places.'
  }
  if (form.planned_order_date && form.delivery_deadline && form.delivery_deadline < form.planned_order_date) {
    errors.delivery_deadline = 'Delivery deadline cannot be earlier than the planned order date.'
  }
  if (form.secondary_preference && form.secondary_preference === form.ranking_preference) {
    errors.secondary_preference = 'Secondary ranking criterion must differ from the primary criterion.'
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
  const [extractionNotice, setExtractionNotice] = useState(() => restoredState ? 'Your unsaved procurement task has been restored. Continue reviewing or create the task.' : '')
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
        setExtractionNotice('The previous requirements draft is stale. Form values were retained; upload the document again if source evidence is required.')
        return
      }
      setRequirementDraft(draft)
    }).catch(() => {
      if (!cancelled) setExtractionNotice('Form values were restored, but the requirements draft could not be validated with the backend.')
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
      setExtractionNotice(`${draft.candidates.length} fields were populated automatically. Review them before creating the task.`)
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
      setLocalError('Requirements attachments must be PDF, Markdown or TXT files.')
      if (fileInput.current) fileInput.current.value = ''
      return
    }
    if (file.size > 10 * 1024 * 1024) {
      setLocalError('Requirements attachments cannot exceed 10 MiB.')
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
      setLocalError('Some fields did not pass validation. Correct the highlighted entries and submit again.')
      return null
    }
    if (bindPolicy) {
      if (policySets.isError) {
        setLocalError('Unable to load published policies. Retry or continue without a policy binding.')
        return null
      }
      if (!selectedPolicyKey) {
        setLocalError('Select a published policy or continue without a policy binding.')
        return null
      }
      if (!selectedPolicy) {
        setLocalError('The selected policy is no longer in the backend catalogue. Select another policy.')
        return null
      }
      if (!policyCategory || !selectedPolicy.categories.includes(policyCategory)) {
        setLocalError('Select an applicable category supported by this policy.')
        return null
      }
      if (!policyRegion || !selectedPolicy.regions.includes(policyRegion)) {
        setLocalError('Select an applicable region supported by this policy.')
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
      <section className="page-heading app-page-heading">
        <div>
          <h1>New task</h1>
          <p>Upload a requirements document or enter the procurement details manually.</p>
        </div>
        <Link className="button button-secondary" to="/">Back to workspace</Link>
      </section>

      <section className="requirement-source-panel task-name-panel">
        <label className={`field field-wide ${fieldErrors.task_name ? 'field-invalid' : ''}`}>
          <span>Task name</span>
          <input
            form="new-task-form"
            required
            aria-invalid={Boolean(fieldErrors.task_name)}
            placeholder={`For example: QW-MCU9-DEMO · ${localDate()}`}
            value={form.task_name}
            onChange={(event) => update('task_name', event.target.value)}
          />
          {fieldErrors.task_name
            ? <small className="field-error-text">{fieldErrors.task_name}</small>
            : <small>A name is generated from the manufacturer part number and creation date. You may change it, but task names must be unique for each user.</small>}
        </label>
      </section>

      <section className="requirement-source-panel">
        <div className="requirement-source-intro">
          <span className="source-step">01</span>
          <div>
            <h2>Import Procurement Requirements <small>Optional</small></h2>
          </div>
        </div>

        <div className="requirement-source-actions">
          <label className="source-file-picker">
            <span aria-hidden="true">↑</span>
            <strong>{requirementFileMetadata ? 'Replace requirements document' : 'Select requirements document'}</strong>
            <small>PDF / MD / TXT · Maximum 10 MiB</small>
            <input ref={fileInput} type="file" accept=".pdf,.md,.txt,application/pdf,text/markdown,text/plain" onChange={(event) => handleRequirementFile(event.target.files?.[0] ?? null)} />
          </label>

          {requirementFileMetadata ? (
            <div className="source-file-selected">
              <span className="source-file-icon">DOC</span>
              <div><strong>{requirementFileMetadata.name}</strong><small>{formatBytes(requirementFileMetadata.sizeBytes)} · {requirementDraft?.status === 'READY' ? 'Parsed' : requirementDraft?.status === 'PROCESSING' ? 'Processing' : 'Awaiting parsing'}</small></div>
              {requirementFile && <button type="button" onClick={() => setPreview({ name: requirementFile.name, mediaType: requirementFile.type, sizeBytes: requirementFile.size, file: requirementFile })}>Preview</button>}
              {requirementDraft?.status === 'READY'
                ? <span className="status-pill status-ready">Parsing complete</span>
                : requirementFile
                  ? <button className="button button-submit" type="button" onClick={runRequirementExtraction} disabled={requirementExtraction.isPending}>{requirementExtraction.isPending ? 'Processing…' : 'Parse and populate'}</button>
                  : <small>Select the source file again to reparse or preview it.</small>}
            </div>
          ) : null}
        </div>
        {requirementExtraction.isError && <div className="form-error compact-error"><strong>Requirements document parsing failed</strong><p>{errorMessage(requirementExtraction.error)}</p></div>}
        {requirementDraft?.status === 'FAILED' && <div className="form-error compact-error"><strong>Requirements document parsing failed</strong><p>{requirementDraft.error_message}</p></div>}
        {extractionNotice && <div className="extraction-notice" role="status">✓ {extractionNotice}</div>}
        {requirementDraft?.status === 'READY' && visibleRequirementCandidates.length > 0 && <details className="card requirement-evidence-list"><summary>View source evidence for populated fields ({visibleRequirementCandidates.length})</summary><div className="audit-list">{visibleRequirementCandidates.map((candidate) => <article key={candidate.field_name} className="audit-record"><strong>{fieldLabel(candidate.field_name)}: {String(candidate.normalized_value ?? candidate.raw_value)}</strong>{candidate.source_refs.map((source) => <small key={source.source_id}>{source.quoted_text}</small>)}</article>)}</div></details>}
      </section>

      <form id="new-task-form" className="requirement-form" noValidate onSubmit={handleSubmit}>
        <div className="form-title-row">
          <div><span className="source-step">02</span><div><h2>Procurement Requirements</h2></div></div>
          <button className="button button-secondary" type="button" onClick={clearForm}>Clear form</button>
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
          <legend>Compliance Review</legend>
          <label className="field checkbox-field policy-binding-toggle"><input type="checkbox" checked={bindPolicy} onChange={(event) => setPolicyMode(event.target.checked)} /><span>Enable compliance review</span></label>

          {bindPolicy && (
            <div className="policy-binding-picker">
              {policySets.isPending && <div className="policy-picker-state">Loading published policies…</div>}
              {policySets.isError && <div className="policy-picker-state policy-picker-error"><span>Unable to load the policy catalogue. You may continue without a policy binding.</span><button className="button button-secondary" type="button" onClick={() => void policySets.refetch()}>Retry</button></div>}
              {policySets.data?.items.length === 0 && <div className="policy-picker-state"><span>No published policy is available. Publish one in the policy library or continue without a policy binding.</span><Link to="/resources">Go to policy library</Link></div>}
              {policySets.data && policySets.data.items.length > 0 && (
                <>
                  <label className="field policy-picker-wide"><span>Published policy</span><select value={selectedPolicyKey} onChange={(event) => selectPolicy(event.target.value)}><option value="">Select a policy version</option>{policySets.data.items.map((policy) => <option key={policyKey(policy)} value={policyKey(policy)}>{policy.policy_set_id} · Version {policy.policy_set_version}</option>)}</select></label>
                  {selectedPolicyKey && !selectedPolicy && <div className="policy-stale-warning" role="alert">The selected policy is no longer available. Select another policy before submitting.</div>}
                  {selectedPolicy && (
                    <>
                      <div className="policy-binding-fields">
                        <label className="field"><span>Applicable category</span><select value={policyCategory} onChange={(event) => { setPolicyCategory(event.target.value); setLocalError(''); createTask.reset() }}><option value="">Select a category</option>{selectedPolicy.categories.map((category) => <option key={category} value={category}>{category}</option>)}</select></label>
                        <label className="field"><span>Applicable region</span><select value={policyRegion} onChange={(event) => { setPolicyRegion(event.target.value); setLocalError(''); createTask.reset() }}><option value="">Select a region</option>{selectedPolicy.regions.map((region) => <option key={region} value={region}>{region}</option>)}</select></label>
                      </div>
                      <dl className="policy-picker-summary"><div><dt>File</dt><dd>{selectedPolicy.document_count}</dd></div><div><dt>Policy Clause</dt><dd>{selectedPolicy.clause_count}</dd></div><div><dt>Published at</dt><dd>{selectedPolicy.published_at ? new Date(selectedPolicy.published_at).toLocaleString('zh-CN') : '—'}</dd></div></dl>
                    </>
                  )}
                  {policySets.data.total > policySets.data.items.length && <small className="policy-picker-limit">The catalogue contains {policySets.data.total} versions; the {policySets.data.items.length} most recent are shown.</small>}
                </>
              )}
            </div>
          )}
        </fieldset>

        {(localError || createTask.isError) && (
          <div className="form-error" role="alert">
            <div>
              <strong>Unable to create task</strong>
              <p>{localError || errorMessage(createTask.error)}</p>
            </div>
            {!localError && lastSubmission && <button className="button button-secondary" type="button" onClick={() => createTask.mutate(lastSubmission)}>Retry same request</button>}
          </div>
        )}

        <div className="form-actions form-actions-compact">
          <button className="button button-submit" type="submit" disabled={createTask.isPending}>{createTask.isPending ? 'Creating…' : 'Create task'}</button>
        </div>
      </form>

      {preview && <FilePreviewDialog source={preview} onClose={() => setPreview(null)} />}
    </div>
  )
}
