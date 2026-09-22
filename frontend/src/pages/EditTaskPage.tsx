import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import type { ProcurementRequirement, RankingCriterion, TaskDetail } from '../api/types'
import { RequirementFields, type RequirementFormValues } from '../components/RequirementFields'

function message(error: unknown) {
  return error instanceof ApiClientError ? error.message : '采购需求更新失败。'
}

function RequirementEditForm({ task }: { task: TaskDetail }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [value, setValue] = useState<RequirementFormValues>({
    ...task.requirement,
    required_quantity: String(task.requirement.required_quantity),
    planned_order_date: task.requirement.planned_order_date ?? '',
    secondary_preference: task.requirement.secondary_preference ?? '',
  })
  const update = useMutation({
    mutationFn: () => {
      const requirement: ProcurementRequirement = {
        ...value,
        required_quantity: Number(value.required_quantity),
        planned_order_date: value.planned_order_date || null,
        ranking_preference: value.ranking_preference as RankingCriterion,
        secondary_preference: (value.secondary_preference || null) as RankingCriterion | null,
      }
      return api.updateRequirement(task.task_id, task.task_revision, requirement, createIdempotencyKey())
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['tasks'] })
      void navigate(`/tasks/${task.task_id}`, { replace: true })
    },
  })
  function set<K extends keyof RequirementFormValues>(field: K, next: RequirementFormValues[K]) {
    setValue((current) => ({ ...current, [field]: next }))
    update.reset()
  }
  function submit(event: FormEvent) {
    event.preventDefault()
    if (window.confirm('保存后会推进任务版本、使旧结果失效；有正式报价时将立即排队全量重算。确认保存吗？')) update.mutate()
  }
  return <form className="requirement-form" onSubmit={submit}>
    <section className="page-heading"><div><p className="eyebrow">修改采购需求</p><h1>修改采购需求</h1><p>当前第 {task.task_revision} 版 · 已绑定的制度保持不变。</p></div><Link className="button button-secondary" to={`/tasks/${task.task_id}`}>取消</Link></section>
    {task.policy_binding && <div className="run-notice">绑定制度：{task.policy_binding.policy_set_version} / {task.policy_binding.policy_index_version} · {task.policy_binding.category} · {task.policy_binding.region}</div>}
    <RequirementFields value={value} onChange={(field, next) => {
      if (field === 'ranking_preference' && value.secondary_preference === next) {
        setValue((current) => ({ ...current, ranking_preference: String(next), secondary_preference: '' }))
        update.reset()
        return
      }
      set(field, next)
    }} />
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
