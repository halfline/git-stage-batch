"""Conservative replay checks for legacy selection metadata."""

from __future__ import annotations

from collections.abc import Sequence
from itertools import chain
from pathlib import Path

from ...core.line_selection import LineRanges
from ...core.text_lines import normalize_line_sequence_endings
from ...exceptions import CommandError
from ...git_paths import display_path
from ...i18n import _
from ..line_matching.match import match_lines
from ..line_matching.line_mapping import LineMapping
from ..line_matching.match_workspace import MatcherWorkspace
from .baseline_reference_positions import (
    baseline_reference_insertion_position,
)
from .presence_reference_index import EffectivePresenceReferenceIndex
from ..ownership.model import BatchOwnership


def _replacement_presence_lines(ownership: BatchOwnership) -> LineRanges:
    """Return presence ranges whose old side was explicitly recorded."""
    return LineRanges.from_specs(
        chain.from_iterable(
            unit.presence_lines for unit in ownership.replacement_units
        )
    )


def _run_has_exact_saved_boundary(
    references: EffectivePresenceReferenceIndex,
    target_lines: Sequence[bytes],
    run_start: int,
    run_end: int,
    insertion_position: int,
) -> bool:
    """Return whether every line in a run independently names this gap."""
    for source_line in range(run_start, run_end + 1):
        if baseline_reference_insertion_position(
            references.reference_for(source_line),
            target_lines,
        ) != insertion_position:
            return False
    return True


def _missing_run_collapses_between_mapped_neighbors(
    references: EffectivePresenceReferenceIndex,
    target_lines: Sequence[bytes],
    mapping: LineMapping,
    source_line_count: int,
    run_start: int,
    run_end: int,
) -> bool:
    """Return whether a missing run occupies an otherwise collapsed gap."""
    if run_start <= 1 or run_end >= source_line_count:
        return False
    preceding_target = mapping.get_target_line_from_source_line(
        run_start - 1
    )
    following_target = mapping.get_target_line_from_source_line(run_end + 1)
    if (
        preceding_target is None
        or following_target != preceding_target + 1
    ):
        return False
    return not _run_has_exact_saved_boundary(
        references,
        target_lines,
        run_start,
        run_end,
        preceding_target,
    )


def reject_ambiguous_legacy_presence_replay(
    file_path: str,
    source_lines: Sequence[bytes],
    ownership: BatchOwnership,
    target_lines: Sequence[bytes],
    *,
    legacy_unmarked_source_alternatives: bool,
    spool_dir: str | Path | None = None,
) -> None:
    """Reject a legacy insertion that could instead be a lost replacement."""
    if not legacy_unmarked_source_alternatives:
        return

    uncertain_presence = ownership.presence_line_set().difference(
        _replacement_presence_lines(ownership)
    )
    if not uncertain_presence:
        return

    normalized_source = normalize_line_sequence_endings(source_lines)
    normalized_target = normalize_line_sequence_endings(target_lines)
    with (
        MatcherWorkspace(spool_dir=spool_dir) as workspace,
        match_lines(
            normalized_source,
            normalized_target,
            spool_dir=spool_dir,
        ) as mapping,
    ):
        references = EffectivePresenceReferenceIndex(workspace, ownership)
        for selection_start, selection_end in uncertain_presence.ranges():
            missing_start: int | None = None
            for source_line in range(selection_start, selection_end + 1):
                line_is_missing = (
                    source_line > len(normalized_source)
                    or mapping.get_target_line_from_source_line(source_line)
                    is None
                )
                if line_is_missing:
                    if missing_start is None:
                        missing_start = source_line
                    continue
                if missing_start is not None and (
                    _missing_run_collapses_between_mapped_neighbors(
                        references,
                        normalized_target,
                        mapping,
                        len(normalized_source),
                        missing_start,
                        source_line - 1,
                    )
                ):
                    break
                missing_start = None
            else:
                if missing_start is None or not (
                    _missing_run_collapses_between_mapped_neighbors(
                        references,
                        normalized_target,
                        mapping,
                        len(normalized_source),
                        missing_start,
                        selection_end,
                    )
                ):
                    continue
            raise CommandError(
                _(
                    "Cannot safely replay legacy batch metadata for {file}: "
                    "the batch does not record whether adjacent historical "
                    "source content was an alternative that should be "
                    "replaced. Recreate this batch with the current version "
                    "of git-stage-batch before replaying it."
                ).format(file=display_path(file_path))
            )
