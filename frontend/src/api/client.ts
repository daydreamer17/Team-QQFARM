import type {
  ApiErrorEnvelope,
  ComparisonResultResponse,
  CreateTaskRequest,
  FieldCorrectionInput,
  HealthResponse,
  QuoteFieldsResponse,
  QuoteHistoryResponse,
  QuoteUploadResponse,
  StartRunResponse,
  TaskDetail,
  TaskListResponse,
  TaskSummary,
} from './types'

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

export class ApiClientError extends Error {
  readonly status: number
  readonly code: string
  readonly details: Record<string, unknown>
  readonly requestId?: string

  constructor(
    status: number,
    code: string,
    message: string,
    details: Record<string, unknown> = {},
    requestId?: string,
  ) {
    super(message)
    this.name = 'ApiClientError'
    this.status = status
    this.code = code
    this.details = details
    this.requestId = requestId
  }
}

function isApiErrorEnvelope(value: unknown): value is ApiErrorEnvelope {
  if (!value || typeof value !== 'object' || !('error' in value)) return false
  const error = value.error
  return Boolean(
    error &&
      typeof error === 'object' &&
      'code' in error &&
      'message' in error,
  )
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${apiBaseUrl}${path}`, {
      ...init,
      headers: {
        Accept: 'application/json',
        'X-Request-ID': crypto.randomUUID(),
        ...init?.headers,
      },
    })
  } catch {
    throw new ApiClientError(
      0,
      'network_error',
      '无法连接后端服务，请确认 Docker 和 API 服务是否正在运行。',
    )
  }

  const contentType = response.headers.get('content-type') ?? ''
  const payload: unknown = contentType.includes('application/json')
    ? await response.json()
    : null

  if (!response.ok) {
    if (isApiErrorEnvelope(payload)) {
      throw new ApiClientError(
        response.status,
        payload.error.code,
        payload.error.message,
        payload.error.details,
        payload.error.request_id,
      )
    }
    throw new ApiClientError(
      response.status,
      'unexpected_response',
      `后端返回了未预期的响应（HTTP ${response.status}）。`,
    )
  }

  return payload as T
}

export function createIdempotencyKey() {
  return crypto.randomUUID()
}

export const api = {
  healthReady: () => request<HealthResponse>('/health/ready'),
  createTask: (body: CreateTaskRequest, idempotencyKey: string) =>
    request<TaskSummary>('/api/v1/tasks', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': idempotencyKey,
      },
      body: JSON.stringify(body),
    }),
  getTask: (taskId: string) =>
    request<TaskDetail>(`/api/v1/tasks/${encodeURIComponent(taskId)}`),
  listQuotes: (taskId: string) =>
    request<QuoteHistoryResponse>(
      `/api/v1/tasks/${encodeURIComponent(taskId)}/quotes`,
    ),
  listTasks: (limit = 8) =>
    request<TaskListResponse>('/api/v1/tasks?limit=' + limit),
  uploadQuote: (
    taskId: string,
    input: {
      expectedTaskRevision: number
      supplierId: string
      isSynthetic: boolean
      file: File
    },
    idempotencyKey: string,
  ) => {
    const body = new FormData()
    body.append('expected_task_revision', String(input.expectedTaskRevision))
    body.append('supplier_id', input.supplierId)
    body.append('is_synthetic', String(input.isSynthetic))
    body.append('file', input.file)
    return request<QuoteUploadResponse>(
      `/api/v1/tasks/${encodeURIComponent(taskId)}/quotes`,
      {
        method: 'POST',
        headers: { 'Idempotency-Key': idempotencyKey },
        body,
      },
    )
  },
  startRun: (
    taskId: string,
    expectedTaskRevision: number,
    idempotencyKey: string,
  ) =>
    request<StartRunResponse>(
      `/api/v1/tasks/${encodeURIComponent(taskId)}/runs`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': idempotencyKey,
        },
        body: JSON.stringify({
          expected_task_revision: expectedTaskRevision,
        }),
      },
    ),
  retryJob: (
    taskId: string,
    jobId: string,
    expectedTaskRevision: number,
    idempotencyKey: string,
  ) =>
    request<StartRunResponse>(
      '/api/v1/tasks/' +
        encodeURIComponent(taskId) +
        '/jobs/' +
        encodeURIComponent(jobId) +
        '/retries',
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': idempotencyKey,
        },
        body: JSON.stringify({
          expected_task_revision: expectedTaskRevision,
        }),
      },
    ),
  answerIssue: (
    taskId: string,
    issueId: string,
    expectedTaskRevision: number,
    answer:
      | { answer_type: 'CONFIRM_MISSING' }
      | { answer_type: 'SHIPPING_AMOUNT'; amount: string; currency: string },
    idempotencyKey: string,
  ) =>
    request<StartRunResponse>(
      '/api/v1/tasks/' +
        encodeURIComponent(taskId) +
        '/issues/' +
        encodeURIComponent(issueId) +
        '/answers',
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': idempotencyKey,
        },
        body: JSON.stringify({
          expected_task_revision: expectedTaskRevision,
          answer,
        }),
      },
    ),
  getResult: (taskId: string, resultId: string) =>
    request<ComparisonResultResponse>(
      '/api/v1/tasks/' +
        encodeURIComponent(taskId) +
        '/results/' +
        encodeURIComponent(resultId),
    ),
  getQuoteFields: (taskId: string, quoteId: string) =>
    request<QuoteFieldsResponse>(
      '/api/v1/tasks/' +
        encodeURIComponent(taskId) +
        '/quotes/' +
      encodeURIComponent(quoteId) +
      '/fields',
    ),
  correctQuoteFields: (
    taskId: string,
    expectedTaskRevision: number,
    corrections: FieldCorrectionInput[],
    idempotencyKey: string,
  ) =>
    request<StartRunResponse>(
      '/api/v1/tasks/' + encodeURIComponent(taskId) + '/corrections',
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': idempotencyKey,
        },
        body: JSON.stringify({
          expected_task_revision: expectedTaskRevision,
          corrections: corrections.map((item) => ({
            quote_id: item.quoteId,
            field_name: item.fieldName,
            raw_value: item.rawValue,
            normalized_value: item.normalizedValue,
            unit: item.unit,
            reason: item.reason,
          })),
        }),
      },
    ),
  correctQuoteField: (
    taskId: string,
    quoteId: string,
    fieldName: string,
    input: {
      expectedTaskRevision: number
      rawValue: string
      normalizedValue: string | number | boolean
      unit: string | null
      reason: string
    },
    idempotencyKey: string,
  ) =>
    request<StartRunResponse>(
      '/api/v1/tasks/' +
        encodeURIComponent(taskId) +
        '/quotes/' +
        encodeURIComponent(quoteId) +
        '/fields/' +
        encodeURIComponent(fieldName) +
        '/corrections',
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': idempotencyKey,
        },
        body: JSON.stringify({
          expected_task_revision: input.expectedTaskRevision,
          raw_value: input.rawValue,
          normalized_value: input.normalizedValue,
          unit: input.unit,
          reason: input.reason,
        }),
      },
    ),
}
