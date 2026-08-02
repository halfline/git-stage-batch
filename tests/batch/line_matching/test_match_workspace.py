"""Tests for batch matcher workspace storage ownership."""

from __future__ import annotations

import gc
import tracemalloc

import pytest

import git_stage_batch.batch.line_matching.match as match_module
import git_stage_batch.core.mapped_storage as mapped_storage_module
from git_stage_batch.batch.line_matching.match import match_lines
from git_stage_batch.batch.line_matching.match_workspace import MatcherWorkspace
from git_stage_batch.core.mapped_storage import MAPPED_STORAGE_OFFLOAD_SIZE_THRESHOLD


_LINE_SCALE_HEAP_LIMIT = 256 * 1024


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


@pytest.mark.parametrize("target_stride", [1, 9])
def test_lis_target_ranking_does_not_use_line_scale_python_heap(target_stride):
    """LIS target ranks should stay in mapped storage for large alignments."""
    line_count = 8192
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

    assert peak_heap < _LINE_SCALE_HEAP_LIMIT


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
