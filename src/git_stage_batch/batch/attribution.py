"""File-centric ownership attribution for filtering.

This module builds a file-centric ownership view and then projects that view
onto displayed diff hunks. Unit generation is derived from direct baseline ↔
working-tree comparison, while batch metadata supplements the model with owned
content that may not currently be visible in the working tree.
"""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
from dataclasses import dataclass
from dataclasses import replace
from enum import Enum

from ..batch.match import match_lines
from ..batch.query import list_batch_names, read_batch_metadata
from ..core.line_selection import parse_line_selection
from ..core.models import LineLevelChange
from ..data.consumed_selections import read_consumed_file_metadata
from ..editor import (
    load_git_object_as_buffer_or_empty,
    load_working_tree_file_as_buffer,
)
from ..utils.git import (
    read_git_blob_as_bytes,
    read_git_object_as_lines,
    read_working_tree_file_as_lines,
)


class AttributionUnitKind(Enum):
    """Type of attribution unit for file-centric filtering."""

    PRESENCE_ONLY = "presence_only"
    """Pure addition (no coupled deletion)."""

    REPLACEMENT = "replacement"
    """Addition coupled with deletion (modification)."""

    DELETION_ONLY = "deletion_only"
    """Pure deletion (no coupled addition)."""


@dataclass
class AttributionUnit:
    """A semantic unit in the working tree file for attribution.

    Represents a change unit derived from baseline ↔ working tree comparison.
    Used for filtering diff output by determining which fragments are owned by batches.
    """

    unit_id: str
    kind: AttributionUnitKind
    file_path: str
    claimed_line_in_working_tree: int | None
    claimed_content: bytes | None
    deletion_anchor_in_working_tree: int | None
    deletion_content: bytes | None


@dataclass
class AttributedUnit:
    """An attribution unit plus the batches that currently own it."""

    unit: AttributionUnit
    owning_batches: set[str]


@dataclass
class FileAttribution:
    """Complete ownership attribution for a file."""

    file_path: str
    units: list[AttributedUnit]


@dataclass
class FileComparison:
    """Canonical baseline ↔ working-tree comparison for a file."""

    file_path: str
    baseline_lines: Sequence[bytes]
    working_tree_lines: Sequence[bytes]
    alignment: object


def _make_unit_id(
    kind: AttributionUnitKind,
    file_path: str,
    claimed_line: int | None = None,
    claimed_content: bytes | None = None,
    deletion_anchor: int | None = None,
    deletion_content: bytes | None = None,
) -> str:
    """Create a semantic, batch-independent identifier for a unit."""
    def _hash(content: bytes | None) -> str:
        if not content:
            return "none"
        return hashlib.sha1(content).hexdigest()[:8]

    if kind == AttributionUnitKind.PRESENCE_ONLY:
        line_str = str(claimed_line) if claimed_line is not None else "missing"
        return f"PRESENCE_ONLY:{file_path}:{line_str}:{_hash(claimed_content)}"

    if kind == AttributionUnitKind.DELETION_ONLY:
        anchor_str = str(deletion_anchor) if deletion_anchor is not None else "start"
        return f"DELETION_ONLY:{file_path}:after-{anchor_str}:{_hash(deletion_content)}"

    if kind == AttributionUnitKind.REPLACEMENT:
        line_str = str(claimed_line) if claimed_line is not None else "missing"
        anchor_str = str(deletion_anchor) if deletion_anchor is not None else "start"
        return (
            f"REPLACEMENT:{file_path}:{line_str}:after-{anchor_str}:"
            f"c{_hash(claimed_content)}:d{_hash(deletion_content)}"
        )

    return f"UNKNOWN:{file_path}"




def _parse_presence_source_lines(file_metadata: dict) -> list[int]:
    presence_lines: list[int] = []
    for claim in file_metadata.get("presence_claims", []):
        source_lines = claim.get("source_lines", [])
        if source_lines:
            presence_lines.extend(
                parse_line_selection(",".join(str(line) for line in source_lines))
            )
    legacy_claimed_lines = file_metadata.get("claimed_lines", [])
    if not presence_lines and legacy_claimed_lines:
        presence_lines.extend(
            parse_line_selection(",".join(str(line) for line in legacy_claimed_lines))
        )
    return presence_lines


