"""Batch ownership data models and transformation."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.line_selection import (
    LineRanges,
)
from .ownership_absence_claims import AbsenceClaim as _AbsenceClaim
from .ownership_claims import (
    PresenceClaim as _PresenceClaim,
    parse_ownership_line_ranges as _claim_parse_line_ranges,
    presence_claims_from_source_lines as _claim_presence_claims_from_source_lines,
)
from .ownership_references import BaselineReference as _BaselineReference
from .ownership_replacement_units import (
    ReplacementUnit as _ReplacementUnit,
    normalize_replacement_units as _replacement_normalize_units,
)


@dataclass
class BatchOwnership:
    """Represents batch ownership in batch source space.

    A batch owns content relative to its batch source commit:
    - presence_claims: Batch-source lines that must exist after application
    - deletions: Suppression constraints for baseline content (absence claims)
    - replacement_units: Optional explicit coupling between claims and deletions
    """
    presence_claims: list[_PresenceClaim]
    deletions: list[_AbsenceClaim]  # Separate deletion constraints
    replacement_units: list[_ReplacementUnit] = field(default_factory=list)

    @classmethod
    def from_presence_lines(
        cls,
        source_lines: list[str],
        deletions: list[_AbsenceClaim] | None = None,
        *,
        replacement_units: list[_ReplacementUnit] | None = None,
        baseline_references: dict[int, _BaselineReference] | None = None,
    ) -> BatchOwnership:
        """Create ownership from source-line ranges.

        This is a construction helper for tests and call sites that naturally
        start with a flat set of source-line ranges. The stored model remains a
        list of PresenceClaim objects.
        """
        return cls(
            presence_claims=_claim_presence_claims_from_source_lines(
                _claim_parse_line_ranges(source_lines),
                baseline_references or {},
            ),
            deletions=deletions or [],
            replacement_units=replacement_units or [],
        )

    def is_empty(self) -> bool:
        """Check if this ownership is empty (no presence claims or deletions)."""
        return not self.presence_claims and not self.deletions

    def presence_line_set(self) -> LineRanges:
        """Return all batch-source lines claimed present by this ownership."""
        presence_lines = LineRanges.empty()
        for claim in self.presence_claims:
            presence_lines = presence_lines.union(claim.source_line_set())
        return presence_lines

    def presence_baseline_references(self) -> dict[int, _BaselineReference]:
        """Return baseline references keyed by claimed batch-source line."""
        references: dict[int, _BaselineReference] = {}
        for claim in self.presence_claims:
            references.update(claim.baseline_references)
        return references

    def to_metadata_dict(self) -> dict:
        """Convert to metadata dictionary format for storage."""
        data = {
            "presence_claims": [claim.to_dict() for claim in self.presence_claims],
            "deletions": [claim.to_dict() for claim in self.deletions]
        }
        replacement_units = [
            unit.to_dict()
            for unit in _replacement_normalize_units(
                self.replacement_units,
                deletion_count=len(self.deletions),
            )
        ]
        if replacement_units:
            data["replacement_units"] = replacement_units
        return data

    def resolve(self) -> ResolvedBatchOwnership:
        """Resolve into representation for materialization and merge.

        Returns presence lines as a selection and absence claims as a list
        (preserving structure).
        """
        return ResolvedBatchOwnership(self.presence_line_set(), self.deletions)


@dataclass
class ResolvedBatchOwnership:
    """Resolved ownership representation for materialization and merge.

    Preserves the structure of absence claims as separate constraints.

    Attributes:
        presence_line_set: Batch source line numbers (1-indexed, identity-based)
        deletion_claims: List of suppression constraints (order and structure preserved)
    """
    presence_line_set: LineRanges  # Batch source line numbers (1-indexed)
    deletion_claims: list[_AbsenceClaim]  # Separate constraints, not collapsed
