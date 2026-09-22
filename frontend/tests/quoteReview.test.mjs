import assert from 'node:assert/strict'
import { test } from 'vitest'
import {
  buildQuoteReviewActions,
  initializeQuoteReviewValues,
  requiredQuoteFields,
  validateQuoteReview,
} from '../src/lib/quoteReview.ts'

const fieldNames = [
  'supplier_name', 'supplier_country', 'category', 'item', 'manufacturer',
  'manufacturer_part_number', 'package', 'revision', 'condition', 'currency',
  'unit_price', 'price_basis_quantity', 'price_basis_unit', 'packaging_type',
  'units_per_pack', 'order_multiple_units', 'moq_quantity', 'moq_unit',
  'shipping_fee_status', 'shipping_fee_amount', 'other_fees_status',
  'other_fees_amount', 'tax_mode', 'lead_time_days', 'day_basis',
  'delivery_semantics', 'start_event', 'payment_terms', 'quote_date', 'valid_until',
]

const alwaysRequired = new Set([
  'supplier_name', 'manufacturer', 'manufacturer_part_number', 'package', 'condition',
  'currency', 'unit_price', 'price_basis_quantity', 'price_basis_unit',
  'order_multiple_units', 'moq_quantity', 'moq_unit', 'shipping_fee_status',
  'other_fees_status', 'tax_mode', 'valid_until',
])