def _has_presence_source_lines(file_metadata: dict) -> bool:
    return bool(
        file_metadata.get("presence_claims")
        or file_metadata.get("claimed_lines")
    )


def _build_file_comparison_from_lines(
    file_path: str,
    *,
    baseline_lines: Sequence[bytes],
    working_tree_lines: Sequence[bytes],
) -> FileComparison:
    alignment = match_lines(source_lines=baseline_lines, target_lines=working_tree_lines)
    return FileComparison(
        file_path=file_path,
        baseline_lines=baseline_lines,
        working_tree_lines=working_tree_lines,
        alignment=alignment,
    )


def compare_baseline_to_working_tree(file_path: str) -> FileComparison:
    """Compare HEAD:file to the working tree directly."""
    baseline_buffer = load_git_object_as_buffer_or_empty(f"HEAD:{file_path}")
    working_tree_buffer = load_working_tree_file_as_buffer(file_path)

    with baseline_buffer as baseline_lines, working_tree_buffer as working_tree_lines:
        return _build_file_comparison_from_lines(
            file_path,
            baseline_lines=list(baseline_lines),
            working_tree_lines=list(working_tree_lines),
        )


def enumerate_units_from_file_comparison(
    comparison: FileComparison,
    units_map: dict[str, AttributionUnit],
) -> None:
    """Enumerate file-derived ownership units from baseline ↔ working tree comparison."""
    file_path = comparison.file_path
    baseline_lines = comparison.baseline_lines
    working_tree_lines = comparison.working_tree_lines
    alignment = comparison.alignment

    baseline_to_working: dict[int, int] = {}
    working_to_baseline: dict[int, int] = {}

    for baseline_idx in range(len(baseline_lines)):
        baseline_line_num = baseline_idx + 1
        working_tree_line_num = alignment.get_target_line_from_source_line(baseline_line_num)
        if working_tree_line_num is not None:
            baseline_to_working[baseline_line_num] = working_tree_line_num
            working_to_baseline[working_tree_line_num] = baseline_line_num

    baseline_unmatched = [
        line_num
        for line_num in range(1, len(baseline_lines) + 1)
        if line_num not in baseline_to_working
    ]
    working_unmatched = [
        line_num
        for line_num in range(1, len(working_tree_lines) + 1)
        if line_num not in working_to_baseline
    ]

    baseline_runs = _group_consecutive(baseline_unmatched)
    working_runs = _group_consecutive(working_unmatched)

    paired_deletion_runs: set[int] = set()
    paired_addition_runs: set[int] = set()
    replacements: list[tuple[list[int], list[int], int | None]] = []

    for del_run_idx, del_run in enumerate(baseline_runs):
        if del_run_idx in paired_deletion_runs:
            continue

        del_anchor_baseline = _find_structural_predecessor(del_run, baseline_to_working)
        del_anchor_working = None if del_anchor_baseline is None else baseline_to_working.get(del_anchor_baseline)

        best_match_idx = None
        for add_run_idx, add_run in enumerate(working_runs):
            if add_run_idx in paired_addition_runs:
                continue

            add_anchor = _find_structural_predecessor(add_run, working_to_baseline)
            if _anchors_match(del_anchor_working, add_anchor):
                best_match_idx = add_run_idx
                break

        if best_match_idx is None:
            continue

        paired_deletion_runs.add(del_run_idx)
        paired_addition_runs.add(best_match_idx)
        replacements.append((del_run, working_runs[best_match_idx], del_anchor_working))

    remaining_deletions = [
        run for idx, run in enumerate(baseline_runs) if idx not in paired_deletion_runs
    ]
    remaining_additions = [
        run for idx, run in enumerate(working_runs) if idx not in paired_addition_runs
    ]

    for del_run, add_run, anchor in replacements:
        deletion_content = b"".join(baseline_lines[i - 1] for i in del_run)
        addition_content = b"".join(working_tree_lines[i - 1] for i in add_run)
        working_tree_line = add_run[0]

        unit = AttributionUnit(
            unit_id=_make_unit_id(
                AttributionUnitKind.REPLACEMENT,
                file_path,
                claimed_line=working_tree_line,
                claimed_content=addition_content,
                deletion_anchor=anchor,
                deletion_content=deletion_content,
            ),
            kind=AttributionUnitKind.REPLACEMENT,
            file_path=file_path,
            claimed_line_in_working_tree=working_tree_line,
            claimed_content=addition_content,
            deletion_anchor_in_working_tree=anchor,
            deletion_content=deletion_content,
        )
        units_map.setdefault(unit.unit_id, unit)

    for del_run in remaining_deletions:
        deletion_content = b"".join(baseline_lines[i - 1] for i in del_run)
        anchor_baseline = _find_structural_predecessor(del_run, baseline_to_working)
        anchor_working = None if anchor_baseline is None else baseline_to_working.get(anchor_baseline)

        unit = AttributionUnit(
            unit_id=_make_unit_id(
                AttributionUnitKind.DELETION_ONLY,
                file_path,
                deletion_anchor=anchor_working,
                deletion_content=deletion_content,
            ),
            kind=AttributionUnitKind.DELETION_ONLY,
            file_path=file_path,
            claimed_line_in_working_tree=None,
            claimed_content=None,
            deletion_anchor_in_working_tree=anchor_working,
            deletion_content=deletion_content,
        )
        units_map.setdefault(unit.unit_id, unit)

    for add_run in remaining_additions:
        for line_num in add_run:
            addition_content = working_tree_lines[line_num - 1]
            unit = AttributionUnit(
                unit_id=_make_unit_id(
                    AttributionUnitKind.PRESENCE_ONLY,
                    file_path,
                    claimed_line=line_num,
                    claimed_content=addition_content,
                ),
                kind=AttributionUnitKind.PRESENCE_ONLY,
                file_path=file_path,
                claimed_line_in_working_tree=line_num,
                claimed_content=addition_content,
                deletion_anchor_in_working_tree=None,
                deletion_content=None,
            )
            units_map.setdefault(unit.unit_id, unit)


