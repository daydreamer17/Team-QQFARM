import { Link } from 'react-router-dom'

export function NewTaskPage() {
  return (
    <div className="page-stack">
      <section className="page-heading">
        <div>
          <p className="eyebrow">NEW PROCUREMENT TASK</p>
          <h1>创建采购任务</h1>
          <p>
            下一步将在这里接入采购需求的 19 个权威字段和
            <code>POST /api/v1/tasks</code>。
          </p>
        </div>
        <Link className="button button-secondary" to="/">返回工作台</Link>
      </section>
      <section className="card contract-notice">
        <span className="notice-icon" aria-hidden="true">i</span>
        <div>
          <h2>接口优先</h2>
          <p>
            表单遵循后端 ProcurementRequirement 契约；金额保持十进制字符串，
            不在浏览器内自行计算成本或推荐结果。
          </p>
        </div>
      </section>
    </div>
  )
}
