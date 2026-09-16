import { useMutation } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, ApiClientError, createIdempotencyKey } from '../api/client'
import type { CreateTaskRequest } from '../api/types'

interface FormState {
  scenario_id: string
  manufacturer: string
  manufacturer_part_number: string
  package: string
  revision: string
  condition: string
  allow_substitutes: boolean
  base_unit: string
  required_quantity: string
  quantity_unit: string
  budget_amount: string
  currency: string
  includes_shipping: boolean
  tax_mode: string
  other_fees_required: boolean
  planned_order_date: string
  delivery_deadline: string
  delivery_location: string
  ranking_preference: string
  secondary_preference: string
}

interface Submission {
  body: CreateTaskRequest
  idempotencyKey: string
}

const initialForm: FormState = {
  scenario_id: 'MCU-DEMO-001',
  manufacturer: 'QQ Demo Components',
  manufacturer_part_number: 'QW-MCU9-DEMO',
  package: 'QFN-32',
  revision: 'R1',
  condition: 'NEW',
  allow_substitutes: false,
  base_unit: 'piece',
  required_quantity: '1000',
  quantity_unit: 'piece',
  budget_amount: '8000.00',
  currency: 'SGD',
  includes_shipping: true,
  tax_mode: 'EXCLUDED',
  other_fees_required: false,
  planned_order_date: '2026-09-14',
  delivery_deadline: '2026-09-19',
  delivery_location: 'Singapore',
  ranking_preference: 'LOWEST_CONFIRMED_TOTAL_COST',
  secondary_preference: '',
}

function errorMessage(error: unknown) {
  if (error instanceof ApiClientError) return error.message
  return '任务创建失败，请稍后重试。'
}

