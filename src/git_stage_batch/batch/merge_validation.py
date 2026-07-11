"""Structural safety validation for batch merge placement."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..core.line_selection import LineRanges, LineSelection
from ..exceptions import MergeError as _MergeError
from ..i18n import _
from .line_mapping import LineMapping
from .presence_missing_claims import (
    mapped_missing_source_lines as _mapped_missing_source_lines,
)

if TYPE_CHECKING:
    from .ownership_absence_claims import AbsenceClaim


@dataclass
class _ClaimedRunIntervalFacts:
    """Structural facts about one contiguous run of missing claimed lines.

    These facts make the merge-time safety decision explicit instead of hiding
    the reasoning inside a single trailing-gap threshold.
    """

    run_start: int
    run_end: int
    run_length: int
    before_source_line: int | None
    after_source_line: int | None
    before_target_line: int | None
    after_target_line: int | None
    leading_unmapped_source_gap: int
    trailing_unmapped_source_gap: int
    bracketed_on_both_sides: bool
    bracketed_on_one_side_only: bool
    source_interval_span: int | None
    target_interval_span: int | None
    surrounding_source_gap_outside_run: int | None
    target_lines_after_before_anchor: int | None
    has_deletion_at_before_anchor: bool
    deletion_line_count_at_before_anchor: int


def check_structural_validity(
    line_mapping: LineMapping,
    claimed_lines: LineSelection,
    deletions: list[AbsenceClaim],
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
) -> None:
    """Validate that batch can be safely applied given structural alignment.

    Checks:
    1. File hasn't been completely rewritten (zero alignment)
    2. Missing claimed lines have nearby aligned context
    3. Missing deletion anchors have nearby aligned context
    4. Claimed runs have structurally coherent surrounding context

    Check #4 prevents corruption when applying partial selections.
    If claimed lines come from a source region whose surrounding source structure
    no longer maps coherently into the working tree, inserting those lines may
    preserve incompatible working-tree content that should have been replaced.

    Args:
        line_mapping: Alignment between batch source and working tree
        claimed_lines: Claimed batch source line numbers
        deletions: List of AbsenceClaim objects
        source_lines: Batch source file lines (bytes)
        target_lines: Working tree file lines (bytes)

    Raises:
        MergeError: If structural requirements aren't met
    """
    present_count = sum(
        1 for line in range(1, len(source_lines) + 1)
        if line_mapping.is_source_line_present(line)
    )

    if len(target_lines) == 0:
        return

    if present_count == 0 and len(target_lines) > 0:
        if claimed_lines:
            first_claimed = _first_selected_line(claimed_lines)
            raise _MergeError(
                _("Cannot reliably place claimed line {line}: file completely rewritten").format(
                    line=first_claimed
                )
            )

    for claimed_line in claimed_lines:
        if claimed_line < 1 or claimed_line > len(source_lines):
            raise _MergeError(
                _("Claimed line {line} is out of range (batch source has {count} lines)").format(
                    line=claimed_line,
                    count=len(source_lines)
                )
            )

        if not line_mapping.is_source_line_present(claimed_line):
            has_context_before = False
            has_context_after = False

            for check_line in range(claimed_line - 1, 0, -1):
                if line_mapping.is_source_line_present(check_line):
                    has_context_before = True
                    break

            for check_line in range(claimed_line + 1, len(source_lines) + 1):
                if line_mapping.is_source_line_present(check_line):
                    has_context_after = True
                    break

            if not has_context_before and not has_context_after:
                raise _MergeError(
                    _("Cannot reliably place claimed line {line}: surrounding context lost").format(
                        line=claimed_line
                    )
                )

    for deletion in deletions:
        after_line = deletion.anchor_line

        if after_line is not None:
            if after_line < 1 or after_line > len(source_lines):
                raise _MergeError(
                    _("Deletion after line {line} is out of range").format(line=after_line)
                )

            if not line_mapping.is_source_line_present(after_line):
                has_context = False
                for check_line in range(
                    max(1, after_line - 3),
                    min(len(source_lines) + 1, after_line + 4),
                ):
                    if (
                        check_line != after_line
                        and line_mapping.is_source_line_present(check_line)
                    ):
                        has_context = True
                        break

                if not has_context and after_line != len(source_lines):
                    raise _MergeError(
                        _("Cannot determine deletion position after line {line}: anchor and neighbors missing").format(
                            line=after_line
                        )
                    )

    _check_claimed_region_compatibility(
        line_mapping,
        claimed_lines,
        deletions,
        source_lines,
        target_lines,
    )


def _check_claimed_region_compatibility(
    line_mapping: LineMapping,
    claimed_lines: LineSelection,
    deletions: list[AbsenceClaim],
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
) -> None:
    """Check whether claimed lines have structurally coherent context."""
    missing_claimed = _get_missing_claimed_lines(
        line_mapping,
        claimed_lines,
        source_lines,
    )

    if not missing_claimed or len(target_lines) == 0:
        return

    for run_start, run_end in missing_claimed.ranges():
        facts = _collect_claimed_run_interval_facts(
            run_start,
            run_end,
            line_mapping,
            source_lines,
            target_lines,
            deletions,
        )

        if not _is_claimed_run_structurally_coherent(facts):
            raise _MergeError(
                _("Batch was created from a different version of the file")
            )


def _get_missing_claimed_lines(
    line_mapping: LineMapping,
    claimed_lines: LineSelection,
    source_lines: Sequence[bytes],
) -> LineRanges:
    """Return claimed source lines that are not present in the working tree."""
    return _mapped_missing_source_lines(
        claimed_lines,
        len(source_lines),
        line_mapping,
    )


def _first_selected_line(lines: LineSelection) -> int | None:
    first = getattr(lines, "first", None)
    if first is not None:
        return first()
    return min(lines) if lines else None


def _find_nearest_mapped_source_line_before(
    line_mapping: LineMapping,
    source_line: int,
) -> int | None:
    """Find the nearest mapped source line strictly before the given line."""
    for check_line in range(source_line - 1, 0, -1):
        if line_mapping.get_target_line_from_source_line(check_line) is not None:
            return check_line

    return None


def _find_nearest_mapped_source_line_after(
    line_mapping: LineMapping,
    source_line: int,
    max_source_line: int,
) -> int | None:
    """Find the nearest mapped source line strictly after the given line."""
    for check_line in range(source_line + 1, max_source_line + 1):
        if line_mapping.get_target_line_from_source_line(check_line) is not None:
            return check_line

    return None


def _collect_claimed_run_interval_facts(
    run_start: int,
    run_end: int,
    line_mapping: LineMapping,
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    deletions: list[AbsenceClaim],
) -> _ClaimedRunIntervalFacts:
    """Collect explicit structural facts about one missing claimed run."""
    before_source_line = _find_nearest_mapped_source_line_before(
        line_mapping,
        run_start,
    )
    after_source_line = _find_nearest_mapped_source_line_after(
        line_mapping,
        run_end,
        len(source_lines),
    )

    before_target_line = None
    after_target_line = None

    if before_source_line is not None:
        before_target_line = line_mapping.get_target_line_from_source_line(
            before_source_line
        )

    if after_source_line is not None:
        after_target_line = line_mapping.get_target_line_from_source_line(
            after_source_line
        )

    leading_unmapped_source_gap = 0
    if before_source_line is not None:
        leading_unmapped_source_gap = run_start - before_source_line - 1

    trailing_unmapped_source_gap = 0
    if after_source_line is not None:
        trailing_unmapped_source_gap = after_source_line - run_end - 1
    else:
        trailing_unmapped_source_gap = len(source_lines) - run_end

    bracketed_on_both_sides = (
        before_source_line is not None and
        after_source_line is not None and
        before_target_line is not None and
        after_target_line is not None
    )
    bracketed_on_one_side_only = (
        (before_source_line is None) != (after_source_line is None)
    )

    source_interval_span = None
    target_interval_span = None
    surrounding_source_gap_outside_run = None
    target_lines_after_before_anchor = None
    has_deletion_at_before_anchor = False
    deletion_line_count_at_before_anchor = 0

    if bracketed_on_both_sides:
        source_interval_span = after_source_line - before_source_line - 1
        target_interval_span = after_target_line - before_target_line - 1
        surrounding_source_gap_outside_run = (
            source_interval_span - (run_end - run_start + 1)
        )
    elif before_target_line is not None and after_target_line is None:
        target_lines_after_before_anchor = len(target_lines) - before_target_line

    if before_source_line is not None:
        deletion_line_count_at_before_anchor = sum(
            len(deletion.content_lines)
            for deletion in deletions
            if deletion.anchor_line == before_source_line
        )
        has_deletion_at_before_anchor = deletion_line_count_at_before_anchor > 0

    return _ClaimedRunIntervalFacts(
        run_start=run_start,
        run_end=run_end,
        run_length=run_end - run_start + 1,
        before_source_line=before_source_line,
        after_source_line=after_source_line,
        before_target_line=before_target_line,
        after_target_line=after_target_line,
        leading_unmapped_source_gap=leading_unmapped_source_gap,
        trailing_unmapped_source_gap=trailing_unmapped_source_gap,
        bracketed_on_both_sides=bracketed_on_both_sides,
        bracketed_on_one_side_only=bracketed_on_one_side_only,
        source_interval_span=source_interval_span,
        target_interval_span=target_interval_span,
        surrounding_source_gap_outside_run=surrounding_source_gap_outside_run,
        target_lines_after_before_anchor=target_lines_after_before_anchor,
        has_deletion_at_before_anchor=has_deletion_at_before_anchor,
        deletion_line_count_at_before_anchor=deletion_line_count_at_before_anchor,
    )


def _is_claimed_run_structurally_coherent(
    facts: _ClaimedRunIntervalFacts,
) -> bool:
    """Check whether a missing claimed run fits its source/target interval."""
    significant_trailing_gap = facts.trailing_unmapped_source_gap >= 3
    significant_leading_gap = facts.leading_unmapped_source_gap >= 3

    if not facts.bracketed_on_both_sides and not facts.bracketed_on_one_side_only:
        return False

    if facts.bracketed_on_both_sides:
        if facts.before_target_line is None or facts.after_target_line is None:
            return False

        if facts.before_target_line >= facts.after_target_line:
            return False

        # A large source-only region before the claimed run means the preceding
        # mapped line may belong to unrelated target content with common text.
        # Automatic placement cannot distinguish that false anchor from an
        # intentionally shared prefix.
        if significant_leading_gap:
            return False

        if significant_trailing_gap:
            if (
                facts.target_interval_span is None
                or facts.surrounding_source_gap_outside_run is None
            ):
                return False

            # There is substantial source-side structure after the run before
            # the next reliable source anchor, but almost no room for it in
            # target-space. This is the characteristic shape of the corruption
            # case: the selected run came from a neighborhood with extra
            # source-only structure, so inserting it would preserve
            # incompatible target content nearby.
            if facts.target_interval_span < facts.surrounding_source_gap_outside_run:
                return False

            # Even if the overall interval is not smaller, a run followed by a
            # large source-only tail with little or no target interval is still
            # too weakly bracketed to trust.
            if facts.target_interval_span <= facts.run_length:
                return False

        return True

    if facts.before_source_line is not None and facts.after_source_line is None:
        if significant_trailing_gap:
            if facts.target_lines_after_before_anchor is None:
                return False

            # A source-only tail after the selected run is safe when applying
            # into an empty target tail: this is the append/interleave case
            # that lets independent batches compose in either order.
            if facts.target_lines_after_before_anchor == 0:
                return True

            # A replacement can also be safe with target content after the
            # anchor when an absence constraint at that same boundary removes
            # the whole target tail before the new claimed lines are inserted.
            if (
                facts.has_deletion_at_before_anchor and
                facts.target_lines_after_before_anchor
                <= facts.deletion_line_count_at_before_anchor
            ):
                return True

            return False
        return True

    if facts.before_source_line is None and facts.after_source_line is not None:
        if significant_leading_gap:
            return False
        return True

    return False
