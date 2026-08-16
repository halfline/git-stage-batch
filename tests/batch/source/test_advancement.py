"""Tests for stale batch source detection and advancement."""

from __future__ import annotations

import gc
import tracemalloc

import pytest

from git_stage_batch.batch.source import advancement as advancement_module
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.ownership.model import (
    BatchOwnership,
)
from git_stage_batch.batch.ownership.merging import merge_batch_ownership
from git_stage_batch.batch.ownership.remapping import remap_batch_ownership_with_lineage
from git_stage_batch.batch.ownership.translation import (
    detect_stale_batch_source_for_selection,
    translate_lines_to_batch_ownership,
)
from git_stage_batch.batch.ownership.references import BaselineReference
from git_stage_batch.batch.ownership.replacement_units import ReplacementUnit
from git_stage_batch.batch.source.advancement import (
    BatchSourceAdvanceError,
    advance_batch_source_for_file_with_provenance,
    advance_source_lines_preserving_existing_presence,
)
from git_stage_batch.batch.line_matching.comparison import (
    SemanticChangeKind,
    SemanticChangeRun,
)
from git_stage_batch.batch.line_matching.match_workspace import MatcherWorkspace
from git_stage_batch.batch.line_matching.lineage import (
    BatchSourceLineage,
    LineageRun,
    SourceSelectionExpansion,
)
from git_stage_batch.core.line_selection import LineRanges
from git_stage_batch.core.models import LineEntry
from git_stage_batch.core.buffer import LineBuffer


_LINE_SCALE_TEST_COUNTS = (1024, 8192)
_LINE_SCALE_HEAP_GROWTH_LIMIT = 32 * 1024


class _IterationGuardedLineSelection:
    """Line selection that rejects full expansion in stale-source tests."""

    def __init__(self, ranges: tuple[tuple[int, int], ...]) -> None:
        self._ranges = ranges

    def __contains__(self, line_number: object) -> bool:
        if type(line_number) is not int:
            return False
        return any(start <= line_number <= end for start, end in self._ranges)

    def __bool__(self) -> bool:
        return bool(self._ranges)

    def __iter__(self):
        raise AssertionError("line selection should not be expanded")

    def ranges(self) -> tuple[tuple[int, int], ...]:
        return self._ranges


class _PresenceLineGuardedOwnership(BatchOwnership):
    """Ownership that returns a guarded presence selection."""

    def __init__(self, selection: _IterationGuardedLineSelection) -> None:
        super().__init__(presence_claims=[], deletions=[])
        self._selection = selection

    def presence_line_set(self) -> _IterationGuardedLineSelection:
        return self._selection


def _advance_source_from_content(
    *,
    old_source_buffer: bytes,
    working_buffer: bytes,
    ownership: BatchOwnership,
):
    with (
        LineBuffer.from_bytes(old_source_buffer) as old_source_lines,
        LineBuffer.from_bytes(working_buffer) as working_lines,
    ):
        return advance_source_lines_preserving_existing_presence(
            old_lines=old_source_lines,
            working_lines=working_lines,
            ownership=ownership,
        )


def test_detect_stale_batch_source_with_none_source_lines():
    """Test detection of stale batch source when source_line is None."""
    # Lines with source_line=None indicate stale source
    stale_lines = [
        LineEntry(id=1, kind='+', old_line_number=None, new_line_number=1,
                 text_bytes=b"new line", text="new line", source_line=None),
    ]

    assert detect_stale_batch_source_for_selection(stale_lines) is True


def test_detect_current_batch_source_with_valid_source_lines():
    """Test detection passes when all source_lines are valid."""
    current_lines = [
        LineEntry(id=1, kind=' ', old_line_number=1, new_line_number=1,
                 text_bytes=b"context", text="context", source_line=1),
        LineEntry(id=2, kind='+', old_line_number=None, new_line_number=2,
                 text_bytes=b"addition", text="addition", source_line=2),
    ]

    assert detect_stale_batch_source_for_selection(current_lines) is False


