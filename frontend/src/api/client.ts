import type {
  ApiErrorEnvelope,
  ComparisonResultResponse,
  CreateTaskRequest,
  FieldCorrectionInput,
  HealthResponse,
  IssueAnswer,
  IssueHistoryItem,
  InvestigationCase,
  PolicyClauseInput,
  PolicyImportListQuery,
  PolicyImportListResponse,
  PolicyImportMetadata,
  PolicyImportResponse,
  PolicySetListQuery,
  PolicySetListResponse,
  QuoteFieldsResponse,
  QuoteDraftCorrectionInput,
  QuoteDraftListResponse,
  QuoteDraftResponse,
  QuoteHistoryResponse,
  QuoteUploadResponse,
  RequirementDraftResponse,
  RequirementSimulationResponse,
  ReviewOverviewResponse,
  SelectionGapResponse,
  StartRunResponse,
  TaskDetail,
  TaskAuditResponse,
  TaskListResponse,
  TaskMutationResponse,
  TaskSummary,
  SummaryListResponse,
  SummaryReportResponse,
  ResultHistoryItem,
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

function queryString(values: object) {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== '') params.set(key, String(value))
  }
  const encoded = params.toString()
  return encoded ? `?${encoded}` : ''
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

export function documentContentUrl(taskId: string, documentId: string, disposition: 'inline' | 'attachment' = 'inline') {
  return `${apiBaseUrl}/api/v1/tasks/${encodeURIComponent(taskId)}/documents/${encodeURIComponent(documentId)}/content?disposition=${disposition}`
}

