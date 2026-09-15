export interface HealthResponse {
  status: string
}

export interface ApiErrorEnvelope {
  error: {
    code: string
    message: string
    details: Record<string, unknown>
    request_id: string
  }
}
