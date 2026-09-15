import type {
  ApiErrorEnvelope,
  CreateTaskRequest,
  HealthResponse,
  TaskDetail,
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
}
