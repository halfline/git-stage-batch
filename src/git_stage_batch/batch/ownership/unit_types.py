"""Ownership unit value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ...core.line_selection import LineRanges
from .absence_claims import AbsenceClaim
from .references import BaselineReference
from .replacement_units import (
    NoReplacementUnitOrigin,
    ReplacementUnitOriginEvidence,
)


class OwnershipUnitKind(Enum):
    """Type of ownership unit for semantic filtering operations."""

    PRESENCE_ONLY = "presence_only"
    """Pure claimed lines with no coupled deletions (non-atomic)."""

    REPLACEMENT = "replacement"
    """Claimed lines coupled with absence claims (atomic)."""

    DELETION_ONLY = "deletion_only"
    """Pure absence claims with no claimed lines (atomic)."""


@dataclass
class OwnershipUnit:
    """Semantic unit of ownership that should be manipulated atomically.

    Represents the coupling between claimed lines and absence claims.
    Used for semantic filtering operations like line-level reset.

    Attributes:
        kind: Type of ownership unit
        claimed_source_lines: Batch source line numbers owned by this unit
        deletion_claims: Absence claims that are part of this unit
        display_line_ids: Display line IDs that map to this unit (from reconstructed display)
        is_atomic: If True, partial removal is not allowed
        preserves_replacement_unit: True when this unit came from persisted replacement metadata
        replacement_origin_evidence: Parent context and its authority tier
    """
    kind: OwnershipUnitKind
    claimed_source_lines: LineRanges
    deletion_claims: list[AbsenceClaim]
    display_line_ids: LineRanges
    baseline_references: dict[int, BaselineReference] = field(default_factory=dict)
    is_atomic: bool = False
    preserves_replacement_unit: bool = False
    replacement_origin_evidence: ReplacementUnitOriginEvidence = field(
        default_factory=NoReplacementUnitOrigin
    )
