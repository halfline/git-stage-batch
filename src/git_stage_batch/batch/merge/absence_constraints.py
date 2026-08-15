"""Absence-constraint application for realized batch entries."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from ...exceptions import (
    AmbiguousAnchorError as _AmbiguousAnchorError,
    MergeError as _MergeError,
    MissingAnchorError as _MissingAnchorError,
)
from ...i18n import _, ngettext
from ...core.buffer import LineBuffer
from ...core.text_lines import normalize_line_endings
from ...editor.piece_table import LineLike
from ...core.mapped_storage import (
    MappedIntVector,
    MappedRecordVector,
    sort_mapped_records,
)
from ...core.resource_cleanup import close_resources_preserving_first
from ..line_matching.match_workspace import MatcherWorkspace
from .candidates import MergeResolution as _MergeResolution
from ..realization.boundaries import (
    boundary_choices_after_source_line as _boundary_choices_for_source_line,
    find_boundary_after_source_line as _locate_boundary_after_source_line,
    find_realization_fallback_boundary as _realization_fallback_boundary,
)
from ..realization.entries import RealizedEntry as _RealizedEntry
from ..realization.entry_storage import (
    RealizedEntries,
    as_realized_entries,
    realized_entry_content_at,
    realized_entry_is_claimed_at,
)

if TYPE_CHECKING:
    from ..ownership.absence_claims import AbsenceClaim


_DEFAULT_CHOICE_SCAN_CAP = 50
ABSENCE_AMBIGUITY_PREFIX = "absence:"


@dataclass(frozen=True)
class AbsenceChoice:
    """Concrete exact-removal choice for one absence claim."""

    choice_index: int
    position: int
    target_after_line: int | None
    target_before_line: int | None
    explanation: str


class _ActiveRealizedLineIndex:
    """Storage-backed order statistics for one-pass absence suppression."""

    def __init__(
        self,
        workspace: MatcherWorkspace,
        entries: RealizedEntries,
    ) -> None:
        self._entries = entries
        self._line_count = len(entries)
        self._active = workspace.int_vector(
            self._line_count,
            width=4,
            fill=1,
        )
        self._nonclaimed = workspace.int_vector(
            self._line_count,
            width=4,
            fill=0,
        )
        self._active_tree = workspace.int_vector(
            self._line_count + 1,
            width=8,
            fill=0,
        )
        self._nonclaimed_tree = workspace.int_vector(
            self._line_count + 1,
            width=8,
            fill=0,
        )
        source_records = workspace.record_vector(
            self._line_count,
            "QQQ",
        )
        self._source_groups = workspace.record_vector(
            self._line_count,
            "QQQQQ",
        )
        self._source_group_by_original = workspace.int_vector(
            self._line_count,
            width=8,
            fill=0,
        )
        try:
            self._initialize_provenance(source_records)
        except BaseException:
            try:
                workspace.close_resource(source_records)
            except BaseException:
                pass
            raise
        else:
            workspace.close_resource(source_records)
        self._initialize_tree(self._active_tree, self._active)
        self._initialize_tree(self._nonclaimed_tree, self._nonclaimed)

    @property
    def active_count(self) -> int:
        """Return the current number of realized lines."""
        return self._prefix_count(self._active_tree, self._line_count)

    def boundary_after_source_line(self, source_line: int | None) -> int:
        """Return an active boundary with normal ambiguity handling."""
        if source_line is None:
            return 0

        group_index = self._first_source_group(source_line)
        if (
            group_index >= len(self._source_groups)
            or self._source_groups[group_index][0] != source_line
        ):
            matching_count = 0
            claimed_count = 0
            matching_original_index = 0
            claimed_original_index = 0
        else:
            (
                _record_source_line,
                matching_count,
                claimed_count,
                matching_original_index,
                claimed_original_index,
            ) = self._source_groups[group_index]

        if matching_count == 0:
            raise _MissingAnchorError(
                _(
                    "Cannot locate anchor boundary after source line {line}: "
                    "anchor not present in realized content"
                ).format(line=source_line)
            )
        if matching_count == 1:
            original_index = matching_original_index
        elif claimed_count == 1:
            original_index = claimed_original_index
        elif claimed_count == 0:
            raise _AmbiguousAnchorError(
                ngettext(
                    "Anchor ambiguity: source line {line} appears {count} "
                    "time in realized content but is not claimed",
                    "Anchor ambiguity: source line {line} appears {count} "
                    "times in realized content but none are claimed",
                    matching_count,
                ).format(line=source_line, count=matching_count)
            )
        else:
            raise _AmbiguousAnchorError(
                ngettext(
                    "Anchor ambiguity: source line {line} claimed {count} time",
                    "Anchor ambiguity: source line {line} claimed {count} times",
                    claimed_count,
                ).format(line=source_line, count=claimed_count)
            )
        return self._prefix_count(self._active_tree, original_index + 1)

    def position_after_claimed(self, position: int) -> int:
        """Skip one contiguous active claimed run in logarithmic time."""
        if position >= self.active_count:
            return position
        original_index = self._select(self._active_tree, position)
        if self._nonclaimed[original_index]:
            return position
        nonclaimed_before = self._prefix_count(
            self._nonclaimed_tree,
            original_index,
        )
        nonclaimed_count = self._prefix_count(
            self._nonclaimed_tree,
            self._line_count,
        )
        if nonclaimed_before >= nonclaimed_count:
            return self.active_count
        next_nonclaimed = self._select(
            self._nonclaimed_tree,
            nonclaimed_before,
        )
        return self._prefix_count(self._active_tree, next_nonclaimed)

    def sequence_matches(
        self,
        lines: Sequence[bytes],
        position: int,
        sequence: Sequence[bytes],
    ) -> bool:
        """Return whether active unclaimed lines match one normalized sequence."""
        if position < 0 or position + len(sequence) > self.active_count:
            return False
        for offset in range(len(sequence)):
            original_index = self._select(
                self._active_tree,
                position + offset,
            )
            if not self._nonclaimed[original_index] or normalize_line_endings(
                lines[original_index]
            ) != normalize_line_endings(sequence[offset]):
                return False
        return True

    def remove_sequence(self, position: int, line_count: int) -> None:
        """Deactivate a consecutive sequence at one current position."""
        for _offset in range(line_count):
            original_index = self._select(self._active_tree, position)
            source_group = self._source_group_by_original[original_index]
            if source_group:
                group_index = source_group - 1
                (
                    source_line,
                    matching_count,
                    claimed_count,
                    matching_original_indexes,
                    claimed_original_indexes,
                ) = self._source_groups[group_index]
                is_claimed = not self._nonclaimed[original_index]
                self._source_groups[group_index] = (
                    source_line,
                    matching_count - 1,
                    claimed_count - int(is_claimed),
                    matching_original_indexes ^ original_index,
                    (
                        claimed_original_indexes ^ original_index
                        if is_claimed
                        else claimed_original_indexes
                    ),
                )
            self._active[original_index] = 0
            self._update_tree(self._active_tree, original_index)
            if self._nonclaimed[original_index]:
                self._update_tree(self._nonclaimed_tree, original_index)

    def build_result(
        self,
        lines: LineBuffer,
    ) -> RealizedEntries:
        """Build surviving entries in one provenance/run traversal."""
        result = RealizedEntries()
        try:
            result.retain_line_buffer(lines)
            for run in self._entries.provenance_runs():
                position = run.dest_start
                while position < run.dest_end:
                    while position < run.dest_end and not self._active[position]:
                        position += 1
                    active_start = position
                    while position < run.dest_end and self._active[position]:
                        position += 1
                    if active_start == position:
                        continue
                    result.append_line_range_from(
                        lines,
                        active_start,
                        position,
                        source_line_start=run.source_line_at(active_start),
                        target_line_start=run.target_line_at(active_start),
                        is_claimed=run.is_claimed,
                    )
            return result
        except BaseException:
            close_resources_preserving_first(
                (result,),
                suppress_errors=True,
            )
            raise

    def _initialize_provenance(
        self,
        source_records: MappedRecordVector,
    ) -> None:
        for run in self._entries.provenance_runs():
            run_length = run.dest_end - run.dest_start
            if not run.is_claimed:
                for original_index in range(run.dest_start, run.dest_end):
                    self._nonclaimed[original_index] = 1
            if run.source_start == 0:
                continue
            for offset in range(run_length):
                source_records.append(
                    (
                        run.source_start + offset,
                        run.dest_start + offset,
                        int(run.is_claimed),
                    )
                )
        sort_mapped_records(source_records)

        record_index = 0
        while record_index < len(source_records):
            source_line = source_records[record_index][0]
            matching_count = 0
            claimed_count = 0
            matching_original_indexes = 0
            claimed_original_indexes = 0
            group_index = len(self._source_groups)
            while (
                record_index < len(source_records)
                and source_records[record_index][0] == source_line
            ):
                (
                    _record_source_line,
                    original_index,
                    is_claimed,
                ) = source_records[record_index]
                matching_count += 1
                matching_original_indexes ^= original_index
                if is_claimed:
                    claimed_count += 1
                    claimed_original_indexes ^= original_index
                self._source_group_by_original[original_index] = group_index + 1
                record_index += 1
            self._source_groups.append(
                (
                    source_line,
                    matching_count,
                    claimed_count,
                    matching_original_indexes,
                    claimed_original_indexes,
                )
            )

    @staticmethod
    def _initialize_tree(
        tree: MappedIntVector,
        values: MappedIntVector,
    ) -> None:
        for original_index in range(len(values)):
            tree_index = original_index + 1
            tree[tree_index] += values[original_index]
            parent = tree_index + (tree_index & -tree_index)
            if parent < len(tree):
                tree[parent] += tree[tree_index]

    @staticmethod
    def _prefix_count(tree: MappedIntVector, stop: int) -> int:
        count = 0
        tree_index = stop
        while tree_index > 0:
            count += tree[tree_index]
            tree_index -= tree_index & -tree_index
        return count

    @staticmethod
    def _update_tree(tree: MappedIntVector, original_index: int) -> None:
        tree_index = original_index + 1
        while tree_index < len(tree):
            tree[tree_index] -= 1
            tree_index += tree_index & -tree_index

    def _select(self, tree: MappedIntVector, position: int) -> int:
        """Return the original index of a zero-based active position."""
        target_count = position + 1
        tree_index = 0
        step = 0 if self._line_count == 0 else 1 << (self._line_count.bit_length() - 1)
        while step:
            candidate = tree_index + step
            if candidate < len(tree) and tree[candidate] < target_count:
                target_count -= tree[candidate]
                tree_index = candidate
            step >>= 1
        if tree_index >= self._line_count:
            raise IndexError(position)
        return tree_index

    def _first_source_group(self, source_line: int) -> int:
        lower = 0
        upper = len(self._source_groups)
        while lower < upper:
            middle = (lower + upper) // 2
            if self._source_groups[middle][0] < source_line:
                lower = middle + 1
            else:
                upper = middle
        return lower


def _apply_strict_absence_constraints_indexed(
    result: RealizedEntries,
    deletion_claims: Sequence[AbsenceClaim],
    *,
    spool_dir: str | Path | None,
) -> RealizedEntries:
    """Apply ordinary strict removals without rebuilding after each claim."""
    lines = LineBuffer.from_line_chunks(
        result.content_chunks(),
        spool_dir=spool_dir,
    )
    realized: RealizedEntries | None = None
    try:
        with MatcherWorkspace(spool_dir=spool_dir) as workspace:
            active = _ActiveRealizedLineIndex(workspace, result)
            changed = False
            for claim in deletion_claims:
                if not claim.content_lines:
                    continue
                forbidden_sequence = claim.content_lines
                boundary = active.boundary_after_source_line(claim.anchor_line)
                removal_position = boundary
                if not active.sequence_matches(
                    lines,
                    removal_position,
                    forbidden_sequence,
                ):
                    after_claimed = active.position_after_claimed(boundary)
                    if after_claimed != boundary and active.sequence_matches(
                        lines,
                        after_claimed,
                        forbidden_sequence,
                    ):
                        removal_position = after_claimed
                    else:
                        search_end = min(
                            boundary + 20,
                            active.active_count - len(forbidden_sequence) + 1,
                        )
                        if any(
                            active.sequence_matches(
                                lines,
                                check_position,
                                forbidden_sequence,
                            )
                            for check_position in range(
                                boundary + 1,
                                search_end,
                            )
                        ):
                            raise _MergeError(
                                _(
                                    "Batch was created from a different "
                                    "version of the file"
                                )
                            )
                        continue
                active.remove_sequence(
                    removal_position,
                    len(forbidden_sequence),
                )
                changed = True

            if changed:
                realized = active.build_result(lines)
    except BaseException:
        close_resources_preserving_first(
            (realized, lines if realized is None else None),
            suppress_errors=True,
        )
        raise

    if realized is None:
        lines.close()
        return result
    return realized


def apply_absence_constraints(
    entries: Sequence[_RealizedEntry],
    deletion_claims: Sequence[AbsenceClaim],
    *,
    strict: bool = True,
    resolution: _MergeResolution | None = None,
    realization_fallback_target_positions: Sequence[tuple[int, ...]] = (),
    spool_dir: str | Path | None = None,
) -> RealizedEntries:
    """Apply absence constraints with boundary enforcement.

    For each absence claim:
    1. Find the structural boundary after the anchor line
    2. Suppress forbidden sequence at that boundary using appropriate mode

    Two enforcement modes controlled by 'strict' parameter:

    Strict mode (strict=True) - for applying batch ownership:
    - Used when merging into live working tree that may have diverged
    - Exact match at boundary: suppress
    - Found nearby but not at boundary: raise MergeError (structural conflict)
    - Not found: no-op (already suppressed or never existed)

    Realization mode (strict=False) - for realized batch content construction:
    - Used when building display/storage content from baseline
    - Exact match at boundary: suppress
    - Not at boundary: no-op (baseline may not have content there)

    Both modes fail if anchor boundary itself cannot be determined (MissingAnchorError
    or AmbiguousAnchorError), as this indicates a real structural inconsistency.

    Args:
        entries: Realized entries with source provenance from presence pass
        deletion_claims: Absence constraints with structural anchors
        strict: If True, use strict enforcement (merge). If False, lenient
            enforcement for realization.
        realization_fallback_target_positions: Ordered ``(claim index, target
            gap)`` records consulted only during lenient realization when a
            source anchor cannot remove content at the verified baseline gap.
        spool_dir: Optional directory for storage-backed fallback planning.

    Returns:
        Entries with forbidden sequences suppressed at their anchored boundaries

    Raises:
        MissingAnchorError: If anchor line is not present in realized content
        AmbiguousAnchorError: If anchor boundary cannot be determined uniquely
        MergeError: If strict=True and sequence is found nearby but not at boundary
    """
    absence_decision: tuple[str, int] | None = None
    if resolution is not None:
        for key, choice in resolution.decisions.items():
            if not isinstance(key, str) or not key.startswith(ABSENCE_AMBIGUITY_PREFIX):
                continue
            if absence_decision is not None or type(choice) is not int or choice < 1:
                raise _MergeError(_("Selected merge resolution is no longer valid"))
            absence_decision = (key, choice)

    result = as_realized_entries(entries)
    if not deletion_claims:
        if absence_decision is not None:
            if result is not entries:
                result.close()
            raise _MergeError(_("Selected merge resolution is no longer valid"))
        return result
    if strict and absence_decision is None:
        try:
            indexed_result = _apply_strict_absence_constraints_indexed(
                result,
                deletion_claims,
                spool_dir=spool_dir,
            )
        except BaseException:
            if result is not entries:
                close_resources_preserving_first(
                    (result,),
                    suppress_errors=True,
                )
            raise
        if indexed_result is not result and result is not entries:
            try:
                result.close()
            except BaseException:
                close_resources_preserving_first(
                    (indexed_result,),
                    suppress_errors=True,
                )
                raise
        return indexed_result

    suppress_fn = (
        _suppress_at_boundary_strict
        if strict
        else _suppress_at_boundary_for_realization
    )

    fallback_workspace: MatcherWorkspace | None = None
    try:
        unresolved_fallbacks = None
        if not strict and realization_fallback_target_positions:
            fallback_workspace = MatcherWorkspace(spool_dir=spool_dir)
            unresolved_fallbacks = fallback_workspace.record_vector(
                len(realization_fallback_target_positions),
                "QQ",
            )

        fallback_index = 0
        absence_decision_consumed = False
        for claim_index, claim in enumerate(deletion_claims):
            fallback_target_position: int | None = None
            if fallback_index < len(realization_fallback_target_positions):
                fallback_claim_index, target_position = (
                    realization_fallback_target_positions[fallback_index]
                )
                if fallback_claim_index == claim_index:
                    fallback_target_position = target_position
                    fallback_index += 1
            if not claim.content_lines:
                continue

            forbidden_sequence = claim.content_lines

            ambiguity_key = absence_ambiguity_key(
                claim_index,
                claim.anchor_line,
                forbidden_sequence,
            )

            if absence_decision is not None and ambiguity_key == absence_decision[0]:
                assert resolution is not None
                old_result = result
                result = _suppress_absence_with_resolution(
                    result,
                    claim.anchor_line,
                    forbidden_sequence,
                    ambiguity_key,
                    resolution,
                )
                if result is not old_result and old_result is not entries:
                    old_result.close()
                absence_decision_consumed = True
                continue

            try:
                boundary = _locate_boundary_after_source_line(
                    result,
                    claim.anchor_line,
                )
            except _MissingAnchorError:
                if strict:
                    raise
                boundary = _realization_fallback_boundary(
                    result,
                    claim.anchor_line,
                )

            old_result = result
            result = suppress_fn(result, boundary, forbidden_sequence)
            if (
                result is old_result
                and fallback_target_position is not None
                and unresolved_fallbacks is not None
            ):
                unresolved_fallbacks.append(
                    (
                        fallback_target_position,
                        claim_index,
                    )
                )
            if result is not old_result and old_result is not entries:
                old_result.close()

        if absence_decision is not None and not absence_decision_consumed:
            raise _MergeError(_("Selected merge resolution is no longer valid"))

        if unresolved_fallbacks is not None and unresolved_fallbacks:
            sort_mapped_records(unresolved_fallbacks)
            fallback_cursor = 0
            for target_position, claim_index in unresolved_fallbacks:
                claim = deletion_claims[claim_index]
                forbidden_sequence = claim.content_lines
                fallback_position = _realized_position_for_target_gap(
                    result,
                    target_position,
                    start_position=fallback_cursor,
                )
                fallback_position = _position_after_claimed_insertions_at_boundary(
                    result,
                    fallback_position,
                )
                fallback_cursor = fallback_position
                if not _sequence_matches_at_position(
                    result,
                    fallback_position,
                    forbidden_sequence,
                ):
                    continue
                old_result = result
                result = _remove_sequence_at_position(
                    result,
                    fallback_position,
                    forbidden_sequence,
                )
                if old_result is not entries:
                    old_result.close()
    except BaseException:
        if result is not entries:
            close_resources_preserving_first(
                (result,),
                suppress_errors=True,
            )
        close_resources_preserving_first(
            (fallback_workspace,),
            suppress_errors=True,
        )
        raise

    try:
        if fallback_workspace is not None:
            fallback_workspace.close()
    except BaseException:
        if result is not entries:
            close_resources_preserving_first(
                (result,),
                suppress_errors=True,
            )
        raise
    return result


def absence_ambiguity_key(
    claim_index: int,
    anchor_line: int | None,
    forbidden_sequence: Sequence[bytes],
) -> str:
    """Return the merge-resolution key for one absence ambiguity."""
    anchor = "start" if anchor_line is None else str(anchor_line)
    hasher = hashlib.sha256()
    for line_index in range(len(forbidden_sequence)):
        hasher.update(normalize_line_endings(bytes(forbidden_sequence[line_index])))
    digest = hasher.hexdigest()[:12]
    return f"{ABSENCE_AMBIGUITY_PREFIX}{claim_index}:{anchor}:{digest}"


def absence_choices_for_claim(
    entries: Sequence[_RealizedEntry],
    anchor_line: int | None,
    forbidden_sequence: Sequence[bytes],
    *,
    max_results: int | None = None,
) -> tuple[AbsenceChoice, ...]:
    """Return concrete exact-removal choices for one absence claim."""
    result_limit = max_results or _DEFAULT_CHOICE_SCAN_CAP
    positions: list[tuple[int, str]] = []
    seen: set[int] = set()

    def add_position(position: int, explanation: str) -> None:
        if position in seen:
            return
        if not _sequence_matches_at_position(entries, position, forbidden_sequence):
            return
        seen.add(position)
        positions.append((position, explanation))

    first_boundary: int | None = None
    for boundary in _boundary_choices_for_source_line(entries, anchor_line):
        if first_boundary is None:
            first_boundary = boundary
        add_position(boundary, _("deletion content appears at the anchored boundary"))
        after_claimed = _position_after_claimed_insertions_at_boundary(
            entries,
            boundary,
        )
        if after_claimed != boundary:
            add_position(
                after_claimed,
                _(
                    "deletion content appears after claimed insertions at the anchored boundary"
                ),
            )
        if len(positions) >= result_limit:
            break

    assert first_boundary is not None
    if len(positions) <= 1:
        for position in _iter_sequence_occurrences_nearby(
            entries,
            first_boundary,
            forbidden_sequence,
            window=20,
            max_results=result_limit,
        ):
            add_position(position, _("deletion content appears nearby"))
            if len(positions) >= result_limit:
                break

    positions.sort(key=lambda item: item[0])

    choices: list[AbsenceChoice] = []
    for index, (position, explanation) in enumerate(positions, start=1):
        after_line = None if position == 0 else position
        before_line = (
            None
            if position + len(forbidden_sequence) >= len(entries)
            else position + len(forbidden_sequence) + 1
        )
        choices.append(
            AbsenceChoice(
                choice_index=index,
                position=position,
                target_after_line=after_line,
                target_before_line=before_line,
                explanation=explanation,
            )
        )
    return tuple(choices)


def _suppress_absence_with_resolution(
    entries: Sequence[_RealizedEntry],
    anchor_line: int | None,
    forbidden_sequence: Sequence[bytes],
    ambiguity_key: str,
    resolution: _MergeResolution,
) -> RealizedEntries:
    choice_index = resolution.decisions.get(ambiguity_key)
    if choice_index is None:
        raise _MergeError(
            _("Missing merge resolution for {key}").format(key=ambiguity_key)
        )
    choices = absence_choices_for_claim(
        entries,
        anchor_line,
        forbidden_sequence,
        max_results=_DEFAULT_CHOICE_SCAN_CAP + 1,
    )
    for choice in choices:
        if choice.choice_index == choice_index:
            return _remove_sequence_at_position(
                entries, choice.position, forbidden_sequence
            )
    raise _MergeError(_("Selected merge resolution is no longer valid"))


def _normalize_line_content(content: LineLike) -> bytes:
    return normalize_line_endings(bytes(content))


def _sequence_matches_at_position(
    entries: Sequence[_RealizedEntry],
    position: int,
    sequence: Sequence[bytes],
) -> bool:
    """Check if sequence matches unclaimed entries at an exact position."""
    if position + len(sequence) > len(entries):
        return False

    return all(
        not realized_entry_is_claimed_at(entries, position + i)
        and _normalize_line_content(realized_entry_content_at(entries, position + i))
        == normalize_line_endings(bytes(sequence[i]))
        for i in range(len(sequence))
    )


def _find_sequence_nearby(
    entries: Sequence[_RealizedEntry],
    position: int,
    sequence: Sequence[bytes],
    window: int = 20,
) -> int | None:
    """Search for sequence within window after position."""
    search_end = min(position + window, len(entries) - len(sequence) + 1)

    for check_pos in range(position + 1, search_end):
        if _sequence_matches_at_position(entries, check_pos, sequence):
            return check_pos

    return None


def _iter_sequence_occurrences_nearby(
    entries: Sequence[_RealizedEntry],
    position: int,
    sequence: Sequence[bytes],
    *,
    window: int,
    max_results: int,
) -> Iterator[int]:
    """Yield exact nearby sequence positions after a boundary."""
    search_end = min(position + window, len(entries) - len(sequence) + 1)
    result_count = 0
    for check_pos in range(position + 1, search_end):
        if _sequence_matches_at_position(entries, check_pos, sequence):
            yield check_pos
            result_count += 1
            if result_count >= max_results:
                return


def _remove_sequence_at_position(
    entries: Sequence[_RealizedEntry],
    position: int,
    sequence: Sequence[bytes],
) -> RealizedEntries:
    """Remove sequence from entries at exact position."""
    return as_realized_entries(entries).without_range(
        position,
        position + len(sequence),
    )


def _position_after_claimed_insertions_at_boundary(
    entries: Sequence[_RealizedEntry],
    position: int,
) -> int:
    """Return the first position after contiguous claimed entries at boundary."""
    check_pos = position

    if isinstance(entries, RealizedEntries):
        for run in entries.provenance_runs(position, len(entries)):
            if not run.is_claimed:
                break
            check_pos = run.dest_end
        return check_pos

    while check_pos < len(entries) and realized_entry_is_claimed_at(
        entries,
        check_pos,
    ):
        check_pos += 1

    return check_pos


def _realized_position_for_target_gap(
    entries: Sequence[_RealizedEntry],
    target_position: int,
    *,
    start_position: int = 0,
) -> int:
    """Translate an original zero-based target gap into realized entries.

    Claimed insertions have no target coordinate and deliberately remain after
    the returned boundary. Earlier removals therefore cannot stale a later
    coordinate-backed realization fallback.
    """
    if target_position < 0 or start_position < 0 or start_position > len(entries):
        raise ValueError("invalid target-gap search position")

    position = start_position
    if isinstance(entries, RealizedEntries):
        for run in entries.provenance_runs(start_position, len(entries)):
            if run.target_start == 0:
                continue
            if run.target_start > target_position:
                break
            run_length = run.dest_end - run.dest_start
            run_target_end = run.target_start + run_length - 1
            if run_target_end <= target_position:
                position = run.dest_end
                continue
            return run.dest_start + target_position - run.target_start + 1
        return position

    for index in range(start_position, len(entries)):
        target_line = entries[index].target_line
        if target_line is None:
            continue
        if target_line > target_position:
            break
        position = index + 1
    return position


def _suppress_at_boundary_strict(
    entries: Sequence[_RealizedEntry],
    position: int,
    forbidden_sequence: Sequence[bytes],
) -> RealizedEntries:
    """Suppress forbidden sequence with strict enforcement for merge operations."""
    if _sequence_matches_at_position(entries, position, forbidden_sequence):
        return _remove_sequence_at_position(entries, position, forbidden_sequence)

    after_claimed_insertions = _position_after_claimed_insertions_at_boundary(
        entries,
        position,
    )
    if after_claimed_insertions != position:
        if _sequence_matches_at_position(
            entries,
            after_claimed_insertions,
            forbidden_sequence,
        ):
            return _remove_sequence_at_position(
                entries,
                after_claimed_insertions,
                forbidden_sequence,
            )

    nearby_pos = _find_sequence_nearby(entries, position, forbidden_sequence, window=20)
    if nearby_pos is not None:
        raise _MergeError(_("Batch was created from a different version of the file"))

    return as_realized_entries(entries)


def _suppress_at_boundary_for_realization(
    entries: Sequence[_RealizedEntry],
    position: int,
    forbidden_sequence: Sequence[bytes],
) -> RealizedEntries:
    """Suppress forbidden sequence leniently for content realization."""
    if _sequence_matches_at_position(entries, position, forbidden_sequence):
        return _remove_sequence_at_position(entries, position, forbidden_sequence)

    after_claimed_insertions = _position_after_claimed_insertions_at_boundary(
        entries,
        position,
    )
    if after_claimed_insertions != position:
        if _sequence_matches_at_position(
            entries,
            after_claimed_insertions,
            forbidden_sequence,
        ):
            return _remove_sequence_at_position(
                entries,
                after_claimed_insertions,
                forbidden_sequence,
            )

    return as_realized_entries(entries)
