import type {
  QuoteDraftResponse,
  QuoteDraftReviewActionInput,
  QuoteField,
  QuoteFieldSchemaDefinition,
  QuoteFieldSchemaResponse,
} from '../api/types'

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
      field?.applicable && definition?.required_level === '条件关键',
    )
  }

  for (const definition of schema.fields) {
    const field = fields.get(definition.field_name)
    if (definition.required_level === '关键' || field?.criticality === 'ALWAYS') {
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
      definition.required_level !== '可选' &&
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
      `字段字典应包含 30 个业务字段，当前返回 ${schema.fields.length} 个。请刷新；若仍出现请联系管理员。`,
    ))
  }

  for (const finding of draft.review_findings) {
    if (finding.field_name === '__batch__' && finding.severity === 'BLOCKING' && !finding.resolved) {
      issues.push(issue(
        finding.codes[0] ?? 'BATCH_PRECHECK_BLOCKING',
        [],
        'batch',
        `整份报价无法进入人工确认：${finding.message}`,
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
        `草稿缺少“${definition.label}”，无法完成整份报价确认。`,
      ))
      continue
    }
    if (!field.field_id) {
      issues.push(issue(
        'FIELD_ID_MISSING',
        [definition.field_name],
        definition.group_id,
        `“${definition.label}”缺少字段版本标识，请刷新草稿后重试。`,
      ))
    }

    const current = value(definition.field_name)
    if (requiredFields.has(definition.field_name) && current === null) {
      issues.push(issue(
        'REQUIRED_VALUE_MISSING',
        [definition.field_name],
        definition.group_id,
        `“${definition.label}”是本报价当前必须确认的字段，不能留空。`,
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
          `“${definition.label}”必须是大于或等于 ${minimum} 的整数。`,
        ))
      }
    } else if (valueType(definition).includes('decimal')) {
      const minimum = definition.minimum ?? 0
      if (!decimalAtLeast(current, minimum)) {
        issues.push(issue(
          'MONEY_VALUE_INVALID',
          [definition.field_name],
          definition.group_id,
          `“${definition.label}”必须是大于或等于 ${minimum} 的十进制金额，不能包含货币符号或文字。`,
        ))
      }
    } else if (valueType(definition).includes('date')) {
      if (!isValidIsoDate(current)) {
        issues.push(issue(
          'DATE_VALUE_INVALID',
          [definition.field_name],
          definition.group_id,
          `“${definition.label}”必须使用 YYYY-MM-DD 格式。`,
        ))
      }
    }

    if (definition.allowed_values && definition.allowed_values.length > 0 && !definition.allowed_values.includes(current)) {
      issues.push(issue(
        'ENUM_VALUE_INVALID',
        [definition.field_name],
        definition.group_id,
        `“${definition.label}”必须从系统提供的选项中选择。`,
      ))
    }
  }

  for (const field of draft.fields) {
    if (!schemaFields.has(field.field_name)) {
      issues.push(issue(
        'FIELD_NOT_IN_SCHEMA',
        [field.field_name],
        'schema',
        `草稿字段 ${field.field_name} 不在当前字段字典中，请重新解析报价。`,
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
      '币种必须是三个大写字母，例如 SGD 或 USD。',
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
      const label = schemaFields.get(statusField)?.label ?? statusField
      if (status === 'UNKNOWN') {
        issues.push(issue(
          'FEE_STATUS_UNKNOWN',
          [statusField],
          relation.group_id,
          `${label}不能保持“未知”；请依据报价原文或向供应商确认。`,
        ))
      } else if (status === 'KNOWN_AMOUNT' && amount === null) {
        issues.push(issue(
          'FEE_AMOUNT_REQUIRED',
          [statusField, amountField],
          relation.group_id,
          `${label}为“另有明确金额”时，必须填写金额。`,
        ))
      } else if (status === 'INCLUDED' && amount !== null) {
        issues.push(issue(
          'INCLUDED_FEE_HAS_AMOUNT',
          [statusField, amountField],
          relation.group_id,
          `${label}为“已包含”时，金额必须留空，避免重复计算。`,
        ))
      } else if ((status === 'FREE' || status === 'NOT_APPLICABLE') && amount !== null && !isZeroDecimal(amount)) {
        issues.push(issue(
          'ZERO_FEE_HAS_NONZERO_AMOUNT',
          [statusField, amountField],
          relation.group_id,
          `${label}免费或不适用时，金额应留空或填写 0。`,
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
            `MOQ 与计价单位不同时，${relation.message}`,
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
    return Number(value)
  }
  if (valueType(definition).includes('boolean')) return value.toLowerCase() === 'true'
  return value
}

export function buildQuoteReviewActions(
  draft: QuoteDraftResponse,
  schema: QuoteFieldSchemaResponse,
  values: QuoteReviewValues,
): QuoteDraftReviewActionInput[] {
  const byName = new Map(draft.fields.map((field) => [field.field_name, field]))
  const requiredFields = requiredQuoteFields(draft, schema, values)
  return schema.fields.map((definition) => {
    const field = byName.get(definition.field_name)
    if (!field?.field_id) {
      throw new Error(`字段 ${definition.field_name} 缺少可审核的字段 ID。`)
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
            reason: '用户核对原始文件后确认冲突候选均不能作为当前字段值。',
          }
        }
        return {
          ...base,
          action: 'CONFIRM_CONFLICT' as const,
          reason: '用户核对原始文件后确认该字段仍存在无法消除的冲突。',
        }
      }
      if (isBlankQuoteValue(quoteValueAsText(field.normalized_value))) {
        return {
          ...base,
          action: 'CONFIRM_MISSING' as const,
          reason: '用户核对整份原始文件后确认该字段未提供或不适用。',
        }
      }
      return {
        ...base,
        action: 'MARK_MISSING' as const,
        reason: '用户核对原始文件后确认自动提取值无效，原报价未提供该字段。',
      }
    }
    if (
      field.validation_status === 'CONFLICT' &&
      valuesEqual(field, current) &&
      !requiredFields.has(field.field_name)
    ) {
      return {
        ...base,
        action: 'CONFIRM_CONFLICT' as const,
        reason: '用户核对原始文件后确认该字段仍存在无法消除的冲突。',
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
        reason: '用户已对照原始文件确认自动提取值。',
      }
    }
    return {
      ...base,
      action: 'SET_VALUE' as const,
      rawValue: current,
      normalizedValue: normalizedActionValue(definition, current),
      unit,
      reason: field.validation_status === 'CONFLICT'
        ? '用户核对原始文件后选择该值作为最终确认值。'
        : unitNeedsCorrection
        ? '用户确认报价币种，并将金额字段的单位与报价币种保持一致。'
        : isBlankQuoteValue(quoteValueAsText(field.normalized_value))
        ? '用户核对原始文件或向供应商确认后补充该字段。'
        : '用户核对原始文件后修正自动提取值。',
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