def _find_structural_predecessor(
    run: list[int],
    line_mapping: dict[int, int],
) -> int | None:
    """Return the last matched line before a run, or None at start-of-file."""
    if not run:
        return None

    for candidate in range(run[0] - 1, 0, -1):
        if candidate in line_mapping:
            return candidate
    return None


def _anchors_match(anchor1: int | None, anchor2: int | None) -> bool:
    return anchor1 == anchor2


def _group_consecutive(line_numbers: list[int]) -> list[list[int]]:
    if not line_numbers:
        return []

    sorted_lines = sorted(line_numbers)
    runs = [[sorted_lines[0]]]
    for line in sorted_lines[1:]:
        if line == runs[-1][-1] + 1:
            runs[-1].append(line)
        else:
            runs.append([line])
    return runs


def build_file_attribution(file_path: str) -> FileAttribution:
    """Build complete ownership attribution for a file."""
    all_batch_metadata = {}
    for batch_name in list_batch_names():
        metadata = read_batch_metadata(batch_name)
        if file_path in metadata.get("files", {}):
            all_batch_metadata[batch_name] = metadata

    consumed_file_metadata = read_consumed_file_metadata(file_path)
    if consumed_file_metadata is not None:
        all_batch_metadata["__consumed__"] = {
            "files": {
                file_path: consumed_file_metadata,
            }
        }

    comparison = compare_baseline_to_working_tree(file_path)
    all_units_map: dict[str, AttributionUnit] = {}
    enumerate_units_from_file_comparison(comparison, all_units_map)

    if all_batch_metadata:
        _enumerate_units_from_batches(file_path, all_batch_metadata, all_units_map)

    attributed_units = [
        AttributedUnit(
            unit=unit,
            owning_batches=_find_owning_batches(unit, file_path, all_batch_metadata),
        )
        for unit in all_units_map.values()
    ]
    return FileAttribution(file_path=file_path, units=attributed_units)


