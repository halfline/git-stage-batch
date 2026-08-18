"""Authority-bearing exact transforms and non-authoritative alignments."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar, Union, cast

from ...core.coordinates import (
    BatchSourceSpace,
    FileSnapshot,
    LineBoundary,
    LineSpan,
    SnapshotBoundary,
    SnapshotSpan,
    WorktreeSpace,
    require_same_snapshot,
    require_snapshot_role,
)
from ...core.line_selection import LineRanges, LineSelection
from .line_mapping import LineMapping
from .lineage import BatchSourceLineage, LineageRun, SourceSelectionExpansion


SourceSpace = TypeVar("SourceSpace")
TargetSpace = TypeVar("TargetSpace")
FinalSpace = TypeVar("FinalSpace")


class ExactTransform(Protocol, Generic[SourceSpace, TargetSpace]):
    """Coordinate authority produced by a recorded transformation."""

    @property
    def source_snapshot(self) -> FileSnapshot[SourceSpace]: ...

    @property
    def target_snapshot(self) -> FileSnapshot[TargetSpace]: ...

    def translate_boundary(
        self,
        boundary: SnapshotBoundary[SourceSpace],
    ) -> SnapshotBoundary[TargetSpace] | None: ...

    def translate_span(
        self,
        span: SnapshotSpan[SourceSpace],
    ) -> SnapshotSpan[TargetSpace] | None: ...


@dataclass(frozen=True, slots=True)
class ComposedExactTransform(Generic[SourceSpace, TargetSpace, FinalSpace]):
    """Composition of two authoritative transforms with checked endpoints."""

    first: ExactTransform[SourceSpace, TargetSpace]
    second: ExactTransform[TargetSpace, FinalSpace]

    def __post_init__(self) -> None:
        require_same_snapshot(
            self.first.target_snapshot,
            self.second.source_snapshot,
        )

    @property
    def source_snapshot(self) -> FileSnapshot[SourceSpace]:
        return self.first.source_snapshot

    @property
    def target_snapshot(self) -> FileSnapshot[FinalSpace]:
        return self.second.target_snapshot

    def translate_boundary(
        self,
        boundary: SnapshotBoundary[SourceSpace],
    ) -> SnapshotBoundary[FinalSpace] | None:
        intermediate = self.first.translate_boundary(boundary)
        if intermediate is None:
            return None
        return self.second.translate_boundary(intermediate)

    def translate_span(
        self,
        span: SnapshotSpan[SourceSpace],
    ) -> SnapshotSpan[FinalSpace] | None:
        intermediate = self.first.translate_span(span)
        if intermediate is None:
            return None
        return self.second.translate_span(intermediate)


def compose_exact_transforms(
    first: ExactTransform[SourceSpace, TargetSpace],
    second: ExactTransform[TargetSpace, FinalSpace],
) -> ComposedExactTransform[SourceSpace, TargetSpace, FinalSpace]:
    """Compose exact transformations only when the middle snapshot is exact."""
    return ComposedExactTransform(first, second)


@dataclass(frozen=True, slots=True)
class UniquePlacement(Generic[TargetSpace]):
    """A structural candidate proven unique for one target snapshot."""

    target: SnapshotBoundary[TargetSpace]


@dataclass(frozen=True, slots=True)
class AmbiguousPlacements(Generic[TargetSpace]):
    """Multiple plausible structural placements without provenance authority."""

    targets: tuple[SnapshotBoundary[TargetSpace], ...]


@dataclass(frozen=True, slots=True)
class NoPlacement:
    """No structural placement satisfies the supplied evidence."""


@dataclass(frozen=True, slots=True)
class StaleEvidence:
    """Alignment evidence belongs to snapshots other than the requested ones."""


PlacementResult = Union[
    UniquePlacement[TargetSpace],
    AmbiguousPlacements[TargetSpace],
    NoPlacement,
    StaleEvidence,
]


@dataclass(slots=True)
class StructuralAlignment(Generic[SourceSpace, TargetSpace]):
    """Content-derived correspondence that cannot silently become provenance."""

    source_snapshot: FileSnapshot[SourceSpace]
    target_snapshot: FileSnapshot[TargetSpace]
    _mapping: LineMapping

    def __post_init__(self) -> None:
        try:
            if self.source_snapshot.path != self.target_snapshot.path:
                raise ValueError(
                    "structural alignment endpoints must have the same path"
                )
            self._validate_mapping()
        except BaseException:
            # Construction transfers ownership of the mapped vectors even when
            # endpoint validation fails.  Deterministic cleanup avoids leaving
            # mmap/temp-file resources to __del__ on malformed evidence.
            self._mapping.close()
            raise

    def _validate_mapping(self) -> None:
        source_to_target = self._mapping.source_to_target
        target_to_source = self._mapping.target_to_source
        source_count = self.source_snapshot.line_count
        target_count = self.target_snapshot.line_count
        if len(source_to_target) != source_count:
            raise ValueError(
                "structural alignment source extent differs from its snapshot"
            )
        if len(target_to_source) != target_count:
            raise ValueError(
                "structural alignment target extent differs from its snapshot"
            )

        previous_target = 0
        for source_index in range(source_count):
            target_line = source_to_target[source_index]
            if type(target_line) is not int or not 0 <= target_line <= target_count:
                raise ValueError(
                    "structural alignment target coordinate is outside its snapshot"
                )
            if target_line == 0:
                continue
            source_line = source_index + 1
            if target_line <= previous_target:
                raise ValueError("structural alignment must preserve line order")
            if target_to_source[target_line - 1] != source_line:
                raise ValueError("structural alignment mappings are not reciprocal")
            previous_target = target_line

        previous_source = 0
        for target_index in range(target_count):
            source_line = target_to_source[target_index]
            if type(source_line) is not int or not 0 <= source_line <= source_count:
                raise ValueError(
                    "structural alignment source coordinate is outside its snapshot"
                )
            if source_line == 0:
                continue
            target_line = target_index + 1
            if source_line <= previous_source:
                raise ValueError("structural alignment must preserve line order")
            if source_to_target[source_line - 1] != target_line:
                raise ValueError("structural alignment mappings are not reciprocal")
            previous_source = source_line

    def prove_unique_placement(
        self,
        boundary: SnapshotBoundary[SourceSpace],
    ) -> PlacementResult[TargetSpace]:
        """Return a placement only when the alignment certifies uniqueness."""
        try:
            require_same_snapshot(boundary.snapshot, self.source_snapshot)
        except ValueError:
            return StaleEvidence()
        offset = boundary.boundary.offset
        placement, ambiguity = self._boundary_placement(offset)
        if ambiguity is not None:
            return AmbiguousPlacements(ambiguity)
        if placement is None:
            return NoPlacement()
        if self._mapping.may_have_unmapped_equal_lines:
            return AmbiguousPlacements((placement,))
        return UniquePlacement(placement)

    def get_target_line_from_source_line(
        self,
        source_line: int,
    ) -> int | None:
        """Return one structural correspondence without granting provenance."""
        return self._mapping.get_target_line_from_source_line(source_line)

    def get_source_line_from_target_line(
        self,
        target_line: int,
    ) -> int | None:
        """Return the reciprocal structural correspondence as evidence only."""
        return self._mapping.get_source_line_from_target_line(target_line)

    def _boundary_placement(
        self,
        offset: int,
    ) -> tuple[
        SnapshotBoundary[TargetSpace] | None,
        tuple[SnapshotBoundary[TargetSpace], ...] | None,
    ]:
        source_count = self.source_snapshot.line_count
        target_count = self.target_snapshot.line_count
        if source_count == 0:
            if target_count == 0:
                return self._target_boundary(0), None
            return None, (
                self._target_boundary(0),
                self._target_boundary(target_count),
            )
        if offset == 0:
            first = self._mapping.get_target_line_from_source_line(1)
            if first is None:
                return None, None
            if first == 1:
                return self._target_boundary(0), None
            return None, (
                self._target_boundary(0),
                self._target_boundary(first - 1),
            )
        if offset == source_count:
            last = self._mapping.get_target_line_from_source_line(source_count)
            if last is None:
                return None, None
            if last == target_count:
                return self._target_boundary(target_count), None
            return None, (
                self._target_boundary(last),
                self._target_boundary(target_count),
            )

        left = self._mapping.get_target_line_from_source_line(offset)
        right = self._mapping.get_target_line_from_source_line(offset + 1)
        if left is None or right is None:
            return None, None
        if right == left + 1:
            return self._target_boundary(left), None
        return None, (
            self._target_boundary(left),
            self._target_boundary(right - 1),
        )

    def _target_boundary(
        self,
        offset: int,
    ) -> SnapshotBoundary[TargetSpace]:
        return SnapshotBoundary(self.target_snapshot, LineBoundary(offset))

    def close(self) -> None:
        """Release the alignment's mapped storage."""
        self._mapping.close()

    def __enter__(self) -> StructuralAlignment[SourceSpace, TargetSpace]:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
