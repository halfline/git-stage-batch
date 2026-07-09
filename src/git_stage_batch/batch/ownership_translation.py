"""Translate selected diff lines into batch ownership claims."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from ..core.models import LineEntry
from .absence_content import (
    AbsenceContentBuilder as _AbsenceContentBuilder,
    build_absence_content_from_range as _build_absence_content_from_range,
)
from .ownership import (
    AbsenceClaim,
    BaselineReference,
    BatchOwnership,
    ReplacementUnit,
    ReplacementUnitOrigin,
)
from .ownership_claims import LineRangeBuilder, presence_claims_from_source_lines
from .ownership_replacement_units import normalize_replacement_units
from .replacement_line_runs import ReplacementLineRun as _ReplacementLineRun


@dataclass
class _ReplacementUnitBuilder:
    deletion_indices: list[int]
    claimed_lines: LineRangeBuilder = field(default_factory=LineRangeBuilder)

    def add_presence_line(self, source_line: int) -> None:
        self.claimed_lines.add_line(source_line)

    def finish(self) -> ReplacementUnit:
        return ReplacementUnit(
            presence_lines=self.claimed_lines.finish().to_range_strings(),
            deletion_indices=self.deletion_indices,
        )


def detect_stale_batch_source_for_selection(selected_lines: list) -> bool:
    """Detect if selected lines cannot be expressed in current batch source.

    Returns True if any claimed/addition line has source_line=None, indicating
    the batch source is stale and must be advanced before translation.

    Args:
        selected_lines: List of LineEntry objects to check

    Returns:
        True if batch source is stale, False otherwise
    """
    for line in selected_lines:
        # Context and addition lines should have source_line populated
        # If they don't, the current batch source cannot express this change
        if line.kind in (' ', '+') and line.source_line is None:
            return True
        # A None deletion anchor is only current for deletions before line 1.
        if (
            line.kind == '-'
            and line.source_line is None
            and line.old_line_number is not None
            and line.old_line_number > 1
        ):
            return True
    return False


def translate_lines_to_batch_ownership(selected_lines: list) -> BatchOwnership:
    """Translate display lines to batch source ownership.

    Creates presence claims and suppression constraints (deletion_claims).
    Each contiguous run of deletions becomes a separate AbsenceClaim.

    This function assumes all selected lines can be expressed in batch source
    space. Call detect_stale_batch_source_for_selection() first and handle stale
    sources before calling this function. If source_line is None for claimed
    lines, this raises an error instead of dropping them.

    Args:
        selected_lines: List of LineEntry objects to translate

    Returns:
        BatchOwnership with presence claims and absence claims

    Raises:
        ValueError: If any claimed line has source_line=None (stale batch source)
    """
    # Translate to batch source-space ownership
    # Diff shows index→working tree, batch source = working tree
    # Context/addition lines exist in batch source → presence claims
    # Deletion lines don't exist in batch source → absence claims (suppression)

    content_view = _LineEntryContentSequence(selected_lines)
    claimed_source_lines = LineRangeBuilder()
    presence_baseline_references: dict[int, BaselineReference] = {}
    absence_claims: list[AbsenceClaim] = []
    replacement_units: list[ReplacementUnit] = []

    # Track current deletion run
    current_absence_anchor: int | None = None
    current_absence_baseline_reference: BaselineReference | None = None
    current_absence_start: int | None = None
    current_absence_stop: int | None = None
    active_replacement_unit: _ReplacementUnitBuilder | None = None

    def finish_replacement_unit(
        builder: _ReplacementUnitBuilder | None,
    ) -> None:
        if builder is not None:
            replacement_units.append(builder.finish())

    def flush_absence_run() -> list[int]:
        """Finalize current deletion run as an AbsenceClaim."""
        nonlocal current_absence_anchor
        nonlocal current_absence_baseline_reference
        nonlocal current_absence_start
        nonlocal current_absence_stop
        if current_absence_start is None or current_absence_stop is None:
            return []

        content_lines = _build_absence_content_from_range(
            content_view,
            current_absence_start,
            current_absence_stop,
        )
        absence_claims.append(
            AbsenceClaim(
                anchor_line=current_absence_anchor,
                content_lines=content_lines,
                baseline_reference=current_absence_baseline_reference,
            )
        )
        absence_index = len(absence_claims) - 1
        current_absence_start = None
        current_absence_stop = None
        current_absence_anchor = None
        current_absence_baseline_reference = None
        return [absence_index]

    for index, line in enumerate(selected_lines):
        if line.kind in (' ', '+'):
            # Context or addition: exists in batch source (working tree)
            # Flush any pending deletion run
            flushed_deletion_indices = flush_absence_run()

            if line.source_line is None:
                raise ValueError(
                    f"Cannot translate line to batch ownership: source_line is None "
                    f"(kind={line.kind!r}, text={line.display_text()!r}). "
                    f"Batch source is stale and must be advanced before translation."
                )

            claimed_source_lines.add_line(line.source_line)
            if line.has_baseline_reference_after:
                presence_baseline_references[line.source_line] = BaselineReference(
                    after_line=line.baseline_reference_after_line,
                    after_content=line.baseline_reference_after_text_bytes,
                    has_after_line=line.has_baseline_reference_after,
                    before_line=line.baseline_reference_before_line,
                    before_content=line.baseline_reference_before_text_bytes,
                    has_before_line=line.has_baseline_reference_before,
                )
            if line.kind == '+':
                if flushed_deletion_indices:
                    finish_replacement_unit(active_replacement_unit)
                    active_replacement_unit = _ReplacementUnitBuilder(
                        deletion_indices=flushed_deletion_indices,
                    )

                if active_replacement_unit is not None:
                    active_replacement_unit.add_presence_line(line.source_line)
            else:
                finish_replacement_unit(active_replacement_unit)
                active_replacement_unit = None

            # Update anchor for next deletion run
            current_absence_anchor = line.source_line

        elif line.kind == '-':
            finish_replacement_unit(active_replacement_unit)
            active_replacement_unit = None
            # Deletion: suppression constraint
            # Anchor each deletion run at its first deleted line. A None anchor
            # means the run starts before the first source line and must not be
            # overwritten by later deleted lines in the same run.
            if current_absence_start is None:
                current_absence_start = index
                current_absence_anchor = line.source_line
                if line.old_line_number is not None:
                    current_absence_baseline_reference = BaselineReference(
                        after_line=(
                            line.old_line_number - 1
                            if line.old_line_number > 1 else
                            None
                        )
                    )
            current_absence_stop = index + 1

    # Flush any final deletion run
    flush_absence_run()
    finish_replacement_unit(active_replacement_unit)

    return BatchOwnership(
        presence_claims=presence_claims_from_source_lines(
            claimed_source_lines.finish(),
            presence_baseline_references,
        ),
        deletions=absence_claims,
        replacement_units=normalize_replacement_units(
            replacement_units,
            deletion_count=len(absence_claims),
        ),
    )


def _old_line_content_by_number(hunk_lines: list[LineEntry]) -> dict[int, bytes]:
    return {
        line.old_line_number: line.text_bytes
        for line in hunk_lines
        if line.old_line_number is not None and line.kind in {" ", "-"}
    }


def _line_entry_content(line: LineEntry) -> bytes:
    return line.text_bytes + (b"\n" if line.has_trailing_newline else b"")


class _LineEntryContentSequence(Sequence[bytes]):
    """Lazy byte-line view over LineEntry content."""

    def __init__(self, lines: Sequence[LineEntry]) -> None:
        self._lines = lines

    def __len__(self) -> int:
        return len(self._lines)

    def __getitem__(self, index: int | slice) -> bytes | Sequence[bytes]:
        if isinstance(index, slice):
            return _LineEntryContentSequence(self._lines[index])

        return _line_entry_content(self._lines[index])


def _baseline_reference_for_old_line_range(
    old_start: int,
    old_end: int,
    old_line_content: dict[int, bytes],
) -> BaselineReference:
    after_line = old_start - 1 if old_start > 1 else None
    before_line = old_end + 1
    before_content = old_line_content.get(before_line)
    return BaselineReference(
        after_line=after_line,
        after_content=(
            old_line_content.get(after_line)
            if after_line is not None else
            None
        ),
        has_after_line=True,
        before_line=before_line if before_content is not None else None,
        before_content=before_content,
        has_before_line=before_content is not None,
    )


def _replacement_unit_origin_for_line_run(
    replacement_run: _ReplacementLineRun,
    old_line_content: dict[int, bytes],
) -> ReplacementUnitOrigin:
    """Build parent replacement context for a file-derived replacement run."""
    return ReplacementUnitOrigin(
        old_start=replacement_run.old_start,
        old_end=replacement_run.old_end,
        new_start=replacement_run.new_start,
        new_end=replacement_run.new_end,
        baseline_reference=_baseline_reference_for_old_line_range(
            replacement_run.old_start,
            replacement_run.old_end,
            old_line_content,
        ),
    )


@dataclass(frozen=True)
class _HunkLineRangeScan:
    start: int
    end: int
    start_index: int
    stop_index: int
    count: int
    selected_count: int

    @property
    def complete(self) -> bool:
        return self.count == self.end - self.start + 1

    @property
    def fully_selected(self) -> bool:
        return self.complete and self.selected_count == self.count


def _scan_hunk_line_range(
    hunk_lines: list[LineEntry],
    cursor: int,
    *,
    kind: str,
    line_number_attr: str,
    start: int,
    end: int,
    selected_display_ids: set[int],
) -> _HunkLineRangeScan:
    index = cursor
    start_index = cursor
    count = 0
    selected_count = 0
    found_first = False

    while index < len(hunk_lines):
        line = hunk_lines[index]
        line_number = getattr(line, line_number_attr)
        if line_number is not None and line_number > end:
            break
        if line.kind == kind and line_number is not None:
            if line_number < start:
                index += 1
                continue
            if line_number > end:
                break
            if not found_first:
                start_index = index
                found_first = True
            count += 1
            if line.id is not None and line.id in selected_display_ids:
                selected_count += 1
        index += 1

    return _HunkLineRangeScan(
        start=start,
        end=end,
        start_index=start_index,
        stop_index=index,
        count=count,
        selected_count=selected_count,
    )


def _hunk_line_indexes_in_range(
    hunk_lines: list[LineEntry],
    scan: _HunkLineRangeScan,
    *,
    kind: str,
    line_number_attr: str,
) -> Iterable[int]:
    for index in range(scan.start_index, scan.stop_index):
        line = hunk_lines[index]
        line_number = getattr(line, line_number_attr)
        if (
            line.kind == kind
            and line_number is not None
            and scan.start <= line_number <= scan.end
        ):
            yield index


def _hunk_line_index_ranges_in_range(
    hunk_lines: list[LineEntry],
    scan: _HunkLineRangeScan,
    *,
    kind: str,
    line_number_attr: str,
) -> Iterable[tuple[int, int]]:
    pending_start: int | None = None
    pending_stop: int | None = None

    for index in _hunk_line_indexes_in_range(
        hunk_lines,
        scan,
        kind=kind,
        line_number_attr=line_number_attr,
    ):
        if pending_stop == index:
            pending_stop = index + 1
            continue

        if pending_start is not None and pending_stop is not None:
            yield pending_start, pending_stop
        pending_start = index
        pending_stop = index + 1

    if pending_start is not None and pending_stop is not None:
        yield pending_start, pending_stop


def translate_hunk_selection_to_batch_ownership(
    hunk_lines: list[LineEntry],
    selected_display_ids: set[int],
    *,
    replacement_line_runs: list[_ReplacementLineRun] | None = None,
) -> BatchOwnership:
    """Translate selected live-hunk IDs while retaining full-hunk boundaries.

    Unlike translate_lines_to_batch_ownership(), this scans the complete live
    diff hunk. Unselected lines are not claimed, but they still delimit selected
    deletion runs and provide source/baseline boundary metadata for conservative
    round trips through batch storage. The IDs are user-facing selection handles;
    the input is not rendered batch-display output.

    Replacement coupling is supplied by the caller as before/after line-number
    runs derived from the full files represented by the hunk. This function does
    not infer semantic replacement units from the pregenerated diff layout.
    """
    claimed_source_lines = LineRangeBuilder()
    presence_baseline_references: dict[int, BaselineReference] = {}
    absence_claims: list[AbsenceClaim] = []
    replacement_units: list[ReplacementUnit] = []
    old_line_content = _old_line_content_by_number(hunk_lines)
    hunk_content_view = _LineEntryContentSequence(hunk_lines)
    consumed_replacement_ids: set[int] = set()

    def add_replacement_unit(
        selected_old_ranges: Iterable[tuple[int, int]],
        selected_new_lines: Iterable[LineEntry],
        *,
        old_start: int,
        old_end: int,
        origin: ReplacementUnitOrigin | None = None,
    ) -> None:
        deletion_anchor: int | None = None
        old_line_seen = False
        selected_source_lines = LineRangeBuilder()
        consumed_ids: list[int] = []
        with _AbsenceContentBuilder() as builder:
            for range_start, range_stop in selected_old_ranges:
                if not old_line_seen:
                    deletion_anchor = hunk_lines[range_start].source_line
                    old_line_seen = True
                builder.append_line_range(
                    hunk_content_view,
                    range_start,
                    range_stop,
                )
                for index in range(range_start, range_stop):
                    old_line = hunk_lines[index]
                    if old_line.id is not None:
                        consumed_ids.append(old_line.id)

            content_lines = builder.finish()

        for new_line in selected_new_lines:
            if new_line.source_line is None:
                raise ValueError(
                    f"Cannot translate line to batch ownership: source_line is None "
                    f"(kind={new_line.kind!r}, text={new_line.display_text()!r}). "
                    f"Batch source is stale and must be advanced before translation."
                )

            claimed_source_lines.add_line(new_line.source_line)
            selected_source_lines.add_line(new_line.source_line)
            if new_line.id is not None:
                consumed_ids.append(new_line.id)
            if new_line.has_baseline_reference_after:
                presence_baseline_references[new_line.source_line] = BaselineReference(
                    after_line=new_line.baseline_reference_after_line,
                    after_content=new_line.baseline_reference_after_text_bytes,
                    has_after_line=new_line.has_baseline_reference_after,
                    before_line=new_line.baseline_reference_before_line,
                    before_content=new_line.baseline_reference_before_text_bytes,
                    has_before_line=new_line.has_baseline_reference_before,
                )

        absence_claims.append(
            AbsenceClaim(
                anchor_line=deletion_anchor,
                content_lines=content_lines,
                baseline_reference=_baseline_reference_for_old_line_range(
                    old_start,
                    old_end,
                    old_line_content,
                ),
            )
        )
        replacement_units.append(
            ReplacementUnit(
                presence_lines=selected_source_lines.finish().to_range_strings(),
                deletion_indices=[len(absence_claims) - 1],
                origin=origin,
            )
        )
        consumed_replacement_ids.update(consumed_ids)

    old_cursor = 0
    new_cursor = 0

    for replacement_run in replacement_line_runs or []:
        replacement_origin = _replacement_unit_origin_for_line_run(
            replacement_run,
            old_line_content,
        )
        old_scan = _scan_hunk_line_range(
            hunk_lines,
            old_cursor,
            kind="-",
            line_number_attr="old_line_number",
            start=replacement_run.old_start,
            end=replacement_run.old_end,
            selected_display_ids=selected_display_ids,
        )
        new_scan = _scan_hunk_line_range(
            hunk_lines,
            new_cursor,
            kind="+",
            line_number_attr="new_line_number",
            start=replacement_run.new_start,
            end=replacement_run.new_end,
            selected_display_ids=selected_display_ids,
        )
        old_cursor = old_scan.stop_index
        new_cursor = new_scan.stop_index

        if not old_scan.complete or not new_scan.complete:
            continue

        if old_scan.count == new_scan.count:
            old_indexes = _hunk_line_indexes_in_range(
                hunk_lines,
                old_scan,
                kind="-",
                line_number_attr="old_line_number",
            )
            new_indexes = _hunk_line_indexes_in_range(
                hunk_lines,
                new_scan,
                kind="+",
                line_number_attr="new_line_number",
            )
            for old_index, new_index in zip(old_indexes, new_indexes):
                old_line = hunk_lines[old_index]
                new_line = hunk_lines[new_index]
                old_selected = (
                    old_line.id is not None
                    and old_line.id in selected_display_ids
                )
                new_selected = (
                    new_line.id is not None
                    and new_line.id in selected_display_ids
                )
                if old_selected and new_selected:
                    if old_line.old_line_number is None:
                        continue
                    add_replacement_unit(
                        ((old_index, old_index + 1),),
                        (new_line,),
                        old_start=old_line.old_line_number,
                        old_end=old_line.old_line_number,
                        origin=replacement_origin,
                    )
            continue

        if old_scan.fully_selected and new_scan.fully_selected:
            add_replacement_unit(
                _hunk_line_index_ranges_in_range(
                    hunk_lines,
                    old_scan,
                    kind="-",
                    line_number_attr="old_line_number",
                ),
                (
                    hunk_lines[index]
                    for index in _hunk_line_indexes_in_range(
                        hunk_lines,
                        new_scan,
                        kind="+",
                        line_number_attr="new_line_number",
                    )
                ),
                old_start=replacement_run.old_start,
                old_end=replacement_run.old_end,
                origin=replacement_origin,
            )

    current_absence_anchor: int | None = None
    current_absence_start: int | None = None
    current_absence_stop: int | None = None
    current_absence_old_start: int | None = None
    current_absence_old_end: int | None = None
    active_replacement_unit: _ReplacementUnitBuilder | None = None

    def finish_replacement_unit(
        builder: _ReplacementUnitBuilder | None,
    ) -> None:
        if builder is not None:
            replacement_units.append(builder.finish())

    def flush_absence_run() -> list[int]:
        nonlocal current_absence_anchor
        nonlocal current_absence_start
        nonlocal current_absence_stop
        nonlocal current_absence_old_start
        nonlocal current_absence_old_end
        if current_absence_start is None or current_absence_stop is None:
            return []

        baseline_reference = (
            _baseline_reference_for_old_line_range(
                current_absence_old_start,
                current_absence_old_end,
                old_line_content,
            )
            if (
                current_absence_old_start is not None
                and current_absence_old_end is not None
            )
            else None
        )
        absence_claims.append(
            AbsenceClaim(
                anchor_line=current_absence_anchor,
                content_lines=_build_absence_content_from_range(
                    hunk_content_view,
                    current_absence_start,
                    current_absence_stop,
                ),
                baseline_reference=baseline_reference,
            )
        )
        absence_index = len(absence_claims) - 1
        current_absence_anchor = None
        current_absence_start = None
        current_absence_stop = None
        current_absence_old_start = None
        current_absence_old_end = None
        return [absence_index]

    for index, line in enumerate(hunk_lines):
        is_selected = (
            line.id is not None
            and line.id in selected_display_ids
            and line.id not in consumed_replacement_ids
        )

        if line.kind in {" ", "+"}:
            flushed_deletion_indices = flush_absence_run()

            if is_selected:
                if line.source_line is None:
                    raise ValueError(
                        f"Cannot translate line to batch ownership: source_line is None "
                        f"(kind={line.kind!r}, text={line.display_text()!r}). "
                        f"Batch source is stale and must be advanced before translation."
                    )

                claimed_source_lines.add_line(line.source_line)
                if line.has_baseline_reference_after:
                    presence_baseline_references[line.source_line] = BaselineReference(
                        after_line=line.baseline_reference_after_line,
                        after_content=line.baseline_reference_after_text_bytes,
                        has_after_line=line.has_baseline_reference_after,
                        before_line=line.baseline_reference_before_line,
                        before_content=line.baseline_reference_before_text_bytes,
                        has_before_line=line.has_baseline_reference_before,
                    )

                if line.kind == "+":
                    if flushed_deletion_indices:
                        finish_replacement_unit(active_replacement_unit)
                        active_replacement_unit = _ReplacementUnitBuilder(
                            deletion_indices=flushed_deletion_indices,
                        )

                    if active_replacement_unit is not None:
                        active_replacement_unit.add_presence_line(line.source_line)
                else:
                    finish_replacement_unit(active_replacement_unit)
                    active_replacement_unit = None
            else:
                finish_replacement_unit(active_replacement_unit)
                active_replacement_unit = None

            if line.source_line is not None:
                current_absence_anchor = line.source_line
            continue

        if line.kind == "-":
            if not is_selected:
                flush_absence_run()
                finish_replacement_unit(active_replacement_unit)
                active_replacement_unit = None
                continue

            finish_replacement_unit(active_replacement_unit)
            active_replacement_unit = None
            if current_absence_start is None:
                current_absence_anchor = line.source_line
                current_absence_start = index
            current_absence_stop = index + 1
            if line.old_line_number is not None:
                if current_absence_old_start is None:
                    current_absence_old_start = line.old_line_number
                current_absence_old_end = line.old_line_number

    flush_absence_run()
    finish_replacement_unit(active_replacement_unit)

    return BatchOwnership(
        presence_claims=presence_claims_from_source_lines(
            claimed_source_lines.finish(),
            presence_baseline_references,
        ),
        deletions=absence_claims,
        replacement_units=normalize_replacement_units(
            replacement_units,
            deletion_count=len(absence_claims),
        ),
    )
