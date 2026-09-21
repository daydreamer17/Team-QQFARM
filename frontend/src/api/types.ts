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
  ranking_preference: RankingCriterion
  secondary_preference: RankingCriterion | null
}

export interface CreateTaskRequest {
  requirement: ProcurementRequirement
  scenario_id: string | null
  policy_binding?: PolicyBinding | null
  requirement_draft_id?: string
  expected_requirement_draft_revision?: number
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
  total: number
  limit: number
  offset: number
  status_counts: Record<string, number>
}

export interface RequirementCandidate {
  field_name: keyof ProcurementRequirement | string
  raw_value: string
  normalized_value: unknown
  validation_status: string
  origin: string
  source_refs: Array<{ source_id: string; quoted_text: string }>
}

export interface RequirementDraftResponse {
  requirement_draft_id: string
  draft_revision: number
  status: 'PROCESSING' | 'READY' | 'FAILED' | 'USED' | 'DISCARDED' | string
  original_filename: string
  media_type: string
  size_bytes: number
  document_sha256: string
  parsed: { sources: Record<string, unknown>[] } | null
  candidates: RequirementCandidate[]
  calls_used: number
  max_calls: number
  provider: string | null
  model_id: string | null
  environment: string | null
  prompt_version: string | null
  error_code: string | null
  error_message: string | null
  submitted_task_id: string | null
  job: { job_id: string; job_type: string; job_status: string; attempts: number } | null
  created_at: string
  updated_at: string
}

export interface TaskMutationResponse {
  task_id: string
  task_revision: number
  status: string
  changed_fields?: string[]
  graph_run_id?: string | null
  job_id?: string | null
  job_status?: string | null
  reason?: string
}

export interface TaskAuditResponse {
  task_id: string
  revisions: Array<{
    revision: number
    change_type: string
    actor_id: string
    details: Record<string, unknown>
    created_at: string
  }>
  document_accesses: Array<{
    access_event_id: string
    document_id: string
    action: string
    actor_id: string
    request_id: string | null
    created_at: string
  }>
}

export interface SummaryNarrative {
  title: string
  overview: string
  sections: Array<{ heading: string; text: string; reference_ids: string[] }>
  disclaimer: string
}

export interface SummaryReportResponse {
  summary_id: string
  task_id: string
  task_revision: number
  result_id: string
  status: 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'STALE' | string
  is_current: boolean
  input_sha256: string
  facts: Record<string, unknown> & {
    requirement?: ProcurementRequirement
    decision_profile?: DecisionProfile
    policy_binding?: PolicyBinding | null
    final_recommendation_allowed?: boolean
    recommended_quote_ids?: string[]
    pending_quote_ids?: string[]
    references?: Record<string, unknown>
    scope?: string
  }
  narrative: SummaryNarrative | null
  provider: string | null
  model_id: string | null
  environment: string | null
  prompt_version: string
  calls_used: number
  max_calls: number
  error_code: string | null
  error_message: string | null
  job: { job_id: string; job_type: string; job_status: string; attempts: number } | null
  created_at: string
  updated_at: string
}

export interface SummaryListResponse {
  task_id: string
  task_revision: number
  items: SummaryReportResponse[]
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
    submit_to?: string
    expected_task_revision?: number
    payment_start_event_options?: string[]
    source_type_options?: string[]
    requires_note?: boolean
    cards?: BatchReviewCard[]
  }
  created_revision: number
}

export interface BatchReviewCard {
  quote_id: string
  field_name: string
  expected_field_version: number | null
  document_id: string | null
  original_filename: string | null
  source_refs: Record<string, unknown>[]
  current_value: unknown
  question: string
  resolution: 'FIELD_CORRECTION' | 'REEXTRACT_OR_SYSTEM_REPAIR' | string
}

export interface TaskDetail extends TaskSummary {
  scenario_id: string | null
  current_graph_run_id: string | null
  current_snapshot_id: string | null
  current_result_id: string | null
  summary_completed: boolean
  progress: {
    requirement_completed: boolean
    quote_review_completed: boolean
    decision_completed: boolean
    summary_completed: boolean
  }
  policy_binding: PolicyBinding | null
  decision_profile: DecisionProfile
  supplier_history_binding?: {
    binding_status: string
    dataset_version: string
    as_of_date: string
    is_synthetic: boolean
  } | null
  current_issue: CurrentIssue | null
  current_job: CurrentJob | null
  quotes: TaskQuote[]
  requirement: ProcurementRequirement
}

