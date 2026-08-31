"""Batch ownership data models and transformation."""

from __future__ import annotations

from dataclasses import dataclass, field

from ...core.line_selection import (
    LineRangeBuilder as _LineRangeBuilder,
    LineRanges,
)
from .absence_claims import AbsenceClaim as _AbsenceClaim
from .claims import (
    PresenceClaim as _PresenceClaim,
    parse_ownership_line_ranges as _claim_parse_line_ranges,
    presence_claims_from_source_lines as _claim_presence_claims_from_source_lines,
)
from .references import BaselineReference as _BaselineReference
from .metadata_types import BatchOwnershipMetadata
from .replacement_units import (
    ReplacementUnit as _ReplacementUnit,
    normalize_replacement_units as _replacement_normalize_units,
)
from .resolved_replacement_alternatives import (
    ResolvedReplacementAlternative as _ResolvedReplacementAlternative,
    resolve_replacement_alternatives as _resolve_replacement_alternatives,
)


@dataclass
class BatchOwnership:
    """Represents batch ownership in batch source space.

    A batch owns content relative to its batch source commit:
    - presence_claims: Batch-source lines that must exist after application
    - deletions: Suppression constraints for old-side content (absence claims)
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
        presence_lines = _LineRangeBuilder()
        for claim in self.presence_claims:
            for range_start, range_end in claim.source_line_set().ranges():
                presence_lines.add_range(range_start, range_end)
        return presence_lines.finish()

    def presence_baseline_references(self) -> dict[int, _BaselineReference]:
        """Return baseline references keyed by claimed batch-source line."""
        references: dict[int, _BaselineReference] = {}
        for claim in self.presence_claims:
            references.update(claim.baseline_references)
        return references

    def to_metadata_dict(self) -> BatchOwnershipMetadata:
        """Convert to metadata dictionary format for storage."""
        data: BatchOwnershipMetadata = {
            "presence_claims": [claim.to_dict() for claim in self.presence_claims],
            "deletions": [claim.to_dict() for claim in self.deletions],
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

    def to_attribution_metadata_dict(self) -> BatchOwnershipMetadata:
        """Serialize only the compact claims required by attribution."""
        presence_lines = LineRanges.from_specs(
            source_line
            for claim in self.presence_claims
            for source_line in claim.source_lines
        )
        return {
            "presence_claims": (
                [{"source_lines": presence_lines.to_range_strings()}]
                if presence_lines
                else []
            ),
            "deletions": [claim.to_attribution_dict() for claim in self.deletions],
        }

    def to_applied_metadata_dict(self) -> BatchOwnershipMetadata:
        """Save only the claims needed to display or reapply the batch."""
        data = self.to_attribution_metadata_dict()
        for serialized, claim in zip(
            data["deletions"],
            self.deletions,
            strict=True,
        ):
            if claim.baseline_reference is not None:
                serialized["baseline_reference"] = claim.baseline_reference.to_dict()
            if claim.source_alternative:
                serialized["source_alternative"] = True
            if claim.complete_file_pair:
                serialized["complete_file_pair"] = True

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
        presence_lines = self.presence_line_set()
        return ResolvedBatchOwnership(
            presence_lines,
            self.deletions,
            self.presence_claims,
            _resolve_replacement_alternatives(
                presence_lines,
                self.deletions,
                self.replacement_units,
            ),
        )


@dataclass
class ResolvedBatchOwnership:
    """Batch claims ready for applying or display.

    Attributes:
        presence_line_set: One-based source lines owned by the batch.
        deletion_claims: Groups of old lines that must remain absent.
        presence_claims: Original claims, kept for the lines beside them.
        replacement_alternatives: Validated saved and live versions.
    """

    presence_line_set: LineRanges  # Batch source line numbers (1-indexed)
    deletion_claims: list[_AbsenceClaim]  # Separate constraints, not collapsed
    presence_claims: list[_PresenceClaim]
    replacement_alternatives: tuple[_ResolvedReplacementAlternative, ...]

    def retained_replacement_lines(self) -> LineRanges:
        """Return source lines that hold older versions kept for replay."""
        return LineRanges.from_ranges(
            (
                alternative.live_envelope.start.offset + 1,
                alternative.live_envelope.end.offset,
            )
            for alternative in self.replacement_alternatives
        )
