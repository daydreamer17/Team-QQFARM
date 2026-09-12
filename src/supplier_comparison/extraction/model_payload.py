"""The deliberately narrow JSON shape that a model is allowed to produce."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from .contracts import SourceCitation


NonEmptyStrictString = Annotated[StrictStr, Field(min_length=1)]
NormalizedModelScalar = StrictStr | StrictInt | StrictBool
NonEmptySourceRefs = Annotated[tuple[SourceCitation, ...], Field(min_length=1)]
EmptySourceRefs = Annotated[tuple[SourceCitation, ...], Field(max_length=0)]


class _ModelFieldCandidateBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: NonEmptyStrictString
    unit: StrictStr | None


class ExtractedModelFieldCandidate(_ModelFieldCandidateBase):
    """A present, unambiguous document value with a usable normalization."""

    raw_value: NonEmptyStrictString
    normalized_value: NormalizedModelScalar
    validation_status: Literal["EXTRACTED"]
    source_refs: NonEmptySourceRefs


class MissingModelFieldCandidate(_ModelFieldCandidateBase):
    """A value that is absent from the document and therefore has no evidence."""

    unit: None
    raw_value: None
    normalized_value: None
    validation_status: Literal["MISSING"]
    source_refs: EmptySourceRefs


class ConflictModelFieldCandidate(_ModelFieldCandidateBase):
    """An ambiguous or contradictory document value requiring later review."""

    raw_value: NonEmptyStrictString
    normalized_value: NormalizedModelScalar | None
    validation_status: Literal["CONFLICT"]
    source_refs: NonEmptySourceRefs


ModelFieldCandidate = Annotated[
    ExtractedModelFieldCandidate | MissingModelFieldCandidate | ConflictModelFieldCandidate,
    Field(discriminator="validation_status"),
]


class ModelExtractionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: tuple[ModelFieldCandidate, ...]
