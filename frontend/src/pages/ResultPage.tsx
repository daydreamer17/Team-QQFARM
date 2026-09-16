import { useQueries, useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api, ApiClientError } from '../api/client'
import type {
  FieldEvidence,
  QuoteFieldsResponse,
  ResultReason,
  SupplierComparisonResult,
} from '../api/types'

const statusLabels: Record<string, string> = {
  FEASIBLE: '符合要求',
  INFEASIBLE: '不符合要求',
  PENDING: '等待确认',
}

function errorMessage(error: unknown) {
  return error instanceof ApiClientError ? error.message : '结果读取失败。'
}

function valueText(value: unknown) {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}

function displayDate(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)
}

function reasonList(title: string, emptyText: string, reasons: ResultReason[], tone: string) {
  return (
    <section className={'reason-panel reason-panel-' + tone}>
      <h4>{title}</h4>
      {reasons.length > 0 ? (
        <ul>
          {reasons.map((reason, index) => (
            <li key={reason.code + index}>
              <strong>{reason.code}</strong>
              <span>{reason.message}</span>
              {reason.fields.length > 0 && (
                <small>相关字段：{reason.fields.join('、')}</small>
              )}
            </li>
          ))}
        </ul>
      ) : <p>{emptyText}</p>}
    </section>
  )
}

function evidenceLocation(evidence: FieldEvidence) {
  const parts: string[] = []
  if (evidence.page_number !== null) parts.push('第 ' + evidence.page_number + ' 页')
  if (evidence.row_number !== null) parts.push('第 ' + evidence.row_number + ' 行')
  if (evidence.column_name) parts.push('列 ' + evidence.column_name)
  return parts.join(' · ') || evidence.kind || '来源位置'
}

