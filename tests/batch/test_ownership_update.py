"""Tests for batch ownership update preparation."""

from __future__ import annotations

import inspect

import git_stage_batch.batch.ownership_update as ownership_update_module
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.ownership_update import (
    PreparedBatchUpdate,
    acquire_batch_ownership_update_for_selection,
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
        ownership_after=ownership
    )

    assert update.batch_source_commit == "def456"
    assert update.ownership_after == ownership


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
