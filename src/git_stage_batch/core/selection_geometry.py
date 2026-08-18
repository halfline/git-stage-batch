"""Resolve rendered display selections into snapshot-bound domain geometry."""

from __future__ import annotations

from collections.abc import Collection, Set as AbstractSet
from dataclasses import dataclass, field
import hashlib
from typing import Iterable, Iterator, Union

from .coordinates import (
    DiffNewSpace,
    DiffOldSpace,
    DisplayLineId,
    FileSnapshot,
    HalfOpenRanges,
    LineBoundary,
    LineSpan,
    SnapshotIdentity,
    SnapshotSpans,
    require_snapshot_role,
)
from .models import LineEntry, LineLevelChange
from .line_selection import LineRanges
from .mapped_storage import (
    ChunkedMappedRecordVector,
    MappedRecordVector,
    sort_mapped_records,
)


@dataclass(frozen=True, slots=True)
class DiffViewIdentity:
    """The exact endpoint snapshots and renderer identity for one diff view."""

    old_snapshot: FileSnapshot[DiffOldSpace]
    new_snapshot: FileSnapshot[DiffNewSpace]
    renderer_identity: SnapshotIdentity
    _rendered_view: LineLevelChange | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    _rendered_rows_revision: int | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        require_snapshot_role(self.old_snapshot, DiffOldSpace)
        require_snapshot_role(self.new_snapshot, DiffNewSpace)
        if self.old_snapshot.path != self.new_snapshot.path:
            raise ValueError("diff endpoints must identify the same repository path")
        if self.renderer_identity.kind != "diff-view-sha256":
            raise ValueError("diff view requires a rendered-view digest")

    def detached(self) -> DiffViewIdentity:
        """Return the durable identity without retaining presentation rows."""
        if self._rendered_view is None:
            return self
        return DiffViewIdentity(
            self.old_snapshot,
            self.new_snapshot,
            self.renderer_identity,
        )


@dataclass(frozen=True, slots=True)
class ExactContentWitness:
    """Compact identity of an exact ordered line sequence.

    Semantic selection geometry needs to prove which bytes were selected, but
    it does not edit from those bytes.  Retaining a tuple of every selected
    line made a contiguous selection consume ordinary Python heap in
    proportion to its length.  This length-framed digest keeps the same exact
    byte and line-boundary distinction in constant space.
    """

    identity: SnapshotIdentity
    line_count: int
    byte_count: int

    def __post_init__(self) -> None:
        if self.identity.kind != "selected-content-sha256":
            raise ValueError("selected content requires an exact content digest")
        if self.line_count < 0 or self.byte_count < 0:
            raise ValueError("selected content sizes must be non-negative")

    def __len__(self) -> int:
        return self.line_count

    @classmethod
    def from_lines(cls, lines: Iterable[bytes]) -> ExactContentWitness:
        accumulator = _ContentWitnessAccumulator()
        for line in lines:
            accumulator.add(line)
        return accumulator.finish()