def test_detect_stale_batch_source_with_missing_deletion_anchor():
    """Deletion-only selections after file start need source refresh."""
    stale_lines = [
        LineEntry(id=1, kind='-', old_line_number=2, new_line_number=None,
                 text_bytes=b"old line", text="old line", source_line=None),
    ]

    assert detect_stale_batch_source_for_selection(stale_lines) is True


def test_detect_current_batch_source_with_file_start_deletion_anchor():
    """A missing deletion source line is valid before the first line."""
    current_lines = [
        LineEntry(id=1, kind='-', old_line_number=1, new_line_number=None,
                 text_bytes=b"old first", text="old first", source_line=None),
    ]

    assert detect_stale_batch_source_for_selection(current_lines) is False


def test_translate_fails_loudly_with_none_source_line():
    """Test that translation fails loudly instead of silently dropping None source_lines."""
    stale_lines = [
        LineEntry(id=1, kind='+', old_line_number=None, new_line_number=1,
                 text_bytes=b"new code", text="new code", source_line=None),
    ]

    with pytest.raises(ValueError, match="Batch source is stale"):
        translate_lines_to_batch_ownership(stale_lines)


def test_batch_source_lineage_translates_ranges():
    """Batch source lineage should translate selections without per-line storage."""
    with BatchSourceLineage(
        source_runs=[
            LineageRun(old_start=1, old_end=2, new_start=10),
            LineageRun(old_start=3, old_end=4, new_start=12),
            LineageRun(old_start=8, old_end=9, new_start=20),
        ],
        working_runs=[
            LineageRun(old_start=20, old_end=21, new_start=30),
        ],
    ) as lineage:
        assert tuple(lineage.source_runs()) == (
            LineageRun(old_start=1, old_end=4, new_start=10),
            LineageRun(old_start=8, old_end=9, new_start=20),
        )
        assert lineage.translate_source_line(3) == 12
        assert lineage.translate_source_line(7) is None
        assert lineage.translate_source_selection(
            LineRanges.from_ranges([(2, 8)])
        ).ranges() == ((11, 13), (20, 20))
        assert lineage.translate_working_line(21) == 31
        assert lineage.translate_working_range(20, 21) == (30, 31)
        assert lineage.translate_working_range(19, 20) is None


def test_batch_source_lineage_expands_complete_owned_replacements():
    """Whole source selections should inherit every expanded destination line."""
    with BatchSourceLineage(
        source_runs=[LineageRun(old_start=1, old_end=3, new_start=1)],
        source_expansions=[
            SourceSelectionExpansion(
                source_start=2,
                source_end=2,
                new_start=2,
                new_end=3,
            ),
        ],
    ) as lineage:
        assert lineage.translate_source_selection(
            LineRanges.from_ranges(((2, 2),))
        ).ranges() == ((2, 3),)
        assert lineage.translate_source_selection(
            LineRanges.from_ranges(((1, 1),))
        ).ranges() == ((1, 1),)


def test_fragmented_source_lineage_translation_avoids_line_scale_python_heap():
    """Fragmented provenance should coalesce before entering heap storage."""
    heap_peaks = []
    for line_count in _LINE_SCALE_TEST_COUNTS:
        selection = LineRanges.from_ranges(((1, line_count),))

        with BatchSourceLineage() as lineage:
            for source_line in range(1, line_count + 1):
                new_start = source_line * 2 - 1
                lineage.append_source_run(
                    LineageRun(
                        old_start=source_line,
                        old_end=source_line,
                        new_start=new_start,
                    )
                )
                lineage.append_source_expansion(
                    SourceSelectionExpansion(
                        source_start=source_line,
                        source_end=source_line,
                        new_start=new_start,
                        new_end=new_start + 1,
                    )
                )

            gc.collect()
            tracemalloc.start()
            try:
                translated = lineage.translate_source_selection(selection)
                _current_heap, peak_heap = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()

        assert translated.ranges() == ((1, line_count * 2),)
        heap_peaks.append(peak_heap)

    small_peak, large_peak = heap_peaks
    assert large_peak < small_peak + _LINE_SCALE_HEAP_GROWTH_LIMIT


