"""Tests for state directory path utilities."""

import subprocess

import pytest

from git_stage_batch.utils.paths import (
    ensure_state_directory_exists,
    get_context_lines,
    get_context_lines_file_path,
    get_state_directory_path,
)


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, cwd=repo, capture_output=True)

    # Create initial commit
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, cwd=repo, capture_output=True)

    return repo


class TestGetStateDirectoryPath:
    """Tests for get_state_directory_path function."""

    def test_get_state_directory_path(self, temp_git_repo):
        """Test getting the state directory path."""
        state_dir = get_state_directory_path()
        assert state_dir == temp_git_repo / ".git" / "git-stage-batch"


class TestEnsureStateDirectoryExists:
    """Tests for ensure_state_directory_exists function."""

    def test_ensure_state_directory_exists_creates_directory(self, temp_git_repo):
        """Test that ensure_state_directory_exists creates the directory."""
        state_dir = get_state_directory_path()
        assert not state_dir.exists()

        ensure_state_directory_exists()

        assert state_dir.exists()
        assert state_dir.is_dir()

    def test_ensure_state_directory_exists_idempotent(self, temp_git_repo):
        """Test that ensure_state_directory_exists is idempotent."""
        ensure_state_directory_exists()
        ensure_state_directory_exists()  # Should not raise

        state_dir = get_state_directory_path()
        assert state_dir.exists()


class TestLineLevelOperationPaths:
    """Tests for line-level operation path functions."""

    def test_get_processed_include_ids_file_path(self, temp_git_repo):
        """Test getting the processed include IDs file path."""
        from git_stage_batch.utils.paths import get_processed_include_ids_file_path

        include_ids_path = get_processed_include_ids_file_path()
        state_dir = get_state_directory_path()
        assert include_ids_path == state_dir / "processed.include"

    def test_get_processed_skip_ids_file_path(self, temp_git_repo):
        """Test getting the processed skip IDs file path."""
        from git_stage_batch.utils.paths import get_processed_skip_ids_file_path

        skip_ids_path = get_processed_skip_ids_file_path()
        state_dir = get_state_directory_path()
        assert skip_ids_path == state_dir / "processed.skip"

    def test_get_line_changes_json_file_path(self, temp_git_repo):
        """Test getting the selected lines JSON file path."""
        from git_stage_batch.utils.paths import get_line_changes_json_file_path

        line_changes_path = get_line_changes_json_file_path()
        state_dir = get_state_directory_path()
        assert line_changes_path == state_dir / "selected-lines.json"

    def test_get_index_snapshot_file_path(self, temp_git_repo):
        """Test getting the index snapshot file path."""
        from git_stage_batch.utils.paths import get_index_snapshot_file_path

        index_snapshot_path = get_index_snapshot_file_path()
        state_dir = get_state_directory_path()
        assert index_snapshot_path == state_dir / "index-snapshot"

    def test_get_working_tree_snapshot_file_path(self, temp_git_repo):
        """Test getting the working tree snapshot file path."""
        from git_stage_batch.utils.paths import get_working_tree_snapshot_file_path

        working_tree_path = get_working_tree_snapshot_file_path()
        state_dir = get_state_directory_path()
        assert working_tree_path == state_dir / "working-tree-snapshot"


class TestGetContextLines:
    """Tests for get_context_lines function."""

    def test_get_context_lines_default(self, temp_git_repo):
        """Test that get_context_lines returns 3 when file doesn't exist."""
        ensure_state_directory_exists()
        assert get_context_lines() == 3

    def test_get_context_lines_reads_file(self, temp_git_repo):
        """Test that get_context_lines reads value from file."""
        ensure_state_directory_exists()
        context_file = get_state_directory_path() / "context-lines"
        context_file.write_text("5\n")
        assert get_context_lines() == 5

    def test_get_context_lines_invalid_content(self, temp_git_repo):
        """Test that get_context_lines returns 3 for invalid content."""
        ensure_state_directory_exists()
        context_file = get_state_directory_path() / "context-lines"
        context_file.write_text("not-a-number\n")
        assert get_context_lines() == 3


class TestGetContextLinesFilePath:
    """Tests for get_context_lines_file_path function."""

    def test_get_context_lines_file_path(self, temp_git_repo):
        """Test getting the context lines file path."""
        context_file = get_context_lines_file_path()
        assert context_file == temp_git_repo / ".git" / "git-stage-batch" / "context-lines"


class TestAbortStatePaths:
    """Tests for abort state file path functions."""

    def test_get_abort_head_file_path(self, temp_git_repo):
        """Test getting the abort head file path."""
        from git_stage_batch.utils.paths import get_abort_head_file_path

        abort_head_path = get_abort_head_file_path()
        state_dir = get_state_directory_path()
        assert abort_head_path == state_dir / "abort-head"

    def test_get_abort_stash_file_path(self, temp_git_repo):
        """Test getting the abort stash file path."""
        from git_stage_batch.utils.paths import get_abort_stash_file_path

        abort_stash_path = get_abort_stash_file_path()
        state_dir = get_state_directory_path()
        assert abort_stash_path == state_dir / "abort-stash"

    def test_get_abort_snapshots_directory_path(self, temp_git_repo):
        """Test getting the abort snapshots directory path."""
        from git_stage_batch.utils.paths import get_abort_snapshots_directory_path

        snapshots_dir = get_abort_snapshots_directory_path()
        state_dir = get_state_directory_path()
        assert snapshots_dir == state_dir / "snapshots"

    def test_get_abort_snapshot_list_file_path(self, temp_git_repo):
        """Test getting the abort snapshot list file path."""
        from git_stage_batch.utils.paths import get_abort_snapshot_list_file_path

        snapshot_list_path = get_abort_snapshot_list_file_path()
        state_dir = get_state_directory_path()
        assert snapshot_list_path == state_dir / "snapshot-list"


