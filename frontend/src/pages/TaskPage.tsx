import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api, ApiClientError } from '../api/client'

export function TaskPage() {
  const { taskId = '' } = useParams()
  const task = useQuery({
    queryKey: ['tasks', taskId],
    queryFn: () => api.getTask(taskId),
    enabled: Boolean(taskId),
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

  return (
    <div className="page-stack">
      <section className="task-header">
        <div>
          <p className="eyebrow">PROCUREMENT TASK</p>
          <h1>{requirement.manufacturer_part_number}</h1>
          <p className="task-id">{task.data.task_id}</p>
        </div>
        <div className="task-badges">
          <span className="status-pill status-ready">{task.data.status}</span>
          <span className="revision-badge">Revision {task.data.task_revision}</span>
        </div>
      </section>

      <section className="card next-action-card">
        <div>
          <p className="eyebrow">NEXT STEP</p>
          <h2>任务已经创建</h2>
          <p>当前需求已保存。下一阶段将接入供应商报价上传和运行触发。</p>
        </div>
        <button className="button button-secondary" type="button" disabled>
          上传报价（下一阶段）
        </button>
      </section>

      <section>
        <div className="section-heading">
          <div>
            <p className="eyebrow">REQUIREMENT SNAPSHOT</p>
            <h2>采购需求</h2>
          </div>
          <span>由后端返回</span>
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
          <div><dt>税费口径</dt><dd>{requirement.tax_mode}</dd></div>
          <div><dt>其他费用要求</dt><dd>{requirement.other_fees_required ? '需要' : '不需要'}</dd></div>
          <div><dt>计划下单日期</dt><dd>{requirement.planned_order_date ?? '—'}</dd></div>
          <div><dt>交付截止日期</dt><dd>{requirement.delivery_deadline}</dd></div>
          <div><dt>交付地点</dt><dd>{requirement.delivery_location}</dd></div>
          <div><dt>主要排序偏好</dt><dd>{requirement.ranking_preference}</dd></div>
          <div><dt>次要偏好</dt><dd>{requirement.secondary_preference ?? '—'}</dd></div>
        </dl>
      </section>
    </div>
  )
}