def test_lineage_constructor_streams_ordered_runs_without_python_sort_heap():
    """Ordered run producers must flow directly into mapped lineage storage."""
    heap_peaks = []
    for line_count in _LINE_SCALE_TEST_COUNTS:
        gc.collect()
        tracemalloc.start()
        try:
            with BatchSourceLineage(
                source_runs=(
                    LineageRun(
                        old_start=line_number,
                        old_end=line_number,
                        new_start=line_number,
                    )
                    for line_number in range(1, line_count + 1)
                )
            ):
                _current_heap, peak_heap = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        heap_peaks.append(peak_heap)

    small_peak, large_peak = heap_peaks
    assert large_peak < small_peak + _LINE_SCALE_HEAP_GROWTH_LIMIT


def test_batch_source_lineage_finds_unmapped_source_ranges():
    """Unmapped-source lookup should scan runs without expanding selections."""
    with BatchSourceLineage(
        source_runs=[
            LineageRun(old_start=1, old_end=20, new_start=100),
            LineageRun(old_start=30, old_end=40, new_start=200),
        ],
    ) as lineage:
        assert lineage.first_unmapped_source_line(
            _IterationGuardedLineSelection(((5, 5), (10, 12)))
        ) is None
        assert lineage.first_unmapped_source_line(
            _IterationGuardedLineSelection(((5, 5), (10, 12), (25, 26)))
        ) == 25


def test_batch_source_lineage_rejects_overlapping_appends():
    """Lineage appends should require monotonic old-coordinate runs."""
    with BatchSourceLineage() as lineage:
        lineage.append_source_run(
            LineageRun(old_start=10, old_end=20, new_start=100)
        )

        with pytest.raises(ValueError, match="lineage runs must not overlap"):
            lineage.append_source_run(
                LineageRun(old_start=5, old_end=9, new_start=200)
            )

        with pytest.raises(ValueError, match="lineage runs must not overlap"):
            lineage.append_source_run(
                LineageRun(old_start=20, old_end=25, new_start=300)
            )


def test_batch_source_lineage_closes_mapped_storage():
    """Batch source lineage should close mapped run storage."""
    lineage = BatchSourceLineage(
        source_runs=[
            LineageRun(old_start=1, old_end=1000, new_start=1),
            LineageRun(old_start=2000, old_end=2000, new_start=5000),
        ],
    )

    assert lineage.byte_count > 0

    lineage.close()

    assert lineage.closed is True
    assert lineage.byte_count == 0
    with pytest.raises(ValueError, match="batch source lineage is closed"):
        lineage.translate_source_line(1)


def test_source_lineage_remaps_guarded_presence_ranges():
    """Source-line remapping should consume presence ranges directly."""
    ownership = _PresenceLineGuardedOwnership(
        _IterationGuardedLineSelection(((1, 1000), (2000, 2001)))
    )
    with BatchSourceLineage(
        source_runs=[
            LineageRun(old_start=1, old_end=1000, new_start=10),
            LineageRun(old_start=2000, old_end=2001, new_start=5000),
        ],
    ) as lineage:
        remapped = remap_batch_ownership_with_lineage(
            ownership,
            lineage,
        )

    assert remapped.presence_claims[0].source_lines == ["10-1009,5000-5001"]


