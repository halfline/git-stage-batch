"""File-centric ownership attribution for filtering.

This module builds a file-centric ownership view and then projects that view
onto displayed diff hunks. Unit generation is derived from direct baseline ↔
working-tree comparison, while batch metadata supplements the model with owned
content that may not currently be visible in the working tree.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
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
    buffer_matches,
    load_git_blob_as_buffer,
    load_git_object_as_buffer_or_empty,
    load_working_tree_file_as_buffer,
)


class AttributionUnitKind(Enum):
    """Type of attribution unit for file-centric filtering."""

    PRESENCE_ONLY = "presence_only"
    """Pure addition (no coupled deletion)."""

    REPLACEMENT = "replacement"
    """Addition coupled with deletion (modification)."""

    DELETION_ONLY = "deletion_only"
    """Pure deletion (no coupled addition)."""


@dataclass(frozen=True)
class ContentFingerprint:
    """Byte count and digest for matching content without retaining it."""

    byte_count: int
    sha1: str


@dataclass(frozen=True)
class _CollectedDeletedRun:
    """Deleted diff run data used while projecting attribution."""

    end_index: int
    fingerprint: ContentFingerprint


@dataclass(frozen=True)
class _CollectedAddedRun:
    """Added diff run data used while projecting attribution."""

    end_index: int
    first_line_number: int | None
    line_count: int
    content: bytes | None
    fingerprint: ContentFingerprint


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
    deletion_fingerprint: ContentFingerprint | None = None
    claimed_fingerprint: ContentFingerprint | None = None
    claimed_line_count: int | None = None


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
    claimed_fingerprint: ContentFingerprint | None = None,
    deletion_anchor: int | None = None,
    deletion_content: bytes | None = None,
    deletion_fingerprint: ContentFingerprint | None = None,
) -> str:
    """Create a semantic, batch-independent identifier for a unit."""
    def _hash(
        content: bytes | None,
        fingerprint: ContentFingerprint | None = None,
    ) -> str:
        if fingerprint is not None:
            if fingerprint.byte_count == 0:
                return "none"
            return fingerprint.sha1[:8]
        if content is None or not content:
            return "none"
        return hashlib.sha1(content).hexdigest()[:8]

    if kind == AttributionUnitKind.PRESENCE_ONLY:
        line_str = str(claimed_line) if claimed_line is not None else "missing"
        return (
            f"PRESENCE_ONLY:{file_path}:{line_str}:"
            f"{_hash(claimed_content, claimed_fingerprint)}"
        )

    if kind == AttributionUnitKind.DELETION_ONLY:
        anchor_str = str(deletion_anchor) if deletion_anchor is not None else "start"
        return (
            f"DELETION_ONLY:{file_path}:after-{anchor_str}:"
            f"{_hash(deletion_content, deletion_fingerprint)}"
        )

    if kind == AttributionUnitKind.REPLACEMENT:
        line_str = str(claimed_line) if claimed_line is not None else "missing"
        anchor_str = str(deletion_anchor) if deletion_anchor is not None else "start"
        return (
            f"REPLACEMENT:{file_path}:{line_str}:after-{anchor_str}:"
            f"c{_hash(claimed_content, claimed_fingerprint)}:"
            f"d{_hash(deletion_content, deletion_fingerprint)}"
        )

    return f"UNKNOWN:{file_path}"


def _fingerprint_chunks(chunks: Iterable[bytes]) -> ContentFingerprint:
    digest = hashlib.sha1()
    byte_count = 0
    for chunk in chunks:
        digest.update(chunk)
        byte_count += len(chunk)
    return ContentFingerprint(byte_count=byte_count, sha1=digest.hexdigest())


def _fingerprint_bytes(content: bytes | None) -> ContentFingerprint | None:
    if content is None:
        return None
    return _fingerprint_chunks([content])


def _fingerprint_git_blob(blob_hash: str) -> ContentFingerprint | None:
    try:
        with load_git_blob_as_buffer(blob_hash) as blob_buffer:
            return _fingerprint_chunks(blob_buffer.byte_chunks())
    except RuntimeError:
        return None


def _fingerprint_numbered_lines(
    lines: Sequence[bytes],
    line_numbers: Iterable[int],
) -> ContentFingerprint:
    return _fingerprint_chunks(lines[line_number - 1] for line_number in line_numbers)


def _single_line_content(
    lines: Sequence[bytes],
    line_numbers: Sequence[int],
) -> bytes | None:
    if len(line_numbers) != 1:
        return None
    return lines[line_numbers[0] - 1]


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
        deletion_fingerprint = _fingerprint_numbered_lines(baseline_lines, del_run)
        addition_content = _single_line_content(working_tree_lines, add_run)
        addition_fingerprint = None
        if addition_content is None:
            addition_fingerprint = _fingerprint_numbered_lines(
                working_tree_lines,
                add_run,
            )
        working_tree_line = add_run[0]

        unit = AttributionUnit(
            unit_id=_make_unit_id(
                AttributionUnitKind.REPLACEMENT,
                file_path,
                claimed_line=working_tree_line,
                claimed_content=addition_content,
                claimed_fingerprint=addition_fingerprint,
                deletion_anchor=anchor,
                deletion_fingerprint=deletion_fingerprint,
            ),
            kind=AttributionUnitKind.REPLACEMENT,
            file_path=file_path,
            claimed_line_in_working_tree=working_tree_line,
            claimed_content=addition_content,
            deletion_anchor_in_working_tree=anchor,
            deletion_content=None,
            deletion_fingerprint=deletion_fingerprint,
            claimed_fingerprint=addition_fingerprint,
            claimed_line_count=len(add_run),
        )
        units_map.setdefault(unit.unit_id, unit)

    for del_run in remaining_deletions:
        deletion_fingerprint = _fingerprint_numbered_lines(baseline_lines, del_run)
        anchor_baseline = _find_structural_predecessor(del_run, baseline_to_working)
        anchor_working = None if anchor_baseline is None else baseline_to_working.get(anchor_baseline)

        unit = AttributionUnit(
            unit_id=_make_unit_id(
                AttributionUnitKind.DELETION_ONLY,
                file_path,
                deletion_anchor=anchor_working,
                deletion_fingerprint=deletion_fingerprint,
            ),
            kind=AttributionUnitKind.DELETION_ONLY,
            file_path=file_path,
            claimed_line_in_working_tree=None,
            claimed_content=None,
            deletion_anchor_in_working_tree=anchor_working,
            deletion_content=None,
            deletion_fingerprint=deletion_fingerprint,
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

    baseline_buffer = load_git_object_as_buffer_or_empty(f"HEAD:{file_path}")
    working_tree_buffer = load_working_tree_file_as_buffer(file_path)
    with baseline_buffer as baseline_lines, working_tree_buffer as working_tree_lines:
        comparison = _build_file_comparison_from_lines(
            file_path,
            baseline_lines=baseline_lines,
            working_tree_lines=working_tree_lines,
        )
        all_units_map: dict[str, AttributionUnit] = {}
        enumerate_units_from_file_comparison(comparison, all_units_map)

        if all_batch_metadata:
            _enumerate_units_from_batches(
                file_path,
                all_batch_metadata,
                all_units_map,
                working_tree_lines=working_tree_lines,
            )

        attributed_units = [
            AttributedUnit(
                unit=unit,
                owning_batches=_find_owning_batches(
                    unit,
                    file_path,
                    all_batch_metadata,
                    working_tree_lines=working_tree_lines,
                ),
            )
            for unit in all_units_map.values()
        ]

    return FileAttribution(file_path=file_path, units=attributed_units)


def _enumerate_units_from_batches(
    file_path: str,
    all_batch_metadata: dict,
    units_map: dict[str, AttributionUnit],
    *,
    working_tree_lines: Sequence[bytes],
) -> None:
    """Add batch-owned units that may not be visible in the working tree."""
    for batch_metadata in all_batch_metadata.values():
        file_metadata = batch_metadata["files"][file_path]
        batch_source_commit = file_metadata["batch_source_commit"]
        batch_source_buffer = load_git_object_as_buffer_or_empty(
            f"{batch_source_commit}:{file_path}"
        )
        with batch_source_buffer as batch_source_lines:
            if len(batch_source_lines) == 0 and _has_presence_source_lines(file_metadata):
                continue

            alignment = match_lines(
                source_lines=batch_source_lines,
                target_lines=working_tree_lines,
            )

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

                deletion_fingerprint = _fingerprint_git_blob(blob_hash)
                if deletion_fingerprint is None:
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
                        deletion_fingerprint=deletion_fingerprint,
                    ),
                    kind=AttributionUnitKind.DELETION_ONLY,
                    file_path=file_path,
                    claimed_line_in_working_tree=None,
                    claimed_content=None,
                    deletion_anchor_in_working_tree=deletion_anchor,
                    deletion_content=None,
                    deletion_fingerprint=deletion_fingerprint,
                )
                units_map.setdefault(unit.unit_id, unit)


def _find_owning_batches(
    unit: AttributionUnit,
    file_path: str,
    all_batch_metadata: dict,
    *,
    working_tree_lines: Sequence[bytes],
) -> set[str]:
    """Determine which batches own a given unit."""
    owning_batches: set[str] = set()

    for batch_name, batch_metadata in all_batch_metadata.items():
        file_metadata = batch_metadata["files"][file_path]
        batch_source_commit = file_metadata["batch_source_commit"]
        batch_source_buffer = load_git_object_as_buffer_or_empty(
            f"{batch_source_commit}:{file_path}"
        )
        with batch_source_buffer as batch_source_lines:
            alignment = match_lines(
                source_lines=batch_source_lines,
                target_lines=working_tree_lines,
            )

            if unit.kind == AttributionUnitKind.PRESENCE_ONLY:
                if _batch_owns_presence_unit(
                    unit,
                    file_metadata,
                    alignment,
                    batch_source_lines,
                ):
                    owning_batches.add(batch_name)
            elif unit.kind == AttributionUnitKind.DELETION_ONLY:
                if _batch_owns_deletion_unit(unit, file_metadata, alignment):
                    owning_batches.add(batch_name)
            elif unit.kind == AttributionUnitKind.REPLACEMENT:
                if (
                    _batch_owns_presence_unit(
                        unit,
                        file_metadata,
                        alignment,
                        batch_source_lines,
                    )
                    and _batch_owns_deletion_unit(unit, file_metadata, alignment)
                ):
                    owning_batches.add(batch_name)

    return owning_batches


def _batch_owns_presence_unit(
    unit: AttributionUnit,
    file_metadata: dict,
    alignment,
    batch_source_lines: Sequence[bytes],
) -> bool:
    """Check whether a batch owns the presence side of a unit.

    For units present in the working tree we require structural identity first,
    and then verify content. For units missing from the working tree we only
    accept claimed source lines that are themselves currently unmapped.
    """
    if unit.claimed_content is None and unit.claimed_fingerprint is None:
        return False

    claimed_source_lines = _parse_presence_source_lines(file_metadata)
    if not claimed_source_lines:
        return False
    claimed_source_line_set = set(claimed_source_lines)

    if unit.claimed_line_in_working_tree is not None:
        mapped_source_line = alignment.get_source_line_from_target_line(
            unit.claimed_line_in_working_tree
        )
        if mapped_source_line is None:
            return False
        if mapped_source_line not in claimed_source_line_set:
            return False
        if mapped_source_line < 1 or mapped_source_line > len(batch_source_lines):
            return False

        if unit.claimed_content is not None:
            return batch_source_lines[mapped_source_line - 1] == unit.claimed_content

        if unit.claimed_fingerprint is None or unit.claimed_line_count is None:
            return False

        source_line_range = range(
            mapped_source_line,
            mapped_source_line + unit.claimed_line_count,
        )
        if source_line_range.stop - 1 > len(batch_source_lines):
            return False
        if any(source_line not in claimed_source_line_set for source_line in source_line_range):
            return False
        return (
            _fingerprint_numbered_lines(batch_source_lines, source_line_range)
            == unit.claimed_fingerprint
        )

    for source_line in claimed_source_lines:
        if source_line < 1 or source_line > len(batch_source_lines):
            continue

        if unit.claimed_content is not None:
            if batch_source_lines[source_line - 1] != unit.claimed_content:
                continue
            if alignment.get_target_line_from_source_line(source_line) is None:
                return True
            continue

        if unit.claimed_fingerprint is None or unit.claimed_line_count is None:
            continue

        source_line_range = range(source_line, source_line + unit.claimed_line_count)
        if source_line_range.stop - 1 > len(batch_source_lines):
            continue
        if any(line not in claimed_source_line_set for line in source_line_range):
            continue
        if any(
            alignment.get_target_line_from_source_line(line) is not None
            for line in source_line_range
        ):
            continue
        if (
            _fingerprint_numbered_lines(batch_source_lines, source_line_range)
            == unit.claimed_fingerprint
        ):
            return True

    return False


def _batch_owns_deletion_unit(
    unit: AttributionUnit,
    file_metadata: dict,
    alignment,
) -> bool:
    """Check whether a batch owns a deletion unit via explicit deletion claims."""
    if unit.deletion_content is None and unit.deletion_fingerprint is None:
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

        if (
            unit.deletion_content is not None
            and _deletion_blob_matches_content(blob_hash, unit.deletion_content)
        ):
            return True

        if (
            unit.deletion_fingerprint is not None
            and _fingerprint_git_blob(blob_hash) == unit.deletion_fingerprint
        ):
            return True

    return False


def _deletion_blob_matches_content(blob_hash: str, content: bytes) -> bool:
    try:
        with load_git_blob_as_buffer(blob_hash) as blob_buffer:
            return buffer_matches(blob_buffer, content)
    except RuntimeError:
        return False


def _collect_deleted_run(
    lines: list,
    start_idx: int,
) -> _CollectedDeletedRun:
    end_idx = start_idx
    while end_idx < len(lines) and lines[end_idx].kind == "-":
        end_idx += 1

    deletion_fingerprint = _fingerprint_chunks(
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
    fingerprint = _fingerprint_chunks(
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
    deletion_by_fingerprint: dict[ContentFingerprint, list[AttributedUnit]] = {}

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
                or _fingerprint_bytes(unit.deletion_content)
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
