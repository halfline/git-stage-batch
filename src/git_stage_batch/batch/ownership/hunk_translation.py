"""Translate selected live-hunk lines into batch ownership claims."""

from __future__ import annotations

from ...core.line_selection import LineRanges
from ...core.models import LineEntry
from . import hunk_replacement_translation as _hunk_replacement_translation
from .absence_content import (
    build_absence_content_from_range as _build_absence_content_from_range,
)
from .model import BatchOwnership
from .absence_claims import AbsenceClaim
from .claims import LineRangeBuilder, presence_claims_from_source_lines
from .line_entries import (
    LineEntryContentSequence as _LineEntryContentSequence,
    ReplacementUnitBuilder as _ReplacementUnitBuilder,
    baseline_reference_for_old_line_range as _baseline_reference_for_old_line_range,
    baseline_reference_for_presence_line as _baseline_reference_for_presence_line,
    old_line_content_by_number as _old_line_content_by_number,
)
from .references import BaselineReference
from .replacement_units import normalize_replacement_units
from .replacement_line_runs import ReplacementLineRun as _ReplacementLineRun


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
    old_line_content = _old_line_content_by_number(hunk_lines)
    hunk_content_view = _LineEntryContentSequence(hunk_lines)
    replacement_translation = (
        _hunk_replacement_translation.translate_hunk_replacement_line_runs(
            hunk_lines=hunk_lines,
            selected_display_ids=selected_display_ids,
            replacement_line_runs=replacement_line_runs or (),
            old_line_content=old_line_content,
            hunk_content_view=hunk_content_view,
        )
    )
    claimed_source_lines = LineRangeBuilder()
    presence_baseline_references: dict[int, BaselineReference] = dict(
        replacement_translation.presence_baseline_references
    )
    absence_claims: list[AbsenceClaim] = list(replacement_translation.absence_claims)
    replacement_units = list(replacement_translation.replacement_units)
    consumed_replacement_ids = replacement_translation.consumed_display_ids

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
                baseline_reference = _baseline_reference_for_presence_line(line)
                if baseline_reference is not None:
                    presence_baseline_references[line.source_line] = (
                        baseline_reference
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
            LineRanges.from_ranges(
                (
                    *replacement_translation.claimed_source_lines.ranges(),
                    *claimed_source_lines.finish().ranges(),
                )
            ),
            presence_baseline_references,
        ),
        deletions=absence_claims,
        replacement_units=normalize_replacement_units(
            replacement_units,
            deletion_count=len(absence_claims),
        ),
    )
