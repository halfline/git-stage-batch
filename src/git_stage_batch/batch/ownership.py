"""Batch ownership data models and transformation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..core.line_selection import (
    LineRanges,
)
from ..core.buffer import (
    LineBuffer,
    buffer_byte_chunks,
)
from ..utils.repository_buffers import (
    load_git_blob_as_buffer,
)
from ..utils.git_object_io import (
    create_git_blob,
    read_git_blobs_as_bytes,
)
from .ownership_claims import (
    parse_ownership_line_ranges as _claim_parse_line_ranges,
    presence_claims_from_source_lines as _claim_presence_claims_from_source_lines,
)
from .ownership_metadata_blobs import (
    deletion_content_blob_ids as _metadata_deletion_content_blob_ids,
    deletion_reference_blob_ids as _metadata_deletion_reference_blob_ids,
    presence_claim_reference_blob_ids as _metadata_presence_reference_blob_ids,
    replacement_origin_reference_blob_ids as _metadata_replacement_reference_blob_ids,
)
from .ownership_references import BaselineReference as _BaselineReference
from .ownership_replacement_units import (
    normalize_replacement_units as _replacement_normalize_units,
)

if TYPE_CHECKING:
    from .ownership_acquisition import (
        AcquiredBatchOwnership as _AcquiredBatchOwnership,
    )


@dataclass
class AbsenceClaim:
    """A suppression constraint: specific baseline content that must not appear.

    Deletions are constraints, not content to replay. Each absence claim represents
    a contiguous run of lines that must be absent from the materialized result.

    Attributes:
        anchor_line: Batch source line after which this absence claim is anchored
                     (None for start-of-file)
        content_lines: Exact baseline line content that must be suppressed,
                       with line endings preserved
        baseline_reference: Optional old-file coordinate where this absence
                            claim was selected. This lets same-source batch
                            round trips apply replacement units back to an
                            unchanged baseline/index without guessing from
                            post-change source anchors.
    """
    anchor_line: int | None
    content_lines: Sequence[bytes]
    baseline_reference: _BaselineReference | None = None

    def to_dict(self) -> dict:
        """Serialize to metadata dictionary."""
        blob_sha = create_git_blob(buffer_byte_chunks(self.content_lines))
        data = {
            "after_source_line": self.anchor_line,
            "blob": blob_sha
        }
        if self.baseline_reference is not None:
            data["baseline_reference"] = self.baseline_reference.to_dict()
        return data

    @classmethod
    def from_dict(
        cls,
        data: dict,
        blob_contents: dict[str, bytes] | None = None,
        blob_buffers: dict[str, LineBuffer] | None = None,
    ) -> AbsenceClaim:
        """Deserialize from metadata dictionary."""
        anchor_line = data.get("after_source_line")
        blob_sha = data["blob"]
        if blob_buffers is None:
            raise ValueError("deletion blobs must be loaded before deserialization")
        content_lines = blob_buffers[blob_sha]
        baseline_metadata = data.get("baseline_reference")
        baseline_reference = (
            _BaselineReference.from_dict(baseline_metadata, blob_contents)
            if baseline_metadata is not None else None
        )
        return cls(
            anchor_line=anchor_line,
            content_lines=content_lines,
            baseline_reference=baseline_reference,
        )


@dataclass
class PresenceClaim:
    """A presence constraint over batch-source lines.

    Presence claims are the first-class representation for content that must
    exist after a batch is applied. Source lines identify the content in the
    batch source; optional baseline references record where those source lines came
    from in the original index/tree diff.
    """

    source_lines: list[str]
    baseline_references: dict[int, _BaselineReference] = field(default_factory=dict)

    def source_line_set(self) -> LineRanges:
        """Return batch-source line numbers covered by this presence claim."""
        return _claim_parse_line_ranges(self.source_lines)

    def to_dict(self) -> dict:
        """Serialize to metadata dictionary."""
        data = {"source_lines": self.source_lines}
        if self.baseline_references:
            data["baseline_references"] = {
                str(line): reference.to_dict()
                for line, reference in sorted(self.baseline_references.items())
            }
        return data

    @classmethod
    def from_dict(
        cls,
        data: dict,
        blob_contents: dict[str, bytes] | None = None,
    ) -> PresenceClaim:
        """Deserialize from metadata dictionary."""
        references_metadata = data.get("baseline_references", {})
        return cls(
            source_lines=data.get("source_lines", []),
            baseline_references={
                int(line): _BaselineReference.from_dict(
                    reference,
                    blob_contents,
                )
                for line, reference in references_metadata.items()
            },
        )


@dataclass
class ReplacementUnitOrigin:
    """Original full replacement region for a selectable replacement sub-unit.

    Split replacement units may be smaller than the file-derived replacement run
    that created them. This context records that original run so merge/discard
    code can validate placement against the parent replacement boundary instead
    of treating the selected sub-unit as an unrelated edit.
    """

    old_start: int
    old_end: int
    new_start: int
    new_end: int
    baseline_reference: _BaselineReference | None = None

    @property
    def old_line_count(self) -> int:
        """Return the number of baseline lines covered by the original unit."""
        return self.old_end - self.old_start + 1

    def to_dict(self) -> dict:
        """Serialize to metadata dictionary."""
        data = {
            "old_start": self.old_start,
            "old_end": self.old_end,
            "new_start": self.new_start,
            "new_end": self.new_end,
        }
        if self.baseline_reference is not None:
            data["baseline_reference"] = self.baseline_reference.to_dict()
        return data

    @classmethod
    def from_dict(
        cls,
        data: dict,
        blob_contents: dict[str, bytes] | None = None,
    ) -> ReplacementUnitOrigin:
        """Deserialize from metadata dictionary."""
        baseline_metadata = data.get("baseline_reference")
        return cls(
            old_start=data["old_start"],
            old_end=data["old_end"],
            new_start=data["new_start"],
            new_end=data["new_end"],
            baseline_reference=(
                _BaselineReference.from_dict(baseline_metadata, blob_contents)
                if baseline_metadata is not None else None
            ),
        )


@dataclass
class ReplacementUnit:
    """Explicit coupling between presence claims and absence claims.

    The deletion side references indexes in BatchOwnership.deletions so the
    canonical deletion constraint is stored only once in metadata.
    """

    presence_lines: list[str]
    deletion_indices: list[int]
    origin: ReplacementUnitOrigin | None = field(default=None, compare=False)

    def to_dict(self) -> dict:
        """Serialize to metadata dictionary."""
        data = {
            "presence_lines": self.presence_lines,
            "deletion_indices": self.deletion_indices,
        }
        if self.origin is not None:
            data["original_unit"] = self.origin.to_dict()
        return data

    @classmethod
    def from_dict(
        cls,
        data: dict,
        blob_contents: dict[str, bytes] | None = None,
    ) -> ReplacementUnit:
        """Deserialize from metadata dictionary."""
        origin_metadata = data.get("original_unit")
        return cls(
            presence_lines=data.get("presence_lines", data.get("claimed_lines", [])),
            deletion_indices=data.get("deletion_indices", []),
            origin=(
                ReplacementUnitOrigin.from_dict(origin_metadata, blob_contents)
                if isinstance(origin_metadata, dict) else None
            ),
        )


@dataclass
class BatchOwnership:
    """Represents batch ownership in batch source space.

    A batch owns content relative to its batch source commit:
    - presence_claims: Batch-source lines that must exist after application
    - deletions: Suppression constraints for baseline content (absence claims)
    - replacement_units: Optional explicit coupling between claims and deletions
    """
    presence_claims: list[PresenceClaim]
    deletions: list[AbsenceClaim]  # Separate deletion constraints
    replacement_units: list[ReplacementUnit] = field(default_factory=list)

    @classmethod
    def from_presence_lines(
        cls,
        source_lines: list[str],
        deletions: list[AbsenceClaim] | None = None,
        *,
        replacement_units: list[ReplacementUnit] | None = None,
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

    @classmethod
    def acquire_for_metadata_dict(
        cls,
        data: dict,
    ) -> _AcquiredBatchOwnership:
        """Acquire ownership for metadata with buffered deletion blobs."""
        from .ownership_acquisition import AcquiredBatchOwnership

        deletion_metadata = data.get("deletions", [])
        presence_metadata = data.get("presence_claims", [])
        replacement_metadata = data.get("replacement_units", [])
        blob_buffers: dict[str, LineBuffer] = {}
        buffers: list[LineBuffer] = []
        try:
            for blob_sha in _metadata_deletion_content_blob_ids(deletion_metadata):
                if blob_sha in blob_buffers:
                    continue
                buffer = load_git_blob_as_buffer(blob_sha)
                blob_buffers[blob_sha] = buffer
                buffers.append(buffer)

            blob_contents = read_git_blobs_as_bytes(
                [
                    *_metadata_deletion_reference_blob_ids(deletion_metadata),
                    *_metadata_presence_reference_blob_ids(presence_metadata),
                    *_metadata_replacement_reference_blob_ids(replacement_metadata),
                ]
            )
            ownership = cls._from_metadata_dict(
                data,
                blob_contents=blob_contents,
                deletion_blob_buffers=blob_buffers,
            )
        except Exception:
            for buffer in buffers:
                buffer.close()
            raise

        return AcquiredBatchOwnership(
            ownership=ownership,
            buffers=buffers,
        )

    @classmethod
    def _from_metadata_dict(
        cls,
        data: dict,
        *,
        blob_contents: dict[str, bytes],
        deletion_blob_buffers: dict[str, LineBuffer] | None = None,
    ) -> BatchOwnership:
        deletion_metadata = data.get("deletions", [])
        presence_metadata = data.get("presence_claims", [])
        legacy_claimed_lines = data.get("claimed_lines", [])
        presence_claims = [
            PresenceClaim.from_dict(d, blob_contents)
            for d in presence_metadata
        ]
        if not presence_claims and legacy_claimed_lines:
            presence_claims = _claim_presence_claims_from_source_lines(
                _claim_parse_line_ranges(legacy_claimed_lines)
            )
        deletions = [
            AbsenceClaim.from_dict(d, blob_contents, deletion_blob_buffers)
            for d in deletion_metadata
        ]
        replacement_units = [
            ReplacementUnit.from_dict(d, blob_contents)
            for d in data.get("replacement_units", [])
        ]
        return cls(
            presence_claims=presence_claims,
            deletions=deletions,
            replacement_units=replacement_units,
        )

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
    deletion_claims: list[AbsenceClaim]  # Separate constraints, not collapsed
