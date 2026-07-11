"""Tests for batch file mergeability probing."""

from __future__ import annotations

from dataclasses import dataclass

import git_stage_batch.batch.file_mergeability as file_mergeability_module
from git_stage_batch.batch.file_mergeability import probe_batch_file_mergeability
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.core.line_selection import LineRanges


class _LineContext:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, exc_type, exc, traceback):
        return None


@dataclass
class _Unit:
    display_line_ids: LineRanges


class _OwnershipForUnit:
    def __init__(self, unit: _Unit) -> None:
        self.unit = unit

    def is_empty(self) -> bool:
        return False


def test_probe_batch_file_mergeability_returns_empty_for_empty_display():
    """Empty displays do not load the worktree or build ownership units."""
    result = probe_batch_file_mergeability(
        file_path="file.txt",
        ownership=BatchOwnership.from_presence_lines([], []),
        display_lines=[],
        batch_source_lines=[],
    )

    assert result.mergeable_id_ranges == LineRanges.empty()
    assert result.units == []


def test_probe_batch_file_mergeability_checks_each_unit_once(monkeypatch):
    """Mergeability probing reuses one source-to-worktree mapping for all units."""
    ownership = BatchOwnership.from_presence_lines(["1-3"], [])
    display_lines = [
        {"id": 1, "type": "claimed"},
        {"id": 2, "type": "claimed"},
        {"id": 3, "type": "claimed"},
    ]
    units = [
        _Unit(LineRanges.from_ranges([(1, 2)])),
        _Unit(LineRanges.from_ranges([(3, 3)])),
    ]
    validated_units = []
    rebuilt_units = []
    merge_checks = []
    match_calls = []

    monkeypatch.setattr(
        file_mergeability_module,
        "load_working_tree_file_as_buffer",
        lambda path: _LineContext([b"one\n", b"two\n", b"three\n"]),
    )

    def fake_match_lines(source_lines, working_lines):
        match_calls.append((source_lines, working_lines))
        return _LineContext("mapping")

    monkeypatch.setattr(file_mergeability_module, "match_lines", fake_match_lines)

    def fake_build_units(seen_ownership, seen_display_lines):
        assert seen_ownership is ownership
        assert seen_display_lines is display_lines
        return units

    monkeypatch.setattr(
        file_mergeability_module,
        "build_ownership_units_from_display_lines",
        fake_build_units,
    )

    def fake_validate_ownership_units(unit_group):
        validated_units.append(tuple(unit_group))

    monkeypatch.setattr(
        file_mergeability_module,
        "validate_ownership_units",
        fake_validate_ownership_units,
    )

    def fake_rebuild_ownership_from_units(unit_group):
        rebuilt_units.append(tuple(unit_group))
        return _OwnershipForUnit(unit_group[0])

    monkeypatch.setattr(
        file_mergeability_module,
        "rebuild_ownership_from_units",
        fake_rebuild_ownership_from_units,
    )

    def fake_can_merge_batch_from_line_sequences(
        source_lines,
        ownership_for_unit,
        working_lines,
        *,
        source_to_working_mapping,
    ):
        merge_checks.append((
            ownership_for_unit.unit,
            source_to_working_mapping,
        ))
        return ownership_for_unit.unit is units[0]

    monkeypatch.setattr(
        file_mergeability_module.batch_merge,
        "can_merge_batch_from_line_sequences",
        fake_can_merge_batch_from_line_sequences,
    )

    result = probe_batch_file_mergeability(
        file_path="file.txt",
        ownership=ownership,
        display_lines=display_lines,
        batch_source_lines=[b"one\n", b"two\n", b"three\n"],
    )

    assert result.units is units
    assert result.mergeable_id_ranges == LineRanges.from_ranges([(1, 2)])
    assert validated_units == [(units[0],), (units[1],)]
    assert rebuilt_units == [(units[0],), (units[1],)]
    assert merge_checks == [(units[0], "mapping"), (units[1], "mapping")]
    assert len(match_calls) == 1