@dataclass(frozen=True, slots=True)
class _SourceLineageVariant:
    """Evidence that the transform projects old batch-source coordinates."""


@dataclass(frozen=True, slots=True)
class _WorkingLineageVariant:
    """Evidence that the transform projects observed worktree coordinates."""


_LineageVariant = Union[_SourceLineageVariant, _WorkingLineageVariant]


@dataclass(frozen=True, slots=True, init=False)
class BatchSourceExactTransform(Generic[SourceSpace, TargetSpace]):
    """Snapshot-bound view of one explicit side of recorded source lineage.

    The compatibility constructor denotes the source-lineage variant. New code
    should use the named factories so the chosen lineage side is visible at the
    call site. A free-form role discriminator is deliberately not accepted.
    """

    source_snapshot: FileSnapshot[SourceSpace]
    target_snapshot: FileSnapshot[TargetSpace]
    lineage: BatchSourceLineage
    _variant: _LineageVariant

    def __init__(
        self,
        source_snapshot: FileSnapshot[BatchSourceSpace],
        target_snapshot: FileSnapshot[BatchSourceSpace],
        lineage: BatchSourceLineage,
    ) -> None:
        self._initialize(
            source_snapshot,
            target_snapshot,
            lineage,
            _SourceLineageVariant(),
        )

    @classmethod
    def from_source_lineage(
        cls,
        source_snapshot: FileSnapshot[BatchSourceSpace],
        target_snapshot: FileSnapshot[BatchSourceSpace],
        lineage: BatchSourceLineage,
    ) -> BatchSourceExactTransform[BatchSourceSpace, BatchSourceSpace]:
        """Bind recorded old-source lineage to its exact endpoint snapshots."""
        return cast(
            "BatchSourceExactTransform[BatchSourceSpace, BatchSourceSpace]",
            cls(source_snapshot, target_snapshot, lineage),
        )

    @classmethod
    def from_working_lineage(
        cls,
        source_snapshot: FileSnapshot[WorktreeSpace],
        target_snapshot: FileSnapshot[BatchSourceSpace],
        lineage: BatchSourceLineage,
    ) -> BatchSourceExactTransform[WorktreeSpace, BatchSourceSpace]:
        """Bind recorded worktree lineage to its exact endpoint snapshots."""
        transform = object.__new__(cls)
        transform._initialize(
            source_snapshot,
            target_snapshot,
            lineage,
            _WorkingLineageVariant(),
        )
        return cast(
            "BatchSourceExactTransform[WorktreeSpace, BatchSourceSpace]",
            transform,
        )

    def _initialize(
        self,
        source_snapshot: FileSnapshot[Any],
        target_snapshot: FileSnapshot[Any],
        lineage: BatchSourceLineage,
        variant: _LineageVariant,
    ) -> None:
        object.__setattr__(self, "source_snapshot", source_snapshot)
        object.__setattr__(self, "target_snapshot", target_snapshot)
        object.__setattr__(self, "lineage", lineage)
        object.__setattr__(self, "_variant", variant)
        self._validate()

    def _validate(self) -> None:
        if self.source_snapshot.path != self.target_snapshot.path:
            raise ValueError("exact transform endpoints must have the same path")
        require_snapshot_role(self.target_snapshot, BatchSourceSpace)
        if isinstance(self._variant, _SourceLineageVariant):
            require_snapshot_role(self.source_snapshot, BatchSourceSpace)
            _validate_lineage_runs(
                self.lineage.source_runs(),
                source_count=self.source_snapshot.line_count,
                target_count=self.target_snapshot.line_count,
                label="source",
            )
            _validate_source_expansions(
                self.lineage.source_expansions(),
                self.lineage.source_runs(),
                source_count=self.source_snapshot.line_count,
                target_count=self.target_snapshot.line_count,
            )
        else:
            require_snapshot_role(self.source_snapshot, WorktreeSpace)
            _validate_lineage_runs(
                self.lineage.working_runs(),
                source_count=self.source_snapshot.line_count,
                target_count=self.target_snapshot.line_count,
                label="working",
            )

    def translate_boundary(
        self,
        boundary: SnapshotBoundary[SourceSpace],
    ) -> SnapshotBoundary[TargetSpace] | None:
        require_same_snapshot(boundary.snapshot, self.source_snapshot)
        offset = boundary.boundary.offset
        source_count = self.source_snapshot.line_count
        target_count = self.target_snapshot.line_count
        if source_count == 0:
            if target_count != 0:
                return None
            return SnapshotBoundary(self.target_snapshot, LineBoundary(0))
        if offset == 0:
            translated = self._translate_line(1)
            if translated != 1:
                return None
            return SnapshotBoundary(self.target_snapshot, LineBoundary(0))
        if offset == source_count:
            translated = self._translate_line(source_count)
            if translated != target_count:
                return None
            return SnapshotBoundary(
                self.target_snapshot,
                LineBoundary(target_count),
            )

        translated = self._translate_line(offset)
        next_translated = self._translate_line(offset + 1)
        if translated is None or next_translated != translated + 1:
            return None
        return SnapshotBoundary(
            self.target_snapshot,
            LineBoundary(translated),
        )

    def _translate_line(self, line_number: int) -> int | None:
        return (
            self.lineage.translate_source_line(line_number)
            if isinstance(self._variant, _SourceLineageVariant)
            else self.lineage.translate_working_line(line_number)
        )

    def translate_line_number(self, line_number: int) -> int | None:
        """Translate one line in the transform's validated source space."""
        if line_number <= 0 or line_number > self.source_snapshot.line_count:
            return None
        return self._translate_line(line_number)

    def translate_source_line(self, line_number: int) -> int | None:
        """Translate one line through a source-lineage transform."""
        if not isinstance(self._variant, _SourceLineageVariant):
            raise ValueError("transform does not represent source lineage")
        return self.translate_line_number(line_number)

    def translate_source_selection(
        self,
        selection: LineSelection,
    ) -> LineRanges:
        """Translate source selection after validating the lineage variant."""
        if not isinstance(self._variant, _SourceLineageVariant):
            raise ValueError("transform does not represent source lineage")
        return self.lineage.translate_source_selection(selection)

    def first_unmapped_source_line(
        self,
        selection: LineSelection,
    ) -> int | None:
        """Return missing source lineage after validating the variant."""
        if not isinstance(self._variant, _SourceLineageVariant):
            raise ValueError("transform does not represent source lineage")
        return self.lineage.first_unmapped_source_line(selection)

    def translate_span(
        self,
        span: SnapshotSpan[SourceSpace],
    ) -> SnapshotSpan[TargetSpace] | None:
        require_same_snapshot(span.snapshot, self.source_snapshot)
        if len(span.span) == 0:
            boundary = self.translate_boundary(
                SnapshotBoundary(self.source_snapshot, span.span.start)
            )
            if boundary is None:
                return None
            return SnapshotSpan(
                self.target_snapshot,
                LineSpan(boundary.boundary, boundary.boundary),
            )

        # Half-open boundaries [start, end) contain one-based source lines
        # start + 1 through end. Translate the complete interior rather than
        # granting authority from two plausible endpoint boundaries alone.
        source_start = span.span.start.offset + 1
        source_end = span.span.end.offset
        translated = (
            self.lineage.translate_source_span(source_start, source_end)
            if isinstance(self._variant, _SourceLineageVariant)
            else self.lineage.translate_working_range(source_start, source_end)
        )
        if translated is None:
            return None
        target_start, target_end = translated
        return SnapshotSpan(
            self.target_snapshot,
            LineSpan(
                LineBoundary(target_start - 1),
                LineBoundary(target_end),
            ),
        )


