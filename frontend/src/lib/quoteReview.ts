import type {
  QuoteDraftResponse,
  QuoteDraftReviewActionInput,
  QuoteField,
  QuoteFieldSchemaDefinition,
  QuoteFieldSchemaResponse,
} from '../api/types'
import { fieldLabel } from './presentation'

export type QuoteReviewValues = Record<string, string>

export interface QuoteReviewValidationIssue {
  code: string
  fieldNames: string[]
  groupId: string
  message: string
}

export function quoteValueAsText(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

export function initializeQuoteReviewValues(
  draft: QuoteDraftResponse,
  schema: QuoteFieldSchemaResponse,
): QuoteReviewValues {
  const byName = new Map(draft.fields.map((field) => [field.field_name, field]))
  return Object.fromEntries(
    schema.fields.map((definition) => [
      definition.field_name,
      quoteValueAsText(byName.get(definition.field_name)?.normalized_value),
    ]),
  )
}

export function isBlankQuoteValue(value: string | undefined): boolean {
  return value === undefined || value.trim() === ''
}

function fieldDefinition(
  schema: QuoteFieldSchemaResponse,
  fieldName: string,
): QuoteFieldSchemaDefinition | undefined {
  return schema.fields.find((definition) => definition.field_name === fieldName)
}

function feeRelationFields(
  schema: QuoteFieldSchemaResponse,
  fieldNames: string[],
): { statusField?: string; amountField?: string } {
  const amountField = fieldNames.find((fieldName) => fieldDefinition(schema, fieldName)?.unit_kind === 'currency')
  return {
    amountField,
    statusField: fieldNames.find((fieldName) => fieldName !== amountField),
  }
}

function currencyFieldName(schema: QuoteFieldSchemaResponse): string | undefined {
  const relation = schema.relation_groups.find((current) => current.kind === 'MONEY_CURRENCY')
  return relation?.field_names.find((fieldName) => fieldDefinition(schema, fieldName)?.unit_kind !== 'currency')
}

function moqPackagingRoles(
  schema: QuoteFieldSchemaResponse,
  fieldNames: string[],
): {
  moqUnitField?: string
  packagingTypeField?: string
  unitsPerPackField?: string
  baseUnitField?: string
} {
  const enumFields = fieldNames.filter((fieldName) => fieldDefinition(schema, fieldName)?.allowed_values?.length)
  const moqUnitField = enumFields.find((fieldName) => fieldName.toLowerCase().includes('moq'))
  const packagingTypeField = enumFields.find((fieldName) => fieldName !== moqUnitField)
  const unitsPerPackField = fieldNames.find((fieldName) => {
    const definition = fieldDefinition(schema, fieldName)
    return Boolean(
      definition &&
      fieldName !== moqUnitField &&
      fieldName !== packagingTypeField &&
      valueType(definition).includes('integer') &&
      fieldName.toLowerCase().includes('pack'),
    )
  })
  const priceBasis = schema.relation_groups.find((relation) => relation.kind === 'ALL_OR_NONE' && (
    relation.field_names.some((fieldName) => fieldDefinition(schema, fieldName)?.unit_kind === 'currency')
  ))
  const baseUnitField = priceBasis?.field_names.find((fieldName) => (
    fieldDefinition(schema, fieldName)?.allowed_values?.length
  ))
  return { moqUnitField, packagingTypeField, unitsPerPackField, baseUnitField }
}

export function requiredQuoteFields(
  draft: QuoteDraftResponse,
  schema: QuoteFieldSchemaResponse,
  values: QuoteReviewValues,
): Set<string> {
  const result = new Set<string>()
  const fields = new Map(draft.fields.map((field) => [field.field_name, field]))
  const value = (name: string) => normalizedText(values[name])
  const coveredConditionalFields = new Set<string>()
  const requiredByServer = (fieldName: string) => {
    const field = fields.get(fieldName)
    const definition = fieldDefinition(schema, fieldName)
    return field?.required_for_submission === true || Boolean(
      field?.applicable && definition?.required_level === 'Conditionally Critical',
    )
  }

  for (const definition of schema.fields) {
    const field = fields.get(definition.field_name)
    if (definition.required_level === 'Critical' || field?.criticality === 'ALWAYS') {
      result.add(definition.field_name)
    }
  }

  for (const relation of schema.relation_groups) {
    if (relation.kind === 'ALL_OR_NONE') {
      relation.field_names.forEach((fieldName) => coveredConditionalFields.add(fieldName))
      if (relation.field_names.some((fieldName) => (
        value(fieldName) !== null || requiredByServer(fieldName)
      ))) {
        relation.field_names.forEach((fieldName) => result.add(fieldName))
      }
      continue
    }

    if (relation.kind === 'FEE_STATUS_AMOUNT') {
      const { statusField, amountField } = feeRelationFields(schema, relation.field_names)
      if (!statusField || !amountField) continue
      coveredConditionalFields.add(amountField)
      const amount = value(amountField)
      if (
        value(statusField) === 'KNOWN_AMOUNT' ||
        Boolean(amount && isDecimal(amount) && !isZeroDecimal(amount))
      ) {
        result.add(amountField)
      }
      continue
    }

    if (relation.kind === 'MOQ_PACKAGING') {
      relation.field_names.forEach((fieldName) => coveredConditionalFields.add(fieldName))
      const { moqUnitField, packagingTypeField, unitsPerPackField, baseUnitField } = moqPackagingRoles(
        schema,
        relation.field_names,
      )
      const packageFields = [packagingTypeField, unitsPerPackField].filter(
        (fieldName): fieldName is string => Boolean(fieldName),
      )
      const baseUnitDefinition = schema.fields.find((definition) => definition.field_name === baseUnitField)
      const baseUnit = baseUnitField
        ? value(baseUnitField) ?? baseUnitDefinition?.allowed_values?.[0] ?? null
        : null
      const moqUnit = moqUnitField ? value(moqUnitField) : null
      if (moqUnit && baseUnit && moqUnit.toLowerCase() !== baseUnit.toLowerCase()) {
        packageFields.forEach((fieldName) => result.add(fieldName))
      }
      continue
    }

    if (relation.kind === 'DATE_ORDER') {
      const [startField, endField] = relation.field_names
      coveredConditionalFields.add(startField)
      if (value(endField) === null && requiredByServer(startField)) result.add(startField)
    }
  }

  for (const definition of schema.fields) {
    if (
      definition.required_level !== 'Optional' &&
      !coveredConditionalFields.has(definition.field_name) &&
      requiredByServer(definition.field_name)
    ) {
      result.add(definition.field_name)
    }
  }
  return result
}

function normalizedText(value: string | undefined): string | null {
  const result = value?.trim() ?? ''
  return result === '' ? null : result
}

function isDecimal(value: string): boolean {
  return /^\d+(?:\.\d+)?$/.test(value)
}

function isZeroDecimal(value: string): boolean {
  if (!isDecimal(value)) return false
  const [whole, fraction = ''] = value.split('.')
  return /^0+$/.test(whole) && (fraction === '' || /^0+$/.test(fraction))
}

function decimalAtLeast(value: string, minimum: number): boolean {
  if (!isDecimal(value)) return false
  const normalize = (input: string) => {
    const [whole = '0', fraction = ''] = input.split('.')
    return {
      whole: whole.replace(/^0+(?=\d)/, ''),
      fraction: fraction.replace(/0+$/, ''),
    }
  }
  const left = normalize(value)
  const right = normalize(String(minimum))
  if (left.whole.length !== right.whole.length) return left.whole.length > right.whole.length
  if (left.whole !== right.whole) return left.whole > right.whole
  const fractionLength = Math.max(left.fraction.length, right.fraction.length)
  return left.fraction.padEnd(fractionLength, '0') >= right.fraction.padEnd(fractionLength, '0')
}

function isValidIsoDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const parsed = new Date(`${value}T00:00:00Z`)
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value
}

