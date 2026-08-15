"""Tests for batch file mergeability probing."""

from __future__ import annotations

from dataclasses import dataclass

import git_stage_batch.batch.file_mergeability as file_mergeability_module
from git_stage_batch.batch.file_mergeability import probe_batch_file_mergeability
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.ownership.replacement_units import ReplacementUnit
from git_stage_batch.batch.ownership.references import BaselineReference
from git_stage_batch.batch.ownership.unit_types import OwnershipUnit, OwnershipUnitKind
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
    replacement_origin: object | None = None


class _OwnershipForUnit:
    def __init__(self, unit: _Unit) -> None:
        self.unit = unit

    def is_empty(self) -> bool:
        return False


def test_ownership_unit_range_strided_slice_stays_lazy():
    """A strided unit view must not copy one Python reference per unit."""
    units = [object() for _index in range(8)]
    view = file_mergeability_module._OwnershipUnitRange(units, 1, 8)

    selected = view[::-2]

    assert isinstance(selected, file_mergeability_module._OwnershipUnitRange)
    assert list(selected) == [units[7], units[5], units[3], units[1]]


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
    monkeypatch.setattr(
        file_mergeability_module,
        "read_git_object_buffer_or_none",
        lambda _spec: None,
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

    def fake_rebuild_ownership_from_units(
        unit_group,
        *,
        normalize_replacement_metadata,
    ):
        assert normalize_replacement_metadata is False
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
        trusted_target_lines,
        source_to_trusted_target_mapping,
        trusted_target_to_working_mapping,
    ):
        assert trusted_target_lines is None
        assert source_to_trusted_target_mapping is None
        assert trusted_target_to_working_mapping is None
        merge_checks.append(
            (
                ownership_for_unit.unit,
                source_to_working_mapping,
            )
        )
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
    assert result.mergeable_selection_groups == (LineRanges.from_ranges([(1, 2)]),)
    assert validated_units == [(units[0],), (units[1],)]
    assert rebuilt_units == [(units[0],), (units[1],)]
    assert merge_checks == [(units[0], "mapping"), (units[1], "mapping")]
    assert len(match_calls) == 1


def test_probe_legacy_replacement_uses_trusted_target(monkeypatch):
    """Legacy replacements may require the index lineage to prove relocation."""
    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        [AbsenceClaim(anchor_line=0, content_lines=[b"old\n"])],
        replacement_units=[ReplacementUnit(["1"], [0])],
    )
    unit = _Unit(LineRanges.from_ranges(((1, 1),)))
    match_calls = []

    monkeypatch.setattr(
        file_mergeability_module,
        "load_working_tree_file_as_buffer",
        lambda _path: _LineContext([b"working\n"]),
    )
    monkeypatch.setattr(
        file_mergeability_module,
        "read_git_object_buffer_or_none",
        lambda _spec: _LineContext([b"trusted\n"]),
    )

    def fake_match_lines(source_lines, target_lines):
        mapping = f"mapping-{len(match_calls)}"
        match_calls.append((source_lines, target_lines))
        return _LineContext(mapping)

    monkeypatch.setattr(
        file_mergeability_module,
        "match_lines",
        fake_match_lines,
    )
    monkeypatch.setattr(
        file_mergeability_module,
        "build_ownership_units_from_display_lines",
        lambda _ownership, _display: [unit],
    )
    monkeypatch.setattr(
        file_mergeability_module,
        "validate_ownership_units",
        lambda _units: None,
    )
    monkeypatch.setattr(
        file_mergeability_module,
        "rebuild_ownership_from_units",
        lambda selected, *, normalize_replacement_metadata: (
            _OwnershipForUnit(selected[0])
        ),
    )

    def can_merge(_source, _selected, _working, **options):
        assert options["trusted_target_lines"] is not None
        assert options["source_to_trusted_target_mapping"] == "mapping-1"
        assert options["trusted_target_to_working_mapping"] == "mapping-2"
        return True

    monkeypatch.setattr(
        file_mergeability_module.batch_merge,
        "can_merge_batch_from_line_sequences",
        can_merge,
    )

    result = probe_batch_file_mergeability(
        file_path="file.txt",
        ownership=ownership,
        display_lines=[{"id": 1, "type": "claimed"}],
        batch_source_lines=[b"source\n"],
    )

    assert result.mergeable_id_ranges == LineRanges.from_ranges(((1, 1),))
    assert len(match_calls) == 3


def test_probe_exposes_complete_split_parent_as_one_selection(monkeypatch):
    """Children mergeable only together form one atomic review selection."""
    origin = object()
    units = [
        _Unit(LineRanges.from_ranges([(1, 2)]), origin),
        _Unit(LineRanges.from_ranges([(3, 4)]), origin),
    ]
    ownership = BatchOwnership.from_presence_lines(["1-2"], [])
    display_lines = [{"id": line_id, "type": "claimed"} for line_id in range(1, 5)]
    checked_groups = []

    monkeypatch.setattr(
        file_mergeability_module,
        "load_working_tree_file_as_buffer",
        lambda _path: _LineContext([b"one\n", b"two\n"]),
    )
    monkeypatch.setattr(
        file_mergeability_module,
        "read_git_object_buffer_or_none",
        lambda _spec: None,
    )
    monkeypatch.setattr(
        file_mergeability_module,
        "match_lines",
        lambda _source, _target: _LineContext("mapping"),
    )
    monkeypatch.setattr(
        file_mergeability_module,
        "build_ownership_units_from_display_lines",
        lambda _ownership, _display: units,
    )
    monkeypatch.setattr(
        file_mergeability_module,
        "validate_ownership_units",
        lambda _units: None,
    )

    class _GroupOwnership:
        def __init__(
            self,
            selected_units,
            *,
            normalize_replacement_metadata,
        ):
            assert normalize_replacement_metadata is False
            self.units = tuple(selected_units)

        def is_empty(self):
            return False

    monkeypatch.setattr(
        file_mergeability_module,
        "rebuild_ownership_from_units",
        _GroupOwnership,
    )

    def can_merge(_source, selected, _working, **_options):
        checked_groups.append(selected.units)
        return len(selected.units) == 2

    monkeypatch.setattr(
        file_mergeability_module.batch_merge,
        "can_merge_batch_from_line_sequences",
        can_merge,
    )

    result = probe_batch_file_mergeability(
        file_path="file.txt",
        ownership=ownership,
        display_lines=display_lines,
        batch_source_lines=[b"one\n", b"two\n"],
    )

    assert checked_groups == [(units[0],), (units[1],), tuple(units)]
    assert result.mergeable_selection_groups == (LineRanges.from_ranges([(1, 4)]),)


