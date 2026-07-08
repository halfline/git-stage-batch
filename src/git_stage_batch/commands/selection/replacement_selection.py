"""Replacement-selection helpers shared by command implementations."""

from __future__ import annotations

from collections.abc import Sequence

from ...batch.comparison import derive_display_id_run_sets_from_lines
from ...batch.ownership import (
    ReplacementLineRun,
    derive_replacement_line_runs_from_lines,
)
from ...exceptions import exit_with_error
from ...i18n import _


def derive_replacement_line_runs(
    *,
    hunk_base_lines: Sequence[bytes],
    hunk_source_lines: Sequence[bytes],
) -> list[ReplacementLineRun]:
    """Derive replacement runs from before/after file comparison."""
    return derive_replacement_line_runs_from_lines(
        old_file_lines=hunk_base_lines,
        new_file_lines=hunk_source_lines,
    )


def require_contiguous_display_selection(selected_ids: set[int]) -> None:
    """Require one contiguous selected display range for replacement text."""
    if not selected_ids:
        return

    selected_range = list(range(min(selected_ids), max(selected_ids) + 1))
    if sorted(selected_ids) != selected_range:
        exit_with_error(_("Replacement selection must be one contiguous line range."))


def build_leading_replacement_addition_selection_error(
    line_changes,
    selected_ids: set[int],
) -> str | None:
    """Reject include selections that split an inserted replacement prefix."""
    changed_run: list = []

    def check_run(run: list) -> str | None:
        if not run:
            return None
        deletion_ids = tuple(
            line.id
            for line in run
            if line.kind == "-" and line.id is not None
        )
        addition_ids = tuple(
            line.id
            for line in run
            if line.kind == "+" and line.id is not None
        )
        if not deletion_ids or not addition_ids:
            return None

        deletion_id_set = set(deletion_ids)
        selected_deletions = selected_ids & deletion_id_set
        selected_addition_positions = [
            index
            for index, line_id in enumerate(addition_ids)
            if line_id in selected_ids
        ]
        if not selected_addition_positions:
            return None

        selects_first_addition = selected_addition_positions[0] == 0
        if selects_first_addition and not selected_deletions:
            return _(
                "That line selection splits the leading edge of a replacement. "
                "Select the removed line with the first inserted line, select only "
                "later inserted lines, or use --as."
            )
        if selected_deletions:
            if selected_deletions != deletion_id_set:
                return _(
                    "That line selection splits the removed side of a replacement. "
                    "Select every removed line with inserted lines, select only "
                    "inserted lines, or use --as."
                )
            expected_prefix = list(range(selected_addition_positions[-1] + 1))
            if selected_addition_positions != expected_prefix:
                return _(
                    "That line selection splits the leading edge of a replacement. "
                    "Select the removed line with a contiguous prefix of inserted "
                    "lines, select only later inserted lines, or use --as."
                )
        return None

    for line in line_changes.lines:
        if line.kind in ("+", "-") and line.id is not None:
            changed_run.append(line)
            continue
        error = check_run(changed_run)
        if error is not None:
            return error
        changed_run = []

    return check_run(changed_run)


def build_partial_structural_run_selection_error(
    line_changes,
    selected_ids: set[int],
    *,
    hunk_base_lines: Sequence[bytes],
    hunk_source_lines: Sequence[bytes],
) -> str | None:
    """Reject contiguous file-scoped selections that only partly include later runs."""
    if len(selected_ids) <= 1:
        return None

    sorted_ids = sorted(selected_ids)
    expected_ids = list(range(sorted_ids[0], sorted_ids[-1] + 1))
    if sorted_ids != expected_ids:
        return None

    run_sets = derive_display_id_run_sets_from_lines(
        line_changes,
        source_lines=hunk_base_lines,
        target_lines=hunk_source_lines,
    )
    intersected_runs = [run_set for run_set in run_sets if selected_ids & run_set]
    if len(intersected_runs) <= 1:
        return None

    partially_selected_runs = [
        run_set
        for run_set in intersected_runs
        if (selected_ids & run_set) != run_set
    ]
    if not partially_selected_runs:
        return None

    return _(
        "That line range crosses separate changes while selecting only part of one. "
        "Select one change at a time, include every line in the range, or use --as."
    )


def expand_replacement_selection_ids(line_changes, requested_ids: set[int]) -> set[int]:
    """Expand a selection to the smallest adjacent mixed replacement run."""
    selected_indices = [
        index
        for index, line in enumerate(line_changes.lines)
        if line.id in requested_ids
    ]
    if not selected_indices:
        return requested_ids

    run_start = min(selected_indices)
    run_end = max(selected_indices)

    run_entries = line_changes.lines[run_start:run_end + 1]
    run_kinds = {line.kind for line in run_entries if line.kind in ("+", "-")}

    if run_kinds != {"+", "-"}:
        selected_kind = next(iter(run_kinds), None)
        opposite_kind = "-" if selected_kind == "+" else "+"

        left_index = run_start - 1
        while left_index >= 0 and line_changes.lines[left_index].kind == selected_kind:
            left_index -= 1
        if left_index >= 0 and line_changes.lines[left_index].kind == opposite_kind:
            run_start = left_index

        right_index = run_end + 1
        while (
            right_index < len(line_changes.lines)
            and line_changes.lines[right_index].kind == selected_kind
        ):
            right_index += 1
        if (
            right_index < len(line_changes.lines)
            and line_changes.lines[right_index].kind == opposite_kind
        ):
            run_end = right_index

        run_entries = line_changes.lines[run_start:run_end + 1]
        run_kinds = {line.kind for line in run_entries if line.kind in ("+", "-")}
        if run_kinds != {"+", "-"}:
            return requested_ids

    return {
        line.id
        for line in run_entries
        if line.id is not None
    }
