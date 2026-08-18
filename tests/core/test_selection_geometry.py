"""Tests for one-time semantic selection resolution."""

from __future__ import annotations

import gc
import tracemalloc

import pytest

from git_stage_batch.core.coordinates import (
    DiffNewSpace,
    DiffOldSpace,
    DisplayLineId,
    FileSnapshot,
    SnapshotIdentity,
)
from git_stage_batch.core.models import HunkHeader, LineEntry, LineLevelChange
from git_stage_batch.core.selection_geometry import (
    DisplayIdRanges,
    DiffViewIdentity,
    ExactContentWitness,
    diff_view_identity,
)


def _view(
    changes: LineLevelChange,
    old_identity: str = "old",
    new_identity: str = "new",
):
    return diff_view_identity(
        changes,
        old_snapshot=FileSnapshot(
            "file.txt", SnapshotIdentity("test", old_identity), 3, DiffOldSpace
        ),
        new_snapshot=FileSnapshot(
            "file.txt", SnapshotIdentity("test", new_identity), 3, DiffNewSpace
        ),
    )


def test_unordered_display_ids_sort_in_mapped_storage_with_bounded_heap() -> None:
    """A large set is compacted without a second line-scale Python container."""
    values = set(range(1, 32769))

    gc.collect()
    tracemalloc.start()
    try:
        ranges = DisplayIdRanges.from_unordered_values(values)
        retained, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert ranges.ranges() == ((1, 32768),)
    assert retained < 64 * 1024
    assert peak < 128 * 1024


def test_generic_unordered_display_id_stream_has_bounded_heap() -> None:
    """The public iterable adapter does not materialize one object per ID."""
    peaks: list[int] = []
    for line_count in (1024, 32768):
        gc.collect()
        tracemalloc.start()
        try:
            ranges = DisplayIdRanges.from_ids(
                DisplayLineId(value)
                for value in range(line_count, 0, -1)
            )
            _retained, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert ranges.ranges() == ((1, line_count),)
        peaks.append(peak)

    # Chunk bookkeeping grows logarithmically; the prior list/sort shape grew
    # by several MiB for the larger stream.
    assert peaks[1] < peaks[0] + 64 * 1024


def test_rendered_view_identity_includes_endpoint_extents() -> None:
    """A forged extent cannot reuse authority from the same opaque identity."""
    changes = LineLevelChange(
        "file.txt",
        HunkHeader(0, 0, 1, 1),
        [LineEntry(1, "+", None, 1, text_bytes=b"same\n")],
    )
    old_snapshot = FileSnapshot(
        "file.txt",
        SnapshotIdentity("test", "old"),
        0,
        DiffOldSpace,
    )
    first = diff_view_identity(
        changes,
        old_snapshot=old_snapshot,
        new_snapshot=FileSnapshot(
            "file.txt",
            SnapshotIdentity("test", "same-new-identity"),
            1,
            DiffNewSpace,
        ),
    )
    forged = diff_view_identity(
        changes,
        old_snapshot=old_snapshot,
        new_snapshot=FileSnapshot(
            "file.txt",
            SnapshotIdentity("test", "same-new-identity"),
            2,
            DiffNewSpace,
        ),
    )

    assert first.renderer_identity != forged.renderer_identity