def test_merge_coalesces_overlapping_replacement_units_after_deduplication():
    """Deduplicated absence claims should keep replacement metadata disjoint."""
    deletion = AbsenceClaim(anchor_line=None, content_lines=[b"old value\n"])
    existing = BatchOwnership.from_presence_lines(
        ["1"],
        [deletion],
        replacement_units=[
            ReplacementUnit(presence_lines=["1"], deletion_indices=[0]),
        ],
    )
    new = BatchOwnership.from_presence_lines(
        ["2"],
        [
            AbsenceClaim(anchor_line=None, content_lines=[b"old value\n"]),
        ],
        replacement_units=[
            ReplacementUnit(presence_lines=["2"], deletion_indices=[0]),
        ],
    )

    merged = merge_batch_ownership(existing, new)

    assert merged.presence_claims[0].source_lines == ["1-2"]
    assert merged.deletions == [deletion]
    assert merged.replacement_units == [
        ReplacementUnit(presence_lines=["1-2"], deletion_indices=[0]),
    ]


def test_merge_deduplicated_deletions_keeps_stronger_baseline_reference():
    """Deduplicated deletions should keep baseline metadata from new claims."""
    existing = BatchOwnership.from_presence_lines(
        [],
        [AbsenceClaim(anchor_line=1, content_lines=[b"old value\n"])],
    )
    reference = BaselineReference(
        after_line=1,
        after_content=b"anchor",
        before_line=2,
        before_content=b"next",
        has_before_line=True,
    )
    new = BatchOwnership.from_presence_lines(
        [],
        [
            AbsenceClaim(
                anchor_line=1,
                content_lines=[b"old value\n"],
                baseline_reference=reference,
            ),
        ],
    )

    merged = merge_batch_ownership(existing, new)

    assert merged.deletions == [
        AbsenceClaim(
            anchor_line=1,
            content_lines=[b"old value\n"],
            baseline_reference=reference,
        )
    ]


def test_merge_presence_claims_keeps_strongest_baseline_reference_sides():
    """Overlapping presence claims should retain both useful boundaries."""
    existing_reference = BaselineReference(
        after_line=7,
        after_content=b"after\n",
        before_line=None,
        has_before_line=False,
    )
    new_reference = BaselineReference(
        after_line=None,
        after_content=None,
        has_after_line=False,
        before_line=11,
        before_content=b"before\n",
        has_before_line=True,
    )
    existing = BatchOwnership.from_presence_lines(
        ["1"],
        baseline_references={1: existing_reference},
    )
    new = BatchOwnership.from_presence_lines(
        ["1"],
        baseline_references={1: new_reference},
    )

    merged = merge_batch_ownership(existing, new)

    assert merged.presence_baseline_references()[1] == BaselineReference(
        after_line=7,
        after_content=b"after\n",
        before_line=11,
        before_content=b"before\n",
        has_before_line=True,
    )


def test_advance_batch_source_reads_dangling_symlink(
    monkeypatch,
    tmp_path,
):
    """A dangling symlink remains a readable Git worktree object."""
    (tmp_path / "link").symlink_to("missing-new-target")
    loaded_paths: list[str] = []
    monkeypatch.setattr(
        advancement_module,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        advancement_module,
        "read_git_object_buffer_or_none",
        lambda _revision: LineBuffer.from_bytes(b"missing-old-target"),
    )

    def load_working_tree_file(path: str) -> LineBuffer:
        loaded_paths.append(path)
        return LineBuffer.from_bytes(b"missing-new-target")

    monkeypatch.setattr(
        advancement_module,
        "load_working_tree_file_as_buffer",
        load_working_tree_file,
    )
    monkeypatch.setattr(
        advancement_module,
        "create_batch_source_commit",
        lambda _path, *, file_buffer_override: "new-source",
    )

    with advance_batch_source_for_file_with_provenance(
        "batch",
        "link",
        "old-source",
        BatchOwnership.from_presence_lines([]),
    ) as result:
        assert result.batch_source_commit == "new-source"
        assert result.source_buffer.to_bytes() == b"missing-new-target"

    assert loaded_paths == ["link"]


