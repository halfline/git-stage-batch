"""Tests for selected-change lifecycle state cleanup."""

import json
import subprocess

import pytest

from git_stage_batch.data.selected_change.lifecycle import (
    clear_selected_change_state_files,
)
from git_stage_batch.utils.paths import (
    ensure_state_directory_exists,
    get_index_snapshot_file_path,
    get_line_changes_json_file_path,
    get_processed_batch_ids_file_path,
    get_processed_include_ids_file_path,
    get_selected_hunk_hash_file_path,
    get_selected_hunk_patch_file_path,
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

    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True, cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        check=True,
        cwd=repo,
        capture_output=True,
    )

    ensure_state_directory_exists()

    return repo


class TestClearSelectedChangeStateFiles:
    """Tests for clear_selected_change_state_files()."""

    def test_clears_per_selection_state_files(self, temp_git_repo):
        """Selected-change cleanup should leave global batch state intact."""
        get_selected_hunk_patch_file_path().write_text("patch")
        get_selected_hunk_hash_file_path().write_text("hash")
        get_line_changes_json_file_path().write_text("{}")
        get_index_snapshot_file_path().write_text("index")
        get_working_tree_snapshot_file_path().write_text("tree")
        get_processed_include_ids_file_path().write_text("1\n2\n")
        get_processed_batch_ids_file_path().write_text(
            json.dumps(
                {
                    "test.py": {
                        "presence_claims": [
                            {"source_lines": ["1", "2"]},
                        ],
                    },
                }
            )
        )

        clear_selected_change_state_files()

        assert not get_selected_hunk_patch_file_path().exists()
        assert not get_selected_hunk_hash_file_path().exists()
        assert not get_line_changes_json_file_path().exists()
        assert not get_index_snapshot_file_path().exists()
        assert not get_working_tree_snapshot_file_path().exists()
        assert not get_processed_include_ids_file_path().exists()
        assert get_processed_batch_ids_file_path().exists()

    def test_handles_missing_files(self, temp_git_repo):
        """Selected-change cleanup should tolerate already-missing files."""
        clear_selected_change_state_files()