class TestAutoAddedFilesPath:
    """Tests for auto-added files path function."""

    def test_get_auto_added_files_file_path(self, temp_git_repo):
        """Test getting the auto-added files file path."""
        from git_stage_batch.utils.paths import get_auto_added_files_file_path

        auto_added_path = get_auto_added_files_file_path()
        state_dir = get_state_directory_path()
        assert auto_added_path == state_dir / "auto-added-files"


class TestBlockedFilesPath:
    """Tests for blocked files path function."""

    def test_get_blocked_files_file_path(self, temp_git_repo):
        """Test getting the blocked files file path."""
        from git_stage_batch.utils.paths import get_blocked_files_file_path

        blocked_files_path = get_blocked_files_file_path()
        state_dir = get_state_directory_path()
        assert blocked_files_path == state_dir / "blocked-files"


class TestLineLevelOperationPaths:
    """Tests for line-level operation path functions."""

    def test_get_processed_include_ids_file_path(self, temp_git_repo):
        """Test getting the processed include IDs file path."""
        from git_stage_batch.utils.paths import get_processed_include_ids_file_path

        include_ids_path = get_processed_include_ids_file_path()
        state_dir = get_state_directory_path()
        assert include_ids_path == state_dir / "processed.include"

    def test_get_processed_skip_ids_file_path(self, temp_git_repo):
        """Test getting the processed skip IDs file path."""
        from git_stage_batch.utils.paths import get_processed_skip_ids_file_path

        skip_ids_path = get_processed_skip_ids_file_path()
        state_dir = get_state_directory_path()
        assert skip_ids_path == state_dir / "processed.skip"

    def test_get_line_changes_json_file_path(self, temp_git_repo):
        """Test getting the selected lines JSON file path."""
        from git_stage_batch.utils.paths import get_line_changes_json_file_path

        line_changes_path = get_line_changes_json_file_path()
        state_dir = get_state_directory_path()
        assert line_changes_path == state_dir / "selected-lines.json"

    def test_get_index_snapshot_file_path(self, temp_git_repo):
        """Test getting the index snapshot file path."""
        from git_stage_batch.utils.paths import get_index_snapshot_file_path

        index_snapshot_path = get_index_snapshot_file_path()
        state_dir = get_state_directory_path()
        assert index_snapshot_path == state_dir / "index-snapshot"

    def test_get_working_tree_snapshot_file_path(self, temp_git_repo):
        """Test getting the working tree snapshot file path."""
        from git_stage_batch.utils.paths import get_working_tree_snapshot_file_path

        working_tree_path = get_working_tree_snapshot_file_path()
        state_dir = get_state_directory_path()
        assert working_tree_path == state_dir / "working-tree-snapshot"


class TestSessionTrackingPaths:
    """Tests for session tracking path functions."""

    def test_get_iteration_count_file_path(self, temp_git_repo):
        """Test getting the iteration count file path."""
        from git_stage_batch.utils.paths import get_iteration_count_file_path

        iteration_count_path = get_iteration_count_file_path()
        state_dir = get_state_directory_path()
        assert iteration_count_path == state_dir / "iteration-count"

    def test_get_start_head_file_path(self, temp_git_repo):
        """Test getting the start HEAD file path."""
        from git_stage_batch.utils.paths import get_start_head_file_path

        start_head_path = get_start_head_file_path()
        state_dir = get_state_directory_path()
        assert start_head_path == state_dir / "start-head"

    def test_get_start_index_tree_file_path(self, temp_git_repo):
        """Test getting the start index tree file path."""
        from git_stage_batch.utils.paths import get_start_index_tree_file_path

        start_index_tree_path = get_start_index_tree_file_path()
        state_dir = get_state_directory_path()
        assert start_index_tree_path == state_dir / "start-index-tree"

    def test_get_suggest_fixup_state_file_path(self, temp_git_repo):
        """Test getting the suggest-fixup state file path."""
        from git_stage_batch.utils.paths import get_suggest_fixup_state_file_path

        suggest_fixup_path = get_suggest_fixup_state_file_path()
        state_dir = get_state_directory_path()
        assert suggest_fixup_path == state_dir / "suggest-fixup-state.json"


class TestBatchMetadataPaths:
    """Tests for batch metadata path functions."""

    def test_get_batches_directory_path(self, temp_git_repo):
        """Test getting the batches directory path."""
        from git_stage_batch.utils.paths import get_batches_directory_path

        batches_dir = get_batches_directory_path()
        assert batches_dir == temp_git_repo / ".git" / "git-stage-batch" / "batches"

    def test_get_batch_directory_path(self, temp_git_repo):
        """Test getting a specific batch directory path."""
        from git_stage_batch.utils.paths import get_batch_directory_path

        batch_dir = get_batch_directory_path("my-batch")
        assert batch_dir == temp_git_repo / ".git" / "git-stage-batch" / "batches" / "my-batch"

    def test_get_batch_metadata_file_path(self, temp_git_repo):
        """Test getting a batch metadata file path."""
        from git_stage_batch.utils.paths import get_batch_metadata_file_path

        metadata_file = get_batch_metadata_file_path("my-batch")
        assert metadata_file == temp_git_repo / ".git" / "git-stage-batch" / "batches" / "my-batch" / "metadata.json"

    def test_get_batch_refs_snapshot_file_path(self, temp_git_repo):
        """Test getting the batch refs snapshot file path."""
        from git_stage_batch.utils.paths import get_batch_refs_snapshot_file_path

        snapshot_file = get_batch_refs_snapshot_file_path()
        assert snapshot_file == temp_git_repo / ".git" / "git-stage-batch" / "batch-refs-snapshot.json"