export const api = {
  healthLive: () => request<HealthResponse>('/health/live'),
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
  uploadRequirementDraft: (file: File, idempotencyKey: string) => {
    const body = new FormData()
    body.append('file', file)
    return request<RequirementDraftResponse>('/api/v1/requirement-drafts', {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body,
    })
  },
  getRequirementDraft: (draftId: string) =>
    request<RequirementDraftResponse>(`/api/v1/requirement-drafts/${encodeURIComponent(draftId)}`),
  discardRequirementDraft: (draftId: string, expectedDraftRevision: number, idempotencyKey: string) =>
    request<RequirementDraftResponse>(`/api/v1/requirement-drafts/${encodeURIComponent(draftId)}/discard`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify({ expected_draft_revision: expectedDraftRevision }),
    }),
  getTask: (taskId: string) =>
    request<TaskDetail>(`/api/v1/tasks/${encodeURIComponent(taskId)}`),
  listQuotes: (taskId: string) =>
    request<QuoteHistoryResponse>(
      `/api/v1/tasks/${encodeURIComponent(taskId)}/quotes`,
    ),
  listTasks: (values: number | { limit?: number; offset?: number; query?: string; status?: string; sort?: string } = 8) =>
    request<TaskListResponse>('/api/v1/tasks' + queryString(typeof values === 'number' ? { limit: values } : values)),
  updateRequirement: (taskId: string, expectedTaskRevision: number, requirement: CreateTaskRequest['requirement'], idempotencyKey: string) =>
    request<TaskMutationResponse>(`/api/v1/tasks/${encodeURIComponent(taskId)}/requirement`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify({ expected_task_revision: expectedTaskRevision, requirement }),
    }),
  abandonTask: (taskId: string, expectedTaskRevision: number, reason: string, idempotencyKey: string) =>
    request<TaskMutationResponse>(`/api/v1/tasks/${encodeURIComponent(taskId)}/abandon`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify({ expected_task_revision: expectedTaskRevision, reason }),
    }),
  getTaskAudit: (taskId: string) =>
    request<TaskAuditResponse>(`/api/v1/tasks/${encodeURIComponent(taskId)}/revisions`),
  listQuoteDrafts: (taskId: string) =>
    request<QuoteDraftListResponse>(
      `/api/v1/tasks/${encodeURIComponent(taskId)}/quote-drafts`,
    ),
  uploadQuoteDraft: (
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
    return request<QuoteDraftResponse>(
      `/api/v1/tasks/${encodeURIComponent(taskId)}/quote-drafts`,
      {
        method: 'POST',
        headers: { 'Idempotency-Key': idempotencyKey },
        body,
      },
    )
  },
  getQuoteDraft: (taskId: string, draftId: string) =>
    request<QuoteDraftResponse>(
      `/api/v1/tasks/${encodeURIComponent(taskId)}/quote-drafts/${encodeURIComponent(draftId)}`,
    ),
  correctQuoteDraft: (
    taskId: string,
    draftId: string,
    expectedDraftRevision: number,
    corrections: QuoteDraftCorrectionInput[],
    idempotencyKey: string,
  ) =>
    request<QuoteDraftResponse>(
      `/api/v1/tasks/${encodeURIComponent(taskId)}/quote-drafts/${encodeURIComponent(draftId)}/corrections`,
      {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': idempotencyKey,
        },
        body: JSON.stringify({
          expected_draft_revision: expectedDraftRevision,
          corrections: corrections.map((item) => ({
            field_name: item.fieldName,
            raw_value: item.rawValue,
            normalized_value: item.normalizedValue,
            unit: item.unit,
            reason: item.reason,
          })),
        }),
      },
    ),
  submitQuoteDraft: (
    taskId: string,
    draftId: string,
    expectedTaskRevision: number,
    expectedDraftRevision: number,
    idempotencyKey: string,
  ) =>
    request<QuoteUploadResponse>(
      `/api/v1/tasks/${encodeURIComponent(taskId)}/quote-drafts/${encodeURIComponent(draftId)}/submit`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': idempotencyKey,
        },
        body: JSON.stringify({
          expected_task_revision: expectedTaskRevision,
          expected_draft_revision: expectedDraftRevision,
        }),
      },
    ),
  discardQuoteDraft: (
    taskId: string,
    draftId: string,
    expectedDraftRevision: number,
    idempotencyKey: string,
  ) =>
    request<QuoteDraftResponse>(
      `/api/v1/tasks/${encodeURIComponent(taskId)}/quote-drafts/${encodeURIComponent(draftId)}/discard`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': idempotencyKey,
        },
        body: JSON.stringify({ expected_draft_revision: expectedDraftRevision }),
      },
    ),
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
    answer: IssueAnswer,
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
  listIssues: (taskId: string) =>
    request<IssueHistoryItem[]>(
      `/api/v1/tasks/${encodeURIComponent(taskId)}/issues`,
    ),
  listResults: (taskId: string) =>
    request<ResultHistoryItem[]>(
      `/api/v1/tasks/${encodeURIComponent(taskId)}/results`,
    ),
  getResult: (taskId: string, resultId: string) =>
    request<ComparisonResultResponse>(
      '/api/v1/tasks/' +
        encodeURIComponent(taskId) +
        '/results/' +
        encodeURIComponent(resultId),
    ),
  listSummaries: (taskId: string) =>
    request<SummaryListResponse>(`/api/v1/tasks/${encodeURIComponent(taskId)}/summaries`),
  createSummary: (taskId: string, expectedTaskRevision: number, resultId: string, idempotencyKey: string) =>
    request<SummaryReportResponse>(`/api/v1/tasks/${encodeURIComponent(taskId)}/summaries`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify({ expected_task_revision: expectedTaskRevision, result_id: resultId }),
    }),
  getSummary: (taskId: string, summaryId: string) =>
    request<SummaryReportResponse>(`/api/v1/tasks/${encodeURIComponent(taskId)}/summaries/${encodeURIComponent(summaryId)}`),
  retrySummary: (taskId: string, summaryId: string, expectedTaskRevision: number, idempotencyKey: string) =>
    request<SummaryReportResponse>(`/api/v1/tasks/${encodeURIComponent(taskId)}/summaries/${encodeURIComponent(summaryId)}/retries`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify({ expected_task_revision: expectedTaskRevision }),
    }),
  getQuoteFields: (taskId: string, quoteId: string) =>
    request<QuoteFieldsResponse>(
      '/api/v1/tasks/' +
        encodeURIComponent(taskId) +
        '/quotes/' +
      encodeURIComponent(quoteId) +
      '/fields',
    ),
  getReview: (taskId: string) =>
    request<ReviewOverviewResponse>(
      `/api/v1/tasks/${encodeURIComponent(taskId)}/review`,
    ),
  listInvestigations: (taskId: string) =>
    request<InvestigationCase[]>(
      `/api/v1/tasks/${encodeURIComponent(taskId)}/investigations`,
    ),
  getSelectionGaps: (taskId: string, expectedTaskRevision: number) =>
    request<SelectionGapResponse>(
      `/api/v1/tasks/${encodeURIComponent(taskId)}/selection-gaps?expected_task_revision=${expectedTaskRevision}`,
    ),
  simulateRequirement: (
    taskId: string,
    expectedTaskRevision: number,
    changes: { budget_amount?: string; delivery_deadline?: string },
  ) =>
    request<RequirementSimulationResponse>(
      `/api/v1/tasks/${encodeURIComponent(taskId)}/requirement-simulations`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          expected_task_revision: expectedTaskRevision,
          confirm_hypothetical: true,
          changes,
        }),
      },
    ),
  correctQuoteFields: (
    taskId: string,
    expectedTaskRevision: number,
    corrections: FieldCorrectionInput[],
    idempotencyKey: string,
  ) =>
    request<StartRunResponse>(
      '/api/v1/tasks/' + encodeURIComponent(taskId) + '/fields/corrections',
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
            expected_field_version: item.expectedFieldVersion,
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
  uploadPolicy: (
    input: { metadata: PolicyImportMetadata; file: File },
    idempotencyKey: string,
  ) => {
    const body = new FormData()
    body.append('metadata', JSON.stringify(input.metadata))
    body.append('file', input.file)
    return request<PolicyImportResponse>('/api/v1/policy-imports', {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body,
    })
  },
  listPolicyImports: (query: PolicyImportListQuery = {}) =>
    request<PolicyImportListResponse>(
      '/api/v1/policy-imports' + queryString(query),
    ),
  listPolicySets: (query: PolicySetListQuery = {}) =>
    request<PolicySetListResponse>(
      '/api/v1/policy-sets' + queryString(query),
    ),
  getPolicyImport: (policyImportId: string) =>
    request<PolicyImportResponse>(
      `/api/v1/policy-imports/${encodeURIComponent(policyImportId)}`,
    ),
  reviewPolicyClauses: (
    policyImportId: string,
    expectedRevision: number,
    clauses: PolicyClauseInput[],
    idempotencyKey: string,
  ) =>
    request<PolicyImportResponse>(
      `/api/v1/policy-imports/${encodeURIComponent(policyImportId)}/clauses`,
      {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': idempotencyKey,
        },
        body: JSON.stringify({
          expected_revision: expectedRevision,
          clauses,
        }),
      },
    ),
  publishPolicy: (
    policyImportId: string,
    expectedRevision: number,
    idempotencyKey: string,
  ) =>
    request<PolicyImportResponse>(
      `/api/v1/policy-imports/${encodeURIComponent(policyImportId)}/publish`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': idempotencyKey,
        },
        body: JSON.stringify({ expected_revision: expectedRevision }),
      },
    ),
}