def _enumerate_units_from_batches(
    file_path: str,
    all_batch_metadata: dict,
    units_map: dict[str, AttributionUnit],
) -> None:
    """Add batch-owned units that may not be visible in the working tree."""
    working_tree_lines = read_working_tree_file_as_lines(file_path)

    for batch_metadata in all_batch_metadata.values():
        file_metadata = batch_metadata["files"][file_path]
        batch_source_commit = file_metadata["batch_source_commit"]
        batch_source_lines = read_git_object_as_lines(f"{batch_source_commit}:{file_path}")
        if not batch_source_lines and _has_presence_source_lines(file_metadata):
            continue

        alignment = match_lines(source_lines=batch_source_lines, target_lines=working_tree_lines)

        for source_line in _parse_presence_source_lines(file_metadata):
            if source_line < 1 or source_line > len(batch_source_lines):
                continue

            claimed_content = batch_source_lines[source_line - 1]
            working_tree_line = alignment.get_target_line_from_source_line(source_line)
            unit = AttributionUnit(
                unit_id=_make_unit_id(
                    AttributionUnitKind.PRESENCE_ONLY,
                    file_path,
                    claimed_line=working_tree_line,
                    claimed_content=claimed_content,
                ),
                kind=AttributionUnitKind.PRESENCE_ONLY,
                file_path=file_path,
                claimed_line_in_working_tree=working_tree_line,
                claimed_content=claimed_content,
                deletion_anchor_in_working_tree=None,
                deletion_content=None,
            )
            units_map.setdefault(unit.unit_id, unit)

        for deletion_entry in file_metadata.get("deletions", []):
            blob_hash = deletion_entry.get("blob")
            if not blob_hash:
                continue

            deletion_content = read_git_blob_as_bytes(blob_hash)
            if deletion_content is None:
                continue

            after_source_line = deletion_entry.get("after_source_line")
            deletion_anchor = (
                None
                if after_source_line is None
                else alignment.get_target_line_from_source_line(after_source_line)
            )

            unit = AttributionUnit(
                unit_id=_make_unit_id(
                    AttributionUnitKind.DELETION_ONLY,
                    file_path,
                    deletion_anchor=deletion_anchor,
                    deletion_content=deletion_content,
                ),
                kind=AttributionUnitKind.DELETION_ONLY,
                file_path=file_path,
                claimed_line_in_working_tree=None,
                claimed_content=None,
                deletion_anchor_in_working_tree=deletion_anchor,
                deletion_content=deletion_content,
            )
            units_map.setdefault(unit.unit_id, unit)


def _find_owning_batches(
    unit: AttributionUnit,
    file_path: str,
    all_batch_metadata: dict,
) -> set[str]:
    """Determine which batches own a given unit."""
    owning_batches: set[str] = set()
    working_tree_lines = read_working_tree_file_as_lines(file_path)

    for batch_name, batch_metadata in all_batch_metadata.items():
        file_metadata = batch_metadata["files"][file_path]
        batch_source_commit = file_metadata["batch_source_commit"]
        batch_source_lines = read_git_object_as_lines(f"{batch_source_commit}:{file_path}")
        alignment = match_lines(source_lines=batch_source_lines, target_lines=working_tree_lines)

        if unit.kind == AttributionUnitKind.PRESENCE_ONLY:
            if _batch_owns_presence_unit(unit, file_metadata, alignment, batch_source_lines):
                owning_batches.add(batch_name)
        elif unit.kind == AttributionUnitKind.DELETION_ONLY:
            if _batch_owns_deletion_unit(unit, file_metadata, alignment):
                owning_batches.add(batch_name)
        elif unit.kind == AttributionUnitKind.REPLACEMENT:
            if (
                _batch_owns_presence_unit(unit, file_metadata, alignment, batch_source_lines)
                and _batch_owns_deletion_unit(unit, file_metadata, alignment)
            ):
                owning_batches.add(batch_name)

    return owning_batches


