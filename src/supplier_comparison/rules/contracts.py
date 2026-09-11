"""Public input and output contracts for deterministic comparison rules.

The rule engine consumes normalized field candidates. It does not parse source
files, call a model, access a database, or own workflow state.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from supplier_comparison.extraction.contracts import QuoteFieldCandidate


RULE_VERSION = "supplier-comparison/1.0.0"


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
    ranking_preference: str = Field(min_length=1)
    secondary_preference: str | None = None

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
        return self


class QuoteInput(FrozenModel):
    """One normalized quote version at the B-to-C/D boundary."""

    quote_id: str = Field(min_length=1)
    quote_version: int = Field(ge=1)
    candidates: tuple[QuoteFieldCandidate, ...]

    @model_validator(mode="after")
    def candidates_match_quote_and_are_unique(self) -> "QuoteInput":
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

    @model_validator(mode="after")
    def quote_versions_are_unique(self) -> "ComparisonRequest":
        quote_ids = [quote.quote_id for quote in self.quotes]
        if len(quote_ids) != len(set(quote_ids)):
            raise ValueError("comparison scope must contain one active version per quote")
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")
        return self


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
