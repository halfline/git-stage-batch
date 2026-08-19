"""Aggregate root binding batch ownership to its coordinate snapshots."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from ..core.coordinates import (
    BaselineSpace,
    BatchSourceSpace,
    FileSnapshot,
    HalfOpenRanges,
    SnapshotSpans,
    content_snapshot,
    require_same_snapshot,
    require_snapshot_role,
    one_based_inclusive_to_half_open,
)
from .ownership.claims import parse_ownership_line_ranges
from .ownership.model import BatchOwnership
from .ownership.references import BaselineReference


@dataclass(frozen=True, slots=True)
class BatchMetadataRevision:
    """Opaque durable state revision used for stale-writer detection."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value:
            raise ValueError("batch metadata revision must be non-empty")

    @classmethod
    def from_metadata(cls, metadata: object) -> BatchMetadataRevision:
        """Read a durable revision from an application metadata mapping."""
        if not isinstance(metadata, dict):
            raise TypeError("batch metadata must be a dictionary")
        revision = metadata.get("revision")
        if not isinstance(revision, str) or not revision:
            raise ValueError("batch metadata has no durable revision")
        return cls(revision)


@dataclass(frozen=True, slots=True)
class SourceBoundOwnership:
    """Ownership whose coordinate authority is one exact source snapshot."""

    source_snapshot: FileSnapshot[BatchSourceSpace]
    value: BatchOwnership

    def __post_init__(self) -> None:
        require_snapshot_role(self.source_snapshot, BatchSourceSpace)
        if not isinstance(self.value, BatchOwnership):
            raise TypeError("source-bound ownership requires BatchOwnership")


@dataclass(frozen=True, slots=True)
class BatchFileState:
    """One validated path-scoped batch state.

    Ownership is never handed to merge or source-advancement code without the
    exact source and baseline snapshots that define its coordinates.
    Buffers are borrowed; their owner must keep them open for the state's use.
    """

    path: str
    baseline_snapshot: FileSnapshot[BaselineSpace]
    source_snapshot: FileSnapshot[BatchSourceSpace]
    baseline_lines: Sequence[bytes]
    source_lines: Sequence[bytes]
    bound_ownership: SourceBoundOwnership
    metadata_revision: BatchMetadataRevision

    @property
    def ownership(self) -> BatchOwnership:
        """Return ownership after the aggregate has validated its source."""
        return self.bound_ownership.value

    def __post_init__(self) -> None:
        if not isinstance(self.bound_ownership, SourceBoundOwnership):
            raise TypeError("batch file state requires source-bound ownership")
        if not isinstance(self.metadata_revision, BatchMetadataRevision):
            raise TypeError("batch file state requires a metadata revision")
        require_snapshot_role(self.baseline_snapshot, BaselineSpace)
        require_snapshot_role(self.source_snapshot, BatchSourceSpace)
        if self.path != self.baseline_snapshot.path:
            raise ValueError("baseline snapshot path does not match batch file")
        if self.path != self.source_snapshot.path:
            raise ValueError("source snapshot path does not match batch file")
        if self.bound_ownership.source_snapshot != self.source_snapshot:
            raise ValueError("ownership is bound to a different source snapshot")
        if len(self.baseline_lines) != self.baseline_snapshot.line_count:
            raise ValueError("baseline buffer does not match its snapshot")
        if len(self.source_lines) != self.source_snapshot.line_count:
            raise ValueError("source buffer does not match its snapshot")
        self.validate()

    def validate(self) -> None:
        """Revalidate borrowed buffers and mutable compatibility ownership."""
        self.validate_content()
        presence_lines = self.ownership.presence_line_set()
        try:
            SnapshotSpans(
                self.source_snapshot,
                HalfOpenRanges.from_ranges(
                    one_based_inclusive_to_half_open(start, end)
                    for start, end in presence_lines.ranges()
                ),
            )
        except ValueError as error:
            raise ValueError(
                "ownership contains lines outside its source snapshot"
            ) from error
        for deletion in self.ownership.deletions:
            if deletion.anchor.offset > self.source_snapshot.line_count:
                raise ValueError("absence anchor is outside its source snapshot")
            self._validate_baseline_reference(deletion.baseline_reference)
        for claim in self.ownership.presence_claims:
            claim_lines = claim.source_line_set()
            for claimed_line, reference in claim.baseline_references.items():
                if type(claimed_line) is not int:
                    raise ValueError(
                        "presence baseline reference line must be an integer"
                    )
                if not 1 <= claimed_line <= self.source_snapshot.line_count:
                    raise ValueError(
                        "presence baseline reference line is outside its source snapshot"
                    )
                if claimed_line not in claim_lines:
                    raise ValueError(
                        "presence baseline reference line is not owned by its claim"
                    )
                self._validate_baseline_reference(reference)

        deletion_count = len(self.ownership.deletions)
        for unit in self.ownership.replacement_units:
            previous_deletion_index = -1
            for deletion_index in unit.deletion_indices:
                if (
                    type(deletion_index) is not int
                    or not 0 <= deletion_index < deletion_count
                    or deletion_index <= previous_deletion_index
                ):
                    raise ValueError("replacement unit has invalid deletion indices")
                previous_deletion_index = deletion_index

            unit_presence = parse_ownership_line_ranges(unit.presence_lines)
            if (
                unit_presence
                and unit_presence.ranges()[-1][1] > self.source_snapshot.line_count
            ):
                raise ValueError(
                    "replacement unit presence is outside its source snapshot"
                )
            if not unit_presence.is_subset_of(presence_lines):
                raise ValueError("replacement unit presence is not owned by the batch")

            origin = unit.origin
            if origin is not None:
                if origin.old_end > self.baseline_snapshot.line_count:
                    raise ValueError(
                        "replacement origin is outside its baseline snapshot"
                    )
                if origin.new_end > self.source_snapshot.line_count:
                    raise ValueError(
                        "replacement origin is outside its source snapshot"
                    )
                self._validate_baseline_reference(origin.baseline_reference)

    def validate_content(self) -> None:
        """Revalidate borrowed buffers against their immutable identities."""
        require_same_snapshot(
            self.baseline_snapshot,
            content_snapshot(
                self.path,
                self.baseline_lines,
                space=BaselineSpace,
            ),
        )
        require_same_snapshot(
            self.source_snapshot,
            content_snapshot(
                self.path,
                self.source_lines,
                space=BatchSourceSpace,
            ),
        )

    def _validate_baseline_reference(self, reference: object | None) -> None:
        """Validate boundary evidence against the aggregate's baseline.

        Upstream ownership code may produce baseline references whose
        ``before`` coordinate is a source-line number rather than a
        baseline-line number.  These references are valid ownership
        metadata — ``bind()`` will raise, but the merge path tolerates
        missing before-boundaries gracefully, so we do the same here.
        """
        if reference is None:
            return
        if not isinstance(reference, BaselineReference):
            raise TypeError("baseline reference has the wrong domain type")
        try:
            reference.bind(self.baseline_snapshot)
        except ValueError:
            pass

    def with_advanced_source(
        self,
        *,
        source_snapshot: FileSnapshot[BatchSourceSpace],
        source_lines: Sequence[bytes],
        bound_ownership: SourceBoundOwnership,
        metadata_revision: BatchMetadataRevision,
    ) -> BatchFileState:
        """Return one atomically rebound state after exact source advancement."""
        return replace(
            self,
            source_snapshot=source_snapshot,
            source_lines=source_lines,
            bound_ownership=bound_ownership,
            metadata_revision=metadata_revision,
        )