def _batch_owns_presence_unit(
    unit: AttributionUnit,
    file_metadata: dict,
    alignment,
    batch_source_lines: list[bytes],
) -> bool:
    """Check whether a batch owns the presence side of a unit.

    For units present in the working tree we require structural identity first,
    and then verify content. For units missing from the working tree we only
    accept claimed source lines that are themselves currently unmapped.
    """
    if unit.claimed_content is None:
        return False

    claimed_source_lines = _parse_presence_source_lines(file_metadata)
    if not claimed_source_lines:
        return False

    if unit.claimed_line_in_working_tree is not None:
        mapped_source_line = alignment.get_source_line_from_target_line(
            unit.claimed_line_in_working_tree
        )
        if mapped_source_line is None:
            return False
        if mapped_source_line not in claimed_source_lines:
            return False
        if mapped_source_line < 1 or mapped_source_line > len(batch_source_lines):
            return False
        return batch_source_lines[mapped_source_line - 1] == unit.claimed_content

    for source_line in claimed_source_lines:
        if source_line < 1 or source_line > len(batch_source_lines):
            continue
        if batch_source_lines[source_line - 1] != unit.claimed_content:
            continue
        if alignment.get_target_line_from_source_line(source_line) is None:
            return True

    return False


def _batch_owns_deletion_unit(
    unit: AttributionUnit,
    file_metadata: dict,
    alignment,
) -> bool:
    """Check whether a batch owns a deletion unit via explicit deletion claims."""
    if unit.deletion_content is None:
        return False

    for deletion_entry in file_metadata.get("deletions", []):
        blob_hash = deletion_entry.get("blob")
        if not blob_hash:
            continue

        after_source_line = deletion_entry.get("after_source_line")
        if after_source_line is None:
            if unit.deletion_anchor_in_working_tree is not None:
                continue
        else:
            mapped_anchor = alignment.get_target_line_from_source_line(after_source_line)
            if mapped_anchor != unit.deletion_anchor_in_working_tree:
                continue

        blob_content = read_git_blob_as_bytes(blob_hash)
        if blob_content == unit.deletion_content:
            return True

    return False


def _collect_deleted_run(
    lines: list,
    start_idx: int,
) -> tuple[int, bytes]:
    end_idx = start_idx
    while end_idx < len(lines) and lines[end_idx].kind == "-":
        end_idx += 1

    deletion_content = b"".join(lines[idx].text_bytes + b"\n" for idx in range(start_idx, end_idx))
    return end_idx, deletion_content


def _collect_added_run(
    lines: list,
    start_idx: int,
) -> tuple[int, bytes, int | None]:
    end_idx = start_idx
    content_parts: list[bytes] = []
    first_line_number = None

    while end_idx < len(lines) and lines[end_idx].kind == "+":
        if first_line_number is None:
            first_line_number = lines[end_idx].new_line_number
        content_parts.append(lines[end_idx].text_bytes + b"\n")
        end_idx += 1

    return end_idx, b"".join(content_parts), first_line_number


