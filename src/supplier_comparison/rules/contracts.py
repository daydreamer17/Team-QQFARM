"""Public input and output contracts for deterministic comparison rules.

The rule engine consumes normalized field candidates. It does not parse source
files, call a model, access a database, or own workflow state.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from supplier_comparison.extraction.contracts import QuoteFieldCandidate


RULE_VERSION = "supplier-comparison/2.0.0"


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FeasibilityStatus(StrEnum):
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    PENDING = "PENDING"


class ComparisonDisposition(StrEnum):
    """Task-level outcome; deliberately separate from supplier feasibility."""

    RECOMMENDATION_AVAILABLE = "RECOMMENDATION_AVAILABLE"
    PENDING_INPUT = "PENDING_INPUT"
    NO_FEASIBLE_QUOTES = "NO_FEASIBLE_QUOTES"
    EMPTY_SCOPE = "EMPTY_SCOPE"


class RankingMode(StrEnum):
    """User-selectable deterministic ranking modes for comparisons/simulations."""

    LOWEST_CONFIRMED_TOTAL_COST = "LOWEST_CONFIRMED_TOTAL_COST"
    FASTEST_CONFIRMED_DELIVERY = "FASTEST_CONFIRMED_DELIVERY"
    LOWEST_COST_THEN_FASTEST_DELIVERY = "LOWEST_COST_THEN_FASTEST_DELIVERY"
    FASTEST_DELIVERY_THEN_LOWEST_COST = "FASTEST_DELIVERY_THEN_LOWEST_COST"


class RankingCriterion(StrEnum):
    LOWEST_CONFIRMED_TOTAL_COST = "LOWEST_CONFIRMED_TOTAL_COST"
    FASTEST_CONFIRMED_DELIVERY = "FASTEST_CONFIRMED_DELIVERY"
    LONGEST_CONFIRMED_PAYMENT_TERM = "LONGEST_CONFIRMED_PAYMENT_TERM"
    HIGHEST_SUPPLIER_PERFORMANCE = "HIGHEST_SUPPLIER_PERFORMANCE"
    HIGHEST_HISTORICAL_ON_TIME_RATE = "HIGHEST_HISTORICAL_ON_TIME_RATE"
    LOWEST_HISTORICAL_REJECTED_LINE_RATE = "LOWEST_HISTORICAL_REJECTED_LINE_RATE"


class CriterionStatus(StrEnum):
    COMPARABLE = "COMPARABLE"
    MISSING = "MISSING"
    INCOMPARABLE = "INCOMPARABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CriterionDirection(StrEnum):
    ASCENDING = "ASCENDING"
    DESCENDING = "DESCENDING"


class IdentityMatchStatus(StrEnum):
    MATCHED = "MATCHED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    CONFLICT = "CONFLICT"


class HistoryAvailabilityStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
    NO_DATA = "NO_DATA"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class PaymentParseStatus(StrEnum):
    COMPARABLE = "COMPARABLE"
    MISSING = "MISSING"
    INCOMPARABLE = "INCOMPARABLE"


class DecisionPreferences(FrozenModel):
    """Decision-only settings; separate from the buyer's hard requirement."""

    schema_version: Literal["decision-preferences/2.0"] = "decision-preferences/2.0"
    primary_criterion: RankingCriterion | None = None
    secondary_criterion: RankingCriterion | None = None
    # Accepted only at the compatibility boundary. It is deliberately excluded
    # from new serialized payloads so V2 is single-write while remaining dual-read.
    ranking_mode: RankingMode | None = Field(default=None, exclude=True)
    excluded_supplier_ids: tuple[str, ...] = ()
    cost_tolerance_amount: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="before")
    @classmethod
    def translate_legacy_ranking_mode(cls, value: Any) -> Any:
        if not isinstance(value, dict) or not value.get("ranking_mode"):
            return value
        payload = dict(value)
        primary, secondary = ranking_pair(RankingMode(payload["ranking_mode"]))
        if payload.get("primary_criterion") not in (None, primary):
            raise ValueError("legacy and V2 primary ranking fields conflict")
        if payload.get("secondary_criterion") not in (None, secondary):
            raise ValueError("legacy and V2 secondary ranking fields conflict")
        payload["primary_criterion"] = primary
        payload["secondary_criterion"] = secondary
        return payload

    @field_validator("cost_tolerance_amount", mode="before")
    @classmethod
    def reject_binary_float_tolerance(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("money must be supplied as a decimal string or Decimal")
        return value

    @field_validator("excluded_supplier_ids")
    @classmethod
    def supplier_ids_are_unique_and_nonempty(cls, value: tuple[str, ...]):
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("excluded supplier IDs cannot be empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("excluded supplier IDs must be unique")
        return normalized

    @model_validator(mode="after")
    def preferences_are_consistent(self) -> "DecisionPreferences":
        if (
            self.primary_criterion is not None
            and self.secondary_criterion == self.primary_criterion
        ):
            raise ValueError("primary and secondary criteria must differ")
        if self.secondary_criterion is not None and self.primary_criterion is None:
            raise ValueError("secondary criterion requires a primary criterion")
        if (
            self.cost_tolerance_amount is not None
            and self.primary_criterion
            != RankingCriterion.LOWEST_CONFIRMED_TOTAL_COST
        ):
            raise ValueError("cost tolerance requires cost-primary ranking")
        return self


class ProcurementRequirement(FrozenModel):
    """Buyer-owned requirement; never inferred from supplier quotations."""

    manufacturer: str = Field(min_length=1)
    manufacturer_part_number: str = Field(min_length=1)
    package: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    condition: str = Field(min_length=1)
    allow_substitutes: bool
    base_unit: str = Field(min_length=1)
    required_quantity: int = Field(gt=0)
    quantity_unit: str = Field(min_length=1)
    budget_amount: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    includes_shipping: bool
    tax_mode: str = Field(min_length=1)
    other_fees_required: bool
    planned_order_date: date | None = None
    delivery_deadline: date
    delivery_location: str = Field(min_length=1)
    ranking_preference: RankingCriterion
    secondary_preference: RankingCriterion | None = None

    @field_validator("budget_amount", mode="before")
    @classmethod
    def reject_binary_float_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("money must be supplied as a decimal string or Decimal")
        return value

    @model_validator(mode="after")
    def dates_are_ordered(self) -> "ProcurementRequirement":
        if (
            self.planned_order_date is not None
            and self.delivery_deadline < self.planned_order_date
        ):
            raise ValueError("delivery_deadline cannot precede planned_order_date")
        if self.secondary_preference == self.ranking_preference:
            raise ValueError("primary and secondary criteria must differ")
        return self


def ranking_pair(mode: RankingMode) -> tuple[RankingCriterion, RankingCriterion | None]:
    if mode == RankingMode.LOWEST_CONFIRMED_TOTAL_COST:
        return RankingCriterion.LOWEST_CONFIRMED_TOTAL_COST, None
    if mode == RankingMode.FASTEST_CONFIRMED_DELIVERY:
        return RankingCriterion.FASTEST_CONFIRMED_DELIVERY, None
    if mode == RankingMode.LOWEST_COST_THEN_FASTEST_DELIVERY:
        return (
            RankingCriterion.LOWEST_CONFIRMED_TOTAL_COST,
            RankingCriterion.FASTEST_CONFIRMED_DELIVERY,
        )
    return (
        RankingCriterion.FASTEST_CONFIRMED_DELIVERY,
        RankingCriterion.LOWEST_CONFIRMED_TOTAL_COST,
    )


def ranking_mode_for(requirement: ProcurementRequirement) -> RankingMode | None:
    pair = (requirement.ranking_preference.value, requirement.secondary_preference.value if requirement.secondary_preference else None)
    return {
        ("LOWEST_CONFIRMED_TOTAL_COST", None): RankingMode.LOWEST_CONFIRMED_TOTAL_COST,
        ("FASTEST_CONFIRMED_DELIVERY", None): RankingMode.FASTEST_CONFIRMED_DELIVERY,
        ("LOWEST_CONFIRMED_TOTAL_COST", "FASTEST_CONFIRMED_DELIVERY"):
            RankingMode.LOWEST_COST_THEN_FASTEST_DELIVERY,
        ("FASTEST_CONFIRMED_DELIVERY", "LOWEST_CONFIRMED_TOTAL_COST"):
            RankingMode.FASTEST_DELIVERY_THEN_LOWEST_COST,
    }.get(pair)


class QuoteInput(FrozenModel):
    """One normalized quote version at the B-to-C/D boundary."""

    quote_id: str = Field(min_length=1)
    quote_version: int = Field(ge=1)
    supplier_id: str | None = None
    payment_start_event_override: str | None = None
    payment_start_event_evidence_ref: str | None = None
    candidates: tuple[QuoteFieldCandidate, ...]

    @field_validator("payment_start_event_override")
    @classmethod
    def supported_payment_start_event(cls, value: str | None) -> str | None:
        if value is not None and value not in {"INVOICE_DATE"}:
            raise ValueError("unsupported payment start event")
        return value

    @model_validator(mode="after")
    def candidates_match_quote_and_are_unique(self) -> "QuoteInput":
        if self.payment_start_event_override and not self.payment_start_event_evidence_ref:
            raise ValueError("payment start event override requires an audit evidence reference")
        seen: set[str] = set()
        for candidate in self.candidates:
            if (
                candidate.quote_id != self.quote_id
                or candidate.quote_version != self.quote_version
            ):
                raise ValueError("candidate belongs to another quote/version")
            if candidate.field_name in seen:
                raise ValueError(f"duplicate candidate field: {candidate.field_name}")
            seen.add(candidate.field_name)
        return self


class ComparisonRequest(FrozenModel):
    """Complete deterministic input supplied by D to C."""

    requirement: ProcurementRequirement
    quotes: tuple[QuoteInput, ...]
    evaluated_at: datetime
    cost_tolerance_amount: Decimal | None = Field(default=None, ge=0)
    excluded_quote_ids: tuple[str, ...] = ()
    supplier_history_snapshots: tuple["SupplierHistorySnapshot", ...] = ()
    history_dataset_context: "SupplierHistoryDatasetContext | None" = None

    @field_validator("cost_tolerance_amount", mode="before")
    @classmethod
    def reject_binary_float_tolerance(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("money must be supplied as a decimal string or Decimal")
        return value

    @model_validator(mode="after")
    def quote_versions_are_unique(self) -> "ComparisonRequest":
        quote_ids = [quote.quote_id for quote in self.quotes]
        if len(quote_ids) != len(set(quote_ids)):
            raise ValueError("comparison scope must contain one active version per quote")
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")
        if not set(self.excluded_quote_ids).issubset(set(quote_ids)):
            raise ValueError("excluded_quote_ids references a quote outside the scope")
        snapshot_ids = [item.quote_id for item in self.supplier_history_snapshots]
        if len(snapshot_ids) != len(set(snapshot_ids)):
            raise ValueError("history snapshots must be unique by quote ID")
        if not set(snapshot_ids).issubset(set(quote_ids)):
            raise ValueError("history snapshot references a quote outside the scope")
        return self


class RateMetric(FrozenModel):
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    rate: Decimal | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def rate_matches_counts(self) -> "RateMetric":
        expected = Decimal(self.numerator) / Decimal(self.denominator) if self.denominator else None
        if self.numerator > self.denominator or self.rate != expected:
            raise ValueError("rate must exactly match numerator / denominator")
        return self


class SupplierHistoryDatasetContext(FrozenModel):
    dataset_id: str
    dataset_version: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rating_method_version: str
    scope: dict[str, Any]
    as_of_date: date
    period_start: date
    period_end: date
    is_synthetic: bool


class SupplierHistorySnapshot(FrozenModel):
    quote_id: str
    supplier_id: str | None = None
    supplier_name: str | None = None
    identity_match_status: IdentityMatchStatus
    history_availability_status: HistoryAvailabilityStatus
    overall_grade: str | None = None
    on_time: RateMetric | None = None
    rejected_lines: RateMetric | None = None
    evidence_refs: tuple[str, ...] = ()

    @field_validator("overall_grade")
    @classmethod
    def valid_grade(cls, value: str | None) -> str | None:
        if value is not None and value not in {"A", "B", "C", "D", "N"}:
            raise ValueError("unsupported supplier history grade")
        return value


class PaymentTermEvaluation(FrozenModel):
    raw_text: str | None = None
    normalized_text: str | None = None
    payment_type: str | None = None
    net_days: int | None = Field(default=None, ge=0)
    payment_start_event: str | None = None
    parse_status: PaymentParseStatus
    reason_codes: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    validation_status: str | None = None
    normalization_rule_version: str = "payment-term-evaluation/1.0.0"


CriterionExactValue = Decimal | int | date | str | None


class CriterionEvaluation(FrozenModel):
    criterion: RankingCriterion
    status: CriterionStatus
    exact_value: CriterionExactValue = None
    display_value: str | None = None
    direction: CriterionDirection
    reason_codes: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def comparable_has_a_value(self) -> "CriterionEvaluation":
        if self.status == CriterionStatus.COMPARABLE and self.exact_value is None:
            raise ValueError("comparable criterion requires an exact value")
        return self


class RankingRound(FrozenModel):
    criterion: RankingCriterion
    candidate_quote_ids_before: tuple[str, ...]
    candidate_quote_ids_after: tuple[str, ...]
    applied: bool
    reason_codes: tuple[str, ...] = ()


class RankingTrace(FrozenModel):
    ordered_criteria: tuple[RankingCriterion, ...]
    criterion_directions: dict[str, CriterionDirection]
    candidate_values: dict[str, dict[str, Any]]
    rounds: tuple[RankingRound, ...]
    cost_tolerance_applied: bool = False
    secondary_applied: bool = False
    tie_group: tuple[str, ...] = ()
    blocker_reason_codes: tuple[str, ...] = ()
    comparison_disposition: ComparisonDisposition
    original_scope_count: int = Field(ge=0)
    excluded_quote_ids: tuple[str, ...] = ()


class RuleIssue(FrozenModel):
    """Stable machine code plus concise human-readable reason."""

    code: str = Field(min_length=1)
    fields: tuple[str, ...] = ()
    message: str = Field(min_length=1)


class QuantityBreakdown(FrozenModel):
    """Auditable quantity conversion, expressed in the requirement base unit."""

    base_unit: str = Field(min_length=1)
    required_quantity: int = Field(gt=0)
    moq_quantity: int = Field(gt=0)
    order_multiple: int = Field(gt=0)
    actual_purchase_quantity: int = Field(gt=0)


class QuantityCalculation(FrozenModel):
    breakdown: QuantityBreakdown | None = None
    pending_reasons: tuple[RuleIssue, ...] = ()

    @model_validator(mode="after")
    def result_is_complete_or_pending(self) -> "QuantityCalculation":
        if self.breakdown is None and not self.pending_reasons:
            raise ValueError("incomplete quantity calculation requires pending reasons")
        if self.breakdown is not None and self.pending_reasons:
            raise ValueError("completed quantity calculation cannot have pending reasons")
        return self


class CostCalculation(FrozenModel):
    """Partial or complete confirmed cost calculation."""

    currency: str = Field(min_length=3, max_length=3)
    goods_cost: Decimal | None = Field(default=None, ge=0)
    shipping_cost: Decimal | None = Field(default=None, ge=0)
    other_fees_cost: Decimal | None = Field(default=None, ge=0)
    known_cost_subtotal: Decimal | None = Field(default=None, ge=0)
    total_cost: Decimal | None = Field(default=None, ge=0)
    pending_reasons: tuple[RuleIssue, ...] = ()

    @field_validator(
        "goods_cost",
        "shipping_cost",
        "other_fees_cost",
        "known_cost_subtotal",
        "total_cost",
        mode="before",
    )
    @classmethod
    def reject_binary_float_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("money must be supplied as a decimal string or Decimal")
        return value

    @model_validator(mode="after")
    def confirmed_total_matches_completeness(self) -> "CostCalculation":
        if self.total_cost is not None and self.pending_reasons:
            raise ValueError("confirmed total_cost cannot coexist with pending reasons")
        return self


class SpecificationCheck(FrozenModel):
    """Confirmed specification failures and unresolved specification questions."""

    failed_reasons: tuple[RuleIssue, ...] = ()
    pending_reasons: tuple[RuleIssue, ...] = ()

    @property
    def is_confirmed_match(self) -> bool:
        return not self.failed_reasons and not self.pending_reasons


class DeliveryCheck(FrozenModel):
    estimated_arrival_date: date | None = None
    failed_reasons: tuple[RuleIssue, ...] = ()
    pending_reasons: tuple[RuleIssue, ...] = ()

    @property
    def is_confirmed_on_time(self) -> bool:
        return (
            self.estimated_arrival_date is not None
            and not self.failed_reasons
            and not self.pending_reasons
        )


class SupplierEvaluation(FrozenModel):
    quote_id: str = Field(min_length=1)
    quote_version: int = Field(ge=1)
    supplier_name: str | None = None
    status: FeasibilityStatus
    actual_quantity: int | None = Field(default=None, gt=0)
    goods_cost: Decimal | None = Field(default=None, ge=0)
    known_cost_subtotal: Decimal | None = Field(default=None, ge=0)
    total_cost: Decimal | None = Field(default=None, ge=0)
    estimated_arrival_date: date | None = None
    payment_term: PaymentTermEvaluation | None = None
    history_snapshot: SupplierHistorySnapshot | None = None
    criterion_evaluations: tuple[CriterionEvaluation, ...] = ()
    failed_reasons: tuple[RuleIssue, ...] = ()
    pending_reasons: tuple[RuleIssue, ...] = ()

    @field_validator("goods_cost", "known_cost_subtotal", "total_cost", mode="before")
    @classmethod
    def reject_binary_float_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("money must be supplied as a decimal string or Decimal")
        return value

    @model_validator(mode="after")
    def status_matches_reasons_and_cost(self) -> "SupplierEvaluation":
        if self.status == FeasibilityStatus.FEASIBLE:
            if self.failed_reasons or self.pending_reasons:
                raise ValueError("FEASIBLE result cannot contain failed or pending reasons")
            if self.total_cost is None:
                raise ValueError("FEASIBLE result requires a confirmed total_cost")
        elif self.status == FeasibilityStatus.INFEASIBLE and not self.failed_reasons:
            raise ValueError("INFEASIBLE result requires at least one failed reason")
        elif self.status == FeasibilityStatus.PENDING and not self.pending_reasons:
            raise ValueError("PENDING result requires at least one pending reason")
        return self


class ComparisonResult(FrozenModel):
    rule_version: str = RULE_VERSION
    evaluated_at: datetime
    disposition: ComparisonDisposition
    supplier_results: tuple[SupplierEvaluation, ...]
    ranked_quote_ids: tuple[tuple[str, ...], ...] = ()
    recommended_quote_ids: tuple[str, ...] = ()
    pending_quote_ids: tuple[str, ...] = ()
    blocking_pending_quote_ids: tuple[str, ...] = ()
    comparison_reasons: tuple[RuleIssue, ...] = ()
    final_recommendation_allowed: bool
    ranking_trace: RankingTrace | None = None

    @model_validator(mode="after")
    def recommendation_matches_disposition(self) -> "ComparisonResult":
        has_recommendation = bool(self.recommended_quote_ids)
        if self.disposition == ComparisonDisposition.RECOMMENDATION_AVAILABLE:
            if not self.final_recommendation_allowed or not has_recommendation:
                raise ValueError("available recommendation requires recommended quote IDs")
            if self.blocking_pending_quote_ids or self.comparison_reasons:
                raise ValueError("available recommendation cannot have blocking reasons")
        elif self.final_recommendation_allowed or has_recommendation:
            raise ValueError("non-final disposition cannot publish a recommendation")
        if (
            self.disposition == ComparisonDisposition.PENDING_INPUT
            and not self.blocking_pending_quote_ids
            and not self.comparison_reasons
        ):
            raise ValueError("PENDING_INPUT requires a blocking quote or comparison reason")
        if self.disposition == ComparisonDisposition.EMPTY_SCOPE and self.supplier_results:
            raise ValueError("EMPTY_SCOPE cannot contain supplier results")
        result_ids = {result.quote_id for result in self.supplier_results}
        referenced_ids = {
            *self.recommended_quote_ids,
            *self.pending_quote_ids,
            *self.blocking_pending_quote_ids,
            *(quote_id for group in self.ranked_quote_ids for quote_id in group),
        }
        if not referenced_ids.issubset(result_ids):
            raise ValueError("comparison output references an unknown quote ID")
        return self