def test_merge_ignores_boolean_replacement_unit_deletion_indices():
    """JSON booleans should not be accepted as deletion indexes."""
    new = BatchOwnership.from_presence_lines(
        ["1"],
        [
            AbsenceClaim(anchor_line=None, content_lines=[b"old one\n"]),
            AbsenceClaim(anchor_line=None, content_lines=[b"old two\n"]),
        ],
        replacement_units=[
            ReplacementUnit(presence_lines=["1"], deletion_indices=[True]),
        ],
    )

    merged = merge_batch_ownership(
        BatchOwnership.from_presence_lines([], []),
        new,
    )

    assert merged.replacement_units == []


def test_advance_source_preserves_claimed_lines_missing_from_working_tree():
    """Previously discarded claimed lines remain available in refreshed source."""
    old_source = b"owned one\nowned two\nremaining change\n"
    working_tree = b"remaining change\nnew later change\n"
    ownership = BatchOwnership.from_presence_lines(["1-2"], [])

    with _advance_source_from_content(
        old_source_buffer=old_source,
        working_buffer=working_tree,
        ownership=ownership,
    ) as source_with_provenance:
        remapped = remap_batch_ownership_with_lineage(
            ownership,
            source_with_provenance.lineage,
        )

        assert source_with_provenance.source_buffer.to_bytes() == (
            b"owned one\nowned two\nremaining change\nnew later change\n"
        )
    assert remapped.presence_claims[0].source_lines == ["1-2"]


def test_advance_source_tracks_working_line_provenance_for_ambiguous_duplicates():
    """Synthesized source should remember working-line identity."""
    old_source = b"owned before\nsame\nsame\nowned after\n"
    working_tree = b"same\nsame\n"
    ownership = BatchOwnership.from_presence_lines(["1,4"], [])

    with _advance_source_from_content(
        old_source_buffer=old_source,
        working_buffer=working_tree,
        ownership=ownership,
    ) as source_with_provenance:
        assert source_with_provenance.source_buffer.to_bytes() == (
            b"owned before\nowned after\nsame\nsame\n"
        )
        assert tuple(source_with_provenance.lineage.source_runs()) == (
            LineageRun(old_start=1, old_end=1, new_start=1),
            LineageRun(old_start=4, old_end=4, new_start=2),
        )
        assert tuple(source_with_provenance.lineage.working_runs()) == (
            LineageRun(old_start=1, old_end=2, new_start=3),
        )


def test_advance_source_does_not_duplicate_changed_method_signature():
    """Refreshing a fully owned file should not concatenate method versions."""
    old_source = (
        b"class Prompt {\n"
        b"    show({message, type}) {\n"
        b"        return message ?? type;\n"
        b"    }\n"
        b"}\n"
    )
    working_tree = (
        b"class Prompt {\n"
        b"    show(serviceName, message, type) {\n"
        b"        return message ?? type;\n"
        b"    }\n"
        b"}\n"
    )
    ownership = BatchOwnership.from_presence_lines(["1-5"], [])

    with _advance_source_from_content(
        old_source_buffer=old_source,
        working_buffer=working_tree,
        ownership=ownership,
    ) as source_with_provenance:
        assert source_with_provenance.source_buffer.to_bytes() == working_tree


def test_advance_source_does_not_nest_superseded_guard():
    """Refreshing an extended guard should not retain its old first line."""
    old_source = (
        b"function stop(serviceName) {\n"
        b"    if (serviceName !== selectedService)\n"
        b"        return;\n"
        b"}\n"
    )
    working_tree = (
        b"function stop(serviceName) {\n"
        b"    if (serviceName !== selectedService &&\n"
        b"        serviceName !== fingerprintService)\n"
        b"        return;\n"
        b"}\n"
    )
    ownership = BatchOwnership.from_presence_lines(["1-4"], [])

    with _advance_source_from_content(
        old_source_buffer=old_source,
        working_buffer=working_tree,
        ownership=ownership,
    ) as source_with_provenance:
        assert source_with_provenance.source_buffer.to_bytes() == working_tree
        remapped = remap_batch_ownership_with_lineage(
            ownership,
            source_with_provenance.lineage,
        )

    assert remapped.presence_line_set().ranges() == ((1, 5),)


