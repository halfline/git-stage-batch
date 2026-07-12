"""Tests for batch ownership update preparation."""

from __future__ import annotations

import inspect

import git_stage_batch.batch.ownership_update as ownership_update_module
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.ownership_update import (
    PreparedBatchUpdate,
    acquire_batch_ownership_update_for_selection,
    prepare_batch_ownership_update_for_selection,
)
from git_stage_batch.commands.selection import (
    selected_change_batch_discarding,
    selected_change_batch_staging,
)
from git_stage_batch.core.models import LineEntry


def test_prepared_batch_update_dataclass():
    """Test PreparedBatchUpdate dataclass construction."""
    ownership = BatchOwnership.from_presence_lines(["1-3"], [])

    update = PreparedBatchUpdate(
        batch_source_commit="def456",
        ownership_before=None,
        ownership_after=ownership
    )

    assert update.batch_source_commit == "def456"
    assert update.ownership_before is None
    assert update.ownership_after == ownership


def test_prepare_batch_ownership_update_first_time_stale_blank_context():
    """First-time stale selections re-annotate blank context before translation."""
    lines = [
        LineEntry(
            id=None, kind=' ', old_line_number=1, new_line_number=1,
            text_bytes=b"", text="", source_line=None
        ),
        LineEntry(
            id=1, kind='+', old_line_number=None, new_line_number=2,
            text_bytes=b"new line", text="new line", source_line=None
        ),
    ]

    result = prepare_batch_ownership_update_for_selection(
        batch_name="test-batch",
        file_path="test.py",
        current_batch_source_commit=None,
        existing_ownership=None,
        selected_lines=lines
    )

    assert result.batch_source_commit is None
    assert result.ownership_before is None
    assert result.ownership_after.presence_claims[0].source_lines == ["1-2"]


def test_prepare_batch_ownership_update_first_time_deletion_anchor():
    """First-time deletion-only selections keep their source anchor."""
    lines = [
        LineEntry(
            id=1, kind='-', old_line_number=2, new_line_number=None,
            text_bytes=b"old line", text="old line", source_line=None
        ),
    ]

    result = prepare_batch_ownership_update_for_selection(
        batch_name="test-batch",
        file_path="test.py",
        current_batch_source_commit=None,
        existing_ownership=None,
        selected_lines=lines
    )

    assert result.batch_source_commit is None
    assert result.ownership_before is None
    assert result.ownership_after.deletions[0].anchor_line == 1


def test_prepare_batch_ownership_update_first_time():
    """Test prepare_batch_ownership_update_for_selection for first-time add."""
    lines = [
        LineEntry(
            id=1, kind='+', old_line_number=None, new_line_number=1,
            text_bytes=b"line1\n", text="line1\n", source_line=1
        ),
        LineEntry(
            id=2, kind='+', old_line_number=None, new_line_number=2,
            text_bytes=b"line2\n", text="line2\n", source_line=2
        ),
    ]

    result = prepare_batch_ownership_update_for_selection(
        batch_name="test-batch",
        file_path="test.py",
        current_batch_source_commit=None,
        existing_ownership=None,
        selected_lines=lines
    )

    assert result.batch_source_commit is None
    assert result.ownership_before is None
    assert result.ownership_after is not None
    assert result.ownership_after.presence_claims[0].source_lines == ["1-2"]


def test_prepare_batch_ownership_update_with_existing():
    """Test prepare_batch_ownership_update_for_selection with existing ownership."""
    existing = BatchOwnership.from_presence_lines(["1-2"], [])

    lines = [
        LineEntry(
            id=3, kind='+', old_line_number=None, new_line_number=3,
            text_bytes=b"line3\n", text="line3\n", source_line=3
        ),
        LineEntry(
            id=4, kind='+', old_line_number=None, new_line_number=4,
            text_bytes=b"line4\n", text="line4\n", source_line=4
        ),
    ]

    result = prepare_batch_ownership_update_for_selection(
        batch_name="test-batch",
        file_path="test.py",
        current_batch_source_commit="source123",
        existing_ownership=existing,
        selected_lines=lines
    )

    assert result.ownership_before == existing
    assert result.ownership_after is not None
    assert "1-4" in ",".join(result.ownership_after.presence_claims[0].source_lines)


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
    lines = [
        LineEntry(
            id=2,
            kind="+",
            old_line_number=None,
            new_line_number=2,
            text_bytes=b"line2\n",
            text="line2\n",
            source_line=2,
        ),
    ]

    with acquire_batch_ownership_update_for_selection(
        batch_name="test-batch",
        file_path="test.py",
        file_metadata={"batch_source_commit": "source123"},
        selected_lines=lines,
    ) as result:
        assert entered is True
        assert exited is False
        assert result.batch_source_commit == "source123"
        assert result.ownership_before is existing
        assert result.ownership_after.presence_line_set() == {1, 2}

    assert exited is True


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
    assert "prepare_batch_ownership_update_for_selection(" not in include_source
    assert "prepare_batch_ownership_update_for_selection(" not in discard_source
