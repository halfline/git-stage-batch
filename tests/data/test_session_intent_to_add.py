"""Tests for session.py intent-to-add handling."""

import subprocess

import pytest

from git_stage_batch.data.session import initialize_abort_state
from git_stage_batch.utils.paths import ensure_state_directory_exists


class TestIntentToAddHandling:
    """Tests for proper handling of intent-to-add files during session initialization."""

    @pytest.fixture
    def temp_git_repo(self, tmp_path, monkeypatch):
        """Create a temporary git repository."""
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, capture_output=True)

        # Create initial commit
        (tmp_path / "README.md").write_text("# Test\n")
        subprocess.run(["git", "add", "README.md"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, capture_output=True)

        # Ensure state directory exists
        ensure_state_directory_exists()

        return tmp_path

    def test_tracked_file_with_intent_to_add_not_removed_from_index(self, temp_git_repo):
        """Tracked files with intent-to-add should NOT have git rm --cached applied."""
        # Create and commit a tracked file
        test_file = temp_git_repo / "tracked.py"
        test_file.write_text("original content\n")
        subprocess.run(["git", "add", "tracked.py"], cwd=temp_git_repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add tracked file"], cwd=temp_git_repo, check=True, capture_output=True)

        # Modify the file
        test_file.write_text("modified content\n")

        # Simulate someone running git rm --cached + git add -N (creates intent-to-add on tracked file)
        subprocess.run(["git", "rm", "--cached", "tracked.py"], cwd=temp_git_repo, check=True, capture_output=True)
        subprocess.run(["git", "add", "-N", "tracked.py"], cwd=temp_git_repo, check=True, capture_output=True)

        # Verify we have intent-to-add state (empty blob in index)
        ls_result = subprocess.run(
            ["git", "ls-files", "--stage", "tracked.py"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
            text=True
        )
        assert "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391" in ls_result.stdout

        # Initialize abort state (this should NOT remove tracked.py from index)
        initialize_abort_state()

        # Check index state - should still have empty blob, NOT be missing
        ls_after = subprocess.run(
            ["git", "ls-files", "--stage", "tracked.py"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
            text=True
        )
        assert ls_after.stdout.strip(), "Tracked file should still be in index"
        assert "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391" in ls_after.stdout

        # Check that file is NOT staged as deleted
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
            text=True
        )
        # Should show as modified/added, not deleted
        assert "D  tracked.py" not in status_result.stdout, "File should not be staged as deleted"

    def test_new_file_with_intent_to_add_temporarily_removed(self, temp_git_repo):
        """New files (not in HEAD) with intent-to-add should be temporarily removed for stash."""
        # Create a new file and add with intent-to-add
        test_file = temp_git_repo / "newfile.py"
        test_file.write_text("new content\n")
        subprocess.run(["git", "add", "-N", "newfile.py"], cwd=temp_git_repo, check=True, capture_output=True)

        # Verify we have intent-to-add state
        ls_result = subprocess.run(
            ["git", "ls-files", "--stage", "newfile.py"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
            text=True
        )
        assert "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391" in ls_result.stdout

        # Initialize abort state
        initialize_abort_state()

        # After initialization, the file should be back in index with intent-to-add
        # (it was temporarily removed for stash, then re-added)
        ls_after = subprocess.run(
            ["git", "ls-files", "--stage", "newfile.py"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
            text=True
        )
        assert ls_after.stdout.strip(), "New file should be back in index"
        assert "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391" in ls_after.stdout

        # File should not be staged as deleted
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
            text=True
        )
        assert "D  newfile.py" not in status_result.stdout