const enumValues = {
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

const values = {
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

function valueType(name) {
  if (['unit_price', 'shipping_fee_amount', 'other_fees_amount'].includes(name)) return 'decimal string/blank'
  if (['price_basis_quantity', 'units_per_pack', 'order_multiple_units', 'moq_quantity', 'lead_time_days'].includes(name)) return 'integer/blank'
  if (['quote_date', 'valid_until'].includes(name)) return 'date/blank'
  if (enumValues[name]) return 'enum/blank'
  return 'string/blank'
}

function makeSchema() {
  return {
    schema_version: 'quote-review-schema/1.0.0',
    dictionary_version: 'test',
    dictionary_sha256: '0'.repeat(64),
    review_policy_version: 'test',
    relation_groups: [
      { group_id: 'price_basis', kind: 'ALL_OR_NONE', field_names: ['unit_price', 'price_basis_quantity', 'price_basis_unit'], message: '价格字段必须完整。' },
      { group_id: 'money_currency', kind: 'MONEY_CURRENCY', field_names: ['currency', 'unit_price', 'shipping_fee_amount', 'other_fees_amount'], message: '金额币种必须一致。' },
      { group_id: 'shipping_fee', kind: 'FEE_STATUS_AMOUNT', field_names: ['shipping_fee_status', 'shipping_fee_amount'], message: '运费字段必须一致。' },
      { group_id: 'other_fees', kind: 'FEE_STATUS_AMOUNT', field_names: ['other_fees_status', 'other_fees_amount'], message: '其他费用字段必须一致。' },
      { group_id: 'moq_packaging', kind: 'MOQ_PACKAGING', field_names: ['packaging_type', 'units_per_pack', 'order_multiple_units', 'moq_quantity', 'moq_unit'], message: 'MOQ 与包装字段必须一致。' },
      { group_id: 'relative_delivery', kind: 'ALL_OR_NONE', field_names: ['lead_time_days', 'day_basis', 'delivery_semantics', 'start_event'], message: '相对交期字段必须完整。' },
      { group_id: 'quote_validity', kind: 'DATE_ORDER', field_names: ['quote_date', 'valid_until'], message: '报价日期不能晚于有效截止日。' },
    ],
    fields: fieldNames.map((name) => ({
      field_name: name,
      label: name,
      group_id: 'test',
      group_label: 'Test',
      value_type: valueType(name),
      required_level: alwaysRequired.has(name) ? '关键' : '可选',
      allowed_values: enumValues[name] ?? [],
      minimum: name === 'lead_time_days' ? 0 : valueType(name).includes('integer') ? 1 : valueType(name).includes('decimal') ? 0 : null,
      unit_kind: ['unit_price', 'shipping_fee_amount', 'other_fees_amount'].includes(name) ? 'currency' : 'none',
      normalization_rule: 'test',
      validation_boundary: 'test',
      evidence_requirement: 'test',
    })),
  }
}

function makeDraft(overrides = {}) {
  const current = { ...values, ...overrides }
  return {
    quote_draft_id: 'draft-1',
    task_id: 'task-1',
    draft_revision: 1,
    fields: fieldNames.map((name) => ({
      field_id: `field-${name}`,
      field_name: name,
      field_version: 1,
      raw_value: current[name] === null ? null : String(current[name]),
      normalized_value: current[name],
      unit: ['unit_price', 'shipping_fee_amount', 'other_fees_amount'].includes(name) ? 'SGD' : null,
      validation_status: current[name] === null ? 'MISSING' : 'EXTRACTED',
      origin: current[name] === null ? null : 'DOCUMENT',
      evidence: [],
      criticality: alwaysRequired.has(name) ? 'ALWAYS' : 'NON_CRITICAL',
      applicable: alwaysRequired.has(name),
      required_for_submission: alwaysRequired.has(name),
      review_state: 'UNREVIEWED',
      accepted_for_calculation: current[name] !== null,
      allowed_actions: ['CONFIRM_VALUE', 'SET_VALUE', 'CONFIRM_MISSING', 'MARK_MISSING'],
    })),
    review_findings: [],
  }
}

test('valid quote produces one confirmation action for every field', () => {
  const schema = makeSchema()
  const draft = makeDraft()
  const formValues = initializeQuoteReviewValues(draft, schema)

  assert.deepEqual(validateQuoteReview(draft, schema, formValues), [])
  const actions = buildQuoteReviewActions(draft, schema, formValues)
  assert.equal(actions.length, 30)
  assert.equal(actions.find((action) => action.fieldName === 'manufacturer').action, 'CONFIRM_VALUE')
  assert.equal(actions.find((action) => action.fieldName === 'shipping_fee_amount').action, 'CONFIRM_MISSING')
})

test('an auto-passed value remains editable and becomes a SET_VALUE action', () => {
  const schema = makeSchema()
  const draft = makeDraft()
  const formValues = initializeQuoteReviewValues(draft, schema)
  formValues.manufacturer = 'Human Confirmed Manufacturer'

  assert.deepEqual(validateQuoteReview(draft, schema, formValues), [])
  const action = buildQuoteReviewActions(draft, schema, formValues)
    .find((item) => item.fieldName === 'manufacturer')
  assert.equal(action.action, 'SET_VALUE')
  assert.equal(action.normalizedValue, 'Human Confirmed Manufacturer')
})

test('confirming quote currency also aligns existing money-field units', () => {
  const schema = makeSchema()
  const draft = makeDraft()
  const price = draft.fields.find((field) => field.field_name === 'unit_price')
  price.unit = 'USD'
  const formValues = initializeQuoteReviewValues(draft, schema)

  assert.deepEqual(validateQuoteReview(draft, schema, formValues), [])
  const action = buildQuoteReviewActions(draft, schema, formValues)
    .find((item) => item.fieldName === 'unit_price')
  assert.equal(action.action, 'SET_VALUE')
  assert.equal(action.unit, 'SGD')
})

test('changing quote currency rewrites every present monetary value with the new unit', () => {
  const schema = makeSchema()
  const draft = makeDraft({ shipping_fee_status: 'KNOWN_AMOUNT', shipping_fee_amount: '20.00' })
  const formValues = initializeQuoteReviewValues(draft, schema)
  formValues.currency = 'USD'

  assert.deepEqual(validateQuoteReview(draft, schema, formValues), [])
  const actions = buildQuoteReviewActions(draft, schema, formValues)
  for (const fieldName of ['unit_price', 'shipping_fee_amount']) {
    const action = actions.find((item) => item.fieldName === fieldName)
    assert.equal(action.action, 'SET_VALUE')
    assert.equal(action.unit, 'USD')
  }
})

test('clearing an extracted optional value is audited as MARK_MISSING', () => {
  const schema = makeSchema()
  const draft = makeDraft()
  const formValues = initializeQuoteReviewValues(draft, schema)
  formValues.supplier_country = ''

  const action = buildQuoteReviewActions(draft, schema, formValues)
    .find((item) => item.fieldName === 'supplier_country')
  assert.equal(action.action, 'MARK_MISSING')
  assert.match(action.reason, /未提供/)
})

test('an unchanged optional conflict remains explicit and cannot masquerade as confirmed', () => {
  const schema = makeSchema()
  const draft = makeDraft({ supplier_country: null })
  const country = draft.fields.find((field) => field.field_name === 'supplier_country')
  country.validation_status = 'CONFLICT'
  const formValues = initializeQuoteReviewValues(draft, schema)

  const action = buildQuoteReviewActions(draft, schema, formValues)
    .find((item) => item.fieldName === 'supplier_country')
  assert.equal(action.action, 'CONFIRM_CONFLICT')
})

test('confirming one value resolves a required conflict through SET_VALUE', () => {
  const schema = makeSchema()
  const draft = makeDraft()
  const manufacturer = draft.fields.find((field) => field.field_name === 'manufacturer')
  manufacturer.validation_status = 'CONFLICT'
  const formValues = initializeQuoteReviewValues(draft, schema)

  const action = buildQuoteReviewActions(draft, schema, formValues)
    .find((item) => item.fieldName === 'manufacturer')
  assert.equal(action.action, 'SET_VALUE')
  assert.equal(action.normalizedValue, 'QQ Demo Components')
})

test('FREE and NOT_APPLICABLE accept a missing fee amount without inventing zero', () => {
  const schema = makeSchema()
  const draft = makeDraft()
  const formValues = initializeQuoteReviewValues(draft, schema)

  const feeIssues = validateQuoteReview(draft, schema, formValues)
    .filter((current) => current.groupId === 'shipping_fee' || current.groupId === 'other_fees')
  assert.deepEqual(feeIssues, [])
  assert.equal(formValues.shipping_fee_amount, '')
  assert.equal(formValues.other_fees_amount, '')
})

test('UNKNOWN can be reviewed while KNOWN_AMOUNT requires an amount', () => {
  const schema = makeSchema()
  const draft = makeDraft()
  const formValues = initializeQuoteReviewValues(draft, schema)

  formValues.shipping_fee_status = 'UNKNOWN'
  assert.ok(!validateQuoteReview(draft, schema, formValues).some((current) => current.code === 'FEE_STATUS_UNKNOWN'))

  formValues.shipping_fee_status = 'KNOWN_AMOUNT'
  assert.ok(validateQuoteReview(draft, schema, formValues).some((current) => current.code === 'FEE_AMOUNT_REQUIRED'))
})

test('conditional requirements follow current values instead of stale draft applicability', () => {
  const schema = makeSchema()
  const draft = makeDraft({
    moq_unit: 'tray',
    packaging_type: 'tray',
    units_per_pack: 100,
    shipping_fee_status: 'KNOWN_AMOUNT',
    shipping_fee_amount: '20.00',
  })
  for (const name of ['packaging_type', 'units_per_pack', 'shipping_fee_amount']) {
    const field = draft.fields.find((item) => item.field_name === name)
    field.required_for_submission = true
    field.criticality = 'CONDITIONAL_APPLICABLE'
  }
  const formValues = initializeQuoteReviewValues(draft, schema)
  formValues.moq_unit = 'piece'
  formValues.packaging_type = ''
  formValues.units_per_pack = ''
  formValues.shipping_fee_status = 'FREE'
  formValues.shipping_fee_amount = ''
  formValues.lead_time_days = ''
  formValues.day_basis = ''
  formValues.delivery_semantics = ''
  formValues.start_event = ''

  const required = requiredQuoteFields(draft, schema, formValues)
  assert.equal(required.has('shipping_fee_amount'), false)
  assert.equal(required.has('packaging_type'), false)
  assert.equal(required.has('units_per_pack'), false)
  assert.equal(required.has('lead_time_days'), false)
  assert.deepEqual(validateQuoteReview(draft, schema, formValues), [])
})

test('server-applicable relative delivery cannot be bypassed by clearing the whole group', () => {
  const schema = makeSchema()
  const draft = makeDraft()
  const deliveryFields = ['lead_time_days', 'day_basis', 'delivery_semantics', 'start_event']
  for (const name of deliveryFields) {
    const field = draft.fields.find((item) => item.field_name === name)
    field.required_for_submission = true
    field.applicable = true
    field.criticality = 'CONDITIONAL_APPLICABLE'
  }
  const formValues = initializeQuoteReviewValues(draft, schema)
  for (const name of deliveryFields) formValues[name] = ''

  const required = requiredQuoteFields(draft, schema, formValues)
  assert.deepEqual(deliveryFields.filter((name) => required.has(name)), deliveryFields)
  const issues = validateQuoteReview(draft, schema, formValues)
  assert.ok(deliveryFields.every((name) => issues.some((current) => current.fieldNames.includes(name))))
})

test('an absolute valid-until date makes an initially relative quote date non-required', () => {
  const schema = makeSchema()
  const draft = makeDraft({ quote_date: null, valid_until: null })
  const quoteDate = draft.fields.find((item) => item.field_name === 'quote_date')
  quoteDate.required_for_submission = true
  quoteDate.applicable = true
  quoteDate.criticality = 'CONDITIONAL_APPLICABLE'
  const formValues = initializeQuoteReviewValues(draft, schema)
  formValues.valid_until = '2026-09-20'

  const required = requiredQuoteFields(draft, schema, formValues)
  assert.equal(required.has('quote_date'), false)
  assert.deepEqual(validateQuoteReview(draft, schema, formValues), [])
})

test('linked price, delivery, MOQ, and date errors name the affected fields', () => {
  const schema = makeSchema()
  const draft = makeDraft()
  const formValues = initializeQuoteReviewValues(draft, schema)
  formValues.price_basis_quantity = ''
  formValues.moq_unit = 'tray'
  formValues.packaging_type = ''
  formValues.units_per_pack = ''
  formValues.day_basis = ''
  formValues.quote_date = '2026-09-21'

  const codes = new Set(validateQuoteReview(draft, schema, formValues).map((current) => current.code))
  assert.ok(codes.has('PRICE_BASIS_INCOMPLETE'))
  assert.ok(codes.has('PACKAGING_CONVERSION_INCOMPLETE'))
  assert.ok(codes.has('RELATIVE_DELIVERY_INCOMPLETE'))
  assert.ok(codes.has('QUOTE_DATE_AFTER_VALID_UNTIL'))
})
