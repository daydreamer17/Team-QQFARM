import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import { TaskWorkspaceHeader } from '../components/TaskWorkspaceHeader'

export function TaskPage() {
  const { taskId = '' } = useParams()
  const task = useQuery({
    queryKey: ['tasks', taskId],
    queryFn: () => api.getTask(taskId),
    enabled: Boolean(taskId),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === 'QUEUED' || status === 'RUNNING' ? 2_000 : false
    },
  })
  const [showAbandon, setShowAbandon] = useState(false)
  const [abandonReason, setAbandonReason] = useState('')
  const abandon = useMutation({
    mutationFn: () => api.abandonTask(taskId, task.data!.task_revision, abandonReason.trim(), createIdempotencyKey()),
    onSuccess: async () => {
      setShowAbandon(false)
      setAbandonReason('')
      await task.refetch()
    },
  })

  if (task.isPending) {
    return <section className="card loading-panel">正在读取任务…</section>
  }

  if (task.isError) {
    const message = task.error instanceof ApiClientError
      ? task.error.message
      : '任务读取失败。'
    return (
      <section className="card error-panel" role="alert">
        <p className="eyebrow">TASK ERROR</p>
        <h1>无法读取任务</h1>
        <p>{message}</p>
        <div className="inline-actions">
          <button className="button button-secondary" type="button" onClick={() => void task.refetch()}>
            重新读取
          </button>
          <Link className="button button-secondary" to="/">返回工作台</Link>
        </div>
      </section>
    )
  }

  const requirement = task.data.requirement
  const reviewBlocked =
    task.data.status === 'NEEDS_INPUT' ||
    (task.data.status === 'FAILED' &&
      task.data.current_job?.error_code === 'review_required') ||
    Boolean(task.data.current_job?.correction_batch_incomplete)

  return (
    <div className="page-stack">
      <TaskWorkspaceHeader
        taskId={task.data.task_id}
        scenarioId={task.data.scenario_id}
        title={requirement.manufacturer_part_number}
        subtitle={`${requirement.required_quantity} ${requirement.quantity_unit} · ${requirement.currency} · 最晚交付 ${requirement.delivery_deadline}`}
        status={task.data.status}
        revision={task.data.task_revision}
        resultId={task.data.current_result_id}
        quoteCount={task.data.quotes.length}
        summaryComplete={task.data.summary_completed}
        progress={task.data.progress}
        reviewBlocked={reviewBlocked}
        policyReviewBlocked={task.data.current_issue?.issue_type === 'POLICY_EVIDENCE_REVIEW'}
        active="overview"
      />

      <section>
        <div className="section-heading">
          <div>
            <p className="eyebrow">REQUIREMENT SNAPSHOT</p>
            <h2>采购需求</h2>
          </div>
        </div>
        <dl className="detail-grid">
          <div><dt>场景编号</dt><dd>{task.data.scenario_id ?? '—'}</dd></div>
          <div><dt>制造商</dt><dd>{requirement.manufacturer}</dd></div>
          <div><dt>制造商料号</dt><dd>{requirement.manufacturer_part_number}</dd></div>
          <div><dt>封装 / 版本</dt><dd>{requirement.package} / {requirement.revision}</dd></div>
          <div><dt>物料状态</dt><dd>{requirement.condition}</dd></div>
          <div><dt>允许替代料</dt><dd>{requirement.allow_substitutes ? '是' : '否'}</dd></div>
          <div><dt>需求数量</dt><dd>{requirement.required_quantity} {requirement.quantity_unit}</dd></div>
          <div><dt>基础单位</dt><dd>{requirement.base_unit}</dd></div>
          <div><dt>预算</dt><dd>{requirement.currency} {requirement.budget_amount}</dd></div>
          <div><dt>预算包含运费</dt><dd>{requirement.includes_shipping ? '是' : '否'}</dd></div>
          <div><dt>成本比较口径</dt><dd>{requirement.tax_mode}</dd></div>
          <div><dt>其他费用要求</dt><dd>{requirement.other_fees_required ? '需要' : '不需要'}</dd></div>
          <div><dt>计划下单日期</dt><dd>{requirement.planned_order_date ?? '—'}</dd></div>
          <div><dt>交付截止日期</dt><dd>{requirement.delivery_deadline}</dd></div>
          <div><dt>交付地点</dt><dd>{requirement.delivery_location}</dd></div>
          <div><dt>主要排序偏好</dt><dd>{requirement.ranking_preference}</dd></div>
          <div><dt>次要偏好</dt><dd>{requirement.secondary_preference ?? '—'}</dd></div>
          <div><dt>制度绑定</dt><dd>{task.data.policy_binding ? `${task.data.policy_binding.policy_set_version} / ${task.data.policy_binding.policy_index_version}` : '未绑定'}</dd></div>
          <div><dt>制度范围</dt><dd>{task.data.policy_binding ? `${task.data.policy_binding.category} · ${task.data.policy_binding.region}` : '未执行制度检索'}</dd></div>
        </dl>
        <div className="requirement-actions">
          <div className="inline-actions">
            {task.data.status !== 'ABANDONED' && <Link className="button button-secondary" to={`/tasks/${taskId}/edit`}>修改采购需求</Link>}
            {task.data.status !== 'ABANDONED' && <button className="button button-danger" type="button" onClick={() => setShowAbandon(true)}>废弃任务</button>}
            {task.data.status === 'ABANDONED' && <span className="status-pill status-muted">任务已软废弃，历史记录保持只读</span>}
          </div>
          {showAbandon && <div className="card abandon-task-panel"><strong>确认废弃任务</strong><p>该操作不可恢复，但不会删除报价、原件、结果和审计记录。</p><label className="field"><span>废弃理由</span><textarea value={abandonReason} onChange={(event) => setAbandonReason(event.target.value)} minLength={3} maxLength={1000} /></label><div className="inline-actions"><button className="button button-secondary" type="button" onClick={() => setShowAbandon(false)}>取消</button><button className="button button-danger" type="button" disabled={abandonReason.trim().length < 3 || abandon.isPending} onClick={() => { if (window.confirm('确认将任务永久设为只读废弃状态吗？')) abandon.mutate() }}>{abandon.isPending ? '正在废弃…' : '确认废弃'}</button></div>{abandon.isError && <div className="form-error">{abandon.error instanceof ApiClientError ? abandon.error.message : '废弃任务失败。'}</div>}</div>}
        </div>
      </section>
    </div>
  )
}
