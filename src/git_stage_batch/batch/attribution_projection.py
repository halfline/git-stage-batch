"""Project file attribution onto displayed diff fragments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import attribution_fingerprints as _attribution_fingerprints
from .attribution import (
    AttributedUnit,
    AttributionUnitKind,
    FileAttribution,
)
from ..core.models import LineLevelChange


@dataclass(frozen=True)
class _CollectedDeletedRun:
    """Deleted diff run data used while projecting attribution."""

    end_index: int
    fingerprint: _attribution_fingerprints.ContentFingerprint


@dataclass(frozen=True)
class _CollectedAddedRun:
    """Added diff run data used while projecting attribution."""

    end_index: int
    first_line_number: int | None
    line_count: int
    content: bytes | None
    fingerprint: _attribution_fingerprints.ContentFingerprint


def _collect_deleted_run(
    lines: list,
    start_idx: int,
) -> _CollectedDeletedRun:
    end_idx = start_idx
    while end_idx < len(lines) and lines[end_idx].kind == "-":
        end_idx += 1

    deletion_fingerprint = _attribution_fingerprints.fingerprint_chunks(
        lines[idx].text_bytes + b"\n"
        for idx in range(start_idx, end_idx)
    )
    return _CollectedDeletedRun(
        end_index=end_idx,
        fingerprint=deletion_fingerprint,
    )


def _collect_added_run(
    lines: list,
    start_idx: int,
) -> _CollectedAddedRun:
    end_idx = start_idx
    first_line_number = None

    while end_idx < len(lines) and lines[end_idx].kind == "+":
        if first_line_number is None:
            first_line_number = lines[end_idx].new_line_number
        end_idx += 1

    line_count = end_idx - start_idx
    fingerprint = _attribution_fingerprints.fingerprint_chunks(
        lines[idx].text_bytes + b"\n"
        for idx in range(start_idx, end_idx)
    )
    content = None
    if line_count == 1:
        content = lines[start_idx].text_bytes + b"\n"

    return _CollectedAddedRun(
        end_index=end_idx,
        first_line_number=first_line_number,
        line_count=line_count,
        content=content,
        fingerprint=fingerprint,
    )


def project_attribution_to_diff(
    attribution: FileAttribution,
    line_changes,
) -> dict[int, AttributedUnit]:
    """Project file attribution onto diff fragments."""
    display_to_unit: dict[int, AttributedUnit] = {}
    lines = line_changes.lines

    presence_by_line: dict[int, list[AttributedUnit]] = {}
    replacement_by_line: dict[int, list[AttributedUnit]] = {}
    deletion_by_fingerprint: dict[
        _attribution_fingerprints.ContentFingerprint,
        list[AttributedUnit],
    ] = {}

    for attr_unit in attribution.units:
        unit = attr_unit.unit
        if unit.kind == AttributionUnitKind.PRESENCE_ONLY:
            if unit.claimed_line_in_working_tree is not None:
                presence_by_line.setdefault(unit.claimed_line_in_working_tree, []).append(attr_unit)
        elif unit.kind == AttributionUnitKind.REPLACEMENT:
            if unit.claimed_line_in_working_tree is not None:
                replacement_by_line.setdefault(unit.claimed_line_in_working_tree, []).append(attr_unit)
        elif unit.kind == AttributionUnitKind.DELETION_ONLY:
            deletion_fingerprint = (
                unit.deletion_fingerprint
                or _attribution_fingerprints.fingerprint_bytes(
                    unit.deletion_content
                )
            )
            if deletion_fingerprint is not None:
                deletion_by_fingerprint.setdefault(
                    deletion_fingerprint,
                    [],
                ).append(attr_unit)

    i = 0
    while i < len(lines):
        line = lines[i]

        if line.kind == "-":
            deleted_run = _collect_deleted_run(lines, i)
            add_start = deleted_run.end_index
            added_run = _collect_added_run(lines, add_start)

            if (
                added_run.end_index > add_start
                and added_run.first_line_number is not None
            ):
                candidates = replacement_by_line.get(added_run.first_line_number, [])
                matched = False
                for attr_unit in candidates:
                    unit = attr_unit.unit
                    if unit.deletion_fingerprint != deleted_run.fingerprint:
                        continue
                    if unit.claimed_content is not None:
                        if unit.claimed_content != added_run.content:
                            continue
                    elif unit.claimed_fingerprint is not None:
                        if unit.claimed_line_count != added_run.line_count:
                            continue
                        if unit.claimed_fingerprint != added_run.fingerprint:
                            continue
                    else:
                        continue
                    if not _anchor_consistent_with_diff_position(
                        unit.deletion_anchor_in_working_tree,
                        i,
                        lines,
                    ):
                        continue
                    for idx in range(i, added_run.end_index):
                        display_to_unit[idx] = attr_unit
                    matched = True
                    break

                if matched:
                    i = added_run.end_index
                    continue

            candidates = deletion_by_fingerprint.get(deleted_run.fingerprint, [])
            for attr_unit in candidates:
                unit = attr_unit.unit
                if not _anchor_consistent_with_diff_position(
                    unit.deletion_anchor_in_working_tree,
                    i,
                    lines,
                ):
                    continue
                for idx in range(i, deleted_run.end_index):
                    display_to_unit[idx] = attr_unit
                break
            i = deleted_run.end_index
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
) -> tuple[bool, Any]:
    """Filter displayed diff fragments that correspond to owned units."""
    display_to_unit = project_attribution_to_diff(attribution, line_changes)
    filtered_lines = []

    for idx, line_entry in enumerate(line_changes.lines):
        attr_unit = display_to_unit.get(idx)
        if attr_unit and attr_unit.owning_batches:
            continue

        filtered_lines.append(line_entry)

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