def test_advance_source_does_not_duplicate_changed_return_statement():
    """Refreshing an extended return should keep one executable statement."""
    old_source = (
        b"function canStart(serviceName) {\n"
        b"    return serviceName === selectedService;\n"
        b"}\n"
    )
    working_tree = (
        b"function canStart(serviceName) {\n"
        b"    return serviceName === selectedService ||\n"
        b"        serviceName === fingerprintService;\n"
        b"}\n"
    )
    ownership = BatchOwnership.from_presence_lines(["1-3"], [])

    with _advance_source_from_content(
        old_source_buffer=old_source,
        working_buffer=working_tree,
        ownership=ownership,
    ) as source_with_provenance:
        assert source_with_provenance.source_buffer.to_bytes() == working_tree
        remapped = remap_batch_ownership_with_lineage(
            ownership,
            source_with_provenance.lineage,
        )
        assert tuple(source_with_provenance.lineage.source_expansions()) == (
            SourceSelectionExpansion(
                source_start=2,
                source_end=2,
                new_start=2,
                new_end=3,
            ),
        )

    assert remapped.presence_line_set().ranges() == ((1, 4),)


def test_advance_source_tracks_contiguous_lineage_as_runs():
    """Large contiguous source refreshes should keep one source-line run."""
    source_lines = b"".join(
        f"line {index}\n".encode("utf-8")
        for index in range(1, 1001)
    )
    ownership = BatchOwnership.from_presence_lines(["1-1000"], [])

    with _advance_source_from_content(
        old_source_buffer=source_lines,
        working_buffer=source_lines,
        ownership=ownership,
    ) as source_with_provenance:
        assert tuple(source_with_provenance.lineage.source_runs()) == (
            LineageRun(old_start=1, old_end=1000, new_start=1),
        )
        assert tuple(source_with_provenance.lineage.working_runs()) == (
            LineageRun(old_start=1, old_end=1000, new_start=1),
        )


def test_advance_source_avoids_line_scale_python_heap():
    """Required source coordinates should remain range- and storage-backed."""
    heap_peaks = []
    for line_count in _LINE_SCALE_TEST_COUNTS:
        source_content = b"".join(
            f"line-{line_index:08d}\n".encode()
            for line_index in range(line_count)
        )
        ownership = BatchOwnership.from_presence_lines([f"1-{line_count}"], [])

        with (
            LineBuffer.from_bytes(source_content) as old_lines,
            LineBuffer.from_bytes(source_content) as working_lines,
        ):
            assert len(old_lines) == line_count
            assert len(working_lines) == line_count
            gc.collect()
            tracemalloc.start()
            try:
                with advance_source_lines_preserving_existing_presence(
                    old_lines,
                    working_lines,
                    ownership,
                ) as source_with_provenance:
                    result_byte_count = (
                        source_with_provenance.source_buffer.byte_count
                    )
                _current_heap, peak_heap = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()

        assert result_byte_count == len(source_content)
        heap_peaks.append(peak_heap)

    small_peak, large_peak = heap_peaks
    assert large_peak < small_peak + _LINE_SCALE_HEAP_GROWTH_LIMIT


def test_advance_source_context_closes_lineage():
    """Source advancement context should release lineage resources."""
    ownership = BatchOwnership.from_presence_lines(["1"], [])

    with _advance_source_from_content(
        old_source_buffer=b"owned\n",
        working_buffer=b"owned\n",
        ownership=ownership,
    ) as source_with_provenance:
        lineage = source_with_provenance.lineage
        assert lineage.closed is False

    assert lineage.closed is True
    with pytest.raises(ValueError, match="batch source lineage is closed"):
        lineage.translate_source_line(1)