def project_attribution_to_diff(
    attribution: FileAttribution,
    line_changes,
) -> dict[int, AttributedUnit]:
    """Project file attribution onto diff fragments."""
    display_to_unit: dict[int, AttributedUnit] = {}
    lines = line_changes.lines

    presence_by_line: dict[int, list[AttributedUnit]] = {}
    replacement_by_line: dict[int, list[AttributedUnit]] = {}
    deletion_by_content: dict[bytes, list[AttributedUnit]] = {}

    for attr_unit in attribution.units:
        unit = attr_unit.unit
        if unit.kind == AttributionUnitKind.PRESENCE_ONLY:
            if unit.claimed_line_in_working_tree is not None:
                presence_by_line.setdefault(unit.claimed_line_in_working_tree, []).append(attr_unit)
        elif unit.kind == AttributionUnitKind.REPLACEMENT:
            if unit.claimed_line_in_working_tree is not None:
                replacement_by_line.setdefault(unit.claimed_line_in_working_tree, []).append(attr_unit)
        elif unit.kind == AttributionUnitKind.DELETION_ONLY:
            deletion_by_content.setdefault(unit.deletion_content or b"", []).append(attr_unit)

    i = 0
    while i < len(lines):
        line = lines[i]

        if line.kind == "-":
            deletion_end_idx, deletion_content = _collect_deleted_run(lines, i)
            add_start = deletion_end_idx
            add_end_idx, addition_content, first_add_line = _collect_added_run(lines, add_start)

            if add_end_idx > add_start and first_add_line is not None:
                candidates = replacement_by_line.get(first_add_line, [])
                matched = False
                for attr_unit in candidates:
                    unit = attr_unit.unit
                    if unit.deletion_content != deletion_content:
                        continue
                    if unit.claimed_content != addition_content:
                        continue
                    if not _anchor_consistent_with_diff_position(
                        unit.deletion_anchor_in_working_tree,
                        i,
                        lines,
                    ):
                        continue
                    for idx in range(i, add_end_idx):
                        display_to_unit[idx] = attr_unit
                    matched = True
                    break

                if matched:
                    i = add_end_idx
                    continue

            candidates = deletion_by_content.get(deletion_content, [])
            for attr_unit in candidates:
                unit = attr_unit.unit
                if not _anchor_consistent_with_diff_position(
                    unit.deletion_anchor_in_working_tree,
                    i,
                    lines,
                ):
                    continue
                for idx in range(i, deletion_end_idx):
                    display_to_unit[idx] = attr_unit
                break
            i = deletion_end_idx
            continue

        if line.kind == "+":
            if line.new_line_number is not None:
                for attr_unit in presence_by_line.get(line.new_line_number, []):
                    unit = attr_unit.unit
                    if unit.claimed_content == line.text_bytes + b"\n":
                        display_to_unit[i] = attr_unit
                        break
            i += 1
            continue

        i += 1

    return display_to_unit


def _anchor_consistent_with_diff_position(
    unit_anchor: int | None,
    deletion_start_idx: int,
    lines: list,
) -> bool:
    """Sanity-check a unit-defined anchor against available diff context."""
    preceding_working_line = None
    for idx in range(deletion_start_idx - 1, -1, -1):
        if lines[idx].kind in (" ", "+") and lines[idx].new_line_number is not None:
            preceding_working_line = lines[idx].new_line_number
            break

    if preceding_working_line is None:
        return True
    if unit_anchor is None:
        return False
    return unit_anchor == preceding_working_line


def filter_owned_diff_fragments(
    line_changes,
    attribution: FileAttribution,
) -> tuple[bool, any]:
    """Filter displayed diff fragments that correspond to owned units."""
    display_to_unit = project_attribution_to_diff(attribution, line_changes)
    filtered_lines = []
    new_id = 1

    for idx, line_entry in enumerate(line_changes.lines):
        attr_unit = display_to_unit.get(idx)
        if attr_unit and attr_unit.owning_batches:
            continue

        filtered_lines.append(
            replace(line_entry, id=new_id if line_entry.kind != " " else None)
        )
        if line_entry.kind != " ":
            new_id += 1

    # Skip only if we had changes but they were all filtered out
    # Don't skip empty files (which have no lines to begin with)
    had_changes = any(line.kind in ("+", "-") for line in line_changes.lines)
    has_changes_after_filter = any(line.kind in ("+", "-") for line in filtered_lines)

    if had_changes and not has_changes_after_filter:
        return (True, None)

    return (
        False,
        LineLevelChange(
            path=line_changes.path,
            header=line_changes.header,
            lines=filtered_lines,
        ),
    )
