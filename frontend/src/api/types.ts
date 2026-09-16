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

export interface TaskListItem extends TaskSummary {
  scenario_id: string | null
  current_result_id: string | null
  manufacturer: string | null
  manufacturer_part_number: string | null
  created_at: string
  updated_at: string
}

export interface TaskListResponse {
  items: TaskListItem[]
}

export interface CurrentJob {
  job_id: string
  job_type: string
  job_status: string
  task_revision: number
  error_code: string | null
  error_message: string | null
  has_corrections: boolean
  correction_batch_incomplete: boolean
}

export interface TaskQuote {
  quote_id: string
  quote_version: number
  supplier_id: string
  document_id: string
  document_version: number
  original_filename: string
}

export interface CurrentIssue {
  issue_id: string
  task_id: string
  graph_run_id: string
  quote_id: string | null
  field_name: string | null
  issue_type: 'CONFIRM_MISSING' | 'SHIPPING_AMOUNT' | string
  status: string
  question: string
  answer_schema: {
    answer_type: string
    currency?: string
    amount?: string
  }
  created_revision: number
}

export interface TaskDetail extends TaskSummary {
  scenario_id: string | null
  current_graph_run_id: string | null
  current_snapshot_id: string | null
  current_result_id: string | null
  current_issue: CurrentIssue | null
  current_job: CurrentJob | null
  quotes: TaskQuote[]
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

export interface QuoteHistoryVersion {
  quote_version: number
  document_id: string
  document_version: number
  original_filename: string
  media_type: string
  size_bytes: number
  document_sha256: string
  is_synthetic: boolean
  is_current: boolean
  created_at: string
}

export interface QuoteHistoryItem {
  quote_id: string
  supplier_id: string
  current_version: number
  active: boolean
  created_at: string
  versions: QuoteHistoryVersion[]
}

export interface QuoteHistoryResponse {
  task_id: string
  task_revision: number
  items: QuoteHistoryItem[]
}

export interface StartRunResponse {
  task_id: string
  task_revision: number
  graph_run_id: string
  job_id: string
  job_type: string
  job_status: string
  correction_count?: number
}

export interface FieldCorrectionInput {
  quoteId: string
  fieldName: string
  rawValue: string
  normalizedValue: string | number | boolean
  unit: string | null
  reason: string
}

export interface FieldEvidence {
  source_id: string | null
  quoted_text: string | null
  kind: string | null
  page_number: number | null
  row_number: number | null
  column_name: string | null
  bbox: number[] | null
  coordinate_space: string | null
}

export interface QuoteField {
  field_name: string
  field_version: string
  raw_value: string | null
  normalized_value: unknown
  unit: string | null
  validation_status: string
  origin: string | null
  evidence: FieldEvidence[]
}

export interface ReviewFinding {
  finding_id: string
  field_name: string
  criticality: string
  applicable: boolean
  decision: string
  severity: string
  review_reason: string | null
  codes: string[]
  message: string
  source_ids: string[]
  accepted_for_calculation: boolean
  resolved: boolean
}

export interface QuoteFieldsResponse {
  quote_id: string
  quote_version?: number
  review_status: string | null
  batch_artifact_id?: string
  review_findings: ReviewFinding[]
  fields: QuoteField[]
}

export interface ResultReason {
  code: string
  fields: string[]
  message: string
}

export interface SupplierComparisonResult {
  status: string
  quote_id: string
  quote_version: number
  supplier_name: string
  goods_cost: string | null
  known_cost_subtotal: string | null
  total_cost: string | null
  actual_quantity: number | null
  estimated_arrival_date: string | null
  failed_reasons: ResultReason[]
  pending_reasons: ResultReason[]
}

export interface ComparisonPayload {
  disposition: string
  evaluated_at: string
  rule_version: string
  ranked_quote_ids: string[]
  supplier_results: SupplierComparisonResult[]
  pending_quote_ids: string[]
  comparison_reasons: ResultReason[]
  recommended_quote_ids: string[]
  blocking_pending_quote_ids: string[]
  final_recommendation_allowed: boolean
}

export interface ComparisonResultResponse {
  result_id: string
  task_revision: number
  graph_run_id: string
  is_current: boolean
  result: ComparisonPayload
}

export interface ApiErrorEnvelope {
  error: {
    code: string
    message: string
    details: Record<string, unknown>
    request_id: string
  }
}
