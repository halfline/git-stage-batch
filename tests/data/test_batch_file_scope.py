"""Tests for session-aware batch file scope resolution."""

from unittest.mock import Mock

import pytest

from git_stage_batch.data import batch_file_scope
from git_stage_batch.data.file_review.records import ReviewSource
from git_stage_batch.data.selected_change.store import SelectedChangeKind
from git_stage_batch.exceptions import CommandError


def test_selected_batch_file_matches_its_review_source(monkeypatch):
    monkeypatch.setattr(
        batch_file_scope,
        "read_selected_change_kind",
        lambda: SelectedChangeKind.BATCH_FILE,
    )
    monkeypatch.setattr(
        batch_file_scope,
        "read_last_file_review_state",
        lambda: Mock(source=ReviewSource.BATCH, batch_name="selected"),
    )

    assert batch_file_scope.selected_batch_change_matches_batch("selected")
    assert not batch_file_scope.selected_batch_change_matches_batch("other")


def test_selected_batch_mode_matches_its_persisted_source(monkeypatch):
    monkeypatch.setattr(
        batch_file_scope,
        "read_selected_change_kind",
        lambda: SelectedChangeKind.BATCH_MODE,
    )
    monkeypatch.setattr(
        batch_file_scope,
        "read_selected_mode_data",
        lambda: {"batch_name": "selected"},
    )

    assert batch_file_scope.selected_batch_change_matches_batch("selected")
    assert not batch_file_scope.selected_batch_change_matches_batch("other")


def test_live_selection_is_available_as_a_batch_path(monkeypatch):
    monkeypatch.setattr(
        batch_file_scope,
        "read_selected_change_kind",
        lambda: SelectedChangeKind.HUNK,
    )

    assert batch_file_scope.selected_batch_change_matches_batch("batch")


def test_pathless_batch_scope_refuses_selection_from_other_batch(monkeypatch):
    selected_path = Mock(return_value="selected.txt")
    monkeypatch.setattr(
        batch_file_scope,
        "selected_batch_change_matches_batch",
        lambda batch_name: False,
    )
    monkeypatch.setattr(
        batch_file_scope,
        "get_selected_change_file_path",
        selected_path,
    )

    with pytest.raises(CommandError, match="came from a different batch"):
        batch_file_scope.resolve_batch_file_scope(
            "requested",
            {"selected.txt": {}},
            file="",
        )

    selected_path.assert_not_called()


def test_pathless_batch_scope_uses_matching_selected_path(monkeypatch):
    monkeypatch.setattr(
        batch_file_scope,
        "selected_batch_change_matches_batch",
        lambda batch_name: True,
    )
    monkeypatch.setattr(
        batch_file_scope,
        "get_selected_change_file_path",
        lambda: "selected.txt",
    )

    resolved = batch_file_scope.resolve_batch_file_scope(
        "requested",
        {"selected.txt": {}, "other.txt": {}},
        file="",
    )

    assert list(resolved) == ["selected.txt"]


def test_resolved_batch_file_paths_remain_literal_and_ordered():
    """Pre-resolved paths must not be reinterpreted as ignore patterns."""
    all_files = {
        "literal[1].txt": {},
        "literal1.txt": {},
        "other.txt": {},
    }

    resolved = batch_file_scope.resolve_batch_file_scope(
        "requested",
        all_files,
        resolved_file_paths=("other.txt", "literal[1].txt", "other.txt"),
    )

    assert list(resolved) == ["other.txt", "literal[1].txt"]
