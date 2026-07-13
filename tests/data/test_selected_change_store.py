"""Tests for selected-change state persistence."""

import subprocess

import pytest

from git_stage_batch.core.models import HunkHeader, LineEntry, LineLevelChange
from git_stage_batch.data.line_state import load_line_changes_from_state
from git_stage_batch.data.selected_change.store import (
    SelectedChangeKind,
    cache_hunk_change,
    read_selected_change_kind,
    restore_selected_change_state,
    snapshot_selected_change_state,
)
from git_stage_batch.utils.paths import (
    ensure_state_directory_exists,
    get_index_snapshot_file_path,
    get_line_changes_json_file_path,
    get_selected_hunk_hash_file_path,
    get_selected_hunk_patch_file_path,
    get_snapshot_metadata_file_path,
    get_working_tree_snapshot_file_path,
)


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository."""
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

    test_file = repo / "test.py"
    test_file.write_text("old\n")
    subprocess.run(["git", "add", "test.py"], check=True, cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add test file"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    test_file.write_text("new\n")

    ensure_state_directory_exists()

    return repo


def test_cache_hunk_change_writes_selected_hunk_state(temp_git_repo):
    patch_lines = [
        b"--- a/test.py\n",
        b"+++ b/test.py\n",
        b"@@ -1,1 +1,1 @@\n",
        b"-old\n",
        b"+new\n",
    ]
    line_changes = LineLevelChange(
        path="test.py",
        header=HunkHeader(old_start=1, old_len=1, new_start=1, new_len=1),
        lines=[
            LineEntry(
                id=1,
                kind="-",
                old_line_number=1,
                new_line_number=None,
                text_bytes=b"old",
            ),
            LineEntry(
                id=2,
                kind="+",
                old_line_number=None,
                new_line_number=1,
                text_bytes=b"new",
            ),
        ],
    )

    cache_hunk_change(patch_lines, "stable-hash", line_changes)

    assert get_selected_hunk_patch_file_path().read_bytes() == b"".join(patch_lines)
    assert get_selected_hunk_hash_file_path().read_text() == "stable-hash"
    assert read_selected_change_kind() is SelectedChangeKind.HUNK
    assert get_line_changes_json_file_path().exists()
    assert get_index_snapshot_file_path().read_text() == "old\n"
    assert get_working_tree_snapshot_file_path().read_text() == "new\n"

    cached_line_changes = load_line_changes_from_state()
    assert cached_line_changes is not None
    assert cached_line_changes.path == "test.py"
    assert cached_line_changes.changed_line_ids() == [1, 2]


def test_selected_state_snapshot_restores_snapshot_metadata(temp_git_repo):
    """Preview rollback should restore the manifest used for stale checks."""
    patch_lines = [
        b"--- a/test.py\n",
        b"+++ b/test.py\n",
        b"@@ -1 +1 @@\n",
        b"-old\n",
        b"+new\n",
    ]
    line_changes = LineLevelChange(
        path="test.py",
        header=HunkHeader(old_start=1, old_len=1, new_start=1, new_len=1),
        lines=[],
    )
    cache_hunk_change(patch_lines, "stable-hash", line_changes)
    metadata_path = get_snapshot_metadata_file_path()
    original_metadata = metadata_path.read_bytes()

    with snapshot_selected_change_state() as snapshot:
        metadata_path.write_text('{"path": "other.py"}\n')
        restore_selected_change_state(snapshot)

    assert metadata_path.read_bytes() == original_metadata