export function NewTaskPage() {
  const navigate = useNavigate()
  const [form, setForm] = useState(initialForm)
  const [localError, setLocalError] = useState('')
  const [lastSubmission, setLastSubmission] = useState<Submission | null>(null)

  const createTask = useMutation({
    mutationFn: ({ body, idempotencyKey }: Submission) =>
      api.createTask(body, idempotencyKey),
    onSuccess: (task) => {
      void navigate(`/tasks/${task.task_id}`, { replace: true })
    },
  })

  function update<K extends keyof FormState>(field: K, value: FormState[K]) {
    setForm((current) => ({ ...current, [field]: value }))
    setLocalError('')
    createTask.reset()
  }

  function updateRankingPreference(value: string) {
    setForm((current) => ({
      ...current,
      ranking_preference: value,
      secondary_preference:
        current.secondary_preference === value
          ? ''
          : current.secondary_preference,
    }))
    setLocalError('')
    createTask.reset()
  }

  function buildSubmission(): Submission | null {
    const requiredQuantity = Number(form.required_quantity)
    if (!Number.isInteger(requiredQuantity) || requiredQuantity <= 0) {
      setLocalError('需求数量必须是大于 0 的整数。')
      return null
    }
    if (!/^\d+(\.\d{1,2})?$/.test(form.budget_amount)) {
      setLocalError('预算金额必须是非负十进制字符串，最多保留两位小数。')
      return null
    }
    if (
      form.planned_order_date &&
      form.delivery_deadline < form.planned_order_date
    ) {
      setLocalError('交付截止日期不能早于计划下单日期。')
      return null
    }
    if (form.secondary_preference === form.ranking_preference) {
      setLocalError('主要排序偏好和次要偏好不能相同。')
      return null
    }

    return {
      idempotencyKey: createIdempotencyKey(),
      body: {
        scenario_id: form.scenario_id.trim() || null,
        requirement: {
          manufacturer: form.manufacturer.trim(),
          manufacturer_part_number: form.manufacturer_part_number.trim(),
          package: form.package.trim(),
          revision: form.revision.trim(),
          condition: form.condition.trim(),
          allow_substitutes: form.allow_substitutes,
          base_unit: form.base_unit.trim(),
          required_quantity: requiredQuantity,
          quantity_unit: form.quantity_unit.trim(),
          budget_amount: form.budget_amount,
          currency: form.currency,
          includes_shipping: form.includes_shipping,
          tax_mode: form.tax_mode,
          other_fees_required: form.other_fees_required,
          planned_order_date: form.planned_order_date || null,
          delivery_deadline: form.delivery_deadline,
          delivery_location: form.delivery_location.trim(),
          ranking_preference: form.ranking_preference,
          secondary_preference: form.secondary_preference.trim() || null,
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
    <div className="page-stack">
      <section className="page-heading">
        <div>
          <p className="eyebrow">NEW PROCUREMENT TASK</p>
          <h1>创建采购任务</h1>
          <p>按照后端权威契约录入采购需求；当前已预填主演示场景。</p>
        </div>
        <Link className="button button-secondary" to="/">返回工作台</Link>
      </section>

      <form className="requirement-form" onSubmit={handleSubmit}>
        <fieldset className="form-section">
          <legend>任务与物料</legend>
          <div className="form-grid">
            <label className="field field-wide">
              <span>场景编号 <small>可选</small></span>
              <input value={form.scenario_id} onChange={(event) => update('scenario_id', event.target.value)} />
            </label>
            <label className="field">
              <span>制造商</span>
              <input required value={form.manufacturer} onChange={(event) => update('manufacturer', event.target.value)} />
            </label>
            <label className="field">
              <span>制造商料号</span>
              <input required value={form.manufacturer_part_number} onChange={(event) => update('manufacturer_part_number', event.target.value)} />
            </label>
            <label className="field">
              <span>封装</span>
              <input required value={form.package} onChange={(event) => update('package', event.target.value)} />
            </label>
            <label className="field">
              <span>版本</span>
              <input required value={form.revision} onChange={(event) => update('revision', event.target.value)} />
            </label>
            <label className="field">
              <span>物料状态</span>
              <input required value={form.condition} onChange={(event) => update('condition', event.target.value)} />
            </label>
            <label className="field checkbox-field">
              <input type="checkbox" checked={form.allow_substitutes} onChange={(event) => update('allow_substitutes', event.target.checked)} />
              <span>允许替代料</span>
            </label>
          </div>
        </fieldset>

        <fieldset className="form-section">
          <legend>数量与预算</legend>
          <div className="form-grid">
            <label className="field">
              <span>基础单位</span>
              <input required value={form.base_unit} onChange={(event) => update('base_unit', event.target.value)} />
            </label>
            <label className="field">
              <span>需求数量</span>
              <input required inputMode="numeric" min="1" step="1" type="number" value={form.required_quantity} onChange={(event) => update('required_quantity', event.target.value)} />
            </label>
            <label className="field">
              <span>数量单位</span>
              <input required value={form.quantity_unit} onChange={(event) => update('quantity_unit', event.target.value)} />
            </label>
            <label className="field">
              <span>预算金额</span>
              <input required inputMode="decimal" value={form.budget_amount} onChange={(event) => update('budget_amount', event.target.value)} />
              <small>以十进制字符串提交，例如 8000.00</small>
            </label>
            <label className="field">
              <span>币种</span>
              <select value={form.currency} onChange={(event) => update('currency', event.target.value)}>
                <option value="SGD">SGD</option>
                <option value="USD">USD</option>
              </select>
            </label>
            <label className="field">
              <span>税费口径</span>
              <select value={form.tax_mode} onChange={(event) => update('tax_mode', event.target.value)}>
                <option value="EXCLUDED">不含税</option>
                <option value="INCLUDED">已含税</option>
                <option value="NOT_APPLICABLE">不适用</option>
              </select>
            </label>
            <label className="field checkbox-field">
              <input type="checkbox" checked={form.includes_shipping} onChange={(event) => update('includes_shipping', event.target.checked)} />
              <span>预算包含运费</span>
            </label>
            <label className="field checkbox-field">
              <input type="checkbox" checked={form.other_fees_required} onChange={(event) => update('other_fees_required', event.target.checked)} />
              <span>要求计入其他费用</span>
            </label>
          </div>
        </fieldset>

        <fieldset className="form-section">
          <legend>交付与排序</legend>
          <div className="form-grid">
            <label className="field">
              <span>计划下单日期 <small>可选</small></span>
              <input type="date" value={form.planned_order_date} onChange={(event) => update('planned_order_date', event.target.value)} />
            </label>
            <label className="field">
              <span>交付截止日期</span>
              <input required type="date" value={form.delivery_deadline} onChange={(event) => update('delivery_deadline', event.target.value)} />
            </label>
            <label className="field field-wide">
              <span>交付地点</span>
              <input required value={form.delivery_location} onChange={(event) => update('delivery_location', event.target.value)} />
            </label>
            <label className="field">
              <span>主要排序偏好</span>
              <select value={form.ranking_preference} onChange={(event) => updateRankingPreference(event.target.value)}>
                <option value="LOWEST_CONFIRMED_TOTAL_COST">最低已确认总成本</option>
                <option value="FASTEST_CONFIRMED_DELIVERY">最快已确认交付</option>
              </select>
            </label>
            <label className="field">
              <span>次要偏好 <small>可选</small></span>
              <select value={form.secondary_preference} onChange={(event) => update('secondary_preference', event.target.value)}>
                <option value="">无</option>
                <option
                  value="LOWEST_CONFIRMED_TOTAL_COST"
                  disabled={form.ranking_preference === 'LOWEST_CONFIRMED_TOTAL_COST'}
                >
                  最低已确认总成本
                </option>
                <option
                  value="FASTEST_CONFIRMED_DELIVERY"
                  disabled={form.ranking_preference === 'FASTEST_CONFIRMED_DELIVERY'}
                >
                  最快已确认交付
                </option>
              </select>
            </label>
          </div>
        </fieldset>

        {(localError || createTask.isError) && (
          <div className="form-error" role="alert">
            <div>
              <strong>无法创建任务</strong>
              <p>{localError || errorMessage(createTask.error)}</p>
              {createTask.error instanceof ApiClientError && createTask.error.requestId && (
                <small>Request ID: {createTask.error.requestId}</small>
              )}
            </div>
            {!localError && lastSubmission && (
              <button className="button button-secondary" type="button" onClick={() => createTask.mutate(lastSubmission)}>
                重试相同请求
              </button>
            )}
          </div>
        )}

        <div className="form-actions">
          <p>提交后将创建 revision 1；后续写操作必须携带最新 revision。</p>
          <button className="button button-submit" type="submit" disabled={createTask.isPending}>
            {createTask.isPending ? '正在创建…' : '创建任务'}
          </button>
        </div>
      </form>
    </div>
  )
}
