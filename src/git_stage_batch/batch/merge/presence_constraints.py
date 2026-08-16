"""Presence constraint realization for batch-source merges."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from .absence_constraints import (
    apply_absence_constraints as _apply_merge_absence_constraints,
)
from ..line_matching.line_mapping import LineMapping
from ..line_matching.match import match_lines
from ..line_matching.match_workspace import MatcherWorkspace
from .candidates import MergeResolution as _MergeResolution
from .presence_context import (
    PresenceRunPlacement as _PresenceRunPlacement,
    contextual_presence_placements as _contextual_presence_placements,
)
from .presence_missing_claims import (
    mapped_missing_source_lines as _mapped_missing_source_lines,
)
from . import presence_placement_choices as _presence_placement_choices
from ..realization.entries import RealizedEntry as _RealizedEntry
from ..realization.entry_storage import (
    RealizedEntries,
    RealizedEntryContentSequence,
    realized_entry_is_claimed_at,
    realized_entry_source_line_at,
)
from ..realization import mapping as _realized_mapping
from ...core.line_selection import (
    LineRangeBuilder,
    LineRanges,
    LineSelection,
    coerce_line_ranges,
)
from ...core.mapped_storage import sort_mapped_records
from ...core.resource_cleanup import close_resources_preserving_first
from ...exceptions import MergeError as _MergeError
from ...i18n import _

if TYPE_CHECKING:
    from ..ownership.absence_claims import AbsenceClaim


_PRESENCE_CANDIDATE_CAP = 50


def apply_presence_constraints(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    presence_line_set: LineSelection,
    *,
    source_to_working_mapping: LineMapping | None = None,
    resolution: _MergeResolution | None = None,
    trusted_source_lines: Collection[int] = (),
    require_distinctive_context: bool = False,
    distinctive_context_lines: LineSelection | None = None,
    contextual_placements: Sequence[_PresenceRunPlacement] | None = None,
    collapsing_target_spans: Sequence[tuple[int, ...]] = (),
    spool_dir: str | Path | None = None,
) -> RealizedEntries:
    """Apply presence constraints: ensure all claimed lines exist in result.

    Uses structural alignment to determine which claimed lines are already present
    and adds missing ones at appropriate positions. Returns structured entries
    that preserve batch-source provenance for anchored absence constraints.

    Args:
        source_lines: Batch source file lines (bytes with newlines)
        working_lines: Working tree file lines (bytes with newlines)
        presence_line_set: Source line numbers that must be present

    Returns:
        Realized entries with all claimed lines present and provenance preserved
    """
    owned_mapping: LineMapping | None = None
    mapping = source_to_working_mapping
    if mapping is None:
        owned_mapping = match_lines(
            source_lines,
            working_lines,
            spool_dir=spool_dir,
        )
        mapping = owned_mapping

    try:
        result = _apply_presence_constraints_with_mapping(
            source_lines,
            working_lines,
            presence_line_set,
            mapping,
            resolution=resolution,
            trusted_source_lines=trusted_source_lines,
            require_distinctive_context=require_distinctive_context,
            distinctive_context_lines=distinctive_context_lines,
            contextual_placements=contextual_placements,
            collapsing_target_spans=collapsing_target_spans,
            spool_dir=spool_dir,
        )
    except BaseException:
        if owned_mapping is not None:
            try:
                owned_mapping.close()
            except BaseException:
                pass
        raise

    if owned_mapping is not None:
        try:
            owned_mapping.close()
        except BaseException:
            try:
                result.close()
            except BaseException:
                pass
            raise
    return result


def _apply_presence_constraints_with_mapping(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    presence_line_set: LineSelection,
    mapping: LineMapping,
    *,
    resolution: _MergeResolution | None = None,
    trusted_source_lines: Collection[int] = (),
    require_distinctive_context: bool = False,
    distinctive_context_lines: LineSelection | None = None,
    contextual_placements: Sequence[_PresenceRunPlacement] | None = None,
    collapsing_target_spans: Sequence[tuple[int, ...]] = (),
    spool_dir: str | Path | None = None,
) -> RealizedEntries:
    """Apply presence constraints using an existing source-to-working mapping."""

    try:
        presence_decision = _presence_placement_choices.presence_resolution_decision(
            {} if resolution is None else resolution.decisions
        )
    except ValueError:
        raise _MergeError(_("Selected merge resolution is no longer valid")) from None

    if not presence_line_set:
        if presence_decision is not None:
            raise _MergeError(_("Selected merge resolution is no longer valid"))
        result = RealizedEntries(spool_dir=spool_dir)
        _realized_mapping.append_working_range_with_mapping(
            result,
            working_lines,
            mapping,
            0,
            len(working_lines),
            presence_line_set,
        )
        return result

    missing_claimed = _mapped_missing_source_lines(
        presence_line_set,
        len(source_lines),
        mapping,
    )

    if not missing_claimed:
        if presence_decision is not None:
            raise _MergeError(_("Selected merge resolution is no longer valid"))
        result = RealizedEntries(spool_dir=spool_dir)
        _realized_mapping.append_working_range_with_mapping(
            result,
            working_lines,
            mapping,
            0,
            len(working_lines),
            presence_line_set,
        )
        return result

    if presence_decision is not None:
        presence_key, presence_choices = (
            _presence_placement_choices.presence_choices_for_missing_claimed_run(
                source_lines,
                working_lines,
                presence_line_set,
                mapping,
                max_results=_PRESENCE_CANDIDATE_CAP + 1,
                trusted_source_lines=trusted_source_lines,
                require_distinctive_context=require_distinctive_context,
                distinctive_context_lines=distinctive_context_lines,
                spool_dir=spool_dir,
            )
        )
        selected_key, selected_choice_index = presence_decision
        if presence_key != selected_key:
            raise _MergeError(_("Selected merge resolution is no longer valid"))
        for choice in presence_choices:
            if choice.choice_index == selected_choice_index:
                result = RealizedEntries(spool_dir=spool_dir)
                _realized_mapping.append_working_range_with_mapping(
                    result,
                    working_lines,
                    mapping,
                    0,
                    choice.gap_index,
                    presence_line_set,
                )
                result.append_line_range_from(
                    source_lines,
                    choice.run_start - 1,
                    choice.run_end,
                    source_line_start=choice.run_start,
                    is_claimed=True,
                )
                _realized_mapping.append_working_range_with_mapping(
                    result,
                    working_lines,
                    mapping,
                    choice.gap_index,
                    len(working_lines),
                    presence_line_set,
                )
                return result
        raise _MergeError(_("Selected merge resolution is no longer valid"))

    if all(
        mapping.is_source_line_present(source_line)
        for source_line in trusted_source_lines
    ):
        placements: Sequence[_PresenceRunPlacement]
        if contextual_placements is None:
            missing_claimed, computed_placements = _contextual_presence_placements(
                source_lines,
                working_lines,
                presence_line_set,
                mapping,
                trusted_source_lines=trusted_source_lines,
                require_distinctive_context=require_distinctive_context,
                distinctive_context_lines=distinctive_context_lines,
                collapsing_target_spans=collapsing_target_spans,
                spool_dir=spool_dir,
            )
            placements = computed_placements
        else:
            placements = contextual_placements
        if placements:
            return _realize_contextual_placements(
                source_lines,
                working_lines,
                presence_line_set,
                mapping,
                placements,
                spool_dir=spool_dir,
            )

    result = RealizedEntries(spool_dir=spool_dir)
    working_idx = 0

    for source_line in range(1, len(source_lines) + 1):
        working_line = mapping.get_target_line_from_source_line(source_line)

        if working_line is not None:
            if working_idx < working_line - 1:
                _realized_mapping.append_working_range_with_mapping(
                    result,
                    working_lines,
                    mapping,
                    working_idx,
                    working_line - 1,
                    presence_line_set,
                )
                working_idx = working_line - 1

            is_claimed = source_line in presence_line_set
            if is_claimed:
                result.append_line_from(
                    source_lines,
                    source_line - 1,
                    source_line=source_line,
                    target_line=working_idx + 1,
                    is_claimed=True,
                )
            else:
                result.append_line_from(
                    working_lines,
                    working_idx,
                    source_line=source_line,
                    target_line=working_idx + 1,
                    is_claimed=False,
                )
            working_idx += 1
        else:
            if source_line in missing_claimed:
                result.append_line_from(
                    source_lines,
                    source_line - 1,
                    source_line=source_line,
                    is_claimed=True,
                )

    while working_idx < len(working_lines):
        _realized_mapping.append_working_range_with_mapping(
            result,
            working_lines,
            mapping,
            working_idx,
            len(working_lines),
            presence_line_set,
        )
        working_idx = len(working_lines)

    return result


def _realize_contextual_placements(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    presence_line_set: LineSelection,
    mapping: LineMapping,
    placements: Sequence[_PresenceRunPlacement],
    *,
    spool_dir: str | Path | None = None,
) -> RealizedEntries:
    """Insert missing runs at gaps chosen from distinctive context."""
    result = RealizedEntries(spool_dir=spool_dir)
    working_idx = 0

    for placement in placements:
        if working_idx < placement.gap_index:
            _realized_mapping.append_working_range_with_mapping(
                result,
                working_lines,
                mapping,
                working_idx,
                placement.gap_index,
                presence_line_set,
            )
            working_idx = placement.gap_index

        result.append_line_range_from(
            source_lines,
            placement.run_start - 1,
            placement.run_end,
            source_line_start=placement.run_start,
            is_claimed=True,
        )

    if working_idx < len(working_lines):
        _realized_mapping.append_working_range_with_mapping(
            result,
            working_lines,
            mapping,
            working_idx,
            len(working_lines),
            presence_line_set,
        )

    return result


def _missing_claimed_lines(
    entries: Sequence[_RealizedEntry], presence_line_set: LineSelection
) -> LineRanges:
    """Return claimed source lines that are not present as claimed entries."""
    claimed_ranges = LineRangeBuilder()
    presence_lines = coerce_line_ranges(presence_line_set)

    if isinstance(entries, RealizedEntries):
        for run in entries.provenance_runs():
            if not run.is_claimed or run.source_start == 0:
                continue
            claimed_ranges.add_range(
                run.source_start,
                run.source_start + (run.dest_end - run.dest_start) - 1,
            )
        return presence_lines.difference(claimed_ranges.finish())

    for index in range(len(entries)):
        source_line = realized_entry_source_line_at(entries, index)
        if source_line is not None and realized_entry_is_claimed_at(entries, index):
            claimed_ranges.add_line(source_line)
    return presence_lines.difference(claimed_ranges.finish())


def satisfy_constraints(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    presence_line_set: LineSelection,
    deletion_claims: Sequence["AbsenceClaim"],
    *,
    strict: bool = True,
    source_to_working_mapping: LineMapping | None = None,
    resolution: _MergeResolution | None = None,
    require_distinctive_context: bool = False,
    distinctive_context_lines: LineSelection | None = None,
    contextual_placements: Sequence[_PresenceRunPlacement] | None = None,
    spool_dir: str | Path | None = None,
) -> RealizedEntries:
    """Apply presence and absence constraints until claimed lines survive."""
    trusted_source_lines = LineRanges.from_lines(
        deletion.anchor_line
        for deletion in deletion_claims
        if deletion.anchor_line is not None and deletion.content_lines
    )
    realization_workspace: MatcherWorkspace | None = None
    collapsing_target_spans: Sequence[tuple[int, ...]] = ()
    realization_fallback_target_positions: Sequence[tuple[int, ...]] = ()

    realized_entries: RealizedEntries | None = None
    updated_entries: RealizedEntries | None = None
    try:
        if not strict:
            from .baseline_anchor_matching import baseline_removal_edit

            realization_workspace = MatcherWorkspace(spool_dir=spool_dir)
            target_span_records = realization_workspace.record_vector(
                len(deletion_claims),
                "QQ",
            )
            fallback_position_records = realization_workspace.record_vector(
                len(deletion_claims),
                "QQ",
            )
            for deletion_index, deletion in enumerate(deletion_claims):
                edit = baseline_removal_edit(deletion, working_lines)
                if edit is None:
                    continue
                target_span_records.append(edit)
                fallback_position_records.append((deletion_index, edit[0]))
            sort_mapped_records(target_span_records)
            collapsing_target_spans = target_span_records
            realization_fallback_target_positions = fallback_position_records

        realized_entries = apply_presence_constraints(
            source_lines,
            working_lines,
            presence_line_set,
            source_to_working_mapping=source_to_working_mapping,
            resolution=resolution,
            trusted_source_lines=trusted_source_lines,
            require_distinctive_context=require_distinctive_context,
            distinctive_context_lines=distinctive_context_lines,
            contextual_placements=contextual_placements,
            collapsing_target_spans=collapsing_target_spans,
            spool_dir=spool_dir,
        )
        updated_entries = _apply_merge_absence_constraints(
            realized_entries,
            deletion_claims,
            strict=strict,
            resolution=resolution,
            realization_fallback_target_positions=(
                realization_fallback_target_positions
            ),
            spool_dir=spool_dir,
        )
    except BaseException:
        close_resources_preserving_first(
            (
                updated_entries,
                realized_entries if realized_entries is not updated_entries else None,
                realization_workspace,
            ),
            suppress_errors=True,
        )
        raise

    assert realized_entries is not None
    assert updated_entries is not None
    try:
        if realization_workspace is not None:
            realization_workspace.close()
    except BaseException:
        close_resources_preserving_first(
            (
                updated_entries,
                realized_entries if realized_entries is not updated_entries else None,
            ),
            suppress_errors=True,
        )
        raise

    if updated_entries is not realized_entries:
        try:
            realized_entries.close()
        except BaseException:
            close_resources_preserving_first(
                (updated_entries,),
                suppress_errors=True,
            )
            raise
    realized_entries = updated_entries

    try:
        if not _missing_claimed_lines(realized_entries, presence_line_set):
            return realized_entries

        previous_entries = realized_entries
        current_lines = RealizedEntryContentSequence(previous_entries)
        try:
            updated_entries = apply_presence_constraints(
                source_lines,
                current_lines,
                presence_line_set,
                resolution=resolution,
                trusted_source_lines=trusted_source_lines,
                require_distinctive_context=require_distinctive_context,
                distinctive_context_lines=distinctive_context_lines,
                spool_dir=spool_dir,
            )
        except BaseException:
            close_resources_preserving_first(
                (previous_entries,),
                suppress_errors=True,
            )
            raise
        try:
            previous_entries.close()
        except BaseException:
            close_resources_preserving_first(
                (updated_entries,),
                suppress_errors=True,
            )
            raise
        realized_entries = updated_entries

        updated_entries = _apply_merge_absence_constraints(
            realized_entries,
            deletion_claims,
            strict=strict,
            resolution=resolution,
        )
        if updated_entries is not realized_entries:
            try:
                realized_entries.close()
            except BaseException:
                close_resources_preserving_first(
                    (updated_entries,),
                    suppress_errors=True,
                )
                raise
        realized_entries = updated_entries

        missing_claimed = _missing_claimed_lines(
            realized_entries,
            presence_line_set,
        )
        if missing_claimed:
            if not strict:
                return realized_entries
            first_missing = missing_claimed.first()
            raise _MergeError(
                _(
                    "Cannot satisfy claimed line {line}: removed by absence constraints"
                ).format(line=first_missing)
            )

        return realized_entries
    except BaseException:
        close_resources_preserving_first(
            (realized_entries,),
            suppress_errors=True,
        )
        raise