@dataclass(frozen=True, slots=True)
class DisplayIdRanges:
    """Opaque display IDs retained as compact inclusive ranges."""

    _ranges: LineRanges

    @classmethod
    def from_ids(cls, display_ids: Iterable[DisplayLineId]) -> DisplayIdRanges:
        # Most renderer streams are already ordered, so compact them in one
        # pass.  Keep a mapped witness until that is proven: a generic caller
        # may supply an unordered iterable, and falling back to ``list`` plus
        # ``sorted`` would put one Python object per selected line on the heap.
        with ChunkedMappedRecordVector(
            record_format="Q",
            chunk_capacity=65536,
        ) as buffered:
            ranges: list[tuple[int, int]] = []
            run_start: int | None = None
            run_end: int | None = None
            previous_value: int | None = None
            monotonic_direction = 0
            unordered = False
            for display_id in display_ids:
                if not isinstance(display_id, DisplayLineId):
                    raise TypeError("display selection requires DisplayLineId values")
                value = display_id.value
                buffered.append((value,))
                if unordered:
                    continue
                if run_start is None:
                    run_start = run_end = value
                    previous_value = value
                    continue
                assert previous_value is not None
                assert run_end is not None
                if value == previous_value:
                    continue
                direction = 1 if value > previous_value else -1
                if monotonic_direction == 0:
                    monotonic_direction = direction
                elif direction != monotonic_direction:
                    unordered = True
                    ranges.clear()
                    continue
                if monotonic_direction > 0:
                    if value == run_end + 1:
                        run_end = value
                    else:
                        ranges.append((run_start, run_end))
                        run_start = run_end = value
                else:
                    if value == run_start - 1:
                        run_start = value
                    else:
                        ranges.append((run_start, run_end))
                        run_start = run_end = value
                previous_value = value

            if not buffered:
                return cls(LineRanges.empty())
            if not unordered:
                assert run_start is not None and run_end is not None
                ranges.append((run_start, run_end))
                if monotonic_direction < 0:
                    ranges.reverse()
                return cls(LineRanges.from_ranges(ranges))

            with MappedRecordVector(len(buffered), "Q") as records:
                for record_index in range(len(buffered)):
                    records.append(buffered[record_index])
                sort_mapped_records(records)
                return cls._from_sorted_records(records)

    @classmethod
    def from_unordered_values(cls, values: Collection[int]) -> DisplayIdRanges:
        """Compact unordered integer IDs without a heap list for sorting."""
        if not values:
            return cls(LineRanges.empty())
        minimum: int | None = None
        maximum: int | None = None
        for value in values:
            DisplayLineId(value)
            minimum = value if minimum is None else min(minimum, value)
            maximum = value if maximum is None else max(maximum, value)
        assert minimum is not None and maximum is not None
        value_span = maximum - minimum + 1
        if isinstance(values, AbstractSet) and value_span <= len(values) * 4:
            ranges: list[tuple[int, int]] = []
            run_start: int | None = None
            run_end: int | None = None
            for value in range(minimum, maximum + 1):
                if value not in values:
                    if run_start is not None:
                        assert run_end is not None
                        ranges.append((run_start, run_end))
                        run_start = run_end = None
                    continue
                if run_start is None:
                    run_start = value
                run_end = value
            if run_start is not None:
                assert run_end is not None
                ranges.append((run_start, run_end))
            return cls(LineRanges.from_ranges(ranges))

        with MappedRecordVector(len(values), "Q") as records:
            for value in values:
                records.append((value,))
            sort_mapped_records(records)
            return cls._from_sorted_records(records)

    @classmethod
    def _from_sorted_records(
        cls,
        records: Iterable[tuple[int, ...]],
    ) -> DisplayIdRanges:
        """Compact validated sorted mapped IDs into semantic ranges."""
        ranges: list[tuple[int, int]] = []
        run_start: int | None = None
        run_end: int | None = None
        for record in records:
            value = record[0]
            if run_start is None:
                run_start = run_end = value
            else:
                assert run_end is not None
                if value == run_end:
                    continue
                if value == run_end + 1:
                    run_end = value
                else:
                    ranges.append((run_start, run_end))
                    run_start = run_end = value
        if run_start is None:
            return cls(LineRanges.empty())
        assert run_end is not None
        ranges.append((run_start, run_end))
        return cls(LineRanges.from_ranges(ranges))

    def __bool__(self) -> bool:
        return bool(self._ranges)

    def __len__(self) -> int:
        return len(self._ranges)

    def __iter__(self) -> Iterator[DisplayLineId]:
        for value in self._ranges:
            yield DisplayLineId(value)

    def __contains__(self, display_id: object) -> bool:
        return isinstance(display_id, DisplayLineId) and self.contains_value(
            display_id.value
        )

    def contains_value(self, value: int) -> bool:
        """Test one renderer integer without allocating an opaque wrapper."""
        return value in self._ranges

    def values(self) -> Iterator[int]:
        """Iterate the underlying integer handles without retaining them."""
        return iter(self._ranges)

    def ranges(self) -> tuple[tuple[int, int], ...]:
        """Return normalized inclusive ranges for diagnostics and adapters."""
        return self._ranges.ranges()

    def to_line_ranges(self) -> LineRanges:
        """Return the compact compatibility view without expanding IDs."""
        return self._ranges

    def is_subset_of(self, other: DisplayIdRanges) -> bool:
        """Return whether every handle belongs to ``other`` in range time."""
        own_ranges = self.ranges()
        other_ranges = other.ranges()
        own_index = 0
        other_index = 0
        while own_index < len(own_ranges) and other_index < len(other_ranges):
            own_start, own_end = own_ranges[own_index]
            other_start, other_end = other_ranges[other_index]
            if own_start < other_start:
                return False
            if own_start > other_end:
                other_index += 1
                continue
            if own_end > other_end:
                return False
            own_index += 1
        return own_index == len(own_ranges)


