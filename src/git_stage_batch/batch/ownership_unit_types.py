"""Ownership unit value objects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..core.line_selection import LineRanges
from .ownership import AbsenceClaim, ReplacementUnitOrigin


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
        atomic_reason: Explanation for why unit is atomic (for debugging/errors)
        preserves_replacement_unit: True when this unit came from persisted replacement metadata
        replacement_origin: Original parent replacement context, when known
    """
    kind: OwnershipUnitKind
    claimed_source_lines: LineRanges
    deletion_claims: list[AbsenceClaim]
    display_line_ids: LineRanges
    is_atomic: bool = False
    atomic_reason: str | None = None
    preserves_replacement_unit: bool = False
    replacement_origin: ReplacementUnitOrigin | None = None
