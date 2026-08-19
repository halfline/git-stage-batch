"""Tests for batch ownership update preparation."""

from __future__ import annotations

import gc
import inspect
import tracemalloc

import git_stage_batch.batch.ownership_update as ownership_update_module
import git_stage_batch.batch.source.refresh as source_refresh
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.file_state import (
    BatchMetadataRevision,
    SourceBoundOwnership,
)
from git_stage_batch.batch.ownership_update import (
    PreparedBatchUpdate,
    acquire_batch_ownership_update_for_selection,
)
from git_stage_batch.commands.selection import (
    selected_change_batch_discarding,
    selected_change_batch_staging,
)
from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.core.coordinates import BatchSourceSpace, content_snapshot
from git_stage_batch.core.models import LineEntry


def test_prepared_batch_update_dataclass():
    """Test PreparedBatchUpdate dataclass construction."""
    ownership = BatchOwnership.from_presence_lines(["1-3"], [])
    source = [b"one\n", b"two\n", b"three\n"]

    update = PreparedBatchUpdate(
        batch_source_commit="def456",
        bound_ownership=SourceBoundOwnership(
            content_snapshot(
                "test.py",
                source,
                space=BatchSourceSpace,
            ),
            ownership,
        ),
        expected_metadata_revision=BatchMetadataRevision("metadata-1"),
    )

    assert update.batch_source_commit == "def456"
    assert update.bound_ownership.value == ownership


def test_refreshed_selected_overlay_does_not_copy_unselected_hunk() -> None:
    """Refreshing one selection retains no second Python reference per row."""
    line_count = 32768
    hunk_lines = [
        LineEntry(None, " ", index, index, text_bytes=b"context")
        for index in range(1, line_count + 1)
    ]
    original = LineEntry(1, "+", None, line_count + 1, text_bytes=b"selected")
    hunk_lines.append(original)
    refreshed = original.with_source_line(7)

    gc.collect()
    tracemalloc.start()
    try:
        with ownership_update_module._RefreshedSelectedLineOverlay(
            hunk_lines,
            [refreshed],
        ) as overlay:
            assert overlay[0] is hunk_lines[0]
            assert overlay[-1] is refreshed
            assert overlay[::-1][0] is refreshed
            assert overlay[::-1][1] is hunk_lines[-2]
            retained, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert retained < 64 * 1024
    assert peak < 128 * 1024


def test_acquire_batch_ownership_update_uses_metadata_acquisition(monkeypatch):
    """Prepared updates can borrow ownership from metadata while open."""
    existing = BatchOwnership.from_presence_lines(["1"], [])
    entered = False
    exited = False

    class OwnershipContext:
        def __enter__(self):
            nonlocal entered
            entered = True
            return existing

        def __exit__(self, exc_type, exc, traceback):
            nonlocal exited
            exited = True

    def acquire_for_metadata_dict(metadata):
        assert metadata == {"batch_source_commit": "source123"}
        return OwnershipContext()

    monkeypatch.setattr(
        ownership_update_module,
        "acquire_ownership_for_metadata_dict",
        acquire_for_metadata_dict,
    )
    monkeypatch.setattr(
        source_refresh,
        "read_git_object_buffer_or_none",
        lambda _object_name: LineBuffer.from_bytes(b"line1\nline2\n"),
    )
    monkeypatch.setattr(
        source_refresh,
        "load_working_tree_file_as_buffer",
        lambda _file_path: LineBuffer.from_bytes(b"line1\nline2\n"),
    )
    cached_sources = {}
    monkeypatch.setattr(
        source_refresh,
        "load_session_batch_sources",
        lambda: dict(cached_sources),
    )
    monkeypatch.setattr(
        source_refresh,
        "save_session_batch_sources",
        lambda sources: cached_sources.update(sources),
    )
    lines = [
        LineEntry(
            id=2,
            kind="+",
            old_line_number=None,
            new_line_number=2,
            text_bytes=b"line2",
            text="line2",
            source_line=2,
        ),
    ]

    with acquire_batch_ownership_update_for_selection(
        batch_name="test-batch",
        file_path="test.py",
        file_metadata={"batch_source_commit": "source123"},
        metadata_revision=BatchMetadataRevision("metadata-1"),
        selected_lines=lines,
    ) as result:
        assert entered is True
        assert exited is False
        assert result.batch_source_commit == "source123"
        assert result.bound_ownership.value.presence_line_set() == {1, 2}

    assert exited is True
    assert cached_sources == {"test.py": "source123"}


def test_both_commands_use_same_helper_interface():
    """Selected-change include and discard use acquired update preparation."""
    include_source = inspect.getsource(selected_change_batch_staging)
    discard_source = inspect.getsource(selected_change_batch_discarding)

    assert (
        "from ...batch.ownership_update import "
        "acquire_batch_ownership_update_for_selection"
    ) in include_source
    assert (
        "from ...batch.ownership_update import "
        "acquire_batch_ownership_update_for_selection"
    ) in discard_source
    assert "acquire_batch_ownership_update_for_selection(" in include_source
    assert "acquire_batch_ownership_update_for_selection(" in discard_source
