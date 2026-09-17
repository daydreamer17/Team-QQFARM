export interface HealthResponse {
  status: string
}

export interface PolicyBinding {
  policy_set_version: string
  policy_index_version: string
  category: string
  region: string
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
  policy_binding?: PolicyBinding | null
}

export interface TaskSummary {
  task_id: string
  task_revision: number
  status: string
  policy_binding?: PolicyBinding | null
}

export interface TaskListItem extends TaskSummary {
  scenario_id: string | null
  current_result_id: string | null
  manufacturer: string | null
  manufacturer_part_number: string | null
  planned_order_date: string | null
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
  created_at: string | null
  started_at: string | null
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
  issue_type: 'CONFIRM_MISSING' | 'SHIPPING_AMOUNT' | 'POLICY_EVIDENCE_REVIEW' | string
  status: string
  question: string
  answer_schema: {
    answer_type: string
    currency?: string
    amount?: string
    changed_policy_requires?: string
    retrieval_statuses?: Record<string, PolicyRetrievalStatus>
  }
  created_revision: number
}

export interface TaskDetail extends TaskSummary {
  scenario_id: string | null
  current_graph_run_id: string | null
  current_snapshot_id: string | null
  current_result_id: string | null
  policy_binding: PolicyBinding | null
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

export type QuoteDraftStatus =
  | 'UPLOADED'
  | 'PROCESSING'
  | 'REVIEW_REQUIRED'
  | 'READY_TO_SUBMIT'
  | 'SUBMITTED'
  | 'FAILED'
  | 'STALE'
  | 'DISCARDED'

export interface QuoteDraftJob {
  job_id: string
  job_type: string
  job_status: string
  attempts: number
  created_at: string
  started_at: string | null
}

export interface QuoteDraftResponse {
  quote_draft_id: string
  task_id: string
  base_task_revision: number
  draft_revision: number
  status: QuoteDraftStatus
  proposed_quote_id: string
  proposed_document_id: string
  supplier_id: string
  original_filename: string
  media_type: string
  size_bytes: number
  document_sha256: string
  is_synthetic: boolean
  review_status: string | null
  review_findings: ReviewFinding[]
  fields: QuoteField[]
  calls_used: number
  max_calls: number
  error_code: string | null
  error_message: string | null
  job: QuoteDraftJob | null
  created_at: string
  updated_at: string
  submitted_at: string | null
}

export interface QuoteDraftListResponse {
  task_id: string
  task_revision: number
  items: QuoteDraftResponse[]
}

export interface QuoteDraftCorrectionInput {
  fieldName: string
  rawValue: string
  normalizedValue: string | number | boolean
  unit: string | null
  reason: string
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

export type IssueAnswer =
  | { answer_type: 'CONFIRM_MISSING' }
  | { answer_type: 'SHIPPING_AMOUNT'; amount: string; currency: string }
  | { answer_type: 'RETRY_POLICY_RETRIEVAL' }

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
  policy_retrievals: PolicyRetrievalResult[]
}

export type PolicyRetrievalStatus = 'OK' | 'NO_EVIDENCE' | 'CONFLICT' | 'ERROR'

export interface PolicyRetrievalCandidate {
  clause_id: string
  bm25_rank: number | null
  bm25_score: number | null
  vector_rank: number | null
  vector_score: number | null
  fusion_rank: number
  fusion_score: number
  rerank_rank: number | null
  rerank_score: number | null
}

export interface PolicyCitation {
  citation_id: string
  retrieval_id: string
  policy_set_version: string
  policy_id: string
  document_id: string
  document_version: string
  clause_id: string
  section: string
  text: string
  content_sha256: string
  control_code: string
  bm25_rank: number | null
  bm25_score: number | null
  vector_rank: number | null
  vector_score: number | null
  fusion_rank: number
  fusion_score: number
  rerank_rank: number
  rerank_score: number
}

export interface PolicyRetrievalResult {
  retrieval_id: string
  status: PolicyRetrievalStatus
  policy_set_version: string
  policy_index_version: string
  embedding_model: string
  rerank_model: string
  filters: Record<string, unknown>
  covered_control_codes: string[]
  missing_control_codes: string[]
  citations: PolicyCitation[]
  candidates: PolicyRetrievalCandidate[]
  latency_ms: Record<string, number>
  attempts: Record<string, number>
  error_code: string | null
}

export interface PolicyImportMetadata {
  policy_set_id: string
  policy_set_version: string
  policy_id: string
  document_id: string
  document_version: string
  title: string
  effective_from: string
  effective_to: string | null
  categories: string[]
  regions: string[]
}

export interface PolicyDraftClause {
  clause_id: string
  title: string
  text: string
  control_code: string | null
  rule_parameters: Record<string, unknown>
  position: number
}

export interface PolicyClauseInput {
  clause_id: string
  title: string
  text: string
  control_code: string
  rule_parameters: Record<string, unknown>
}

export interface PolicyImportResponse extends PolicyImportMetadata {
  policy_import_id: string
  status: 'REVIEW_REQUIRED' | 'READY_TO_PUBLISH' | 'PUBLISHING' | 'PUBLISHED' | string
  revision: number
  original_filename: string
  media_type: string
  size_bytes: number
  source_sha256: string
  extracted_text: string
  extraction_metadata: {
    parser: string
    page_count: number | null
    [key: string]: unknown
  }
  policy_index_version: string | null
  published_import_run_id: string | null
  clauses: PolicyDraftClause[]
}

export type PolicyImportStatus =
  | 'REVIEW_REQUIRED'
  | 'READY_TO_PUBLISH'
  | 'PUBLISHING'
  | 'PUBLISHED'

export interface PolicyImportSummary {
  policy_import_id: string
  status: PolicyImportStatus
  revision: number
  original_filename: string
  media_type: string
  size_bytes: number
  policy_set_id: string
  policy_set_version: string
  policy_id: string
  document_id: string
  document_version: string
  title: string
  categories: string[]
  regions: string[]
  policy_index_version: string | null
  clause_count: number
  created_at: string
  updated_at: string
}

export interface PolicyImportListQuery {
  status?: PolicyImportStatus
  policy_set_version?: string
  category?: string
  region?: string
  limit?: number
  offset?: number
}

export interface PolicyImportListResponse {
  items: PolicyImportSummary[]
  total: number
  limit: number
  offset: number
}

export interface PolicySetSummary {
  policy_set_id: string
  policy_set_version: string
  policy_index_version: string
  status: 'PUBLISHED'
  categories: string[]
  regions: string[]
  document_count: number
  clause_count: number
  provider: string
  embedding_model: string
  embedding_dimension: number
  preprocessing_version: string
  published_at: string | null
}

export interface PolicySetListQuery {
  category?: string
  region?: string
  limit?: number
  offset?: number
}

export interface PolicySetListResponse {
  items: PolicySetSummary[]
  total: number
  limit: number
  offset: number
}

export interface IssueHistoryItem extends CurrentIssue {
  answer: Record<string, unknown> | null
  resolved_revision: number | null
  answered_by: string | null
  answered_at: string | null
}

export type ResultHistoryItem = ComparisonResultResponse

export interface ApiErrorEnvelope {
  error: {
    code: string
    message: string
    details: Record<string, unknown>
    request_id: string
  }
}
