"""Integration tests for cross-feature interactions.

These tests validate that features work together correctly, covering scenarios
like state transitions, session lifecycle, and edge cases that span multiple
features.
"""

import json
import subprocess
from pathlib import Path

import pytest

from git_stage_batch.commands import (
    command_abort,
    command_again,
    command_discard,
    command_discard_file,
    command_include,
    command_include_file,
    command_skip,
    command_skip_file,
    command_start,
    command_status,
    command_stop,
    command_suggest_fixup,
    command_suggest_fixup_line,
)
from git_stage_batch.state import CommandError, get_state_directory_path


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


class TestSessionLifecycle:
    """Test basic session lifecycle: start and stop."""

    def test_start_creates_state_directory(self, temp_git_repo):
        """Test that start creates the state directory."""
        # State shouldn't exist initially
        assert not get_state_directory_path().exists()

        # Create changes
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\nmodified\nline3\n")

        # Start session
        command_start()
        assert get_state_directory_path().exists()

        # Clean up
        command_stop()

    def test_stop_removes_state_directory(self, temp_git_repo):
        """Test that stop removes the state directory."""
        # Create changes
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\nmodified\nline3\n")

        # Start session
        command_start()
        assert get_state_directory_path().exists()

        # Stop session
        command_stop()
        assert not get_state_directory_path().exists()

    def test_stop_without_session_succeeds(self, temp_git_repo, capsys):
        """Test that stopping when no session exists still prints success."""
        # No session active
        assert not get_state_directory_path().exists()

        # Stop should not error
        command_stop()

        # Should print success message
        captured = capsys.readouterr()
        assert "State cleared" in captured.out

    def test_multiple_starts_are_idempotent(self, temp_git_repo):
        """Test that calling start multiple times is safe."""
        # Create changes
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\nmodified\nline3\n")

        # First start
        command_start()
        state_dir = get_state_directory_path()
        assert state_dir.exists()

        # Modify again to have changes for second start
        test_file.write_text("line1\nmodified again\nline3\n")

        # Second start should not error
        command_start()
        assert state_dir.exists()

        # Clean up
        command_stop()

    def test_session_state_directory_persists_until_stop(self, temp_git_repo):
        """Test that the state directory persists across commands until stop is called."""
        # Create changes
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\nmodified\nline3\n")

        # Start session
        command_start()
        state_dir = get_state_directory_path()
        assert state_dir.exists()

        # Include the hunk (simulating doing operations)
        command_include()

        # State should still exist after operations
        assert state_dir.exists()

        # Only stop should remove it
        command_stop()
        assert not get_state_directory_path().exists()


