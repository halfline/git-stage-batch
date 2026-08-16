"""Tests for batch matcher workspace storage ownership."""

from __future__ import annotations

import gc
import tracemalloc

import pytest

import git_stage_batch.batch.line_matching.line_mapping as line_mapping_module
import git_stage_batch.batch.line_matching.match as match_module
import git_stage_batch.core.mapped_storage as mapped_storage_module
from git_stage_batch.batch.line_matching.match import match_lines
from git_stage_batch.batch.line_matching.line_mapping import LineMapping
from git_stage_batch.batch.line_matching.match_workspace import MatcherWorkspace
from git_stage_batch.batch.line_matching.occurrence_index import (
    LinePayloadOccurrenceIndex,
)
from git_stage_batch.core.mapped_storage import MAPPED_STORAGE_OFFLOAD_SIZE_THRESHOLD


_LINE_SCALE_TEST_COUNTS = (1024, 8192)
_LINE_SCALE_HEAP_GROWTH_LIMIT = 32 * 1024


def test_matcher_workspace_tracks_and_closes_resources():
    """Matcher workspaces close all vectors they allocate."""
    workspace = MatcherWorkspace()
    vector = workspace.int_vector(2, width=4, fill=1)
    records = workspace.record_vector(1, "QQ")
    records.append((2, 3))

    assert workspace._current_bytes == vector.byte_count + records.byte_count
    assert workspace._high_water_bytes == workspace._current_bytes

    workspace.close_resource(vector)
    assert vector.closed
    assert workspace._current_bytes == records.byte_count

    workspace.close()
    assert records.closed
    assert workspace._current_bytes == 0


def test_occurrence_index_sizes_and_releases_scoped_storage():
    """A parent-range index should not reserve storage for the whole file."""
    lines = [b"outside\n"] * 10_000
    workspace = MatcherWorkspace()

    occurrence_index = LinePayloadOccurrenceIndex(
        workspace,
        lines,
        target_indexes=range(4000, 4010),
    )

    assert occurrence_index._contents.capacity == 10
    assert occurrence_index._positions.capacity == 10
    assert workspace._current_bytes < 2048
    occurrence_index.close()
    assert workspace._current_bytes == 0
    assert occurrence_index._contents.closed
    assert occurrence_index._positions.closed

    workspace.close()


def test_match_lines_routes_mapped_storage_to_requested_spool(
    tmp_path,
    monkeypatch,
):
    """Mappings and matcher scratch should stay in invocation-owned storage."""
    temporary_directories = []
    real_temporary_file = mapped_storage_module._temporary_file

    def recording_temporary_file(spool_dir=None):
        temporary_directories.append(spool_dir)
        return real_temporary_file(spool_dir)

    monkeypatch.setattr(
        mapped_storage_module,
        "_temporary_file",
        recording_temporary_file,
    )
    spool_dir = tmp_path / "scratch"
    spool_dir.mkdir()
    line_count = MAPPED_STORAGE_OFFLOAD_SIZE_THRESHOLD // 4 + 1
    lines = [f"line {index}\n".encode() for index in range(line_count)]

    with match_lines(lines, lines, spool_dir=spool_dir) as mapping:
        assert len(tuple(mapping.mapped_line_pairs())) == len(lines)

    assert temporary_directories
    assert all(
        directory is not None
        and directory.resolve() == spool_dir.resolve()
        for directory in temporary_directories
    )


