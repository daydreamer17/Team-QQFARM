import type { ReactNode } from 'react'

export interface RequirementFormValues {
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

type RequirementField = keyof RequirementFormValues

interface RequirementFieldsProps {
  value: RequirementFormValues
  onChange: <K extends RequirementField>(field: K, value: RequirementFormValues[K]) => void
  errors?: Partial<Record<RequirementField, string>>
  highlightedFields?: ReadonlySet<RequirementField>
  materialPrefix?: ReactNode
}

export function RequirementFields({
  value,
  onChange,
  errors = {},
  highlightedFields,
  materialPrefix,
}: RequirementFieldsProps) {
  const fieldClass = (field: RequirementField, extra = '') => [
    'field',
    extra,
    errors[field] ? 'field-invalid' : '',
    highlightedFields?.has(field) ? 'field-autofilled' : '',
  ].filter(Boolean).join(' ')
  const error = (field: RequirementField) => errors[field]
    ? <small className="field-error-text">{errors[field]}</small>
    : null

  return <>
    <fieldset className="form-section">
      <legend>任务与物料</legend>
      <div className="form-grid">
        {materialPrefix}
        <label className={fieldClass('manufacturer')}><span>制造商</span><input required aria-invalid={Boolean(errors.manufacturer)} value={value.manufacturer} onChange={(event) => onChange('manufacturer', event.target.value)} />{error('manufacturer')}</label>
        <label className={fieldClass('manufacturer_part_number')}><span>制造商料号</span><input required aria-invalid={Boolean(errors.manufacturer_part_number)} value={value.manufacturer_part_number} onChange={(event) => onChange('manufacturer_part_number', event.target.value)} />{error('manufacturer_part_number')}</label>
        <label className={fieldClass('package')}><span>封装</span><input required aria-invalid={Boolean(errors.package)} value={value.package} onChange={(event) => onChange('package', event.target.value)} />{error('package')}</label>
        <label className={fieldClass('revision')}><span>版本</span><input required aria-invalid={Boolean(errors.revision)} value={value.revision} onChange={(event) => onChange('revision', event.target.value)} />{error('revision')}</label>
        <label className={fieldClass('condition')}><span>物料状态</span><input required aria-invalid={Boolean(errors.condition)} value={value.condition} onChange={(event) => onChange('condition', event.target.value)} />{error('condition')}</label>
        <label className="field checkbox-field"><input type="checkbox" checked={value.allow_substitutes} onChange={(event) => onChange('allow_substitutes', event.target.checked)} /><span>允许替代料</span></label>
      </div>
    </fieldset>

    <fieldset className="form-section">
      <legend>数量与预算</legend>
      <div className="form-grid">
        <label className={fieldClass('base_unit')}><span>基础单位</span><input required aria-invalid={Boolean(errors.base_unit)} value={value.base_unit} onChange={(event) => onChange('base_unit', event.target.value)} />{error('base_unit')}</label>
        <label className={fieldClass('required_quantity')}><span>需求数量</span><input required min="1" aria-invalid={Boolean(errors.required_quantity)} inputMode="numeric" type="number" value={value.required_quantity} onChange={(event) => onChange('required_quantity', event.target.value)} />{error('required_quantity')}</label>
        <label className={fieldClass('quantity_unit')}><span>数量单位</span><input required aria-invalid={Boolean(errors.quantity_unit)} value={value.quantity_unit} onChange={(event) => onChange('quantity_unit', event.target.value)} />{error('quantity_unit')}</label>
        <label className={fieldClass('budget_amount')}><span>预算金额</span><input required aria-invalid={Boolean(errors.budget_amount)} inputMode="decimal" value={value.budget_amount} onChange={(event) => onChange('budget_amount', event.target.value)} />{error('budget_amount') ?? <small>非负十进制字符串，最多两位小数</small>}</label>
        <label className={fieldClass('currency')}><span>币种</span><select value={value.currency} onChange={(event) => onChange('currency', event.target.value)}><option value="SGD">SGD</option><option value="USD">USD</option></select></label>
        <label className={fieldClass('tax_mode')}><span>税费口径</span><select value={value.tax_mode} onChange={(event) => onChange('tax_mode', event.target.value)}><option value="EXCLUDED">不含税</option><option value="INCLUDED">已含税</option><option value="NOT_APPLICABLE">不适用</option></select></label>
        <label className="field checkbox-field"><input type="checkbox" checked={value.includes_shipping} onChange={(event) => onChange('includes_shipping', event.target.checked)} /><span>预算包含运费</span></label>
        <label className="field checkbox-field"><input type="checkbox" checked={value.other_fees_required} onChange={(event) => onChange('other_fees_required', event.target.checked)} /><span>要求计入其他费用</span></label>
      </div>
    </fieldset>

    <fieldset className="form-section">
      <legend>交付与排序</legend>
      <div className="form-grid">
        <label className={fieldClass('planned_order_date')}><span>计划下单日期 <small>可选</small></span><input type="date" value={value.planned_order_date} onChange={(event) => onChange('planned_order_date', event.target.value)} />{error('planned_order_date')}</label>
        <label className={fieldClass('delivery_deadline')}><span>交付截止日期</span><input required aria-invalid={Boolean(errors.delivery_deadline)} type="date" value={value.delivery_deadline} onChange={(event) => onChange('delivery_deadline', event.target.value)} />{error('delivery_deadline')}</label>
        <label className={fieldClass('delivery_location', 'field-wide')}><span>交付地点</span><input required aria-invalid={Boolean(errors.delivery_location)} value={value.delivery_location} onChange={(event) => onChange('delivery_location', event.target.value)} />{error('delivery_location')}</label>
        <label className={fieldClass('ranking_preference')}><span>主要排序偏好</span><select value={value.ranking_preference} onChange={(event) => onChange('ranking_preference', event.target.value)}><option value="LOWEST_CONFIRMED_TOTAL_COST">最低已确认总成本</option><option value="FASTEST_CONFIRMED_DELIVERY">最快已确认交付</option></select>{error('ranking_preference')}</label>
        <label className={fieldClass('secondary_preference')}><span>次要偏好 <small>可选</small></span><select aria-invalid={Boolean(errors.secondary_preference)} value={value.secondary_preference} onChange={(event) => onChange('secondary_preference', event.target.value)}><option value="">无</option><option value="LOWEST_CONFIRMED_TOTAL_COST" disabled={value.ranking_preference === 'LOWEST_CONFIRMED_TOTAL_COST'}>最低已确认总成本</option><option value="FASTEST_CONFIRMED_DELIVERY" disabled={value.ranking_preference === 'FASTEST_CONFIRMED_DELIVERY'}>最快已确认交付</option></select>{error('secondary_preference')}</label>
      </div>
    </fieldset>
  </>
}
