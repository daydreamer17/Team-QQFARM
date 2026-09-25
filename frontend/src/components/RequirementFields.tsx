import { RankingCriterionSelect } from './RankingCriterionSelect'

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
}

export function RequirementFields({
  value,
  onChange,
  errors = {},
  highlightedFields,
}: RequirementFieldsProps) {
  const historyApplicable = value.manufacturer_part_number.trim() === 'QW-MCU9-DEMO'
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
      <legend>Task and item</legend>
      <div className="form-grid">
        <label className={fieldClass('manufacturer')}><span>Manufacturer</span><input required aria-invalid={Boolean(errors.manufacturer)} value={value.manufacturer} onChange={(event) => onChange('manufacturer', event.target.value)} />{error('manufacturer')}</label>
        <label className={fieldClass('manufacturer_part_number')}><span>Manufacturer part number</span><input required aria-invalid={Boolean(errors.manufacturer_part_number)} value={value.manufacturer_part_number} onChange={(event) => onChange('manufacturer_part_number', event.target.value)} />{error('manufacturer_part_number')}</label>
        <label className={fieldClass('package')}><span>Package</span><input required aria-invalid={Boolean(errors.package)} value={value.package} onChange={(event) => onChange('package', event.target.value)} />{error('package')}</label>
        <label className={fieldClass('revision')}><span>Item revision</span><input required aria-invalid={Boolean(errors.revision)} value={value.revision} onChange={(event) => onChange('revision', event.target.value)} />{error('revision')}</label>
        <label className={fieldClass('condition')}><span>Item condition</span><select required aria-invalid={Boolean(errors.condition)} value={value.condition} onChange={(event) => onChange('condition', event.target.value)}><option value="">Select item condition</option><option value="NEW">New</option><option value="REFURBISHED">Refurbished</option><option value="USED">Used</option>{value.condition && !['NEW', 'REFURBISHED', 'USED'].includes(value.condition) && <option value={value.condition}>{value.condition}</option>}</select>{error('condition')}</label>
        <label className="field checkbox-field"><input type="checkbox" checked={value.allow_substitutes} onChange={(event) => onChange('allow_substitutes', event.target.checked)} /><span>Allow Substitutes</span></label>
      </div>
    </fieldset>

    <fieldset className="form-section">
      <legend>Quantity and budget</legend>
      <div className="form-grid">
        <label className={fieldClass('required_quantity')}><span>Required Quantity</span><input required min="1" aria-invalid={Boolean(errors.required_quantity)} inputMode="numeric" type="number" value={value.required_quantity} onChange={(event) => onChange('required_quantity', event.target.value)} />{error('required_quantity')}</label>
        <label className={fieldClass('quantity_unit')}><span>Quantity Unit</span><input required aria-invalid={Boolean(errors.quantity_unit)} value={value.quantity_unit} onChange={(event) => onChange('quantity_unit', event.target.value)} />{error('quantity_unit')}</label>
        <label className={fieldClass('budget_amount')}><span>Budget amount</span><input required aria-invalid={Boolean(errors.budget_amount)} inputMode="decimal" value={value.budget_amount} onChange={(event) => onChange('budget_amount', event.target.value)} />{error('budget_amount') ?? <small>Use no more than two decimal places</small>}</label>
        <label className={fieldClass('currency')}><span>Currency</span><select required aria-invalid={Boolean(errors.currency)} value={value.currency} onChange={(event) => onChange('currency', event.target.value)}><option value="">Select a currency</option><option value="SGD">SGD</option><option value="USD">USD</option></select>{error('currency')}</label>
        <label className={fieldClass('tax_mode')}><span>Cost Comparison Basis</span><select required aria-invalid={Boolean(errors.tax_mode)} value={value.tax_mode} onChange={(event) => onChange('tax_mode', event.target.value)}><option value="">Select cost comparison basis</option><option value="EXCLUDED">Compare tax-exclusive prices</option><option value="INCLUDED">Compare tax-inclusive prices</option><option value="NOT_APPLICABLE">Tax not applicable</option></select>{error('tax_mode')}</label>
        <label className="field checkbox-field"><input type="checkbox" checked={value.includes_shipping} onChange={(event) => onChange('includes_shipping', event.target.checked)} /><span>Budget includes shipping</span></label>
        <label className="field checkbox-field"><input type="checkbox" checked={value.other_fees_required} onChange={(event) => onChange('other_fees_required', event.target.checked)} /><span>Include Other Fees</span></label>
      </div>
    </fieldset>

    <fieldset className="form-section">
      <legend>Delivery and ranking</legend>
      <div className="form-grid">
        <label className={fieldClass('planned_order_date')}><span>Planned Order Date <small>Optional</small></span><input type="date" value={value.planned_order_date} onChange={(event) => onChange('planned_order_date', event.target.value)} />{error('planned_order_date')}</label>
        <label className={fieldClass('delivery_deadline')}><span>Delivery Deadline</span><input required aria-invalid={Boolean(errors.delivery_deadline)} type="date" value={value.delivery_deadline} onChange={(event) => onChange('delivery_deadline', event.target.value)} />{error('delivery_deadline')}</label>
        <label className={fieldClass('delivery_location', 'field-wide')}><span>Delivery Location</span><input required aria-invalid={Boolean(errors.delivery_location)} value={value.delivery_location} onChange={(event) => onChange('delivery_location', event.target.value)} />{error('delivery_location')}</label>
        <label className={fieldClass('ranking_preference')}><span>Primary ranking criterion</span><RankingCriterionSelect required historyApplicable={historyApplicable} invalid={Boolean(errors.ranking_preference)} value={value.ranking_preference} exclude={value.secondary_preference} onChange={(next) => onChange('ranking_preference', next)} />{error('ranking_preference')}</label>
        <label className={fieldClass('secondary_preference')}><span>Secondary ranking criterion <small>Optional; used only when the primary criterion is tied</small></span><RankingCriterionSelect allowEmpty historyApplicable={historyApplicable} invalid={Boolean(errors.secondary_preference)} value={value.secondary_preference} exclude={value.ranking_preference} onChange={(next) => onChange('secondary_preference', next)} />{error('secondary_preference')}</label>
      </div>
    </fieldset>

  </>
}
