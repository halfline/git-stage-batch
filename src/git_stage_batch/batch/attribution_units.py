"""File-comparison attribution units."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from . import attribution_fingerprints as _attribution_fingerprints
from .line_mapping import LineMapping
from .match import match_lines


class AttributionUnitKind(Enum):
    """Type of attribution unit for file-centric filtering."""

    PRESENCE_ONLY = "presence_only"
    """Pure addition without a coupled deletion."""

    REPLACEMENT = "replacement"
    """Addition coupled with deletion."""

    DELETION_ONLY = "deletion_only"
    """Pure deletion without a coupled addition."""


@dataclass
class AttributionUnit:
    """A semantic unit in the working tree file for attribution."""

    unit_id: str
    kind: AttributionUnitKind
    file_path: str
    claimed_line_in_working_tree: int | None
    claimed_content: bytes | None
    deletion_anchor_in_working_tree: int | None
    deletion_content: bytes | None
    deletion_fingerprint: _attribution_fingerprints.ContentFingerprint | None = None
    claimed_fingerprint: _attribution_fingerprints.ContentFingerprint | None = None
    claimed_line_count: int | None = None


@dataclass
class FileComparison:
    """Canonical baseline versus working-tree comparison for a file."""

    file_path: str
    baseline_lines: Sequence[bytes]
    working_tree_lines: Sequence[bytes]
    alignment: LineMapping

    def close(self) -> None:
        """Close the owned alignment mapping."""
        self.alignment.close()

    def __enter__(self) -> FileComparison:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def make_attribution_unit_id(
    kind: AttributionUnitKind,
    file_path: str,
    claimed_line: int | None = None,
    claimed_content: bytes | None = None,
    claimed_fingerprint: _attribution_fingerprints.ContentFingerprint | None = None,
    deletion_anchor: int | None = None,
    deletion_content: bytes | None = None,
    deletion_fingerprint: _attribution_fingerprints.ContentFingerprint | None = None,
) -> str:
    """Create a semantic, batch-independent identifier for a unit."""

    def _hash(
        content: bytes | None,
        fingerprint: _attribution_fingerprints.ContentFingerprint | None = None,
    ) -> str:
        if fingerprint is not None:
            if fingerprint.byte_count == 0:
                return "none"
            return fingerprint.sha1[:8]
        if content is None or not content:
            return "none"
        return _attribution_fingerprints.fingerprint_bytes(content).sha1[:8]

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


def _single_line_content(
    lines: Sequence[bytes],
    line_numbers: Sequence[int],
) -> bytes | None:
    if len(line_numbers) != 1:
        return None
    return lines[line_numbers[0] - 1]


def build_file_comparison_from_lines(
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
    """Enumerate file-derived units from baseline versus working-tree lines."""
    file_path = comparison.file_path
    baseline_lines = comparison.baseline_lines
    working_tree_lines = comparison.working_tree_lines
    alignment = comparison.alignment

    baseline_to_working: dict[int, int] = {}
    working_to_baseline: dict[int, int] = {}

    for baseline_idx in range(len(baseline_lines)):
        baseline_line_num = baseline_idx + 1
        working_tree_line_num = alignment.get_target_line_from_source_line(
            baseline_line_num
        )
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

        del_anchor_baseline = _find_structural_predecessor(
            del_run,
            baseline_to_working,
        )
        del_anchor_working = (
            None
            if del_anchor_baseline is None
            else baseline_to_working.get(del_anchor_baseline)
        )

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
        deletion_fingerprint = _attribution_fingerprints.fingerprint_numbered_lines(
            baseline_lines,
            del_run,
        )
        addition_content = _single_line_content(working_tree_lines, add_run)
        addition_fingerprint = None
        if addition_content is None:
            addition_fingerprint = _attribution_fingerprints.fingerprint_numbered_lines(
                working_tree_lines,
                add_run,
            )
        working_tree_line = add_run[0]

        unit = AttributionUnit(
            unit_id=make_attribution_unit_id(
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
        deletion_fingerprint = _attribution_fingerprints.fingerprint_numbered_lines(
            baseline_lines,
            del_run,
        )
        anchor_baseline = _find_structural_predecessor(del_run, baseline_to_working)
        anchor_working = (
            None
            if anchor_baseline is None
            else baseline_to_working.get(anchor_baseline)
        )

        unit = AttributionUnit(
            unit_id=make_attribution_unit_id(
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
                unit_id=make_attribution_unit_id(
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