function FieldsTable({ fields }: { fields: QuoteFieldsResponse }) {
  return (
    <section className='fields-section'>
      <div className='section-heading compact-heading'>
        <div>
          <p className='eyebrow'>NORMALIZED FIELDS</p>
          <h3>解析字段与证据</h3>
        </div>
        <span>{fields.fields.length} 个字段 · {fields.review_status ?? '未审核'}</span>
      </div>
      <div className='fields-table-wrap'>
        <table className='fields-table'>
          <thead>
            <tr>
              <th>标准字段</th>
              <th>原始表达</th>
              <th>标准化值</th>
              <th>单位</th>
              <th>状态 / 来源</th>
              <th>证据</th>
            </tr>
          </thead>
          <tbody>
            {fields.fields.map((field) => (
              <tr key={field.field_name}>
                <td><code>{field.field_name}</code></td>
                <td>{valueText(field.raw_value)}</td>
                <td className='normalized-value'>{valueText(field.normalized_value)}</td>
                <td>{valueText(field.unit)}</td>
                <td>
                  <span className={'field-status field-status-' + field.validation_status.toLowerCase()}>
                    {field.validation_status}
                  </span>
                  <small className='field-origin'>{valueText(field.origin)}</small>
                </td>
                <td>
                  {field.evidence.length > 0 ? (
                    <details className='evidence-details'>
                      <summary>{field.evidence.length} 条</summary>
                      <div className='evidence-list'>
                        {field.evidence.map((evidence, index) => (
                          <article key={(evidence.source_id ?? 'source') + index}>
                            <div>
                              <code>{evidence.source_id ?? '—'}</code>
                              <span>{evidenceLocation(evidence)}</span>
                            </div>
                            <blockquote>{evidence.quoted_text ?? '无引用片段'}</blockquote>
                          </article>
                        ))}
                      </div>
                    </details>
                  ) : <span className='muted-value'>无证据</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export function ResultPage() {
  const { taskId = '', resultId = '' } = useParams()
  const resultQuery = useQuery({
    queryKey: ['tasks', taskId, 'results', resultId],
    queryFn: () => api.getResult(taskId, resultId),
    enabled: Boolean(taskId && resultId),
  })
  const suppliers = resultQuery.data?.result.supplier_results ?? []
  const fieldQueries = useQueries({
    queries: suppliers.map((supplier) => ({
      queryKey: ['tasks', taskId, 'quotes', supplier.quote_id, 'fields'],
      queryFn: () => api.getQuoteFields(taskId, supplier.quote_id),
    })),
  })

  if (resultQuery.isPending) {
    return <section className='card loading-panel'>正在读取解析与比较结果…</section>
  }
  if (resultQuery.isError) {
    return (
      <section className='card error-panel' role='alert'>
        <p className='eyebrow'>RESULT ERROR</p>
        <h1>无法读取结果</h1>
        <p>{errorMessage(resultQuery.error)}</p>
        <Link className='button button-secondary' to={'/tasks/' + taskId}>返回任务</Link>
      </section>
    )
  }

  const payload = resultQuery.data.result
  const recommended = new Set(payload.recommended_quote_ids)
  const recommendedNames = suppliers
    .filter((supplier) => recommended.has(supplier.quote_id))
    .map((supplier) => supplier.supplier_name)

  return (
    <div className='page-stack result-page'>
      <section className='result-header'>
        <div>
          <p className='eyebrow'>AUDITABLE RESULT</p>
          <h1>报价分析与推荐结果</h1>
          <p className='task-id'>{resultQuery.data.result_id}</p>
        </div>
        <Link className='button button-secondary' to={'/tasks/' + taskId}>返回任务</Link>
      </section>

      {!resultQuery.data.is_current && (
        <div className='run-notice'>这是历史结果，不代表任务当前版本。</div>
      )}

      <section className='result-summary-grid'>
        <article className='result-metric'><span>整体结论</span><strong>{payload.disposition}</strong></article>
        <article className='result-metric'>
          <span>推荐报价</span>
          <strong>{recommendedNames.length > 0 ? recommendedNames.join('、') : '暂无最终推荐'}</strong>
        </article>
        <article className='result-metric'>
          <span>最终结果可发布</span>
          <strong>{payload.final_recommendation_allowed ? '是' : '否'}</strong>
        </article>
        <article className='result-metric'>
          <span>报价数量</span><strong>{payload.supplier_results.length} 份</strong>
          <small>{payload.rule_version} · {displayDate(payload.evaluated_at)}</small>
        </article>
      </section>

      <p className='result-boundary'>金额、数量、可行性和推荐均来自后端冻结结果；前端只负责展示，不重新计算。</p>

      {suppliers.map((supplier: SupplierComparisonResult, index) => {
        const fieldsQuery = fieldQueries[index]
        return (
          <article className='card supplier-result-card' key={supplier.quote_id}>
            <header className='supplier-result-header'>
              <div>
                <p className='eyebrow'>SUPPLIER RESULT</p>
                <h2>{supplier.supplier_name}</h2>
                <p className='task-id'>{supplier.quote_id} · Version {supplier.quote_version}</p>
              </div>
              <div className='task-badges'>
                {recommended.has(supplier.quote_id) && <span className='status-pill status-ready'>推荐</span>}
                <span className={'supplier-status supplier-status-' + supplier.status.toLowerCase()}>
                  {statusLabels[supplier.status] ?? supplier.status}
                </span>
              </div>
            </header>

            <dl className='cost-grid'>
              <div><dt>货款</dt><dd>{valueText(supplier.goods_cost)}</dd></div>
              <div><dt>已知成本小计</dt><dd>{valueText(supplier.known_cost_subtotal)}</dd></div>
              <div><dt>确认总成本</dt><dd>{valueText(supplier.total_cost)}</dd></div>
              <div><dt>实际采购数量</dt><dd>{valueText(supplier.actual_quantity)}</dd></div>
              <div><dt>预计到货</dt><dd>{valueText(supplier.estimated_arrival_date)}</dd></div>
            </dl>

            <div className='reason-grid'>
              {reasonList('不符合项', '没有发现不符合项。', supplier.failed_reasons, 'failed')}
              {reasonList('待确认项', '没有待确认项。', supplier.pending_reasons, 'pending')}
            </div>

            {fieldsQuery?.isPending && <div className='fields-loading'>正在读取字段和来源证据…</div>}
            {fieldsQuery?.isError && (
              <div className='form-error compact-error'>
                <strong>字段结果读取失败</strong><p>{errorMessage(fieldsQuery.error)}</p>
              </div>
            )}
            {fieldsQuery?.data && <FieldsTable fields={fieldsQuery.data} />}
          </article>
        )
      })}
    </div>
  )
}