def test_advance_source_preserves_shared_method_boundary_for_partial_ownership():
    """Refreshing an owned method body must retain its shared closing brace."""
    old_source = (
        b"class S {\n"
        b"    static {\n"
        b"        register();\n"
        b"    }\n"
        b"\n"
        b"    static enabled() {\n"
        b"        return true;\n"
        b"    }\n"
        b"\n"
        b"    constructor() {\n"
        b"    }\n"
        b"}\n"
    )
    working_tree = (
        b"class S {\n"
        b"    static {\n"
        b"        register();\n"
        b"    }\n"
        b"\n"
        b"    constructor() {\n"
        b"    }\n"
        b"}\n"
    )
    ownership = BatchOwnership.from_presence_lines(["2-7"], [])

    with _advance_source_from_content(
        old_source_buffer=old_source,
        working_buffer=working_tree,
        ownership=ownership,
    ) as source_with_provenance:
        assert source_with_provenance.source_buffer.to_bytes() == old_source


@pytest.mark.parametrize(
    ("working_tree", "expected_source", "expected_presence", "working_extra"),
    [
        (
            b"head\nold\nextra\ntail\n",
            b"head\nnew\nextra\ntail\n",
            ((2, 2),),
            (3, 3),
        ),
        (
            b"head\nextra\nold\ntail\n",
            b"head\nextra\nnew\ntail\n",
            ((3, 3),),
            (2, 2),
        ),
    ],
)
def test_advance_source_replaces_suppressed_span_without_losing_live_neighbors(
    working_tree,
    expected_source,
    expected_presence,
    working_extra,
):
    """Saved replacements stay authoritative inside a larger semantic run."""
    ownership = BatchOwnership.from_presence_lines(
        ["2"],
        [AbsenceClaim(anchor_line=1, content_lines=[b"old\n"])],
        replacement_units=[
            ReplacementUnit(presence_lines=["2"], deletion_indices=[0]),
        ],
    )

    with _advance_source_from_content(
        old_source_buffer=b"head\nnew\ntail\n",
        working_buffer=working_tree,
        ownership=ownership,
    ) as source_with_provenance:
        remapped = remap_batch_ownership_with_lineage(
            ownership,
            source_with_provenance.lineage,
        )

        assert source_with_provenance.source_buffer.to_bytes() == expected_source
        assert source_with_provenance.lineage.translate_working_line(
            working_extra[0]
        ) == working_extra[1]

    assert remapped.presence_line_set().ranges() == expected_presence
    assert remapped.replacement_units[0].presence_lines == [
        str(expected_presence[0][0])
    ]


@pytest.mark.parametrize(
    ("working_tree", "expected_source"),
    [
        (
            b"head\nold-a\nold-b\ntail\n",
            b"head\nnew\ntail\n",
        ),
        (
            b"head\nold-a\nlive extra\nold-b\ntail\n",
            b"head\nnew\nlive extra\ntail\n",
        ),
    ],
)
def test_advance_source_replaces_all_suppressed_spans_for_one_unit(
    working_tree,
    expected_source,
):
    """A replacement unit may own several distinct deletion-side runs."""
    ownership = BatchOwnership.from_presence_lines(
        ["2"],
        [
            AbsenceClaim(anchor_line=1, content_lines=[b"old-a\n"]),
            AbsenceClaim(anchor_line=1, content_lines=[b"old-b\n"]),
        ],
        replacement_units=[
            ReplacementUnit(presence_lines=["2"], deletion_indices=[0, 1]),
        ],
    )

    with _advance_source_from_content(
        old_source_buffer=b"head\nnew\ntail\n",
        working_buffer=working_tree,
        ownership=ownership,
    ) as source_with_provenance:
        assert source_with_provenance.source_buffer.to_bytes() == expected_source


def test_advance_source_refuses_ambiguous_saved_replacement_baseline_spans():
    """Repeated live baseline variants must not replace saved ownership."""
    ownership = BatchOwnership.from_presence_lines(
        ["2"],
        [AbsenceClaim(anchor_line=1, content_lines=[b"old\n"])],
        replacement_units=[
            ReplacementUnit(presence_lines=["2"], deletion_indices=[0]),
        ],
    )

    with pytest.raises(
        BatchSourceAdvanceError,
        match="multiple matching live baseline",
    ):
        with _advance_source_from_content(
            old_source_buffer=b"head\nnew\ntail\n",
            working_buffer=b"head\nold\nold\ntail\n",
            ownership=ownership,
        ):
            pass


