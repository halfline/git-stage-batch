"""Mergeability probing for displayed batch file ownership units."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from typing import overload

from ..core.line_selection import LineRangeBuilder, LineRanges
from ..core.text_lines import normalize_line_sequence_endings
from ..utils.repository_buffers import (
    load_working_tree_file_as_buffer,
    read_git_object_buffer_or_none,
)
from .merge import merge as batch_merge
from .line_matching.match import match_lines
from .ownership.model import BatchOwnership
from .ownership.display_lines import OwnershipDisplayLine
from .ownership.unit_rebuild import rebuild_ownership_from_units
from .ownership.unit_types import OwnershipUnit, OwnershipUnitKind
from .ownership.unit_validation import validate_ownership_units
from .ownership.units import build_ownership_units_from_display_lines


@dataclass
class BatchFileMergeability:
    """Mergeability result for a rendered batch file."""

    mergeable_id_ranges: LineRanges
    units: list[OwnershipUnit]
    mergeable_selection_groups: tuple[LineRanges, ...] = ()


class _OwnershipUnitRange(Sequence[OwnershipUnit]):
    """Constant-size view over consecutive ownership units."""

    def __init__(
        self,
        units: Sequence[OwnershipUnit],
        start: int,
        end: int,
    ) -> None:
        self._units = units
        self._start = start
        self._end = end

    def __len__(self) -> int:
        return self._end - self._start

    @overload
    def __getitem__(self, index: int) -> OwnershipUnit: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[OwnershipUnit]: ...

    def __getitem__(
        self,
        index: int | slice,
    ) -> OwnershipUnit | Sequence[OwnershipUnit]:
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            if step == 1:
                return _OwnershipUnitRange(
                    self._units,
                    self._start + start,
                    self._start + stop,
                )
            return tuple(self[child] for child in range(start, stop, step))
        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError(index)
        return self._units[self._start + index]


def _shared_presence_boundary_group_end(
    units: Sequence[OwnershipUnit],
    unit_index: int,
) -> int:
    """Return the end of one contiguous pure-addition boundary group."""
    unit = units[unit_index]
    if getattr(unit, "kind", None) is not OwnershipUnitKind.PRESENCE_ONLY:
        return unit_index + 1
    source_line = unit.claimed_source_lines.first()
    if source_line is None or unit.claimed_source_lines.count() != 1:
        return unit_index + 1
    reference = unit.baseline_references.get(source_line)
    if reference is None:
        return unit_index + 1

    group_end = unit_index + 1
    previous_source_line = source_line
    while group_end < len(units):
        sibling = units[group_end]
        sibling_source_line = sibling.claimed_source_lines.first()
        if (
            sibling.kind is not OwnershipUnitKind.PRESENCE_ONLY
            or sibling_source_line is None
            or sibling.claimed_source_lines.count() != 1
            or sibling_source_line != previous_source_line + 1
            or sibling.baseline_references.get(sibling_source_line) != reference
        ):
            break
        previous_source_line = sibling_source_line
        group_end += 1
    return group_end


def probe_batch_file_mergeability(
    *,
    file_path: str,
    ownership: BatchOwnership,
    display_lines: list[OwnershipDisplayLine],
    batch_source_lines: Sequence[bytes],
) -> BatchFileMergeability:
    """Return mergeable display IDs and ownership units for batch display lines."""
    if not display_lines:
        return BatchFileMergeability(
            mergeable_id_ranges=LineRanges.empty(),
            units=[],
        )

    mergeable_id_ranges = LineRangeBuilder()
    source_match_lines = normalize_line_sequence_endings(batch_source_lines)
    needs_trusted_target = bool(ownership.replacement_units or ownership.deletions)
    with ExitStack() as resources:
        working_tree_lines = resources.enter_context(
            load_working_tree_file_as_buffer(file_path)
        )
        trusted_target_buffer = (
            read_git_object_buffer_or_none(f":{file_path}")
            if needs_trusted_target
            else None
        )
        trusted_target_lines = (
            None
            if trusted_target_buffer is None
            else resources.enter_context(trusted_target_buffer)
        )
        working_match_lines = normalize_line_sequence_endings(working_tree_lines)
        trusted_match_lines = (
            None
            if trusted_target_lines is None
            else normalize_line_sequence_endings(trusted_target_lines)
        )
        source_to_working_mapping = resources.enter_context(
            match_lines(source_match_lines, working_match_lines)
        )
        source_to_trusted_target_mapping = (
            None
            if trusted_match_lines is None
            else resources.enter_context(
                match_lines(source_match_lines, trusted_match_lines)
            )
        )
        trusted_target_to_working_mapping = (
            None
            if trusted_match_lines is None
            else resources.enter_context(
                match_lines(trusted_match_lines, working_match_lines)
            )
        )

        units = build_ownership_units_from_display_lines(
            ownership,
            display_lines,
        )

        def units_are_mergeable(
            selected_units: Sequence[OwnershipUnit],
        ) -> bool:
            try:
                validate_ownership_units(selected_units)
                ownership_for_units = rebuild_ownership_from_units(
                    selected_units,
                    normalize_replacement_metadata=False,
                )
                if ownership_for_units.is_empty():
                    return False
                return batch_merge.can_merge_batch_from_line_sequences(
                    source_match_lines,
                    ownership_for_units,
                    working_match_lines,
                    source_to_working_mapping=source_to_working_mapping,
                    trusted_target_lines=trusted_match_lines,
                    source_to_trusted_target_mapping=(source_to_trusted_target_mapping),
                    trusted_target_to_working_mapping=(
                        trusted_target_to_working_mapping
                    ),
                )
            except Exception:
                return False

        mergeable_selection_groups: list[LineRanges] = []
        unit_index = 0
        while unit_index < len(units):
            group_end = unit_index + 1
            origin = units[unit_index].replacement_origin
            if origin is not None:
                while (
                    group_end < len(units)
                    and units[group_end].replacement_origin == origin
                ):
                    group_end += 1
            else:
                group_end = _shared_presence_boundary_group_end(units, unit_index)

            all_children_mergeable = True
            for child_index in range(unit_index, group_end):
                child = units[child_index]
                if units_are_mergeable([child]):
                    mergeable_selection_groups.append(child.display_line_ids)
                else:
                    all_children_mergeable = False

            if (
                group_end > unit_index + 1
                and not all_children_mergeable
                and units_are_mergeable(
                    _OwnershipUnitRange(units, unit_index, group_end)
                )
            ):
                composite_ids = LineRanges.from_ranges(
                    display_range
                    for child_index in range(unit_index, group_end)
                    for display_range in units[child_index].display_line_ids.ranges()
                )
                mergeable_selection_groups.append(composite_ids)
            unit_index = group_end

        for selection_group in mergeable_selection_groups:
            for range_start, range_end in selection_group.ranges():
                mergeable_id_ranges.add_range(range_start, range_end)

    return BatchFileMergeability(
        mergeable_id_ranges=mergeable_id_ranges.finish(),
        units=units,
        mergeable_selection_groups=tuple(mergeable_selection_groups),
    )
