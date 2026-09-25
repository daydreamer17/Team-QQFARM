import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import type { PolicySetSummary, ProcurementRequirement, RankingCriterion, TaskDetail } from '../api/types'
import { RequirementFields, type RequirementFormValues } from '../components/RequirementFields'

function message(error: unknown) {
  if (error instanceof ApiClientError && error.code === 'requirement_unchanged') return 'Neither the procurement requirements nor the policy binding has changed.'
  return error instanceof ApiClientError ? error.message : 'Unable to update the procurement task.'
}

function policyKey(policy: PolicySetSummary) {
  return JSON.stringify([policy.policy_set_id, policy.policy_set_version, policy.policy_index_version])
}

function RequirementEditForm({ task }: { task: TaskDetail }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [localError, setLocalError] = useState('')
  const [value, setValue] = useState<RequirementFormValues>({
    ...task.requirement,
    required_quantity: String(task.requirement.required_quantity),
    planned_order_date: task.requirement.planned_order_date ?? '',
    secondary_preference: task.requirement.secondary_preference ?? '',
  })
  const [bindPolicy, setBindPolicy] = useState(Boolean(task.policy_binding))
  const [selectedPolicyKey, setSelectedPolicyKey] = useState('')
  const [policyCategory, setPolicyCategory] = useState(task.policy_binding?.category ?? '')
  const [policyRegion, setPolicyRegion] = useState(task.policy_binding?.region ?? '')
  const policySets = useQuery({
    queryKey: ['policy-sets', 'list', 'edit-task'],
    queryFn: () => api.listPolicySets({ limit: 100, offset: 0 }),
  })
  const currentPolicy = policySets.data?.items.find((policy) =>
    policy.policy_set_version === task.policy_binding?.policy_set_version
    && policy.policy_index_version === task.policy_binding?.policy_index_version)
  const effectiveSelectedPolicyKey = selectedPolicyKey || (currentPolicy ? policyKey(currentPolicy) : '')
  const selectedPolicy = policySets.data?.items.find((policy) => policyKey(policy) === effectiveSelectedPolicyKey)
  const policyBinding = selectedPolicy ? {
    policy_set_version: selectedPolicy.policy_set_version,
    policy_index_version: selectedPolicy.policy_index_version,
    category: policyCategory,
    region: policyRegion,
  } : task.policy_binding

  const update = useMutation({
    mutationFn: () => {
      const requirement: ProcurementRequirement = {
        ...value,
        base_unit: value.quantity_unit.trim(),
        required_quantity: Number(value.required_quantity),
        planned_order_date: value.planned_order_date || null,
        ranking_preference: value.ranking_preference as RankingCriterion,
        secondary_preference: (value.secondary_preference || null) as RankingCriterion | null,
      }
      return api.updateRequirement(
        task.task_id,
        task.task_revision,
        requirement,
        createIdempotencyKey(),
        bindPolicy ? policyBinding : null,
      )
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['tasks'] })
      void navigate(`/tasks/${task.task_id}`, { replace: true })
    },
  })
  function set<K extends keyof RequirementFormValues>(field: K, next: RequirementFormValues[K]) {
    setValue((current) => ({
      ...current,
      [field]: next,
      ...(field === 'quantity_unit' ? { base_unit: String(next) } : {}),
    }))
    setLocalError('')
    update.reset()
  }
  function selectPolicy(key: string) {
    const policy = policySets.data?.items.find((item) => policyKey(item) === key)
    setSelectedPolicyKey(key)
    setPolicyCategory(policy?.categories.length === 1 ? policy.categories[0] : '')
    setPolicyRegion(policy?.regions.length === 1 ? policy.regions[0] : '')
    setLocalError('')
    update.reset()
  }
  function submit(event: FormEvent) {
    event.preventDefault()
    if (bindPolicy && (!policyBinding || !policyCategory || !policyRegion)) {
      setLocalError('Select a published policy, applicable category and region.')
      return
    }
    if (window.confirm('Saving creates a new task revision. Existing quotations will be reanalysed against the new requirements and policy. Continue?')) update.mutate()
  }
  return <form className="requirement-form" onSubmit={submit}>
    <section className="page-heading"><div><h1>Edit Procurement Task</h1><p>Current revision: {task.task_revision}. Changes to requirements or policy binding retain history and trigger reanalysis.</p></div><Link className="button button-secondary" to={`/tasks/${task.task_id}`}>Cancel</Link></section>
    <RequirementFields value={value} onChange={(field, next) => {
      if (field === 'ranking_preference' && value.secondary_preference === next) {
        setValue((current) => ({ ...current, ranking_preference: String(next), secondary_preference: '' }))
        update.reset()
        return
      }
      set(field, next)
    }} />
    <fieldset className="form-section policy-binding-section">
      <legend>Compliance Review</legend>
      <label className="field checkbox-field policy-binding-toggle"><input type="checkbox" checked={bindPolicy} onChange={(event) => {
        setBindPolicy(event.target.checked)
        setLocalError('')
        update.reset()
      }} /><span>Enable compliance review for this task</span></label>
      {bindPolicy && <div className="policy-binding-picker">
        {policySets.isPending && <div className="policy-picker-state">Loading published policies…</div>}
        {policySets.isError && <div className="policy-picker-state policy-picker-error"><span>Unable to load policies.</span><button className="button button-secondary" type="button" onClick={() => void policySets.refetch()}>Retry</button></div>}
        {policySets.data?.items.length === 0 && <div className="policy-picker-state"><span>No policies are available.</span><Link to="/resources">Go to policy library</Link></div>}
        {policySets.data && policySets.data.items.length > 0 && <>
          <label className="field policy-picker-wide"><span>Published policy</span><select value={effectiveSelectedPolicyKey} onChange={(event) => selectPolicy(event.target.value)}><option value="">Select a policy version</option>{policySets.data.items.map((policy) => <option key={policyKey(policy)} value={policyKey(policy)}>{policy.policy_set_id} · Version {policy.policy_set_version}</option>)}</select></label>
          {task.policy_binding && !selectedPolicy && <div className="policy-stale-warning">The bound policy is no longer available. Select another policy.</div>}
          {selectedPolicy && <>
            <div className="policy-binding-fields">
              <label className="field"><span>Applicable procurement categories</span><select value={policyCategory} onChange={(event) => { setPolicyCategory(event.target.value); setLocalError(''); update.reset() }}><option value="">Select an option</option>{selectedPolicy.categories.map((category) => <option key={category} value={category}>{category}</option>)}</select></label>
              <label className="field"><span>Applicable region</span><select value={policyRegion} onChange={(event) => { setPolicyRegion(event.target.value); setLocalError(''); update.reset() }}><option value="">Select an option</option>{selectedPolicy.regions.map((region) => <option key={region} value={region}>{region}</option>)}</select></label>
            </div>
            <p className="policy-picker-limit">{selectedPolicy.document_count} documents · {selectedPolicy.clause_count} policy clauses · Published {selectedPolicy.published_at ? new Date(selectedPolicy.published_at).toLocaleString('en-SG') : '—'}.</p>
          </>}
        </>}
      </div>}
    </fieldset>
    {localError && <div className="form-error" role="alert">{localError}</div>}
    {update.isError && <div className="form-error" role="alert">{message(update.error)}</div>}
    <button className="button button-submit" disabled={update.isPending}>{update.isPending ? 'Saving and queueing…' : 'Save new revision'}</button>
  </form>
}

export function EditTaskPage() {
  const { taskId = '' } = useParams()
  const task = useQuery({ queryKey: ['tasks', taskId], queryFn: () => api.getTask(taskId), enabled: Boolean(taskId) })
  if (task.isPending) return <section className="card loading-panel">Loading procurement requirements…</section>
  if (task.isError) return <section className="card error-panel">{message(task.error)}</section>
  if (task.data.status === 'ABANDONED') return <section className="card error-panel">An abandoned task is read-only. Its procurement requirements cannot be changed.</section>
  return <RequirementEditForm task={task.data} />
}