class TestAbortWorkflow:
    """Test abort functionality in various workflow scenarios."""

    def test_abort_restores_working_tree_after_discard(self, temp_git_repo):
        """Test that abort restores changes that were discarded."""
        # Create a file and commit it
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("original content\n")
        subprocess.run(["git", "add", "test.txt"], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Add test file"], cwd=temp_git_repo, check=True)

        # Modify the file
        test_file.write_text("modified content\n")
        original_modified_content = test_file.read_text()

        # Start session and discard the change
        command_start()
        command_discard()

        # File should be reverted
        assert test_file.read_text() == "original content\n"

        # Abort should restore the modified content
        command_abort()
        assert test_file.read_text() == original_modified_content

        # Session state should be cleared
        assert not get_state_directory_path().exists()

    def test_abort_undoes_staged_changes(self, temp_git_repo):
        """Test that abort removes changes from the index."""
        # Create changes
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\nmodified\nline3\n")

        # Start and include (stage) the change
        command_start()
        command_include()

        # Change should be staged
        diff_result = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "modified" in diff_result.stdout

        # Abort should unstage it
        command_abort()

        # Index should be clean
        diff_result = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert diff_result.stdout.strip() == ""

        # But working tree should still have the change
        assert "modified" in test_file.read_text()

    def test_abort_with_both_staged_and_discarded_changes(self, temp_git_repo):
        """Test abort with complex state: some changes staged, some discarded."""
        # Create two files
        file1 = temp_git_repo / "file1.txt"
        file2 = temp_git_repo / "file2.txt"
        file1.write_text("content1\n")
        file2.write_text("content2\n")
        subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Add files"], cwd=temp_git_repo, check=True)

        # Modify both files
        file1.write_text("modified1\n")
        file2.write_text("modified2\n")

        # Start session
        command_start()

        # Include first file (stages it)
        command_include()

        # Discard second file (reverts it)
        command_discard()

        # Verify state: file1 staged, file2 reverted
        assert file2.read_text() == "content2\n"
        diff_result = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "modified1" in diff_result.stdout

        # Abort should restore everything
        command_abort()

        # Index should be clean
        diff_result = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert diff_result.stdout.strip() == ""

        # Both files should have modifications back
        assert file1.read_text() == "modified1\n"
        assert file2.read_text() == "modified2\n"

    def test_abort_without_session_fails_gracefully(self, temp_git_repo):
        """Test that abort gives a clear error when no session exists."""
        # No session active
        assert not get_state_directory_path().exists()

        # Abort should error
        with pytest.raises(CommandError) as exc_info:
            command_abort()

        assert "No session to abort" in exc_info.value.message or "not found" in exc_info.value.message

    def test_abort_clears_session_state(self, temp_git_repo):
        """Test that abort removes the session state directory."""
        # Create changes and start session
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\nmodified\nline3\n")
        command_start()

        # State should exist
        assert get_state_directory_path().exists()

        # Abort should remove state
        command_abort()
        assert not get_state_directory_path().exists()


class TestFileLevelOperations:
    """Test file-level operations: include-file, skip-file, discard-file."""

    def test_include_file_with_multiple_files(self, temp_git_repo):
        """Test that include-file stages entire file and advances to next file."""
        # Create multiple files with changes
        file1 = temp_git_repo / "file1.txt"
        file2 = temp_git_repo / "file2.txt"
        file1.write_text("content1\n")
        file2.write_text("content2\n")
        subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Add files"], cwd=temp_git_repo, check=True)

        # Modify both files
        file1.write_text("modified1\n")
        file2.write_text("modified2\n")

        # Start session
        command_start()

        # Include entire first file
        command_include_file()

        # First file should be fully staged
        diff_result = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "modified1" in diff_result.stdout

        # Second file should still be in working tree
        diff_result = subprocess.run(
            ["git", "diff"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "modified2" in diff_result.stdout

        # Clean up
        command_stop()

    def test_skip_file_advances_to_next(self, temp_git_repo):
        """Test that skip-file moves past all hunks in current file."""
        # Create two files
        file1 = temp_git_repo / "aaa_first.txt"
        file2 = temp_git_repo / "zzz_second.txt"
        file1.write_text("line1\nline2\n")
        file2.write_text("lineA\nlineB\n")
        subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Add files"], cwd=temp_git_repo, check=True)

        # Modify both
        file1.write_text("mod1\nmod2\n")
        file2.write_text("modA\nmodB\n")

        # Start session
        command_start()

        # Skip entire first file
        command_skip_file()

        # Include next file
        command_include()

        # Only second file should be staged
        diff_result = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "modA" in diff_result.stdout or "modB" in diff_result.stdout
        assert "mod1" not in diff_result.stdout and "mod2" not in diff_result.stdout

        # First file should still be in working tree
        diff_result = subprocess.run(
            ["git", "diff"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "mod1" in diff_result.stdout or "mod2" in diff_result.stdout

        # Clean up
        command_stop()

    def test_discard_file_removes_entire_file(self, temp_git_repo):
        """Test that discard-file stages deletion of modified tracked files."""
        # Create and commit a file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("original\n")
        subprocess.run(["git", "add", "test.txt"], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Add file"], cwd=temp_git_repo, check=True)

        # Modify the file
        test_file.write_text("modified\n")

        # Start and discard entire file
        command_start()
        command_discard_file()

        # File should be staged for deletion (removed from working tree)
        assert not test_file.exists()

        # Verify it's staged for deletion
        diff_result = subprocess.run(
            ["git", "diff", "--cached", "--name-status"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "D\ttest.txt" in diff_result.stdout

        # Clean up
        command_stop()

    def test_abort_restores_file_discarded_with_discard_file(self, temp_git_repo):
        """Test that abort restores files that were discarded with discard-file."""
        # Create and commit a file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("original\n")
        subprocess.run(["git", "add", "test.txt"], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Add file"], cwd=temp_git_repo, check=True)

        # Modify the file
        test_file.write_text("modified\n")

        # Start, discard file, then abort
        command_start()
        command_discard_file()

        # File should be staged for deletion (removed from working tree)
        assert not test_file.exists()

        # Abort should restore the modification
        command_abort()
        assert test_file.exists()
        assert test_file.read_text() == "modified\n"

    def test_mixing_file_and_hunk_operations(self, temp_git_repo):
        """Test using both file-level and hunk-level operations in same session."""
        # Create three files
        file1 = temp_git_repo / "file1.txt"
        file2 = temp_git_repo / "file2.txt"
        file3 = temp_git_repo / "file3.txt"
        file1.write_text("content1\n")
        file2.write_text("content2\n")
        file3.write_text("content3\n")
        subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Add files"], cwd=temp_git_repo, check=True)

        # Modify all three
        file1.write_text("modified1\n")
        file2.write_text("modified2\n")
        file3.write_text("modified3\n")

        # Start session
        command_start()

        # Include file1 (file-level)
        command_include_file()

        # Include file2 (hunk-level)
        command_include()

        # Skip file3 (file-level)
        command_skip_file()

        # Check results: file1 and file2 should be staged
        diff_result = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "modified1" in diff_result.stdout
        assert "modified2" in diff_result.stdout

        # file3 should still be in working tree only
        diff_result = subprocess.run(
            ["git", "diff"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "modified3" in diff_result.stdout

        # Clean up
        command_stop()


class TestSuggestFixupWorkflow:
    """Test suggest-fixup feature integration with workflow."""

    def test_suggest_fixup_finds_commit_that_modified_lines(self, temp_git_repo, capsys):
        """Test that suggest-fixup finds the commit that last modified the changed lines."""
        # Create a file and commit
        test_file = temp_git_repo / "code.py"
        test_file.write_text("def foo():\n    return 1\n")
        subprocess.run(["git", "add", "code.py"], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Add foo function"], cwd=temp_git_repo, check=True)

        # Modify and commit again
        test_file.write_text("def foo():\n    return 2\n")
        subprocess.run(["git", "add", "code.py"], cwd=temp_git_repo, check=True)
        result = subprocess.run(
            ["git", "commit", "-m", "Change foo return value"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
            text=True
        )

        # Make another change (don't commit)
        test_file.write_text("def foo():\n    return 3\n")

        # Start session
        command_start()

        # Suggest fixup should find the "Change foo return value" commit
        capsys.readouterr()
        command_suggest_fixup(boundary="HEAD~1")

        captured = capsys.readouterr()
        assert "Change foo return value" in captured.out

        # Clean up
        command_stop()

    def test_suggest_fixup_iterates_through_candidates(self, temp_git_repo, capsys):
        """Test that repeated suggest-fixup calls iterate through commit history."""
        # Create a file with multiple commits modifying same lines
        test_file = temp_git_repo / "data.txt"
        test_file.write_text("value: 1\n")
        subprocess.run(["git", "add", "data.txt"], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Set value to 1"], cwd=temp_git_repo, check=True)

        test_file.write_text("value: 2\n")
        subprocess.run(["git", "add", "data.txt"], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Set value to 2"], cwd=temp_git_repo, check=True)

        test_file.write_text("value: 3\n")
        subprocess.run(["git", "add", "data.txt"], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Set value to 3"], cwd=temp_git_repo, check=True)

        # Make a new change
        test_file.write_text("value: 4\n")

        # Start session
        command_start()

        # First suggest-fixup should find most recent ("Set value to 3")
        # Use HEAD~3 as boundary (goes back before "Set value to 1")
        capsys.readouterr()
        command_suggest_fixup(boundary="HEAD~3")
        captured = capsys.readouterr()
        assert "Set value to 3" in captured.out

        # Second call should find next older commit
        capsys.readouterr()
        command_suggest_fixup()
        captured = capsys.readouterr()
        assert "Set value to 2" in captured.out

        # Third call should find oldest
        capsys.readouterr()
        command_suggest_fixup()
        captured = capsys.readouterr()
        assert "Set value to 1" in captured.out

        # Clean up
        command_stop()

    def test_suggest_fixup_with_reset_flag(self, temp_git_repo, capsys):
        """Test that suggest-fixup --reset restarts iteration from most recent."""
        # Create file with multiple commits
        test_file = temp_git_repo / "data.txt"
        test_file.write_text("value: 1\n")
        subprocess.run(["git", "add", "data.txt"], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "First"], cwd=temp_git_repo, check=True)

        test_file.write_text("value: 2\n")
        subprocess.run(["git", "add", "data.txt"], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Second"], cwd=temp_git_repo, check=True)

        # Modify again
        test_file.write_text("value: 3\n")

        # Start session
        command_start()

        # First suggest-fixup
        capsys.readouterr()
        command_suggest_fixup(boundary="HEAD~2")
        captured = capsys.readouterr()
        assert "Second" in captured.out

        # Call again to get next candidate
        capsys.readouterr()
        command_suggest_fixup()
        captured = capsys.readouterr()
        assert "First" in captured.out

        # Reset should go back to most recent
        capsys.readouterr()
        command_suggest_fixup(reset=True)
        captured = capsys.readouterr()
        assert "Second" in captured.out

        # Clean up
        command_stop()

    def test_abort_clears_suggest_fixup_state(self, temp_git_repo):
        """Test that abort clears suggest-fixup iteration state."""
        # Create file with commit
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("original\n")
        subprocess.run(["git", "add", "test.txt"], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Add test"], cwd=temp_git_repo, check=True)

        # Modify file
        test_file.write_text("modified\n")

        # Start session and suggest fixup
        command_start()
        command_suggest_fixup(boundary="HEAD~1")

        # Abort should clear all state including suggest-fixup state
        command_abort()

        # Suggest-fixup state should be cleared
        # (No direct way to verify, but it shouldn't error)


class TestProgressTracking:
    """Test progress tracking across workflow."""

    def test_progress_tracking_accumulates_actions(self, temp_git_repo, capsys):
        """Test that progress tracking accumulates include/skip/discard actions."""
        # Create three files
        file1 = temp_git_repo / "file1.txt"
        file2 = temp_git_repo / "file2.txt"
        file3 = temp_git_repo / "file3.txt"
        file1.write_text("content1\n")
        file2.write_text("content2\n")
        file3.write_text("content3\n")
        subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Add files"], cwd=temp_git_repo, check=True)

        # Modify all three
        file1.write_text("modified1\n")
        file2.write_text("modified2\n")
        file3.write_text("modified3\n")

        # Start session
        command_start()

        # Include first file
        capsys.readouterr()
        command_include()
        captured = capsys.readouterr()
        assert "Hunk staged" in captured.err or "Hunk staged" in captured.out

        # Skip second file
        capsys.readouterr()
        command_skip()
        captured = capsys.readouterr()
        assert "Hunk skipped" in captured.err or "Hunk skipped" in captured.out

        # Discard third file
        capsys.readouterr()
        command_discard()
        captured = capsys.readouterr()
        assert "Hunk discarded" in captured.err or "Hunk discarded" in captured.out

        # Check status shows progress
        capsys.readouterr()
        command_status()
        output = capsys.readouterr().out
        # Should mention processed hunks
        assert "1" in output or "included" in output.lower()

        # Clean up
        command_stop()

    def test_again_clears_progress_state_files(self, temp_git_repo):
        """Test that 'again' command clears progress state files."""
        # Create a file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("content\n")
        subprocess.run(["git", "add", "test.txt"], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Add test"], cwd=temp_git_repo, check=True)

        # Modify it
        test_file.write_text("modified\n")

        # Start and include
        command_start()
        command_include()

        # Progress state files should exist
        from git_stage_batch.state import (
            get_included_hunks_file_path,
            get_iteration_count_file_path,
        )

        included_path = get_included_hunks_file_path()
        iteration_path = get_iteration_count_file_path()

        # Files should exist after include
        state_dir = get_state_directory_path()
        assert state_dir.exists()
        assert included_path.exists()

        # Get iteration count before 'again'
        from git_stage_batch.commands import get_iteration_count
        old_iteration = get_iteration_count()

        # Run again - should clear state
        command_again()

        # State directory should still exist but included hunks should be cleared
        assert state_dir.exists()
        assert not included_path.exists()
        # Iteration count should be incremented
        assert iteration_path.exists()
        assert get_iteration_count() == old_iteration + 1

        # Clean up
        command_stop()