function valueType(definition: QuoteFieldSchemaDefinition): string {
  return definition.value_type.toLowerCase()
}

function issue(
  code: string,
  fieldNames: string[],
  groupId: string,
  message: string,
): QuoteReviewValidationIssue {
  return { code, fieldNames, groupId, message }
}

export function validateQuoteReview(
  draft: QuoteDraftResponse,
  schema: QuoteFieldSchemaResponse,
  values: QuoteReviewValues,
): QuoteReviewValidationIssue[] {
  const issues: QuoteReviewValidationIssue[] = []
  const draftFields = new Map(draft.fields.map((field) => [field.field_name, field]))
  const schemaFields = new Map(schema.fields.map((field) => [field.field_name, field]))
  const value = (name: string) => normalizedText(values[name])
  const requiredFields = requiredQuoteFields(draft, schema, values)

  if (schema.fields.length !== 30) {
    issues.push(issue(
      'SCHEMA_FIELD_COUNT_INVALID',
      [],
      'schema',
      `The field dictionary should contain 30 business fields, but ${schema.fields.length} were returned. Refresh the page; contact an administrator if the issue persists.`,
    ))
  }

  for (const finding of draft.review_findings) {
    if (finding.field_name === '__batch__' && finding.severity === 'BLOCKING' && !finding.resolved) {
      issues.push(issue(
        finding.codes[0] ?? 'BATCH_PRECHECK_BLOCKING',
        [],
        'batch',
        `The quotation cannot proceed to human confirmation: ${finding.message}`,
      ))
    }
  }

  for (const definition of schema.fields) {
    const field = draftFields.get(definition.field_name)
    if (!field) {
      issues.push(issue(
        'DRAFT_FIELD_MISSING',
        [definition.field_name],
        definition.group_id,
        `The draft is missing “${fieldLabel(definition.field_name)}”, so quotation confirmation cannot be completed.`,
      ))
      continue
    }
    if (!field.field_id) {
      issues.push(issue(
        'FIELD_ID_MISSING',
        [definition.field_name],
        definition.group_id,
        `“${fieldLabel(definition.field_name)}” has no field version. Refresh the draft and try again.`,
      ))
    }

    const current = value(definition.field_name)
    if (requiredFields.has(definition.field_name) && current === null) {
      issues.push(issue(
        'REQUIRED_VALUE_MISSING',
        [definition.field_name],
        definition.group_id,
        `The system did not identify “${fieldLabel(definition.field_name)}”. Enter a value.`,
      ))
      continue
    }
    if (current === null) continue

    if (valueType(definition).includes('integer')) {
      const minimum = definition.minimum ?? 0
      const parsed = Number(current)
      if (!/^\d+$/.test(current) || !Number.isSafeInteger(parsed) || parsed < minimum) {
        issues.push(issue(
          'INTEGER_VALUE_INVALID',
          [definition.field_name],
          definition.group_id,
          `“${fieldLabel(definition.field_name)}” must be an integer greater than or equal to ${minimum}.`,
        ))
      }
    } else if (valueType(definition).includes('decimal')) {
      const minimum = definition.minimum ?? 0
      if (!decimalAtLeast(current, minimum)) {
        issues.push(issue(
          'MONEY_VALUE_INVALID',
          [definition.field_name],
          definition.group_id,
          `“${fieldLabel(definition.field_name)}” must be a decimal amount greater than or equal to ${minimum}, without a currency symbol or text.`,
        ))
      }
    } else if (valueType(definition).includes('date')) {
      if (!isValidIsoDate(current)) {
        issues.push(issue(
          'DATE_VALUE_INVALID',
          [definition.field_name],
          definition.group_id,
          `“${fieldLabel(definition.field_name)}” must use YYYY-MM-DD format.`,
        ))
      }
    }

    if (definition.allowed_values && definition.allowed_values.length > 0 && !definition.allowed_values.includes(current)) {
      issues.push(issue(
        'ENUM_VALUE_INVALID',
        [definition.field_name],
        definition.group_id,
        `Select “${fieldLabel(definition.field_name)}” from the options provided.`,
      ))
    }
  }

  for (const field of draft.fields) {
    if (!schemaFields.has(field.field_name)) {
      issues.push(issue(
        'FIELD_NOT_IN_SCHEMA',
        [field.field_name],
        'schema',
        `Draft field ${field.field_name} is not in the current field dictionary. Parse the quotation again.`,
      ))
    }
  }

  const currencyField = currencyFieldName(schema)
  const currency = currencyField ? value(currencyField) : null
  if (currency !== null && !/^[A-Z]{3}$/.test(currency)) {
    issues.push(issue(
      'CURRENCY_INVALID',
      currencyField ? [currencyField] : [],
      'price',
      'Currency must be three uppercase letters, such as SGD or USD.',
    ))
  }

  for (const relation of schema.relation_groups) {
    if (relation.kind === 'ALL_OR_NONE') {
      const presence = relation.field_names.map((fieldName) => value(fieldName) !== null)
      if (presence.some(Boolean) && !presence.every(Boolean)) {
        issues.push(issue(
          `${relation.group_id.toUpperCase()}_INCOMPLETE`,
          relation.field_names,
          relation.group_id,
          relation.message,
        ))
      }
      continue
    }

    if (relation.kind === 'FEE_STATUS_AMOUNT') {
      const { statusField, amountField } = feeRelationFields(schema, relation.field_names)
      if (!statusField || !amountField) continue
      const status = value(statusField)
      const amount = value(amountField)
      const label = fieldLabel(statusField)
      if (status === 'KNOWN_AMOUNT' && amount === null) {
        issues.push(issue(
          'FEE_AMOUNT_REQUIRED',
          [statusField, amountField],
          relation.group_id,
          `Enter an amount when ${label} is “Known amount”.`,
        ))
      } else if (status === 'INCLUDED' && amount !== null) {
        issues.push(issue(
          'INCLUDED_FEE_HAS_AMOUNT',
          [statusField, amountField],
          relation.group_id,
          `Leave the amount blank when ${label} is “Included” to avoid double counting.`,
        ))
      } else if ((status === 'FREE' || status === 'NOT_APPLICABLE') && amount !== null && !isZeroDecimal(amount)) {
        issues.push(issue(
          'ZERO_FEE_HAS_NONZERO_AMOUNT',
          [statusField, amountField],
          relation.group_id,
          `Leave the amount blank or enter 0 when ${label} is Free or Not Applicable.`,
        ))
      }
      continue
    }

    if (relation.kind === 'MOQ_PACKAGING') {
      const { moqUnitField, packagingTypeField, unitsPerPackField, baseUnitField } = moqPackagingRoles(
        schema,
        relation.field_names,
      )
      const moqUnit = moqUnitField ? value(moqUnitField)?.toLowerCase() : null
      const baseUnit = baseUnitField ? value(baseUnitField)?.toLowerCase() : null
      if (moqUnit && baseUnit && moqUnit !== baseUnit) {
        const packagingType = packagingTypeField ? value(packagingTypeField)?.toLowerCase() : null
        const unitsPerPack = unitsPerPackField ? value(unitsPerPackField) : null
        if (!packagingType || !unitsPerPack) {
          issues.push(issue(
            'PACKAGING_CONVERSION_INCOMPLETE',
            relation.field_names,
            relation.group_id,
            `When the MOQ unit differs from the price basis unit: ${relation.message}`,
          ))
        } else if (packagingType !== moqUnit) {
          issues.push(issue(
            'MOQ_PACKAGING_UNIT_MISMATCH',
            [moqUnitField, packagingTypeField].filter(
              (fieldName): fieldName is string => Boolean(fieldName),
            ),
            relation.group_id,
            relation.message,
          ))
        }
      }
      continue
    }

    if (relation.kind === 'DATE_ORDER') {
      const [startField, endField] = relation.field_names
      const start = value(startField)
      const end = value(endField)
      if (start && end && isValidIsoDate(start) && isValidIsoDate(end) && start > end) {
        issues.push(issue(
          'QUOTE_DATE_AFTER_VALID_UNTIL',
          relation.field_names,
          relation.group_id,
          relation.message,
        ))
      }
    }
  }

  return deduplicateIssues(issues)
}