def test_advance_source_refuses_two_deletions_claiming_same_live_span():
    """Distinct deletion claims cannot both consume one baseline occurrence."""
    ownership = BatchOwnership.from_presence_lines(
        ["2"],
        [
            AbsenceClaim(anchor_line=1, content_lines=[b"old\n"]),
            AbsenceClaim(anchor_line=1, content_lines=[b"old\n"]),
        ],
        replacement_units=[
            ReplacementUnit(presence_lines=["2"], deletion_indices=[0, 1]),
        ],
    )

    with pytest.raises(
        BatchSourceAdvanceError,
        match="overlapping live baseline spans",
    ):
        with _advance_source_from_content(
            old_source_buffer=b"head\nnew\ntail\n",
            working_buffer=b"head\nold\ntail\n",
            ownership=ownership,
        ):
            pass


def test_ambiguous_replacement_span_scan_avoids_line_scale_python_heap():
    """Repeated baseline matches should be refused with bounded scan state."""
    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        [AbsenceClaim(anchor_line=None, content_lines=[b"old\n"])],
        replacement_units=[
            ReplacementUnit(presence_lines=["1"], deletion_indices=[0]),
        ],
    )
    heap_peaks = []
    for line_count in _LINE_SCALE_TEST_COUNTS:
        working_lines = [b"old\n"] * line_count
        run = SemanticChangeRun(
            SemanticChangeKind.REPLACEMENT,
            source_start=1,
            source_end=1,
            target_start=1,
            target_end=line_count,
        )

        gc.collect()
        tracemalloc.start()
        try:
            with (
                MatcherWorkspace() as workspace,
                pytest.raises(
                    BatchSourceAdvanceError,
                    match="multiple matching live baseline",
                ),
            ):
                advancement_module._saved_replacement_target_spans(
                    run,
                    working_lines,
                    ownership,
                    workspace,
                )
            _current_heap, peak_heap = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        heap_peaks.append(peak_heap)

    small_peak, large_peak = heap_peaks
    assert large_peak < small_peak + _LINE_SCALE_HEAP_GROWTH_LIMIT


def test_advance_source_refuses_owned_replacement_contraction() -> None:
    """A contracted owned range cannot retain unique source-line lineage."""
    ownership = BatchOwnership.from_presence_lines(["2-3"])

    with pytest.raises(
        BatchSourceAdvanceError,
        match="contracts multiple owned lines",
    ):
        with _advance_source_from_content(
            old_source_buffer=b"head\none\ntwo\ntail\n",
            working_buffer=b"head\ncombined\ntail\n",
            ownership=ownership,
        ):
            pass


def test_advance_source_lines_accepts_non_list_line_sequences(line_sequence):
    """Source construction accepts indexed line sequences."""
    old_lines = line_sequence([
        b"owned before\n",
        b"same\n",
        b"same\n",
        b"owned after\n",
    ])
    working_lines = line_sequence([b"same\n", b"same\n"])
    ownership = BatchOwnership.from_presence_lines(["1,4"], [])

    with advance_source_lines_preserving_existing_presence(
        old_lines=old_lines,
        working_lines=working_lines,
        ownership=ownership,
    ) as source_with_provenance:
        assert source_with_provenance.source_buffer.to_bytes() == (
            b"owned before\nowned after\nsame\nsame\n"
        )
        assert tuple(source_with_provenance.lineage.source_runs()) == (
            LineageRun(old_start=1, old_end=1, new_start=1),
            LineageRun(old_start=4, old_end=4, new_start=2),
        )
        assert tuple(source_with_provenance.lineage.working_runs()) == (
            LineageRun(old_start=1, old_end=2, new_start=3),
        )
