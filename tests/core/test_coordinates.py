"""Tests for snapshot-bound coordinate primitives."""

from __future__ import annotations

import gc
import tracemalloc

import pytest

from git_stage_batch.core.coordinates import (
    BaselineSpace,
    BatchSourceSpace,
    DisplayLineId,
    FileSnapshot,
    HalfOpenRanges,
    LineBoundary,
    LineSpan,
    SnapshotBoundary,
    SnapshotIdentity,
    SnapshotSpans,
    content_snapshot,
    require_same_snapshot,
)


def test_content_snapshot_frames_each_line() -> None:
    first = content_snapshot("file.txt", (b"a", b"bc"), space=BaselineSpace)
    second = content_snapshot("file.txt", (b"ab", b"c"), space=BaselineSpace)

    assert first.identity != second.identity


def test_content_snapshot_heap_does_not_scale_with_file_lines() -> None:
    peaks: list[int] = []
    for line_count in (1024, 32768):
        lines = (b"same\n",) * line_count
        gc.collect()
        tracemalloc.start()
        try:
            snapshot = content_snapshot(
                "file.txt",
                lines,
                space=BaselineSpace,
            )
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert snapshot.line_count == line_count
        peaks.append(peak)

    assert peaks[1] < peaks[0] + 8 * 1024


def _snapshot(space, value: str, line_count: int = 5):
    return FileSnapshot(
        "file.txt",
        SnapshotIdentity("git-tree-path", value),
        line_count,
        space,
    )


def test_snapshot_mismatch_rejects_equal_numeric_coordinates():
    """Equal offsets from different snapshots are not interchangeable."""
    old = _snapshot(BaselineSpace, "old:file.txt")
    new = _snapshot(BaselineSpace, "new:file.txt")

    with pytest.raises(ValueError, match="do not match"):
        require_same_snapshot(old, new)


def test_snapshot_mismatch_rejects_another_coordinate_role():
    """Runtime identity retains the role erased from Python generics."""
    baseline = _snapshot(BaselineSpace, "same:file.txt")
    source = _snapshot(BatchSourceSpace, "same:file.txt")

    with pytest.raises(ValueError, match="do not match"):
        require_same_snapshot(baseline, source)


def test_snapshot_mismatch_rejects_another_declared_extent():
    """An opaque identity cannot authorize contradictory file geometry."""
    first = _snapshot(BaselineSpace, "same:file.txt", line_count=1)
    second = _snapshot(BaselineSpace, "same:file.txt", line_count=99)

    with pytest.raises(ValueError, match="do not match"):
        require_same_snapshot(first, second)



def test_snapshot_primitives_reject_runtime_type_forgery() -> None:
    """Python's bool/int overlap cannot forge domain coordinates or extents."""
    identity = SnapshotIdentity("test", "content")

    with pytest.raises(ValueError, match="line count"):
        FileSnapshot("file.txt", identity, True, BaselineSpace)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="boundary"):
        LineBoundary(False)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="boundaries"):
        HalfOpenRanges(((False, 1),))