def test_probe_exposes_complete_shared_addition_boundary(monkeypatch):
    """Pure additions mergeable only together form one review selection."""
    reference = BaselineReference(after_line=1)
    units = [
        OwnershipUnit(
            kind=OwnershipUnitKind.PRESENCE_ONLY,
            claimed_source_lines=LineRanges.from_lines([line_id]),
            deletion_claims=[],
            display_line_ids=LineRanges.from_lines([line_id]),
            baseline_references={line_id: reference},
        )
        for line_id in range(1, 6)
    ]
    monkeypatch.setattr(
        file_mergeability_module,
        "load_working_tree_file_as_buffer",
        lambda _path: _LineContext([b"target\n"]),
    )
    monkeypatch.setattr(
        file_mergeability_module,
        "read_git_object_buffer_or_none",
        lambda _spec: None,
    )
    monkeypatch.setattr(
        file_mergeability_module,
        "match_lines",
        lambda _source, _target: _LineContext("mapping"),
    )
    monkeypatch.setattr(
        file_mergeability_module,
        "build_ownership_units_from_display_lines",
        lambda _ownership, _display: units,
    )
    monkeypatch.setattr(
        file_mergeability_module,
        "validate_ownership_units",
        lambda _units: None,
    )

    class _GroupOwnership:
        def __init__(self, selected):
            self.units = tuple(selected)

        def is_empty(self):
            return False

    monkeypatch.setattr(
        file_mergeability_module,
        "rebuild_ownership_from_units",
        lambda selected, **_options: _GroupOwnership(selected),
    )
    monkeypatch.setattr(
        file_mergeability_module.batch_merge,
        "can_merge_batch_from_line_sequences",
        lambda _source, selected, _working, **_options: len(selected.units) == 5,
    )

    result = probe_batch_file_mergeability(
        file_path="file.txt",
        ownership=BatchOwnership.from_presence_lines(["1-5"], []),
        display_lines=[
            {"id": line_id, "type": "claimed"} for line_id in range(1, 6)
        ],
        batch_source_lines=[b"source\n"],
    )

    assert result.mergeable_selection_groups == (
        LineRanges.from_ranges(((1, 5),)),
    )
    assert result.mergeable_id_ranges == LineRanges.from_ranges(((1, 5),))


def test_composite_probe_skips_repeated_replacement_normalization(
    monkeypatch,
):
    """One large parent probe must use the trusted linear rebuild path."""
    unit_count = 512
    origin = object()
    units = [
        _Unit(LineRanges.from_ranges(((line_id, line_id),)), origin)
        for line_id in range(1, unit_count + 1)
    ]
    ownership = BatchOwnership.from_presence_lines([f"1-{unit_count}"], [])
    display_lines = [
        {"id": line_id, "type": "claimed"} for line_id in range(1, unit_count + 1)
    ]
    rebuilt_sizes = []

    monkeypatch.setattr(
        file_mergeability_module,
        "load_working_tree_file_as_buffer",
        lambda _path: _LineContext([b"line\n"]),
    )
    monkeypatch.setattr(
        file_mergeability_module,
        "read_git_object_buffer_or_none",
        lambda _spec: None,
    )
    monkeypatch.setattr(
        file_mergeability_module,
        "match_lines",
        lambda _source, _target: _LineContext("mapping"),
    )
    monkeypatch.setattr(
        file_mergeability_module,
        "build_ownership_units_from_display_lines",
        lambda _ownership, _display: units,
    )
    monkeypatch.setattr(
        file_mergeability_module,
        "validate_ownership_units",
        lambda _units: None,
    )

    class _GroupOwnership:
        def __init__(self, selected_units):
            self.units = tuple(selected_units)

        def is_empty(self):
            return False

    def rebuild(
        selected_units,
        *,
        normalize_replacement_metadata,
    ):
        assert normalize_replacement_metadata is False
        rebuilt_sizes.append(len(selected_units))
        return _GroupOwnership(selected_units)

    monkeypatch.setattr(
        file_mergeability_module,
        "rebuild_ownership_from_units",
        rebuild,
    )
    monkeypatch.setattr(
        file_mergeability_module.batch_merge,
        "can_merge_batch_from_line_sequences",
        lambda _source, selected, _working, **_options: (
            len(selected.units) == unit_count
        ),
    )

    result = probe_batch_file_mergeability(
        file_path="file.txt",
        ownership=ownership,
        display_lines=display_lines,
        batch_source_lines=[b"line\n"],
    )

    assert rebuilt_sizes == [1] * unit_count + [unit_count]
    assert result.mergeable_selection_groups == (
        LineRanges.from_ranges(((1, unit_count),)),
    )
