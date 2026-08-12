"""Replacement-selection helpers shared by command implementations."""

from __future__ import annotations

from collections.abc import Sequence

from ...batch.line_matching.comparison import derive_display_id_run_sets_from_lines
from ...batch.ownership.replacement_line_runs import (
    ReplacementLineRun,
    derive_replacement_line_runs_from_lines,
)
from ...exceptions import exit_with_error
from ...core.models import LineEntry, LineLevelChange
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

    if len(selected_ids) != max(selected_ids) - min(selected_ids) + 1:
        exit_with_error(_("Replacement selection must be one contiguous line range."))


def build_leading_replacement_addition_selection_error(
    line_changes: LineLevelChange,
    selected_ids: set[int],
) -> str | None:
    """Reject include selections that split an inserted replacement prefix."""
    changed_run: list[LineEntry] = []

    def check_run(run: list[LineEntry]) -> str | None:
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
    line_changes: LineLevelChange,
    selected_ids: set[int],
    *,
    hunk_base_lines: Sequence[bytes],
    hunk_source_lines: Sequence[bytes],
) -> str | None:
    """Reject contiguous file-scoped selections that only partly include later runs."""
    if len(selected_ids) <= 1:
        return None

    if len(selected_ids) != max(selected_ids) - min(selected_ids) + 1:
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


def expand_replacement_selection_ids(
    line_changes: LineLevelChange,
    requested_ids: set[int],
    *,
    preserve_partial_addition_prefix: bool = False,
) -> set[int]:
    """Expand selected rows to every adjacent mixed replacement core.

    A discard replacement may preserve an explicit leading batch alternative;
    other callers retain the historical complete-core expansion.
    """
    expanded_ids: set[int] | None = None
    line_index = 0
    while line_index < len(line_changes.lines):
        if line_changes.lines[line_index].kind not in ("+", "-"):
            line_index += 1
            continue

        run_start = line_index
        first_addition: int | None = None
        first_requested_index: int | None = None
        malformed_run = False
        while (
            line_index < len(line_changes.lines)
            and line_changes.lines[line_index].kind in ("+", "-")
        ):
            line = line_changes.lines[line_index]
            if line.id in requested_ids and first_requested_index is None:
                first_requested_index = line_index
            if line.kind == "+":
                if first_addition is None:
                    first_addition = line_index
            elif first_addition is not None:
                malformed_run = True
            line_index += 1
        run_stop = line_index

        if (
            malformed_run
            or first_addition is None
            or first_addition == run_start
        ):
            continue

        deletion_count = first_addition - run_start
        addition_count = run_stop - first_addition
        replacement_stop = first_addition + min(
            deletion_count,
            addition_count,
        )
        if (
            first_requested_index is None
            or first_requested_index >= replacement_stop
        ):
            continue

        selected_addition_count = 0
        for run_index in range(first_addition, run_stop):
            line_id = line_changes.lines[run_index].id
            if line_id not in requested_ids:
                break
            selected_addition_count += 1
        selects_complete_old_side = all(
            line_changes.lines[run_index].id is not None
            and line_changes.lines[run_index].id in requested_ids
            for run_index in range(run_start, first_addition)
        )
        selects_partial_addition_prefix = (
            0 < selected_addition_count < addition_count
            and all(
                line_changes.lines[run_index].id not in requested_ids
                for run_index in range(
                    first_addition + selected_addition_count,
                    run_stop,
                )
            )
        )
        if (
            preserve_partial_addition_prefix
            and selects_complete_old_side
            and selects_partial_addition_prefix
        ):
            continue

        if any(
            line_changes.lines[run_index].id is None
            for run_index in range(run_start, replacement_stop)
        ):
            exit_with_error(
                _(
                    "Cannot replace a partial replacement run because another "
                    "changed line in the run is unavailable."
                )
            )

        if expanded_ids is None:
            expanded_ids = set(requested_ids)
        for run_index in range(run_start, replacement_stop):
            line_id = line_changes.lines[run_index].id
            if line_id is not None:
                expanded_ids.add(line_id)

    return requested_ids if expanded_ids is None else expanded_ids
