import type { QuoteDraftResponse, QuoteFieldSchemaResponse } from '../src/api/types'

export const quoteFieldNames = [
  'supplier_name', 'supplier_country', 'category', 'item', 'manufacturer',
  'manufacturer_part_number', 'package', 'revision', 'condition', 'currency',
  'unit_price', 'price_basis_quantity', 'price_basis_unit', 'packaging_type',
  'units_per_pack', 'order_multiple_units', 'moq_quantity', 'moq_unit',
  'shipping_fee_status', 'shipping_fee_amount', 'other_fees_status',
  'other_fees_amount', 'tax_mode', 'lead_time_days', 'day_basis',
  'delivery_semantics', 'start_event', 'payment_terms', 'quote_date', 'valid_until',
] as const

const alwaysRequired = new Set([
  'supplier_name', 'manufacturer', 'manufacturer_part_number', 'package', 'condition',
  'currency', 'unit_price', 'price_basis_quantity', 'price_basis_unit',
  'order_multiple_units', 'moq_quantity', 'moq_unit', 'shipping_fee_status',
  'other_fees_status', 'tax_mode', 'valid_until',
])

const optional = new Set(['supplier_country', 'category', 'item', 'payment_terms'])

const enumValues: Record<string, string[]> = {
  condition: ['NEW', 'REFURBISHED', 'USED'],
  price_basis_unit: ['piece'],
  packaging_type: ['piece', 'tray'],
  moq_unit: ['piece', 'tray'],
  shipping_fee_status: ['KNOWN_AMOUNT', 'FREE', 'INCLUDED', 'NOT_APPLICABLE', 'UNKNOWN'],
  other_fees_status: ['KNOWN_AMOUNT', 'FREE', 'INCLUDED', 'NOT_APPLICABLE', 'UNKNOWN'],
  tax_mode: ['NOT_APPLICABLE', 'INCLUDED', 'EXCLUDED'],
  day_basis: ['CALENDAR_DAYS', 'BUSINESS_DAYS'],
  delivery_semantics: ['ARRIVAL', 'SHIPMENT', 'NOT_APPLICABLE'],
  start_event: ['ORDER_DATE', 'PAYMENT_RECEIPT'],
}

const initialValues: Record<(typeof quoteFieldNames)[number], string | number | null> = {
  supplier_name: 'Redwood Components',
  supplier_country: 'USA',
  category: 'Electronics',
  item: 'MCU-9',
  manufacturer: 'QQ Demo Components',
  manufacturer_part_number: 'QW-MCU9-DEMO',
  package: 'QFN-32',
  revision: 'R1',
  condition: 'NEW',
  currency: 'SGD',
  unit_price: '6.80',
  price_basis_quantity: 1,
  price_basis_unit: 'piece',
  packaging_type: 'piece',
  units_per_pack: 1,
  order_multiple_units: 1,
  moq_quantity: 100,
  moq_unit: 'piece',
  shipping_fee_status: 'FREE',
  shipping_fee_amount: null,
  other_fees_status: 'NOT_APPLICABLE',
  other_fees_amount: null,
  tax_mode: 'NOT_APPLICABLE',
  lead_time_days: 10,
  day_basis: 'CALENDAR_DAYS',
  delivery_semantics: 'ARRIVAL',
  start_event: 'ORDER_DATE',
  payment_terms: 'Net 30',
  quote_date: '2026-09-13',
  valid_until: '2026-09-20',
}

function valueType(fieldName: string): string {
  if (['unit_price', 'shipping_fee_amount', 'other_fees_amount'].includes(fieldName)) return 'decimal string/blank'
  if (['price_basis_quantity', 'units_per_pack', 'order_multiple_units', 'moq_quantity', 'lead_time_days'].includes(fieldName)) return 'integer/blank'
  if (['quote_date', 'valid_until'].includes(fieldName)) return 'date/blank'
  if (enumValues[fieldName]) return 'enum/blank'
  return 'string/blank'
}

function group(fieldName: string): { id: string; label: string } {
  if (quoteFieldNames.indexOf(fieldName as (typeof quoteFieldNames)[number]) < 5) return { id: 'identity', label: '身份' }
  if (['manufacturer', 'manufacturer_part_number', 'package', 'revision', 'condition'].includes(fieldName)) return { id: 'specification', label: '规格' }
  if (['currency', 'unit_price', 'price_basis_quantity', 'price_basis_unit'].includes(fieldName)) return { id: 'price', label: '价格' }
  if (['packaging_type', 'units_per_pack', 'order_multiple_units'].includes(fieldName)) return { id: 'packaging', label: '包装' }
  if (['moq_quantity', 'moq_unit'].includes(fieldName)) return { id: 'moq', label: 'MOQ' }
  if (fieldName.includes('fee') || fieldName === 'tax_mode') return { id: 'fees', label: '费用' }
  if (['lead_time_days', 'day_basis', 'delivery_semantics', 'start_event'].includes(fieldName)) return { id: 'delivery', label: '交期' }
  return { id: 'commercial', label: '商务' }
}