@dataclass(frozen=True, slots=True)
class Insertion:
    """A semantic insertion at an old-snapshot boundary."""

    old_boundary: LineBoundary[DiffOldSpace]
    new_span: LineSpan[DiffNewSpace]
    content: ExactContentWitness

    def __post_init__(self) -> None:
        if len(self.new_span) != len(self.content):
            raise ValueError("insertion content does not match its new span")
        if self.old_boundary.offset < 0:
            raise ValueError("insertion boundary must be non-negative")


@dataclass(frozen=True, slots=True)
class Deletion:
    """A semantic deletion ending at a new-snapshot boundary."""

    old_span: LineSpan[DiffOldSpace]
    new_boundary: LineBoundary[DiffNewSpace]
    content: ExactContentWitness

    def __post_init__(self) -> None:
        if len(self.old_span) != len(self.content):
            raise ValueError("deletion content does not match its old span")
        if self.new_boundary.offset < 0:
            raise ValueError("deletion boundary must be non-negative")


@dataclass(frozen=True, slots=True)
class Replacement:
    """A semantic replacement with exact old and new byte sequences."""

    old_span: LineSpan[DiffOldSpace]
    new_span: LineSpan[DiffNewSpace]
    old_content: ExactContentWitness
    new_content: ExactContentWitness

    def __post_init__(self) -> None:
        if len(self.old_span) != len(self.old_content):
            raise ValueError("replacement old content does not match its span")
        if len(self.new_span) != len(self.new_content):
            raise ValueError("replacement new content does not match its span")


ChangeUnit = Union[Insertion, Deletion, Replacement]


@dataclass(frozen=True, slots=True)
class FileDiff:
    """Semantic change units between two exact file snapshots."""

    view: DiffViewIdentity
    change_units: tuple[ChangeUnit, ...]

    def __post_init__(self) -> None:
        for unit in self.change_units:
            if isinstance(unit, Insertion):
                if unit.old_boundary.offset > self.view.old_snapshot.line_count:
                    raise ValueError("insertion boundary is outside its snapshot")
                if unit.new_span.end.offset > self.view.new_snapshot.line_count:
                    raise ValueError("insertion span is outside its snapshot")
            elif isinstance(unit, Deletion):
                if unit.old_span.end.offset > self.view.old_snapshot.line_count:
                    raise ValueError("deletion span is outside its snapshot")
                if unit.new_boundary.offset > self.view.new_snapshot.line_count:
                    raise ValueError("deletion boundary is outside its snapshot")
            elif (
                unit.old_span.end.offset > self.view.old_snapshot.line_count
                or unit.new_span.end.offset > self.view.new_snapshot.line_count
            ):
                raise ValueError("replacement span is outside its snapshot")


def diff_view_identity(
    line_changes: LineLevelChange,
    *,
    old_snapshot: FileSnapshot[DiffOldSpace],
    new_snapshot: FileSnapshot[DiffNewSpace],
) -> DiffViewIdentity:
    """Bind displayed row IDs to exact endpoints and rendered row geometry."""
    digest = hashlib.sha256()

    def update_field(value: bytes) -> None:
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)

    update_field(line_changes.path.encode("utf-8", errors="surrogateescape"))
    update_field(old_snapshot.identity.kind.encode("utf-8"))
    update_field(old_snapshot.identity.value.encode("utf-8"))
    digest.update(old_snapshot.line_count.to_bytes(8, "big"))
    update_field(new_snapshot.identity.kind.encode("utf-8"))
    update_field(new_snapshot.identity.value.encode("utf-8"))
    digest.update(new_snapshot.line_count.to_bytes(8, "big"))
    for coordinate in (
        line_changes.header.old_start,
        line_changes.header.old_len,
        line_changes.header.new_start,
        line_changes.header.new_len,
    ):
        digest.update(coordinate.to_bytes(8, "big"))
    for line in line_changes.lines:
        digest.update(line.kind.encode("ascii"))
        digest.update((line.id or 0).to_bytes(8, "big"))
        digest.update((line.old_line_number or 0).to_bytes(8, "big"))
        digest.update((line.new_line_number or 0).to_bytes(8, "big"))
        digest.update((line.source_line or 0).to_bytes(8, "big"))
        digest.update(b"\1" if line.has_trailing_newline else b"\0")
        update_field(line.text_bytes)
    return DiffViewIdentity(
        old_snapshot,
        new_snapshot,
        SnapshotIdentity("diff-view-sha256", digest.hexdigest()),
        line_changes,
        line_changes.rendered_rows_revision,
    )