def test_match_lines_closes_first_mapping_if_second_allocation_fails(
    monkeypatch,
):
    """Partial mapping allocation should not leak the first mapped vector."""
    class ClosingVector(list):
        closed = False

        def close(self):
            self.closed = True

    source_mapping = ClosingVector([0])
    calls = 0

    def allocate_mapping(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return source_mapping
        raise KeyboardInterrupt

    monkeypatch.setattr(
        match_module,
        "_new_line_mapping",
        allocate_mapping,
    )

    with pytest.raises(KeyboardInterrupt):
        match_lines([b"line\n"], [b"line\n"])

    assert source_mapping.closed is True


def test_allocate_line_mapping_preserves_allocation_error_during_cleanup(
    monkeypatch,
):
    """Partial direct allocation closes its vector without masking cancellation."""

    class CancellingVector(list):
        def __init__(self):
            super().__init__([0])
            self.close_calls = 0

        def close(self):
            self.close_calls += 1
            raise RuntimeError("close failed")

    source_mapping = CancellingVector()
    calls = 0

    def allocate_vector(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return source_mapping
        raise KeyboardInterrupt

    monkeypatch.setattr(
        line_mapping_module,
        "allocate_mapping_vector",
        allocate_vector,
    )

    with pytest.raises(KeyboardInterrupt):
        line_mapping_module.allocate_line_mapping(1, 1)

    assert source_mapping.close_calls == 1


def test_line_mapping_close_attempts_both_vectors_before_raising():
    """A close failure in one mapping vector cannot strand its sibling."""

    class ClosingVector(list):
        def __init__(self, *, cancel_close=False):
            super().__init__([0])
            self.cancel_close = cancel_close
            self.close_calls = 0

        def close(self):
            self.close_calls += 1
            if self.cancel_close:
                raise KeyboardInterrupt

    source_mapping = ClosingVector(cancel_close=True)
    target_mapping = ClosingVector()
    mapping = LineMapping(source_mapping, target_mapping)

    with pytest.raises(KeyboardInterrupt):
        mapping.close()

    assert source_mapping.close_calls == 1
    assert target_mapping.close_calls == 1


def test_match_lines_attempts_every_mapping_close_after_cancellation(monkeypatch):
    """A failing partial-allocation close cannot strand the other vector."""

    class ClosingVector(list):
        def __init__(self, *, cancel_close=False):
            super().__init__([0])
            self.cancel_close = cancel_close
            self.close_calls = 0

        def close(self):
            self.close_calls += 1
            if self.cancel_close:
                raise KeyboardInterrupt

    source_mapping = ClosingVector(cancel_close=True)
    target_mapping = ClosingVector()
    mappings = iter((source_mapping, target_mapping))

    monkeypatch.setattr(
        match_module,
        "_new_line_mapping",
        lambda *_args, **_kwargs: next(mappings),
    )
    monkeypatch.setattr(
        match_module,
        "_align_segments_around_anchors",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cancel")),
    )

    with pytest.raises(RuntimeError, match="cancel"):
        match_lines([b"line\n"], [b"line\n"])

    assert source_mapping.close_calls == 1
    assert target_mapping.close_calls == 1


@pytest.mark.parametrize("target_stride", [1, 9])
def test_lis_target_ranking_does_not_use_line_scale_python_heap(target_stride):
    """LIS target ranks should stay in mapped storage for large alignments."""
    heap_peaks = []
    for line_count in _LINE_SCALE_TEST_COUNTS:
        target_end = line_count * target_stride

        with MatcherWorkspace() as workspace:
            pairs = workspace.record_vector(line_count, "QQ")
            for source_index in range(line_count):
                pairs.append(
                    (
                        source_index,
                        (line_count - source_index - 1) * target_stride,
                    )
                )

            gc.collect()
            tracemalloc.start()
            try:
                anchors = match_module._longest_increasing_subsequence_records(
                    pairs,
                    0,
                    target_end,
                    workspace,
                )
                _current_heap, peak_heap = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()

            try:
                assert tuple(anchors) == (
                    (0, (line_count - 1) * target_stride),
                )
            finally:
                workspace.close_resource(anchors)

        heap_peaks.append(peak_heap)

    small_peak, large_peak = heap_peaks
    assert large_peak < small_peak + _LINE_SCALE_HEAP_GROWTH_LIMIT


def test_dense_lis_target_ranking_does_not_sort_candidates(monkeypatch):
    """Dense target coordinates should use the direct mapped rank index."""
    line_count = 128

    def reject_sort(_records):
        raise AssertionError("dense target ranks should not be sorted")

    monkeypatch.setattr(match_module, "sort_mapped_records", reject_sort)

    with MatcherWorkspace() as workspace:
        pairs = workspace.record_vector(line_count, "QQ")
        for source_index in range(line_count):
            pairs.append((source_index, line_count - source_index - 1))

        anchors = match_module._longest_increasing_subsequence_records(
            pairs,
            0,
            line_count,
            workspace,
        )
        try:
            assert tuple(anchors) == ((0, line_count - 1),)
        finally:
            workspace.close_resource(anchors)
