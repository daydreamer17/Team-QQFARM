"""Deterministic supplier-comparison rules and their public contracts."""

from .contracts import (
    ComparisonDisposition,
    ComparisonRequest,
    ComparisonResult,
    CostCalculation,
    DecisionPreferences,
    DeliveryCheck,
    FeasibilityStatus,
    ProcurementRequirement,
    QuantityBreakdown,
    QuantityCalculation,
    QuoteInput,
    RankingMode,
    RuleIssue,
    SpecificationCheck,
    SupplierEvaluation,
    ranking_mode_for,
    ranking_pair,
)
from .cost import calculate_cost
from .delivery import check_delivery
from .engine import compare_suppliers, evaluate_supplier
from .decision_impact import (
    DecisionImpactRequest, DecisionImpactResult, ImpactStatus, QuoteDecisionImpact,
    analyze_decision_impact, decision_comparison_request,
)
from .integration import (
    compare_extraction_batches,
    compare_reviewed_extractions,
    quote_input_from_extraction,
    quote_input_from_reviewed_extraction,
    analyze_reviewed_decision_impact,
    quote_input_for_decision_impact,
)
from .quantity import calculate_quantity
from .specification import check_specification
from .selection_gap import (
    RequirementChanges, SelectionGapResult, analyze_selection_gap,
    simulate_requirement_change, draft_clarification,
)

__all__ = [
    "RequirementChanges", "SelectionGapResult", "analyze_selection_gap",
    "simulate_requirement_change", "draft_clarification",
    "DecisionImpactRequest",
    "DecisionImpactResult",
    "ImpactStatus",
    "QuoteDecisionImpact",
    "analyze_decision_impact",
    "decision_comparison_request",
    "analyze_reviewed_decision_impact",
    "quote_input_for_decision_impact",
    "ComparisonDisposition",
    "ComparisonRequest",
    "ComparisonResult",
    "CostCalculation",
    "DecisionPreferences",
    "DeliveryCheck",
    "FeasibilityStatus",
    "ProcurementRequirement",
    "QuantityBreakdown",
    "QuantityCalculation",
    "QuoteInput",
    "RankingMode",
    "RuleIssue",
    "SpecificationCheck",
    "SupplierEvaluation",
    "ranking_mode_for",
    "ranking_pair",
    "calculate_cost",
    "calculate_quantity",
    "check_delivery",
    "check_specification",
    "compare_suppliers",
    "compare_extraction_batches",
    "compare_reviewed_extractions",
    "evaluate_supplier",
    "quote_input_from_extraction",
    "quote_input_from_reviewed_extraction",
]