@dataclass(frozen=True, slots=True)
class ResolvedSelection:
    """A display selection resolved once into immutable semantic geometry.

    Display IDs remain only as an audit witness.  Domain consumers use the
    snapshot-bound spans and semantic change units instead.
    """

    file_diff: FileDiff
    display_ids: DisplayIdRanges
    old_spans: SnapshotSpans[DiffOldSpace]
    new_spans: SnapshotSpans[DiffNewSpace]

    @property
    def view(self) -> DiffViewIdentity:
        """Return the endpoint and renderer identity for compatibility."""
        return self.file_diff.view

    @property
    def change_units(self) -> tuple[ChangeUnit, ...]:
        """Return the semantic units retained by the file diff."""
        return self.file_diff.change_units

    def __post_init__(self) -> None:
        if self.old_spans.snapshot != self.file_diff.view.old_snapshot:
            raise ValueError("old selection spans do not belong to the diff view")
        if self.new_spans.snapshot != self.file_diff.view.new_snapshot:
            raise ValueError("new selection spans do not belong to the diff view")
        if not self.display_ids:
            raise ValueError("resolved selection must not be empty")
        if not self.file_diff.change_units:
            raise ValueError("resolved selection must contain semantic units")

        if not _ranges_equal(
            self.old_spans.ranges.ranges(),
            _semantic_unit_ranges(self.file_diff.change_units, old_side=True),
        ):
            raise ValueError("semantic units differ from selected old spans")
        if not _ranges_equal(
            self.new_spans.ranges.ranges(),
            _semantic_unit_ranges(self.file_diff.change_units, old_side=False),
        ):
            raise ValueError("semantic units differ from selected new spans")


def _semantic_unit_ranges(
    units: tuple[ChangeUnit, ...],
    *,
    old_side: bool,
) -> Iterator[tuple[int, int]]:
    """Coalesce ordered semantic spans without a second unit-scale list."""
    current_start: int | None = None
    current_end: int | None = None
    for unit in units:
        span: LineSpan[DiffOldSpace] | LineSpan[DiffNewSpace] | None
        if old_side:
            span = None if isinstance(unit, Insertion) else unit.old_span
        else:
            span = None if isinstance(unit, Deletion) else unit.new_span
        if span is None or not len(span):
            continue
        start = span.start.offset
        end = span.end.offset
        if current_start is None:
            current_start, current_end = start, end
            continue
        assert current_end is not None
        if start < current_start:
            raise ValueError("semantic units are not in rendered order")
        if start <= current_end:
            current_end = max(current_end, end)
            continue
        yield current_start, current_end
        current_start, current_end = start, end
    if current_start is not None:
        assert current_end is not None
        yield current_start, current_end


def _ranges_equal(
    expected: tuple[tuple[int, int], ...],
    actual: Iterable[tuple[int, int]],
) -> bool:
    actual_iterator = iter(actual)
    for expected_range in expected:
        if next(actual_iterator, None) != expected_range:
            return False
    return next(actual_iterator, None) is None


