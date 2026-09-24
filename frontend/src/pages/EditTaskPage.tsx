import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import type { PolicySetSummary, ProcurementRequirement, RankingCriterion, TaskDetail } from '../api/types'
import { RequirementFields, type RequirementFormValues } from '../components/RequirementFields'

function message(error: unknown) {
  if (error instanceof ApiClientError && error.code === 'requirement_unchanged') return '采购需求和绑定制度均未发生变化。'
  return error instanceof ApiClientError ? error.message : '采购任务更新失败。'
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
      setLocalError('请选择已发布制度及其适用采购类别、地区。')
      return
    }
    if (window.confirm('保存后会生成新的任务版本；已有报价将按新需求和制度重新分析。确认保存吗？')) update.mutate()
  }
  return <form className="requirement-form" onSubmit={submit}>
    <section className="page-heading"><div><h1>修改采购任务</h1><p>当前第 {task.task_revision} 版。采购需求或绑定制度变更后，系统会保留历史并重新分析。</p></div><Link className="button button-secondary" to={`/tasks/${task.task_id}`}>取消</Link></section>
    <RequirementFields value={value} onChange={(field, next) => {
      if (field === 'ranking_preference' && value.secondary_preference === next) {
        setValue((current) => ({ ...current, ranking_preference: String(next), secondary_preference: '' }))
        update.reset()
        return
      }
      set(field, next)
    }} />
    <fieldset className="form-section policy-binding-section">
      <legend>制度检查</legend>
      <label className="field checkbox-field policy-binding-toggle"><input type="checkbox" checked={bindPolicy} onChange={(event) => {
        setBindPolicy(event.target.checked)
        setLocalError('')
        update.reset()
      }} /><span>为该任务启用制度检查</span></label>
      {bindPolicy && <div className="policy-binding-picker">
        {policySets.isPending && <div className="policy-picker-state">正在读取已发布制度…</div>}
        {policySets.isError && <div className="policy-picker-state policy-picker-error"><span>无法读取制度列表。</span><button className="button button-secondary" type="button" onClick={() => void policySets.refetch()}>重试</button></div>}
        {policySets.data?.items.length === 0 && <div className="policy-picker-state"><span>暂无可用制度。</span><Link to="/resources">前往制度资源库</Link></div>}
        {policySets.data && policySets.data.items.length > 0 && <>
          <label className="field policy-picker-wide"><span>已发布制度</span><select value={effectiveSelectedPolicyKey} onChange={(event) => selectPolicy(event.target.value)}><option value="">请选择制度版本</option>{policySets.data.items.map((policy) => <option key={policyKey(policy)} value={policyKey(policy)}>{policy.policy_set_id} · 版本 {policy.policy_set_version}</option>)}</select></label>
          {task.policy_binding && !selectedPolicy && <div className="policy-stale-warning">当前绑定的制度已不在可用目录中，请重新选择。</div>}
          {selectedPolicy && <>
            <div className="policy-binding-fields">
              <label className="field"><span>适用采购类别</span><select value={policyCategory} onChange={(event) => { setPolicyCategory(event.target.value); setLocalError(''); update.reset() }}><option value="">请选择</option>{selectedPolicy.categories.map((category) => <option key={category} value={category}>{category}</option>)}</select></label>
              <label className="field"><span>适用地区</span><select value={policyRegion} onChange={(event) => { setPolicyRegion(event.target.value); setLocalError(''); update.reset() }}><option value="">请选择</option>{selectedPolicy.regions.map((region) => <option key={region} value={region}>{region}</option>)}</select></label>
            </div>
            <p className="policy-picker-limit">包含 {selectedPolicy.document_count} 个文件、{selectedPolicy.clause_count} 条制度条款，发布于 {selectedPolicy.published_at ? new Date(selectedPolicy.published_at).toLocaleString('zh-CN') : '—'}。</p>
          </>}
        </>}
      </div>}
    </fieldset>
    {localError && <div className="form-error" role="alert">{localError}</div>}
    {update.isError && <div className="form-error" role="alert">{message(update.error)}</div>}
    <button className="button button-submit" disabled={update.isPending}>{update.isPending ? '正在保存并排队…' : '保存新版本'}</button>
  </form>
}

export function EditTaskPage() {
  const { taskId = '' } = useParams()
  const task = useQuery({ queryKey: ['tasks', taskId], queryFn: () => api.getTask(taskId), enabled: Boolean(taskId) })
  if (task.isPending) return <section className="card loading-panel">正在读取采购需求…</section>
  if (task.isError) return <section className="card error-panel">{message(task.error)}</section>
  if (task.data.status === 'ABANDONED') return <section className="card error-panel">废弃任务只读，不能再修改采购需求。</section>
  return <RequirementEditForm task={task.data} />
}