function deduplicateIssues(issues: QuoteReviewValidationIssue[]): QuoteReviewValidationIssue[] {
  const seen = new Set<string>()
  return issues.filter((item) => {
    const key = `${item.code}:${item.fieldNames.join(',')}:${item.message}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function valuesEqual(field: QuoteField, current: string | null): boolean {
  return quoteValueAsText(field.normalized_value).trim() === (current ?? '')
}

function actionUnit(
  field: QuoteField,
  definition: QuoteFieldSchemaDefinition,
  schema: QuoteFieldSchemaResponse,
  values: QuoteReviewValues,
): string | null {
  const currencyField = currencyFieldName(schema)
  if (definition.unit_kind === 'currency' && currencyField) {
    return normalizedText(values[currencyField]) ?? field.unit
  }
  return field.unit
}

function normalizedActionValue(
  definition: QuoteFieldSchemaDefinition,
  value: string,
): string | number | boolean {
  if (valueType(definition).includes('integer')) {
    const parsed = Number(value)
    return /^\d+$/.test(value) && Number.isSafeInteger(parsed) ? parsed : value
  }
  if (valueType(definition).includes('boolean')) return value.toLowerCase() === 'true'
  return value
}

export function buildQuoteReviewActions(
  draft: QuoteDraftResponse,
  schema: QuoteFieldSchemaResponse,
  values: QuoteReviewValues,
  adoptedFields: ReadonlySet<string> = new Set(),
): QuoteDraftReviewActionInput[] {
  const byName = new Map(draft.fields.map((field) => [field.field_name, field]))
  const requiredFields = requiredQuoteFields(draft, schema, values)
  return schema.fields.map((definition) => {
    const field = byName.get(definition.field_name)
    if (!field?.field_id) {
      throw new Error(`Field ${definition.field_name} has no reviewable field ID.`)
    }
    const current = normalizedText(values[definition.field_name])
    const unit = actionUnit(field, definition, schema, values)
    const unitNeedsCorrection = definition.unit_kind === 'currency' && field.unit !== unit
    const base = {
      fieldName: definition.field_name,
      expectedFieldId: field.field_id,
      expectedFieldVersion: field.field_version,
    }
    if (current === null) {
      if (field.validation_status === 'CONFLICT') {
        if (!isBlankQuoteValue(quoteValueAsText(field.normalized_value))) {
          return {
            ...base,
            action: 'MARK_MISSING' as const,
            reason: 'The user reviewed the source file and confirmed that none of the conflicting candidates is the current field value.',
          }
        }
        return {
          ...base,
          action: 'CONFIRM_CONFLICT' as const,
          reason: 'The user reviewed the source file and confirmed that the field still has an unresolved conflict.',
        }
      }
      if (isBlankQuoteValue(quoteValueAsText(field.normalized_value))) {
        return {
          ...base,
          action: 'CONFIRM_MISSING' as const,
          reason: 'The user reviewed the complete source file and confirmed that the field was not provided or is not applicable.',
        }
      }
      return {
        ...base,
        action: 'MARK_MISSING' as const,
        reason: 'The user reviewed the source file and confirmed that the extracted value was invalid and the quotation did not provide this field.',
      }
    }
    if (
      field.validation_status === 'CONFLICT' &&
      !adoptedFields.has(field.field_name) &&
      valuesEqual(field, current) &&
      !requiredFields.has(field.field_name)
    ) {
      return {
        ...base,
        action: 'CONFIRM_CONFLICT' as const,
        reason: 'The user reviewed the source file and confirmed that the field still has an unresolved conflict.',
      }
    }
    if (
      field.validation_status !== 'CONFLICT' &&
      valuesEqual(field, current) &&
      !unitNeedsCorrection
    ) {
      return {
        ...base,
        action: 'CONFIRM_VALUE' as const,
        reason: 'The user confirmed the extracted value against the source file.',
      }
    }
    return {
      ...base,
      action: 'SET_VALUE' as const,
      rawValue: current,
      normalizedValue: normalizedActionValue(definition, current),
      unit,
      reason: field.validation_status === 'CONFLICT'
        ? 'The user reviewed the source file and selected this as the final confirmed value.'
        : unitNeedsCorrection
        ? 'The user confirmed the quotation currency and aligned monetary field units with it.'
        : isBlankQuoteValue(quoteValueAsText(field.normalized_value))
        ? 'The user entered the field after reviewing the source file or obtaining supplier confirmation.'
        : 'The user corrected the extracted value after reviewing the source file.',
    }
  })
}

export function issuesByField(
  issues: QuoteReviewValidationIssue[],
): Record<string, QuoteReviewValidationIssue[]> {
  const result: Record<string, QuoteReviewValidationIssue[]> = {}
  for (const current of issues) {
    for (const fieldName of current.fieldNames) {
      ;(result[fieldName] ??= []).push(current)
    }
  }
  return result
}