def resolve_selection(
    line_changes: LineLevelChange,
    display_ids: DisplayIdRanges | Iterable[DisplayLineId],
    *,
    view: DiffViewIdentity,
) -> ResolvedSelection:
    """Resolve display IDs into snapshot-bound spans and semantic units."""
    if line_changes.path != view.old_snapshot.path:
        raise ValueError("line view path does not match its endpoint snapshots")
    # ``LineLevelChange`` takes ownership of the caller's existing row list to
    # avoid allocating a second reference per rendered line.  Python cannot
    # revoke the caller's alias to that list, so mutations through the alias do
    # not pass through the tracked sequence and cannot advance its revision.
    # Rehash at this authority boundary rather than letting object identity or
    # a best-effort mutation counter authorize different rendered bytes.
    if (
        diff_view_identity(
            line_changes,
            old_snapshot=view.old_snapshot,
            new_snapshot=view.new_snapshot,
        )
        != view
    ):
        raise ValueError("rendered diff rows do not match their view identity")

    requested = (
        display_ids
        if isinstance(display_ids, DisplayIdRanges)
        else DisplayIdRanges.from_ids(display_ids)
    )
    if not requested:
        raise ValueError("resolved selection must not be empty")
    old_ranges, new_ranges, found, units = _resolve_rows(
        line_changes,
        requested,
    )
    if found.ranges() != requested.ranges():
        raise ValueError("display selection contains IDs outside its diff view")

    return ResolvedSelection(
        file_diff=FileDiff(view.detached(), units),
        display_ids=requested,
        old_spans=SnapshotSpans(view.old_snapshot, old_ranges),
        new_spans=SnapshotSpans(view.new_snapshot, new_ranges),
    )


def _resolve_rows(
    line_changes: LineLevelChange,
    selected_ids: DisplayIdRanges,
) -> tuple[
    HalfOpenRanges,
    HalfOpenRanges,
    DisplayIdRanges,
    tuple[ChangeUnit, ...],
]:
    old_ranges = _HalfOpenRangeAccumulator()
    new_ranges = _HalfOpenRangeAccumulator()
    found_ids = _DisplayIdRangeAccumulator()
    selected_id_cursor = _DisplayIdMembershipCursor(selected_ids)
    units: list[ChangeUnit] = []
    old_content = _ContentWitnessAccumulator()
    new_content = _ContentWitnessAccumulator()
    old_start: int | None = None
    old_end: int | None = None
    new_start: int | None = None
    new_end: int | None = None
    old_cursor = line_changes.header.old_prefix_line_count()
    new_cursor = line_changes.header.new_prefix_line_count()
    run_old_boundary = old_cursor
    run_new_boundary = new_cursor
    block_old_boundary = old_cursor
    block_new_boundary = new_cursor
    in_change_block = False

    def flush() -> None:
        nonlocal old_start, old_end, new_start, new_end
        nonlocal old_content, new_content
        if old_start is None and new_start is None:
            return
        if old_start is not None:
            assert old_end is not None
            old_span_start = old_start - 1
            old_span_end = old_end
        else:
            old_span_start = run_old_boundary
            old_span_end = run_old_boundary
        if new_start is not None:
            assert new_end is not None
            new_span_start = new_start - 1
            new_span_end = new_end
        else:
            new_span_start = run_new_boundary
            new_span_end = run_new_boundary

        old_span: LineSpan[DiffOldSpace] = LineSpan(
            LineBoundary(old_span_start),
            LineBoundary(old_span_end),
        )
        new_span: LineSpan[DiffNewSpace] = LineSpan(
            LineBoundary(new_span_start),
            LineBoundary(new_span_end),
        )
        if old_start is not None and new_start is not None:
            units.append(
                Replacement(
                    old_span,
                    new_span,
                    old_content.finish(),
                    new_content.finish(),
                )
            )
        elif old_start is not None:
            units.append(
                Deletion(
                    old_span,
                    new_span.start,
                    old_content.finish(),
                )
            )
        else:
            units.append(
                Insertion(
                    old_span.start,
                    new_span,
                    new_content.finish(),
                )
            )
        old_start = old_end = new_start = new_end = None
        old_content = _ContentWitnessAccumulator()
        new_content = _ContentWitnessAccumulator()

    for line in line_changes.lines:
        if line.kind != " " and not in_change_block:
            block_old_boundary = old_cursor
            block_new_boundary = new_cursor
            in_change_block = True
        selected = line.id is not None and selected_id_cursor.contains(line.id)
        if not selected:
            flush()
        elif old_start is None and new_start is None:
            run_old_boundary = block_old_boundary
            run_new_boundary = block_new_boundary

        if selected:
            assert line.id is not None
            found_ids.add(line.id)
            if line.old_line_number is not None:
                old_ranges.add(line.old_line_number - 1, line.old_line_number)
            if line.new_line_number is not None:
                new_ranges.add(line.new_line_number - 1, line.new_line_number)
            if line.kind == "-":
                assert line.old_line_number is not None
                old_start = old_start or line.old_line_number
                old_end = line.old_line_number
                old_content.add_rendered_line(line)
            elif line.kind == "+":
                assert line.new_line_number is not None
                new_start = new_start or line.new_line_number
                new_end = line.new_line_number
                new_content.add_rendered_line(line)
            else:
                raise ValueError("display selection contains a context row")

        if line.old_line_number is not None:
            old_cursor = line.old_line_number
        if line.new_line_number is not None:
            new_cursor = line.new_line_number
        if line.kind == " ":
            in_change_block = False
    flush()
    return (
        old_ranges.finish(),
        new_ranges.finish(),
        found_ids.finish(),
        tuple(units),
    )


