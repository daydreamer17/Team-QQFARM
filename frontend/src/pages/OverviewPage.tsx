import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, ApiClientError } from '../api/client'

const workflow = [
  { step: '01', title: '录入采购需求', description: '建立采购任务并冻结当前需求版本。' },
  { step: '02', title: '上传供应商报价', description: '上传 PDF 或固定 CSV，并保留文件证据。' },
  { step: '03', title: '确认问题与证据', description: '处理缺失、冲突和 OCR 关键字段确认。' },
  { step: '04', title: '比较、审批与报告', description: '查看可行性、合规性和可审计推荐结果。' },
]

function getErrorMessage(error: unknown) {
  if (error instanceof ApiClientError) return error.message
  return '后端连接检查失败。'
}

export function OverviewPage() {
  const health = useQuery({
    queryKey: ['health', 'ready'],
    queryFn: api.healthReady,
    refetchInterval: 30_000,
  })
  const isReady = health.data?.status === 'ready'

  return (
    <div className="page-stack">
      <section className="hero-panel">
        <div>
          <p className="eyebrow">PROCUREMENT WORKSPACE</p>
          <h1>让每一项采购推荐都有依据。</h1>
          <p className="hero-copy">
            从采购需求和供应商报价出发，逐项核对字段、证据、成本与合规条件。
          </p>
        </div>
        <Link className="button button-primary" to="/tasks/new">创建采购任务</Link>
      </section>

      <section className="status-grid" aria-label="系统状态">
        <article className="card health-card">
          <div className="card-heading">
            <div>
              <p className="eyebrow">BACKEND</p>
              <h2>服务连接</h2>
            </div>
            <span className={`status-pill ${health.isPending ? 'status-pending' : isReady ? 'status-ready' : 'status-offline'}`}>
              {health.isPending ? '检查中' : isReady ? '已连接' : '未连接'}
            </span>
          </div>
          {isReady ? (
            <p className="status-message">FastAPI 与数据库已准备好，可以开始联调。</p>
          ) : (
            <p className="status-message">
              {health.isPending ? '正在请求 /health/ready…' : getErrorMessage(health.error)}
            </p>
          )}
          <button
            className="button button-secondary"
            type="button"
            onClick={() => void health.refetch()}
            disabled={health.isFetching}
          >
            {health.isFetching ? '检查中…' : '重新检查'}
          </button>
        </article>

        <article className="card boundary-card">
          <p className="eyebrow">FRONTEND BOUNDARY</p>
          <h2>前端不复制业务规则</h2>
          <p>金额、MOQ、日期、可行性、合规结论和审批有效性均以后端结果为准。</p>
        </article>
      </section>

      <section className="workflow-section">
        <div className="section-heading">
          <div>
            <p className="eyebrow">WORKFLOW</p>
            <h2>本轮实现路径</h2>
          </div>
          <span>4 个阶段</span>
        </div>
        <div className="workflow-grid">
          {workflow.map((item) => (
            <article className="workflow-card" key={item.step}>
              <span className="step-number">{item.step}</span>
              <h3>{item.title}</h3>
              <p>{item.description}</p>
            </article>
          ))}
        </div>
      </section>
    </div>
  )
}
