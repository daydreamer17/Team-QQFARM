export interface HealthResponse {
  status: string
}

export interface ProcurementRequirement {
  manufacturer: string
  manufacturer_part_number: string
  package: string
  revision: string
  condition: string
  allow_substitutes: boolean
  base_unit: string
  required_quantity: number
  quantity_unit: string
  budget_amount: string
  currency: string
  includes_shipping: boolean
  tax_mode: string
  other_fees_required: boolean
  planned_order_date: string | null
  delivery_deadline: string
  delivery_location: string
  ranking_preference: string
  secondary_preference: string | null
}

export interface CreateTaskRequest {
  requirement: ProcurementRequirement
  scenario_id: string | null
}

export interface TaskSummary {
  task_id: string
  task_revision: number
  status: string
}

export interface TaskDetail extends TaskSummary {
  scenario_id: string | null
  current_graph_run_id: string | null
  current_snapshot_id: string | null
  current_result_id: string | null
  current_issue: unknown | null
  current_job: unknown | null
  requirement: ProcurementRequirement
}

export interface QuoteUploadResponse {
  task_id: string
  task_revision: number
  quote_id: string
  quote_version: number
  document_id: string
  document_version: number
  document_sha256: string
}

export interface ApiErrorEnvelope {
  error: {
    code: string
    message: string
    details: Record<string, unknown>
    request_id: string
  }
}