export type RankingCriterion =
  | 'LOWEST_CONFIRMED_TOTAL_COST'
  | 'FASTEST_CONFIRMED_DELIVERY'
  | 'LONGEST_CONFIRMED_PAYMENT_TERM'
  | 'HIGHEST_SUPPLIER_PERFORMANCE'
  | 'HIGHEST_HISTORICAL_ON_TIME_RATE'
  | 'LOWEST_HISTORICAL_REJECTED_LINE_RATE'

export interface DecisionPreferences {
  schema_version: string
  primary_criterion: RankingCriterion | null
  secondary_criterion: RankingCriterion | null
  excluded_supplier_ids: string[]
  cost_tolerance_amount: string | null
}

export interface DecisionProfile {
  decision_profile_id: string | null
  task_revision: number | null
  profile_version: number
  preferences: DecisionPreferences
  source_scenario_id: string | null
}

export interface DecisionChanges {
  budget_amount?: string
  delivery_deadline?: string
  primary_criterion?: RankingCriterion | null
  secondary_criterion?: RankingCriterion | null
  excluded_supplier_ids?: string[]
  cost_tolerance_amount?: string | null
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

export type QuoteReviewAction =
  | 'CONFIRM_VALUE'
  | 'SET_VALUE'
  | 'CONFIRM_MISSING'
  | 'MARK_MISSING'
  | 'CONFIRM_CONFLICT'

export interface QuoteFieldSchemaDefinition {
  field_name: string
  label: string
  group_id: string
  group_label: string
  value_type: string
  editor?: string
  required_level: string
  nullable?: boolean
  allowed_values: string[] | null
  minimum?: number | null
  unit_kind?: string
  missing_handling?: string
  normalization_rule: string
  validation_boundary: string
  evidence_requirement: string
}

export interface QuoteFieldRelationGroup {
  group_id: string
  kind: string
  field_names: string[]
  message: string
}

export interface QuoteFieldSchemaResponse {
  schema_version: string
  dictionary_version: string
  dictionary_sha256: string
  review_policy_version: string
  groups?: Array<{ group_id: string; label: string; field_names: string[] }>
  fields: QuoteFieldSchemaDefinition[]
  relation_groups: QuoteFieldRelationGroup[]
}

export interface QuoteDraftReviewProgress {
  total: number
  reviewed: number
  confirmed: number
  corrected: number
  missing_confirmed: number
}

export interface QuoteDraftReviewActionInput {
  action: QuoteReviewAction
  fieldName: string
  expectedFieldId: string
  expectedFieldVersion: number
  rawValue?: string
  normalizedValue?: string | number | boolean | null
  unit?: string | null
  reason?: string
}

export interface QuoteDraftResponse {
  quote_draft_id: string
  task_id: string
  base_task_revision: number
  draft_revision: number
  status: QuoteDraftStatus
  proposed_quote_id: string
  replacement_quote_id: string | null
  proposed_document_id: string
  supplier_id: string
  original_filename: string
  media_type: string
  size_bytes: number
  document_sha256: string
  is_synthetic: boolean
  review_status: string | null
  schema_version?: string | null
  review_envelope_schema_version?: string | null
  review_policy_version?: string | null
  human_review_complete?: boolean
  submission_ready?: boolean
  calculation_ready?: boolean
  submission_blocking_fields?: string[]
  unconfirmed_fields?: string[]
  review_progress?: QuoteDraftReviewProgress
  review_errors?: Array<{
    code: string
    field_names: string[]
    group_id: string | null
    message: string
  }>
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
  normalizedValue: string | number | boolean | null
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

export interface QuoteDeactivateResponse {
  task_id: string
  task_revision: number
  status: string
  quote_id: string
  supplier_id: string
  active: false
}

export interface QuoteReactivateResponse {
  task_id: string
  task_revision: number
  status: string
  quote_id: string
  supplier_id: string
  active: true
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
  | {
      answer_type: 'PAYMENT_INFORMATION'
      payment_start_event: 'INVOICE_DATE'
      note: string
      source_type: 'SUPPLIER_CONFIRMATION' | 'DOCUMENT_CLARIFICATION' | 'USER_INPUT'
      source_refs: string[]
    }

export interface FieldCorrectionInput {
  quoteId: string
  fieldName: string
  expectedFieldVersion: number
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
  field_id?: string
  field_name: string
  field_version: number
  raw_value: string | null
  normalized_value: unknown
  unit: string | null
  validation_status: string
  origin: string | null
  evidence: FieldEvidence[]
  criticality?: string
  applicable?: boolean
  required_for_submission?: boolean
  review_state?: string
  accepted_for_calculation?: boolean
  allowed_actions?: QuoteReviewAction[]
  findings?: ReviewFinding[]
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
  payment_term?: {
    raw_text: string | null
    normalized_text: string | null
    net_days: number | null
    payment_start_event: string | null
    parse_status: string
    reason_codes: string[]
  } | null
  history_snapshot?: SupplierHistorySnapshot | null
  criterion_evaluations?: CriterionEvaluation[]
  failed_reasons: ResultReason[]
  pending_reasons: ResultReason[]
}

export interface RateMetric {
  numerator: number
  denominator: number
  rate: string | null
}

export interface SupplierHistorySnapshot {
  quote_id: string
  supplier_id: string | null
  supplier_name: string | null
  identity_match_status: string
  history_availability_status: string
  overall_grade: string | null
  on_time: RateMetric | null
  rejected_lines: RateMetric | null
  evidence_refs: string[]
}

export interface CriterionEvaluation {
  criterion: RankingCriterion
  status: string
  exact_value: string | number | null
  display_value: string | null
  direction: string
  reason_codes: string[]
  evidence_refs: string[]
}

export interface RankingTrace {
  ordered_criteria: RankingCriterion[]
  excluded_quote_ids: string[]
  secondary_applied: boolean
  tie_group: string[]
  comparison_disposition: string
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
  ranking_trace?: RankingTrace | null
}

export interface SupplierInformationQuote {
  quote_id: string
  quote_version: number
  active: boolean | null
  in_scope_at_result: boolean | null
  document_id: string | null
  document_sha256: string | null
  is_synthetic: boolean | null
  evaluation: SupplierComparisonResult | null
  policy_assessment: PolicyComplianceSupplierAssessment | null
  decision_impact: QuoteDecisionImpact | null
}

export interface SupplierInformationEntry {
  supplier_identity_id: string | null
  display_name: string
  supplier_id: string | null
  identity_match_status: string
  history_availability_status: string
  history_snapshot: SupplierHistorySnapshot | null
  quotes: SupplierInformationQuote[]
}

export interface SupplierInformationResponse {
  schema_version: string
  task_id: string
  snapshot_revision: number
  result_id: string | null
  snapshot_id: string | null
  view_state: 'QUOTE_ONLY' | 'CURRENT_RESULT' | 'HISTORICAL_RESULT'
  is_current: boolean
  context_sha256: string
  data_availability: string
  dataset_status: string
  history_dataset_context: Record<string, unknown> | null
  history_binding: Record<string, unknown> | null
  effective_preferences: DecisionPreferences | null
  ranking_trace: RankingTrace | null
  quote_count: number
  matched_supplier_count: number
  unresolved_identity_quote_count: number
  draft_count: number | null
  inactive_quote_count: number | null
  suppliers: SupplierInformationEntry[]
  unresolved_identity_quotes: Array<SupplierInformationQuote & Partial<SupplierInformationEntry>>
}

export interface ComparisonResultResponse {
  result_id: string
  snapshot_id: string | null
  input_snapshot: {
    requirement: ProcurementRequirement | null
    decision_profile: DecisionProfile | null
    policy_set_version: string | null
    policy_index_version: string | null
    policy_category: string | null
    policy_region: string | null
  } | null
  task_revision: number
  graph_run_id: string
  is_current: boolean
  result: ComparisonPayload
  decision_impact: DecisionImpactResult | null
  policy_retrievals: PolicyRetrievalResult[]
  policy_compliance: PolicyComplianceResult
}

export type PolicyComplianceStatus = 'COMPLIANT' | 'NON_COMPLIANT' | 'REVIEW_REQUIRED' | 'NOT_EVALUATED'
export type PolicyComplianceCheckStatus = 'PASS' | 'FAIL' | 'REVIEW_REQUIRED' | 'NOT_EVALUATED'

export interface PolicyComplianceCheck {
  control_code: string
  status: PolicyComplianceCheckStatus
  reason_code: string
  message: string
  citation_ids: string[]
}

export interface PolicyComplianceSupplierAssessment {
  quote_id: string
  quote_version: number
  supplier_name: string | null
  status: PolicyComplianceStatus
  checks: PolicyComplianceCheck[]
}

export interface PolicyComplianceResult {
  schema_version: string
  disposition: 'COMPLIANT_SUPPLIERS_AVAILABLE' | 'NO_CONFIRMED_COMPLIANT_SUPPLIER' | 'NO_SUPPLIERS'
  recommendation_scope: 'COMPLIANCE_VERIFIED' | 'PROCUREMENT_COMPARISON_ONLY'
  requires_human_review: boolean
  counts: Record<PolicyComplianceStatus, number>
  assessments: PolicyComplianceSupplierAssessment[]
}

export interface HypotheticalComparison {
  hypothetical: true
  formal_recommendation_allowed: false
  policy_assessment_performed: false
  changes: DecisionChanges
  decision_preferences: DecisionPreferences
  excluded_quote_ids: string[]
  assumptions: string[]
  comparison: ComparisonPayload
}

export interface ScenarioSupplierDelta {
  quote_id: string
  baseline_status: string | null
  simulated_status: string | null
  baseline_total_cost: string | null
  simulated_total_cost: string | null
  total_cost_delta: string | null
  baseline_arrival_date: string | null
  simulated_arrival_date: string | null
  excluded: boolean
}

export interface DecisionScenario {
  decision_scenario_id: string
  task_id: string
  base_task_revision: number
  base_result_id: string
  input_sha256: string
  status: 'READY' | 'APPLIED' | 'STALE' | string
  is_current: boolean
  changes: DecisionChanges
  baseline: ComparisonPayload
  simulated: HypotheticalComparison
  delta: {
    recommendation_changed: boolean
    baseline_disposition: string
    simulated_disposition: string
    baseline_recommended_quote_ids: string[]
    simulated_recommended_quote_ids: string[]
    added_recommended_quote_ids: string[]
    removed_recommended_quote_ids: string[]
    supplier_deltas: ScenarioSupplierDelta[]
  }
  applied_task_revision: number | null
  created_at: string
  updated_at: string
}

export interface DecisionScenarioListResponse {
  task_id: string
  task_revision: number
  items: DecisionScenario[]
}

export interface DecisionScenarioApplyResponse {
  task_id: string
  task_revision: number
  status: string
  decision_scenario_id: string
  decision_profile_id: string | null
  changed_requirement_fields: string[]
  changed_decision_preference_fields: string[]
  graph_run_id: string | null
  job_id: string | null
  job_status: string | null
}

export interface DecisionMessage {
  message_id: string
  sequence: number
  role: 'USER' | 'ASSISTANT'
  status: 'SUCCEEDED' | 'FAILED' | 'STALE' | string
  content: string | null
  reference_ids: string[]
  proposed_changes: DecisionChanges | null
  decision_intent_id: string | null
  reply_to_message_id: string | null
  provider: string | null
  model_id: string | null
  prompt_version: string | null
  attempts: number
  error_code: string | null
  error_message: string | null
  created_at: string
}

export interface DecisionConversation {
  conversation_id: string
  task_id: string
  base_task_revision: number
  base_result_id: string
  status: 'ACTIVE' | 'STALE' | 'CLOSED' | string
  title: string
  messages: DecisionMessage[]
  created_at: string
  updated_at: string
}

export interface DecisionConversationListResponse {
  task_id: string
  task_revision: number
  items: DecisionConversation[]
}

export interface ConversationJob {
  job_id: string
  task_id: string
  graph_run_id: string | null
  conversation_id: string
  conversation_message_id: string
  issue_id: string | null
  job_type: string
  status: string
  task_revision: number
  attempts: number
}

export interface SendDecisionMessageResponse {
  conversation_id: string
  message: DecisionMessage
  job: ConversationJob
}

export interface ConfirmDecisionIntentResponse {
  decision_intent_id: string
  status: 'CONFIRMED'
  scenario: DecisionScenario
}

export interface DecisionIntent {
  decision_intent_id: string
  task_id: string
  base_task_revision: number
  base_result_id: string
  source_text: string
  source_sha256: string
  status: 'PROCESSING' | 'READY' | 'CONFIRMED' | 'FAILED' | 'STALE' | string
  is_current: boolean
  parsed_changes: DecisionChanges | null
  confirmation_text: string | null
  provider: string | null
  model_id: string | null
  prompt_version: string | null
  attempts: number
  error_code: string | null
  error_message: string | null
  decision_scenario_id: string | null
  created_at: string
  updated_at: string
}

export interface DecisionIntentListResponse {
  task_id: string
  task_revision: number
  items: DecisionIntent[]
}

export interface QuoteDecisionImpact {
  quote_id: string
  quote_version: number
  status: 'NO_ISSUE' | 'REQUIRES_INVESTIGATION' | 'NON_BLOCKING' | 'UNDETERMINED' | string
  reason_code: string
  message: string
  unknown_fields: string[]
  cost_lower_bound: string | null
  best_confirmed_cost: string | null
  additional_cost_to_tie: string | null
  assumptions: string[]
}

export interface DecisionImpactResult {
  schema_version: string
  task_id: string
  task_revision: number
  input_sha256: string
  comparison: ComparisonPayload
  quote_impacts: QuoteDecisionImpact[]
  blocking_quote_ids: string[]
  nonblocking_unknown_quote_ids: string[]
  scope: string
}

export interface ReviewEvidenceSource {
  source_id: string | null
  kind: string | null
  raw_text: string | null
  page_number: number | null
  row_number: number | null
  column_name: string | null
}

export interface ReviewCandidateField {
  field_id: string
  quote_id: string
  quote_version: number
  field_version: number
  field_name: string
  raw_value: string | null
  normalized_value: unknown
  unit: string | null
  validation_status: string
  origin: string | null
  source_refs: Array<{ source_id: string; quoted_text: string }>
  producer: string
  adapter_version: string | null
  prompt_version: string | null
}

export interface ReviewQuoteReport {
  quote_id: string
  supplier_id: string
  quote_version: number
  document_id: string | null
  original_filename: string | null
  batch_artifact_id: string | null
  review_artifact_id: string | null
  review_status: string | null
  review_pending: boolean
  fields: ReviewCandidateField[]
  evidence_sources: ReviewEvidenceSource[]
}

export interface ReviewProblem extends ReviewFinding {
  quote_id: string
  quote_version: number
  field_version: number | null
  raw_value: string | null
  normalized_value: unknown
  unit: string | null
  document_id: string | null
  original_filename: string | null
  needs_resolution: boolean
  resolution: 'FIELD_CORRECTION' | 'REEXTRACT_OR_SYSTEM_REPAIR' | string
}

export interface ReviewOverviewResponse {
  task_id: string
  task_revision: number
  graph_run_id: string | null
  task_status: string
  review_pending: boolean
  quotes: ReviewQuoteReport[]
  problems: ReviewProblem[]
  blocking_problem_count: number
  problem_count: number
}

export interface InvestigationToolResult {
  tool_name: string
  task_id: string
  task_revision: number
  quote_id: string | null
  input_sha256: string
  status: string
  data: Record<string, unknown>
  sources: Record<string, unknown>[]
  error_code: string | null
}

export interface InvestigationObservation {
  sequence: number
  reason: string
  arguments: Record<string, unknown>
  result: InvestigationToolResult
  latency_ms: number
}

export interface InvestigationCase {
  schema_version: string
  case_id: string
  artifact_id: string
  task_id: string
  task_revision: number
  graph_run_id: string
  kind: 'QUOTE' | 'POLICY'
  quote_id: string | null
  quote_version: number | null
  impact_input_sha256: string
  policy_binding: Record<string, string | null>
  goal: string
  known_facts: Record<string, unknown>
  unknown_fields: string[]
  impact_status: string
  plan: string[]
  observations: InvestigationObservation[]
  status: string
  stored_status: string
  stop_reason: string | null
  model_calls: number
  model_id: string | null
  error_code: string | null
  started_at: string
  clarification: Record<string, unknown>[]
  is_current: boolean
}

export interface SelectionGap {
  quote_id: string
  quote_version: number
  status: string
  failed_reasons: ResultReason[]
  pending_reasons: ResultReason[]
  comparison_reasons: ResultReason[]
  actual_quantity: number | null
  currency: string
  known_cost_subtotal: string | null
  confirmed_total_cost: string | null
  best_other_confirmed_cost: string | null
  cost_difference_vs_other: string | null
  budget_excess: string | null
  total_cost_reduction_to_tie_other: string | null
  delivery_days_late: number | null
  target_arrival_deadline: string
  target_lead_time_days: number | null
  delivery_improvement: HypotheticalComparison | null
  would_be_quote_comparison_choice: boolean | null
  assumptions: string[]
}

export interface ClarificationDraft {
  quote_id: string
  draft_only: true
  sent: false
  text: string
}

export interface SelectionGapResponse {
  schema_version: string
  task_id: string
  task_revision: number
  input_sha256: string
  gaps: SelectionGap[]
  scope: string
  clarification_drafts: ClarificationDraft[]
}

export interface RequirementSimulationResponse {
  task_id: string
  task_revision: number
  result: HypotheticalComparison
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

export interface PolicyImportUploadMetadata {
  title: string
  effective_from: string
  effective_to: string | null
  categories: string[]
  regions: string[]
  policy_set_id?: string
  policy_set_version?: string
  policy_id?: string
  document_id?: string
  document_version?: string
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
