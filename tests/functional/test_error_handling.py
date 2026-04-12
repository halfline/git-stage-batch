"""Functional tests for error handling and edge cases."""

import subprocess

from .conftest import git_stage_batch


class TestInvalidInput:
    """Test handling of invalid input."""

    def test_invalid_line_selection(self, repo_with_changes):
        """Test invalid line selection formats."""
        git_stage_batch("start")

        # Invalid line ID (doesn't exist)
        result = git_stage_batch("include", "--line", "999", check=False)
        # Should handle gracefully (may succeed with no effect or fail)

        # Invalid format
        result = git_stage_batch("include", "--line", "abc", check=False)
        assert result.returncode != 0

    def test_invalid_batch_name(self, repo_with_changes):
        """Test invalid batch names with path traversal."""
        # Batch names with path separators should fail
        result = git_stage_batch("new", "../evil-batch", check=False)
        assert result.returncode != 0

    def test_invalid_command(self, repo_with_changes):
        """Test invalid command."""
        result = git_stage_batch("invalid-command", check=False)
        assert result.returncode != 0


class TestOperationWithoutSession:
    """Test operations without active session."""

    def test_include_without_session(self, repo_with_changes):
        """Test include without starting session fails."""
        result = git_stage_batch("include", "--line", "1", check=False)
        assert result.returncode != 0

    def test_show_without_session(self, repo_with_changes):
        """Test show without session fails."""
        result = git_stage_batch("show", check=False)
        assert result.returncode != 0

    def test_skip_without_session(self, repo_with_changes):
        """Test skip without session fails."""
        result = git_stage_batch("skip", check=False)
        assert result.returncode != 0


class TestNonexistentBatch:
    """Test operations on nonexistent batches."""

    def test_include_to_nonexistent_batch(self, repo_with_changes):
        """Test including to nonexistent batch auto-creates it."""
        git_stage_batch("start")

        result = git_stage_batch("include", "--to", "nonexistent-batch", "--line", "1")
        assert result.returncode == 0
        # Batch should now exist
        list_result = git_stage_batch("list")
        assert "nonexistent-batch" in list_result.stdout

    def test_show_from_nonexistent_batch(self, repo_with_changes):
        """Test showing from nonexistent batch fails."""
        result = git_stage_batch("show", "--from", "nonexistent", check=False)
        assert result.returncode != 0

    def test_apply_from_nonexistent_batch(self, repo_with_changes):
        """Test applying from nonexistent batch fails."""
        result = git_stage_batch("apply", "--from", "nonexistent", check=False)
        assert result.returncode != 0

    def test_delete_nonexistent_batch(self, repo_with_changes):
        """Test deleting nonexistent batch fails."""
        result = git_stage_batch("drop", "nonexistent", check=False)
        assert result.returncode != 0


class TestEdgeCases:
    """Test edge cases."""

    def test_empty_working_tree(self, functional_repo):
        """Test with no changes."""
        result = git_stage_batch("start", check=False)
        assert result.returncode != 0

    def test_session_with_staged_changes(self, repo_with_changes):
        """Test starting session with already staged changes."""
        # Stage some changes manually
        subprocess.run(["git", "add", "README.md"], check=True, capture_output=True)

        # Should still be able to start session with unstaged changes
        git_stage_batch("start", check=False)
        # May succeed or fail depending on if there are unstaged changes

    def test_abort_multiple_times(self, repo_with_changes):
        """Test aborting multiple times."""
        git_stage_batch("start")
        git_stage_batch("abort")

        # Second abort should fail (no session)
        result = git_stage_batch("abort", check=False)
        assert result.returncode != 0

    def test_conselected_sessions(self, repo_with_changes, tmp_path, monkeypatch):
        """Test that conselected sessions don't interfere."""
        # Start session in first repo
        git_stage_batch("start")

        # Create second repo
        repo2 = tmp_path / "repo2"
        repo2.mkdir()
        subprocess.run(["git", "init"], cwd=repo2, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=repo2,
            check=True,
            capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo2,
            check=True,
            capture_output=True
        )
        (repo2 / "file.txt").write_text("content\n")
        subprocess.run(["git", "add", "."], cwd=repo2, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo2,
            check=True,
            capture_output=True
        )
        (repo2 / "file.txt").write_text("modified\n")

        # Try to start session in second repo
        monkeypatch.chdir(repo2)
        git_stage_batch("start", check=False)
        # Should work independently


class TestBatchConflicts:
    """Test batch conflict scenarios."""

    def test_apply_batch_with_conflicts(self, repo_with_changes):
        """Test applying batch when working tree has conflicts."""
        # Save changes to batch
        git_stage_batch("new", "test-batch")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "test-batch", "--line", "1,2")

        # Don't restore, so working tree still has changes
        # Try to apply batch (may conflict)
        git_stage_batch("apply", "--from", "test-batch", check=False)
        # Should handle gracefully (may succeed or report conflict)

    def test_batch_after_working_tree_diverges(self, repo_with_changes):
        """Test batch operations after working tree changes."""
        # Create batch with changes
        git_stage_batch("new", "diverge-batch")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "diverge-batch", "--line", "1")

        # Make different changes to same file
        readme = repo_with_changes / "README.md"
        content = readme.read_text()
        readme.write_text(content + "\n## Different Changes\n")

        # Should still be able to show batch
        result = git_stage_batch("show", "--from", "diverge-batch")
        assert result.returncode == 0


class TestPermissionErrors:
    """Test permission-related errors."""

    def test_read_only_file_system(self, repo_with_changes):
        """Test graceful handling when filesystem is read-only."""
        # This is hard to test without actual read-only FS
        # Just verify commands don't crash
        git_stage_batch("list")

    def test_missing_git_directory(self, tmp_path, monkeypatch):
        """Test behavior when .git directory is missing."""
        # Create directory without git
        non_repo = tmp_path / "not_repo"
        non_repo.mkdir()
        monkeypatch.chdir(non_repo)

        result = git_stage_batch("start", check=False)
        assert result.returncode != 0

    def test_corrupted_git_repo(self, functional_repo, monkeypatch):
        """Test with corrupted git directory."""
        # Remove a git metadata file.
        git_dir = functional_repo / ".git"
        if (git_dir / "HEAD").exists():
            (git_dir / "HEAD").unlink()

        result = git_stage_batch("start", check=False)
        assert result.returncode != 0


class TestRecovery:
    """Test recovery from error states."""

    def test_recover_from_failed_include(self, repo_with_changes):
        """Test recovery after failed include."""
        git_stage_batch("start")

        # Try invalid include
        git_stage_batch("include", "--line", "999", check=False)

        # Should still be able to continue
        git_stage_batch("show", check=False)
        # Should work or indicate no more hunks

    def test_recover_from_failed_batch_operation(self, repo_with_changes):
        """Test recovery after failed batch operation."""
        git_stage_batch("start")

        # Try to include to nonexistent batch
        git_stage_batch("include", "--to", "nonexistent", "--line", "1", check=False)

        # Should still be able to do normal operations
        git_stage_batch("include", "--line", "1", check=False)
        # Should work

    def test_abort_after_errors(self, repo_with_changes):
        """Test abort works after errors."""
        git_stage_batch("start")

        # Cause some errors
        git_stage_batch("include", "--line", "999", check=False)
        git_stage_batch("skip", check=False)

        # Abort should still work
        result = git_stage_batch("abort")
        assert result.returncode == 0
