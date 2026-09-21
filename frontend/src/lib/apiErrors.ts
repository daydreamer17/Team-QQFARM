import { ApiClientError } from '../api/client'

export function backendFieldErrors(
  error: unknown,
  validFields: readonly string[],
): Record<string, string> {
  if (!(error instanceof ApiClientError)) return {}
  const output: Record<string, string> = {}
  const allowed = new Set(validFields)
  const detailErrors = error.details.errors
  if (Array.isArray(detailErrors)) {
    for (const item of detailErrors) {
      if (!item || typeof item !== 'object') continue
      const location = 'location' in item && Array.isArray(item.location)
        ? item.location
        : 'loc' in item && Array.isArray(item.loc)
          ? item.loc
          : []
      const field = String(location.at(-1) ?? '')
      const message = 'message' in item
        ? String(item.message)
        : 'msg' in item
          ? String(item.msg)
          : '该字段未通过后端校验。'
      if (allowed.has(field)) output[field] = message
    }
  }
  const fieldErrors = error.details.field_errors
  if (fieldErrors && typeof fieldErrors === 'object') {
    for (const [field, message] of Object.entries(fieldErrors)) {
      if (allowed.has(field)) output[field] = String(message)
    }
  }
  return output
}
