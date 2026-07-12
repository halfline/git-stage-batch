"""Structural safety validation for batch merge placement."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from ...core.line_selection import LineSelection
from ...exceptions import MergeError as _MergeError
from ...i18n import _
from ..line_matching.line_mapping import LineMapping
from .presence_context import (
    contextual_presence_placements as _contextual_presence_placements,
)
from .presence_missing_claims import (
    mapped_missing_source_lines as _mapped_missing_source_lines,
)

if TYPE_CHECKING:
    from ..ownership.absence_claims import AbsenceClaim


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

    has_unmapped_deletion_anchor = False
    for deletion in deletions:
        after_line = deletion.anchor_line

        if after_line is not None:
            if after_line < 1 or after_line > len(source_lines):
                raise _MergeError(
                    _("Deletion after line {line} is out of range").format(line=after_line)
                )

            if not line_mapping.is_source_line_present(after_line):
                has_unmapped_deletion_anchor = True
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

    # Let absence realization report its more precise missing-anchor error once
    # nearby mapped context has allowed an unmapped deletion anchor through.
    if has_unmapped_deletion_anchor:
        return

    _contextual_presence_placements(
        source_lines,
        target_lines,
        claimed_lines,
        line_mapping,
        trusted_source_lines={
            deletion.anchor_line
            for deletion in deletions
            if deletion.anchor_line is not None
        },
    )
    _check_unbounded_trailing_context(
        line_mapping,
        claimed_lines,
        deletions,
        source_lines,
        target_lines,
    )


def _check_unbounded_trailing_context(
    line_mapping: LineMapping,
    claimed_lines: LineSelection,
    deletions: list[AbsenceClaim],
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
) -> None:
    """Reject large unmatched tails not resolved by contextual anchors.

    Distinctive anchors choose insertion ordering.  They cannot establish that
    a large, unselected source tail is compatible with unrelated target
    content, so retain the existing conservative protection for that separate
    version-skew shape.
    """
    missing = _mapped_missing_source_lines(
        claimed_lines,
        len(source_lines),
        line_mapping,
    )

    for run_start, run_end in missing.ranges():
        before_source_line = next(
            (
                line
                for line in range(run_start - 1, 0, -1)
                if line_mapping.is_source_line_present(line)
            ),
            None,
        )
        after_source_line = next(
            (
                line
                for line in range(run_end + 1, len(source_lines) + 1)
                if line_mapping.is_source_line_present(line)
            ),
            None,
        )
        trailing_gap = (
            after_source_line - run_end - 1
            if after_source_line is not None
            else len(source_lines) - run_end
        )
        if trailing_gap < 3:
            continue

        before_target_line = (
            line_mapping.get_target_line_from_source_line(before_source_line)
            if before_source_line is not None
            else None
        )
        after_target_line = (
            line_mapping.get_target_line_from_source_line(after_source_line)
            if after_source_line is not None
            else None
        )

        if before_target_line is not None and after_target_line is not None:
            target_span = after_target_line - before_target_line - 1
            source_span_outside_run = (
                after_source_line - before_source_line - 1
                - (run_end - run_start + 1)
            )
            if (
                target_span < source_span_outside_run
                or target_span <= run_end - run_start + 1
            ):
                raise _MergeError(
                    _("Batch was created from a different version of the file")
                )
            continue

        if before_target_line is None or after_target_line is not None:
            continue

        target_tail = len(target_lines) - before_target_line
        deleted_at_boundary = sum(
            len(deletion.content_lines)
            for deletion in deletions
            if deletion.anchor_line == before_source_line
        )
        if target_tail != 0 and target_tail > deleted_at_boundary:
            raise _MergeError(
                _("Batch was created from a different version of the file")
            )


def _first_selected_line(lines: LineSelection) -> int | None:
    first = getattr(lines, "first", None)
    if first is not None:
        return first()
    return min(lines) if lines else None