export function makeQuoteFieldSchema(): QuoteFieldSchemaResponse {
  return {
    schema_version: 'quote-review-schema/1.0.0',
    dictionary_version: '1.2.0',
    dictionary_sha256: '0'.repeat(64),
    review_policy_version: 'quote-review-policy/1.0.0',
    fields: quoteFieldNames.map((fieldName) => {
      const currentGroup = group(fieldName)
      return {
        field_name: fieldName,
        label: fieldName,
        group_id: currentGroup.id,
        group_label: currentGroup.label,
        value_type: valueType(fieldName),
        editor: enumValues[fieldName] ? 'select' : valueType(fieldName).includes('date') ? 'date' : 'text',
        required_level: alwaysRequired.has(fieldName) ? 'Critical' : optional.has(fieldName) ? 'Optional' : 'Conditionally Critical',
        nullable: !alwaysRequired.has(fieldName),
        allowed_values: enumValues[fieldName] ?? null,
        minimum: fieldName === 'lead_time_days' ? 0 : valueType(fieldName).includes('integer') ? 1 : valueType(fieldName).includes('decimal') ? 0 : null,
        unit_kind: ['unit_price', 'shipping_fee_amount', 'other_fees_amount'].includes(fieldName) ? 'currency' : 'none',
        missing_handling: 'test',
        normalization_rule: '测试规范化规则',
        validation_boundary: '测试校验边界',
        evidence_requirement: '测试Evidence要求',
      }
    }),
    relation_groups: [
      { group_id: 'price_basis', kind: 'ALL_OR_NONE', field_names: ['unit_price', 'price_basis_quantity', 'price_basis_unit'], message: 'Unit Price、计价数量和计价单位必须同时有效。' },
      { group_id: 'money_currency', kind: 'MONEY_CURRENCY', field_names: ['currency', 'unit_price', 'shipping_fee_amount', 'other_fees_amount'], message: '金额Currency必须一致。' },
      { group_id: 'shipping_fee', kind: 'FEE_STATUS_AMOUNT', field_names: ['shipping_fee_status', 'shipping_fee_amount'], message: 'Shipping fee status and amount are inconsistent.' },
      { group_id: 'other_fees', kind: 'FEE_STATUS_AMOUNT', field_names: ['other_fees_status', 'other_fees_amount'], message: 'Other fee status and amount are inconsistent.' },
      { group_id: 'moq_packaging', kind: 'MOQ_PACKAGING', field_names: ['packaging_type', 'units_per_pack', 'order_multiple_units', 'moq_quantity', 'moq_unit'], message: 'The MOQ unit is inconsistent with the packaging type or units per pack.' },
      { group_id: 'relative_delivery', kind: 'ALL_OR_NONE', field_names: ['lead_time_days', 'day_basis', 'delivery_semantics', 'start_event'], message: '相对交期字段必须完整。' },
      { group_id: 'quote_validity', kind: 'DATE_ORDER', field_names: ['quote_date', 'valid_until'], message: 'The quotation date cannot be later than the validity date.' },
    ],
  }
}

export function makeQuoteDraft(
  valueOverrides: Partial<Record<(typeof quoteFieldNames)[number], string | number | null>> = {},
  draftOverrides: Partial<QuoteDraftResponse> = {},
): QuoteDraftResponse {
  const values = { ...initialValues, ...valueOverrides }
  return {
    quote_draft_id: 'draft-1',
    task_id: 'task-1',
    base_task_revision: 1,
    draft_revision: 2,
    status: 'REVIEW_REQUIRED',
    proposed_quote_id: 'quote-1',
    replacement_quote_id: null,
    proposed_document_id: 'document-1',
    supplier_id: 'supplier-redwood',
    original_filename: 'supplier-redwood.pdf',
    media_type: 'application/pdf',
    size_bytes: 1024,
    document_sha256: '1'.repeat(64),
    is_synthetic: true,
    review_status: 'REVIEW_REQUIRED',
    schema_version: 'quote-review-schema/1.0.0',
    review_envelope_schema_version: 'review-envelope/1.1.0',
    review_policy_version: 'quote-review-policy/1.0.0',
    human_review_complete: false,
    submission_ready: false,
    calculation_ready: false,
    submission_blocking_fields: [...quoteFieldNames],
    unconfirmed_fields: [...quoteFieldNames],
    review_progress: { total: 30, reviewed: 0, confirmed: 0, corrected: 0, missing_confirmed: 0 },
    review_errors: [],
    review_findings: [],
    fields: quoteFieldNames.map((fieldName) => ({
      field_id: `field-${fieldName}`,
      field_name: fieldName,
      field_version: 1,
      raw_value: values[fieldName] === null ? null : String(values[fieldName]),
      normalized_value: values[fieldName],
      unit: ['unit_price', 'shipping_fee_amount', 'other_fees_amount'].includes(fieldName) ? 'SGD' : null,
      validation_status: values[fieldName] === null ? 'MISSING' : 'EXTRACTED',
      origin: values[fieldName] === null ? null : 'DOCUMENT',
      evidence: [],
      criticality: alwaysRequired.has(fieldName) ? 'ALWAYS' : optional.has(fieldName) ? 'NON_CRITICAL' : 'CONDITIONAL_NOT_APPLICABLE',
      applicable: alwaysRequired.has(fieldName),
      required_for_submission: alwaysRequired.has(fieldName),
      review_state: 'UNREVIEWED',
      accepted_for_calculation: values[fieldName] !== null,
      allowed_actions: ['CONFIRM_VALUE', 'SET_VALUE', 'CONFIRM_MISSING', 'MARK_MISSING'],
      findings: [],
    })),
    calls_used: 1,
    max_calls: 3,
    error_code: null,
    error_message: null,
    job: null,
    created_at: '2026-09-20T00:00:00Z',
    updated_at: '2026-09-20T00:00:00Z',
    submitted_at: null,
    ...draftOverrides,
  }
}
