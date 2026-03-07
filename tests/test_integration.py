"""End-to-end integration tests for cross-feature interactions.

These tests validate that features work together correctly, covering scenarios
like state transitions, abort/restore, interactive flows, and real-world workflows.
"""

import json
import subprocess
from pathlib import Path

import pytest

from git_stage_batch.commands import (
    command_abort,
    command_again,
    command_discard,
    command_include,
    command_skip,
    command_start,
    command_status,
)
from git_stage_batch.state import (
    get_abort_head_file_path,
    get_abort_snapshots_directory_path,
    get_abort_stash_file_path,
    get_current_hunk_patch_file_path,
    get_included_hunks_file_path,
    get_skipped_hunks_jsonl_file_path,
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

    # Create initial commit with a file
    (repo / "test.txt").write_text("line1\nline2\nline3\n")
    subprocess.run(["git", "add", "test.txt"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, cwd=repo, capture_output=True)

    return repo


class TestStartIncludeSkipDiscardStatusFlow:
    """Test the complete workflow from start through various actions to status."""

    def test_full_workflow_with_status_porcelain(self, temp_git_repo, capsys):
        """Test start → include/skip/discard → status --porcelain interaction."""
        # Create three separate files to ensure separate hunks
        file1 = temp_git_repo / "file1.txt"
        file2 = temp_git_repo / "file2.txt"
        file3 = temp_git_repo / "file3.txt"

        file1.write_text("original1\n")
        file2.write_text("original2\n")
        file3.write_text("original3\n")

        subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Initial"], cwd=temp_git_repo, check=True)

        # Modify all three files
        file1.write_text("modified1\n")
        file2.write_text("modified2\n")
        file3.write_text("modified3\n")

        # Start staging
        command_start()

        # Include first hunk
        command_include()

        # Skip second hunk
        command_skip()

        # Get status as JSON
        capsys.readouterr()  # Clear
        command_status(porcelain=True)
        output = capsys.readouterr().out
        status_data = json.loads(output)

        # Verify session info
        assert status_data["session"]["iteration"] == 1
        assert status_data["session"]["in_progress"] is True

        # Verify progress counts
        assert status_data["progress"]["included"] == 1
        assert status_data["progress"]["skipped"] == 1
        assert status_data["progress"]["discarded"] == 0

        # Verify current hunk exists (third file)
        assert status_data["current"] is not None
        assert status_data["current"]["file"] in ["file1.txt", "file2.txt", "file3.txt"]

        # Verify skipped hunks list
        assert len(status_data["skipped_hunks"]) == 1

        # Discard current hunk
        command_discard()

        # Check final status
        capsys.readouterr()
        command_status(porcelain=True)
        output = capsys.readouterr().out
        status_data = json.loads(output)

        assert status_data["progress"]["included"] == 1
        assert status_data["progress"]["skipped"] == 1
        assert status_data["progress"]["discarded"] == 1
        assert status_data["current"] is None  # No more hunks

        # Verify git state: first file staged, second in worktree, third discarded
        diff_cached = subprocess.run(
            ["git", "diff", "--cached"], cwd=temp_git_repo, capture_output=True, text=True
        ).stdout
        assert "modified1" in diff_cached  # First file staged

        diff_worktree = subprocess.run(
            ["git", "diff"], cwd=temp_git_repo, capture_output=True, text=True
        ).stdout
        assert "modified2" in diff_worktree  # Second file still in worktree

        # Third file should be reverted to original
        assert file3.read_text() == "original3\n"  # Third file discarded


class TestAgainAfterCommit:
    """Test 'again' command after state transitions."""

    def test_again_after_commit_preserves_skipped_hunks(self, temp_git_repo, capsys):
        """Test that 'again' after a commit shows previously skipped hunks."""
        # Create two files with separate changes
        file1 = temp_git_repo / "code1.py"
        file2 = temp_git_repo / "code2.py"
        file1.write_text("x = 1\n")
        file2.write_text("y = 2\n")
        subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Initial"], cwd=temp_git_repo, check=True)

        file1.write_text("x = 10\n")
        file2.write_text("y = 20\n")

        command_start()

        # Include first file's hunk
        command_include()

        # Skip second file's hunk
        command_skip()

        # Commit the staged change
        subprocess.run(["git", "commit", "-m", "Change x"], cwd=temp_git_repo, check=True)

        # Run again - should show the skipped hunk
        capsys.readouterr()
        command_again()

        # Verify we have a current hunk
        assert get_current_hunk_patch_file_path().exists()

        # It should be the y = 20 change
        captured = capsys.readouterr()
        assert "y = 20" in captured.out

    def test_again_clears_previous_iteration_state(self, temp_git_repo):
        """Test that 'again' properly clears included/skipped/discarded state."""
        file1 = temp_git_repo / "test1.txt"
        file2 = temp_git_repo / "test2.txt"
        file1.write_text("line 1\n")
        file2.write_text("line 2\n")
        subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Initial"], cwd=temp_git_repo, check=True)

        file1.write_text("changed 1\n")
        file2.write_text("changed 2\n")

        command_start()
        command_include()  # Include first file
        command_skip()     # Skip second file

        # Verify state files exist
        assert get_included_hunks_file_path().exists()
        assert get_skipped_hunks_jsonl_file_path().exists()

        command_again()

        # Verify state was cleared (files might not exist or be empty)
        included_path = get_included_hunks_file_path()
        skipped_path = get_skipped_hunks_jsonl_file_path()

        if included_path.exists():
            assert included_path.read_text().strip() == ""
        if skipped_path.exists():
            assert skipped_path.read_text().strip() == ""


class TestAbortAfterDiscards:
    """Test abort functionality with discarded changes."""

    def test_abort_restores_discarded_hunks(self, temp_git_repo, capsys):
        """Test that abort restores hunks that were discarded."""
        # Create file and commit
        test_file = temp_git_repo / "restore.py"
        test_file.write_text("original = 1\n")
        subprocess.run(["git", "add", "restore.py"], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Initial"], cwd=temp_git_repo, check=True)

        # Make changes
        test_file.write_text("modified = 2\n")

        # Start and discard
        command_start()
        command_discard()

        # Verify file was modified
        assert test_file.read_text() == "original = 1\n"

        # Abort should restore the discarded change
        capsys.readouterr()
        command_abort()

        # File should have the modified content back
        assert test_file.read_text() == "modified = 2\n"

        # Should show success message (abort messages go to stderr)
        captured = capsys.readouterr()
        assert "Session aborted" in captured.err or "reverted" in captured.err

    def test_abort_with_staged_and_discarded_changes(self, temp_git_repo):
        """Test abort with both staged (included) and discarded changes."""
        # Create two files (named so they process in expected order)
        file1 = temp_git_repo / "a_staged.txt"
        file2 = temp_git_repo / "b_discarded.txt"

        file1.write_text("original 1\n")
        file2.write_text("original 2\n")
        subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Initial"], cwd=temp_git_repo, check=True)

        file1.write_text("modified 1\n")
        file2.write_text("modified 2\n")

        command_start()
        command_include()  # Stage first file (a_staged.txt)
        command_discard()  # Discard second file (b_discarded.txt)

        # Verify states
        diff_cached = subprocess.run(
            ["git", "diff", "--cached"], cwd=temp_git_repo, capture_output=True, text=True
        ).stdout
        assert "modified 1" in diff_cached

        assert file2.read_text() == "original 2\n"  # Discarded

        # Abort
        command_abort()

        # Index should be clean
        diff_cached = subprocess.run(
            ["git", "diff", "--cached"], cwd=temp_git_repo, capture_output=True, text=True
        ).stdout
        assert diff_cached == ""

        # Both files should have modified content
        assert file1.read_text() == "modified 1\n"
        assert file2.read_text() == "modified 2\n"

    def test_abort_without_session_errors_gracefully(self, temp_git_repo, capsys):
        """Test that abort without a session gives clear error."""
        capsys.readouterr()
        with pytest.raises(SystemExit):
            command_abort()

        captured = capsys.readouterr()
        assert "No session to abort" in captured.err or "not found" in captured.err


class TestInteractiveModeQuitPaths:
    """Test various quit/exit scenarios in interactive mode."""

    def test_quit_with_pending_hunks_preserves_state(self, temp_git_repo):
        """Test that quitting interactive mode preserves current state."""
        # Create two files with changes
        file1 = temp_git_repo / "test1.py"
        file2 = temp_git_repo / "test2.py"
        file3 = temp_git_repo / "test3.py"
        file1.write_text("line 1\n")
        file2.write_text("line 2\n")
        file3.write_text("line 3\n")
        subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Initial"], cwd=temp_git_repo, check=True)

        file1.write_text("changed 1\n")
        file2.write_text("changed 2\n")
        file3.write_text("changed 3\n")

        command_start()
        command_include()  # Include first file
        command_skip()     # Skip second file

        # At this point, if interactive mode quit, state should persist
        # Verify state files
        assert get_current_hunk_patch_file_path().exists()
        assert get_included_hunks_file_path().read_text().strip() != ""
        assert get_skipped_hunks_jsonl_file_path().read_text().strip() != ""

        # Verify we can continue with 'show'
        from git_stage_batch.commands import command_show
        # Should not raise
        command_show(porcelain=False)

    def test_session_state_survives_shell_exit(self, temp_git_repo):
        """Test that session state persists across shell sessions."""
        test_file = temp_git_repo / "persist.txt"
        test_file.write_text("old\n")
        subprocess.run(["git", "add", "persist.txt"], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Initial"], cwd=temp_git_repo, check=True)

        test_file.write_text("new\n")

        command_start()

        # Simulate shell exit by just not continuing
        # State should be preserved
        assert get_current_hunk_patch_file_path().exists()

        # In a "new shell session" (same Python process, but conceptually separate)
        # we should be able to continue
        command_include()

        # Should have staged the change
        diff_cached = subprocess.run(
            ["git", "diff", "--cached"], cwd=temp_git_repo, capture_output=True, text=True
        ).stdout
        assert "new" in diff_cached


class TestAbortSnapshot:
    """Test abort snapshot creation and restoration."""

    def test_abort_snapshots_created_on_start(self, temp_git_repo):
        """Test that start creates abort state (HEAD and stash)."""
        test_file = temp_git_repo / "file.txt"
        test_file.write_text("original\n")
        subprocess.run(["git", "add", "file.txt"], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Initial"], cwd=temp_git_repo, check=True)

        test_file.write_text("modified\n")

        command_start()

        # Abort metadata should exist
        assert get_abort_head_file_path().exists()
        assert get_abort_stash_file_path().exists()
        # Note: abort-snapshots directory is created lazily only when needed for untracked files

    def test_abort_restores_exactly_original_state(self, temp_git_repo):
        """Test that abort restores exact original index and worktree state."""
        # Set up complex state: staged change + unstaged change
        file1 = temp_git_repo / "a_staged.txt"
        file2 = temp_git_repo / "b_unstaged.txt"
        file3 = temp_git_repo / "c_other.txt"

        file1.write_text("v1\n")
        file2.write_text("v1\n")
        file3.write_text("v1\n")
        subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Initial"], cwd=temp_git_repo, check=True)

        # Make changes
        file1.write_text("v2\n")
        file2.write_text("v2\n")
        file3.write_text("v2\n")

        # Stage one
        subprocess.run(["git", "add", "a_staged.txt"], cwd=temp_git_repo, check=True)

        # Now we have: a_staged.txt in index, b_unstaged.txt and c_other.txt in worktree only
        # Capture this state
        index_before = subprocess.run(
            ["git", "diff", "--cached"], cwd=temp_git_repo, capture_output=True, text=True
        ).stdout
        worktree_before = subprocess.run(
            ["git", "diff"], cwd=temp_git_repo, capture_output=True, text=True
        ).stdout

        command_start()

        # Do some operations with the worktree hunks
        command_include()  # Include b_unstaged.txt
        command_skip()     # Skip c_other.txt

        # Abort
        command_abort()

        # State should be exactly as before
        index_after = subprocess.run(
            ["git", "diff", "--cached"], cwd=temp_git_repo, capture_output=True, text=True
        ).stdout
        worktree_after = subprocess.run(
            ["git", "diff"], cwd=temp_git_repo, capture_output=True, text=True
        ).stdout

        assert index_after == index_before
        assert worktree_after == worktree_before


class TestCrossFeatureEdgeCases:
    """Test edge cases in feature interactions."""

    def test_discard_then_again_shows_remaining_hunks(self, temp_git_repo):
        """Test that discarding some hunks then running again works correctly."""
        file1 = temp_git_repo / "multi1.txt"
        file2 = temp_git_repo / "multi2.txt"
        file1.write_text("a\n")
        file2.write_text("b\n")
        subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Initial"], cwd=temp_git_repo, check=True)

        file1.write_text("A\n")
        file2.write_text("B\n")

        command_start()
        command_discard()  # Discard first file's change
        command_skip()     # Skip second file's change

        command_again()

        # Should show remaining changes (second file was skipped)
        assert get_current_hunk_patch_file_path().exists()

    def test_include_with_no_changes_handles_gracefully(self, temp_git_repo, capsys):
        """Test start when there are no changes."""
        # Create and commit a file
        test_file = temp_git_repo / "clean.txt"
        test_file.write_text("content\n")
        subprocess.run(["git", "add", "clean.txt"], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Initial"], cwd=temp_git_repo, check=True)

        # No changes - should exit with code 2
        capsys.readouterr()
        with pytest.raises(SystemExit) as excinfo:
            command_start()

        captured = capsys.readouterr()
        # Should indicate no changes
        assert "No pending hunks" in captured.err

    def test_status_with_stale_current_hunk_updates(self, temp_git_repo, capsys):
        """Test that status handles stale current hunk gracefully."""
        test_file = temp_git_repo / "file.py"
        test_file.write_text("x = 1\n")
        subprocess.run(["git", "add", "file.py"], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Initial"], cwd=temp_git_repo, check=True)

        test_file.write_text("x = 2\n")

        command_start()

        # Externally modify the file (simulating concurrent edit)
        test_file.write_text("x = 3\n")

        # Status should handle stale hunk
        capsys.readouterr()
        command_status()
        # Should not crash
        captured = capsys.readouterr()
        assert captured.out != ""  # Should produce some output


class TestFileWideActions:
    """Test file-wide action interactions with other features."""

    def test_file_action_with_multiple_files(self, temp_git_repo, capsys):
        """Test file-wide actions when multiple files have changes."""
        file1 = temp_git_repo / "file1.txt"
        file2 = temp_git_repo / "file2.txt"

        file1.write_text("a\nb\n")
        file2.write_text("x\ny\n")
        subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Initial"], cwd=temp_git_repo, check=True)

        file1.write_text("A\nB\n")
        file2.write_text("X\nY\n")

        command_start()

        # We're on first hunk of first file
        # Skip the entire file
        from git_stage_batch.commands import command_skip_file
        command_skip_file()

        # Should advance to second file
        assert get_current_hunk_patch_file_path().exists()
        current_lines = json.loads(get_current_hunk_patch_file_path().parent.joinpath("current-lines.json").read_text())
        # Should be on file2 now
        assert current_lines["path"] == "file2.txt"


# Note: Wheel/packaging tests should be in a separate test file or CI script
# that actually builds and installs the wheel, as they require a full build process.
class TestTranslationIntegration:
    """Test that translation infrastructure is wired correctly."""

    def test_translation_module_imports_successfully(self):
        """Test that i18n module can be imported."""
        from git_stage_batch import i18n
        assert hasattr(i18n, '_')
        assert callable(i18n._)

    def test_translation_function_returns_strings(self):
        """Test that translation function works with English fallback."""
        from git_stage_batch.i18n import _

        result = _("No batch staging session in progress.")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_format_hotkey_with_translations(self):
        """Test that format_hotkey works with translated strings."""
        from git_stage_batch.display import format_hotkey

        # English - hotkey in word
        result = format_hotkey("include", "i")
        assert "[i]" in result or "include" in result

        # Hypothetical translation where hotkey isn't in word
        result = format_hotkey("einschließen", "i")
        assert "i" in result.lower()
        # The 'i' should be bracketed in the result
        assert "[i]" in result.lower()