def _validate_lineage_runs(
    runs: Iterator[LineageRun],
    *,
    source_count: int,
    target_count: int,
    label: str,
) -> None:
    previous_old_end = 0
    previous_new_end = 0
    for run in runs:
        if run.old_start <= previous_old_end:
            raise ValueError(f"{label} lineage source ranges overlap")
        if run.old_end > source_count:
            raise ValueError(f"{label} lineage is outside its source snapshot")
        if run.new_start <= previous_new_end:
            raise ValueError(f"{label} lineage does not preserve target order")
        if run.new_end > target_count:
            raise ValueError(f"{label} lineage is outside its target snapshot")
        previous_old_end = run.old_end
        previous_new_end = run.new_end


def _validate_source_expansions(
    expansions: Iterator[SourceSelectionExpansion],
    source_runs: Iterator[LineageRun],
    *,
    source_count: int,
    target_count: int,
) -> None:
    current_run = next(source_runs, None)
    previous_source_end = 0
    previous_target_end = 0
    for expansion in expansions:
        if expansion.source_end > source_count:
            raise ValueError("source expansion is outside its source snapshot")
        if expansion.new_end > target_count:
            raise ValueError("source expansion is outside its target snapshot")
        if expansion.source_start <= previous_source_end:
            raise ValueError("source expansions overlap in source order")
        if expansion.new_start <= previous_target_end:
            raise ValueError("source expansions do not preserve target order")

        while current_run is not None and (
            current_run.old_end < expansion.source_start
        ):
            current_run = next(source_runs, None)
        expected_source = expansion.source_start
        expected_target = expansion.new_start
        while expected_source <= expansion.source_end:
            if (
                current_run is None
                or current_run.old_start > expected_source
                or current_run.translate(expected_source) != expected_target
            ):
                raise ValueError(
                    "source expansion lacks contiguous direct lineage"
                )
            covered_end = min(current_run.old_end, expansion.source_end)
            covered_count = covered_end - expected_source + 1
            expected_source = covered_end + 1
            expected_target += covered_count
            if expected_source <= expansion.source_end:
                current_run = next(source_runs, None)

        # The extra destination tail belongs to this complete source range.
        # A direct run continuing past the range, or the next run entering the
        # tail, would assign the same target lines to unrelated source lines.
        assert current_run is not None
        if current_run.old_end > expansion.source_end:
            raise ValueError("source expansion overlaps direct source lineage")
        current_run = next(source_runs, None)
        if (
            current_run is not None
            and current_run.new_start <= expansion.new_end
        ):
            raise ValueError("source expansion overlaps later source lineage")

        previous_source_end = expansion.source_end
        previous_target_end = expansion.new_end