class _ContentWitnessAccumulator:
    """Build one exact content witness without retaining individual lines."""

    __slots__ = ("_digest", "_line_count", "_byte_count")

    def __init__(self) -> None:
        self._digest = hashlib.sha256()
        self._line_count = 0
        self._byte_count = 0

    def add(self, line: bytes) -> None:
        self._digest.update(len(line).to_bytes(8, "big"))
        self._digest.update(line)
        self._line_count += 1
        self._byte_count += len(line)

    def add_rendered_line(self, line: LineEntry) -> None:
        """Hash one rendered line without allocating its newline-appended copy."""
        trailing_length = 1 if line.has_trailing_newline else 0
        line_length = len(line.text_bytes) + trailing_length
        self._digest.update(line_length.to_bytes(8, "big"))
        self._digest.update(line.text_bytes)
        if trailing_length:
            self._digest.update(b"\n")
        self._line_count += 1
        self._byte_count += line_length

    def finish(self) -> ExactContentWitness:
        return ExactContentWitness(
            SnapshotIdentity("selected-content-sha256", self._digest.hexdigest()),
            self._line_count,
            self._byte_count,
        )


class _HalfOpenRangeAccumulator:
    """Accumulate ordered individual coordinates as compact ranges."""

    __slots__ = ("_ranges", "_run_start", "_run_end")

    def __init__(self) -> None:
        self._ranges: list[tuple[int, int]] = []
        self._run_start: int | None = None
        self._run_end: int | None = None

    def add(self, start: int, end: int) -> None:
        if self._run_start is None:
            self._run_start, self._run_end = start, end
            return
        assert self._run_end is not None
        if start <= self._run_end:
            self._run_end = max(self._run_end, end)
            return
        self._ranges.append((self._run_start, self._run_end))
        self._run_start, self._run_end = start, end

    def finish(self) -> HalfOpenRanges:
        if self._run_start is not None:
            assert self._run_end is not None
            self._ranges.append((self._run_start, self._run_end))
        return HalfOpenRanges.from_ranges(self._ranges)


class _DisplayIdRangeAccumulator:
    """Collect rendered IDs in row order without a set of every ID."""

    __slots__ = ("_last", "_ranges")

    def __init__(self) -> None:
        self._ranges: list[tuple[int, int]] = []
        self._last: int | None = None

    def add(self, display_id: int) -> None:
        if self._last is not None and display_id <= self._last:
            raise ValueError(
                "rendered diff contains duplicate or unordered display IDs"
            )
        if self._ranges and display_id == self._ranges[-1][1] + 1:
            start, _end = self._ranges[-1]
            self._ranges[-1] = (start, display_id)
        else:
            self._ranges.append((display_id, display_id))
        self._last = display_id

    def finish(self) -> DisplayIdRanges:
        return DisplayIdRanges(LineRanges.from_ranges(self._ranges))


class _DisplayIdMembershipCursor:
    """Match monotonically rendered IDs against ranges in linear time."""

    __slots__ = ("_index", "_last", "_ranges")

    def __init__(self, selected_ids: DisplayIdRanges) -> None:
        self._ranges = selected_ids.ranges()
        self._index = 0
        self._last: int | None = None

    def contains(self, display_id: int) -> bool:
        if self._last is not None and display_id <= self._last:
            raise ValueError(
                "rendered diff contains duplicate or unordered display IDs"
            )
        self._last = display_id
        while (
            self._index < len(self._ranges)
            and self._ranges[self._index][1] < display_id
        ):
            self._index += 1
        if self._index == len(self._ranges):
            return False
        start, end = self._ranges[self._index]
        return start <= display_id <= end
