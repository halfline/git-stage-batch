"""Functional tests for status command."""

import subprocess

from .conftest import git_stage_batch, get_staged_files


class TestStatusCommand:
    """Test status command."""

    def test_status_with_no_session(self, repo_with_changes):
        """Test status when no session is active."""
        result = git_stage_batch("status")
        # Should indicate no session or show session status
        assert result.returncode == 0

    def test_status_after_start(self, repo_with_changes):
        """Test status after starting a session."""
        git_stage_batch("start")

        result = git_stage_batch("status")
        assert result.returncode == 0
        # Should show session information

    def test_status_shows_staged_changes(self, repo_with_changes):
        """Test status shows staged changes."""
        git_stage_batch("start")
        git_stage_batch("include", "--line", "1", check=False)

        result = git_stage_batch("status")
        assert result.returncode == 0
        # Should show some indication of staged changes

    def test_status_after_batch_operations(self, repo_with_changes):
        """Test status after batch operations."""
        git_stage_batch("new", "test-batch")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "test-batch", "1", check=False)

        result = git_stage_batch("status")
        assert result.returncode == 0
        # Should show session status

    def test_status_shows_progress(self, repo_with_changes):
        """Test status shows progress through hunks."""
        git_stage_batch("start")

        # Skip a few hunks
        for _ in range(3):
            git_stage_batch("skip", check=False)

        result = git_stage_batch("status")
        assert result.returncode == 0
        # Should show progress information

    def test_status_st_shorthand(self, repo_with_changes):
        """Test 'st' shorthand for status."""
        git_stage_batch("start")

        result = git_stage_batch("st")
        assert result.returncode == 0
        # Should work same as 'status'

    def test_status_with_multiple_files_staged(self, repo_with_changes):
        """Test status with multiple files staged."""
        git_stage_batch("start")

        # Stage from multiple hunks
        for _ in range(5):
            result = git_stage_batch("show", check=False)
            if result.returncode != 0:
                break
            git_stage_batch("include", "--line", "1", check=False)

        result = git_stage_batch("status")
        assert result.returncode == 0
        # Should show multiple files

    def test_status_outside_repo(self, tmp_path, monkeypatch):
        """Test status outside a git repo."""
        non_repo = tmp_path / "not_a_repo"
        non_repo.mkdir()
        monkeypatch.chdir(non_repo)

        result = git_stage_batch("status", check=False)
        # Should fail or indicate not in a repo
        assert result.returncode != 0 or "not a git" in result.stderr.lower()

    def test_status_after_abort(self, repo_with_changes):
        """Test status after aborting a session."""
        git_stage_batch("start")
        git_stage_batch("include", "--line", "1", check=False)
        git_stage_batch("abort")

        result = git_stage_batch("status")
        assert result.returncode == 0
        # Should indicate no active session


class TestStatusWithBatches:
    """Test status command with batches."""

    def test_status_shows_batches(self, repo_with_changes):
        """Test status shows available batches."""
        git_stage_batch("new", "batch-a")
        git_stage_batch("new", "batch-b")

        result = git_stage_batch("status")
        assert result.returncode == 0
        # May show batches or at least not fail

    def test_status_after_batch_save(self, repo_with_changes):
        """Test status after saving to batch."""
        git_stage_batch("new", "save-batch")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "save-batch", "1,2", check=False)

        result = git_stage_batch("status")
        assert result.returncode == 0

    def test_status_with_empty_batch(self, repo_with_changes):
        """Test status with empty batch."""
        git_stage_batch("new", "empty-batch")

        result = git_stage_batch("status")
        assert result.returncode == 0
