"""Tests for selected hunk filtering."""

from __future__ import annotations

import subprocess

import pytest

import git_stage_batch.data.selected_change.hunk_filtering as hunk_filtering_module
from git_stage_batch.commands.start import command_start
from git_stage_batch.data.hunk_tracking import fetch_next_change
from git_stage_batch.data.line_id_files import write_line_ids_file
from git_stage_batch.data.selected_change.hunk_filtering import (
    apply_line_level_batch_filter_to_cached_hunk,
    filter_line_level_change_with_attribution,
)
from git_stage_batch.batch.attribution import FileAttribution
from git_stage_batch.core.models import HunkHeader, LineEntry, LineLevelChange
from git_stage_batch.utils.paths import (
    ensure_state_directory_exists,
    get_processed_batch_ids_file_path,
)


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        check=True,
        cwd=repo,
        capture_output=True,
    )

    (repo / "README.md").write_text("# Test\n")
    subprocess.run(
        ["git", "add", "README.md"], check=True, cwd=repo, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        check=True,
        cwd=repo,
        capture_output=True,
    )

    ensure_state_directory_exists()

    return repo


def test_apply_line_level_batch_filter_returns_false_without_batched_ids(
    temp_git_repo,
    monkeypatch,
):
    test_file = temp_git_repo / "test.txt"
    test_file.write_text("line1\nline2\nline3\n")
    subprocess.run(
        ["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "Add file"],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
    )

    test_file.write_text("changed1\nchanged2\nchanged3\n")

    command_start()
    fetch_next_change()
    write_line_ids_file(get_processed_batch_ids_file_path(), set())
    journal_entries = []
    monkeypatch.setattr(
        hunk_filtering_module,
        "log_journal",
        lambda operation, **fields: journal_entries.append((operation, fields)),
    )

    assert apply_line_level_batch_filter_to_cached_hunk() is False
    assert journal_entries == [
        (
            "file_attribution_complete",
            {
                "file_path": "test.txt",
                "candidate_batches": 0,
                "claimed_batches": 0,
                "object_resolution_requests": 0,
                "object_requests": 0,
                "object_bytes": 0,
                "unique_source_contents": 0,
                "mapping_computations": 0,
                "deletion_fingerprints": 0,
                "attributed_units": 1,
            },
        )
    ]


def test_apply_line_level_batch_filter_returns_true_without_cached_hunk(
    temp_git_repo,
):
    assert apply_line_level_batch_filter_to_cached_hunk() is True


def test_explicit_attribution_filter_is_io_free(monkeypatch):
    """Prepared filtering should consume only caller-supplied resources."""
    line_changes = LineLevelChange(
        path="file.txt",
        header=HunkHeader(old_start=1, old_len=1, new_start=1, new_len=1),
        lines=[
            LineEntry(
                id=1,
                kind="+",
                old_line_number=None,
                new_line_number=1,
                text_bytes=b"new\n",
            )
        ],
    )
    monkeypatch.setattr(
        hunk_filtering_module,
        "filter_owned_diff_fragments",
        lambda changes, _attribution: (False, changes),
    )
    monkeypatch.setattr(
        hunk_filtering_module,
        "read_consumed_file_metadata",
        lambda _path: (_ for _ in ()).throw(AssertionError("unexpected session read")),
    )
    monkeypatch.setattr(
        hunk_filtering_module,
        "log_journal",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unexpected journal write")
        ),
    )

    filtered = filter_line_level_change_with_attribution(
        line_changes,
        attribution=FileAttribution(file_path="file.txt", units=[]),
        batch_metadata_by_name={},
        consumed_file_metadata=None,
    )

    assert filtered is line_changes
