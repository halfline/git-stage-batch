"""Typed shapes for serialized batch-ownership metadata."""

from __future__ import annotations

from typing import TypedDict


class BaselineReferenceMetadata(TypedDict, total=False):
    """Serialized boundary identity for a baseline position."""

    after_line: int | None
    after_blob: str
    before_line: int | None
    before_blob: str


class PresenceClaimMetadata(TypedDict, total=False):
    """Serialized presence claim."""

    source_lines: list[str]
    baseline_references: dict[str, BaselineReferenceMetadata]


class AbsenceClaimMetadata(TypedDict, total=False):
    """Serialized absence claim."""

    after_source_line: int | None
    blob: str
    baseline_reference: BaselineReferenceMetadata


class ReplacementUnitOriginMetadata(TypedDict, total=False):
    """Serialized parent replacement coordinates."""

    old_start: int
    old_end: int
    new_start: int
    new_end: int
    baseline_reference: BaselineReferenceMetadata


class ReplacementUnitMetadata(TypedDict, total=False):
    """Serialized coupling between presence and absence claims."""

    presence_lines: list[str | int]
    claimed_lines: list[str | int]
    deletion_indices: list[int]
    original_unit: ReplacementUnitOriginMetadata


class BatchOwnershipMetadata(TypedDict, total=False):
    """Complete serialized ownership record for one batch file."""

    presence_claims: list[PresenceClaimMetadata]
    claimed_lines: list[str | int]
    deletions: list[AbsenceClaimMetadata]
    replacement_units: list[ReplacementUnitMetadata]
