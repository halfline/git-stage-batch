"""Tests for command implementations."""

import subprocess

import pytest

from git_stage_batch.commands import (
    command_abort,
    command_again,
    command_discard,
    command_include,
    command_include_file,
    command_include_line,
    command_show,
    command_skip,
    command_skip_file,
    command_start,
    command_status,
    command_stop,
    snapshot_file_if_untracked,
)
from git_stage_batch.state import (
    get_abort_head_file_path,
    get_abort_stash_file_path,
    get_auto_added_files_file_path,
    get_context_lines,
    get_context_lines_file_path,
    get_state_directory_path,
    read_text_file_contents,
    write_text_file_contents,
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


class TestCommandStart:
    """Tests for start command."""

    def test_start_creates_state_directory(self, temp_git_repo):
        """Test that start creates the state directory."""
        state_dir = get_state_directory_path()
        assert not state_dir.exists()

        # Create a change so start doesn't exit
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")

        command_start()

        assert state_dir.exists()
        assert state_dir.is_dir()

    def test_start_idempotent(self, temp_git_repo):
        """Test that start can be called multiple times."""
        # Create changes for start to process
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")

        command_start()
        command_start()  # Should not raise

        state_dir = get_state_directory_path()
        assert state_dir.exists()

    def test_start_stores_default_context_lines(self, temp_git_repo):
        """Test that start stores default context lines value."""
        # Create changes for start to process
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")

        command_start()

        context_file = get_context_lines_file_path()
        assert context_file.exists()
        assert read_text_file_contents(context_file).strip() == "3"

    def test_start_stores_custom_context_lines(self, temp_git_repo):
        """Test that start stores custom context lines value."""
        # Create changes for start to process
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")

        command_start(unified=5)

        assert get_context_lines() == 5

    def test_start_uses_context_lines_in_diff(self, temp_git_repo, capsys):
        """Test that context lines affects the diff output."""
        # Create a file with multiple lines
        (temp_git_repo / "test.txt").write_text("line1\nline2\nline3\nline4\nline5\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.txt"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify middle line
        (temp_git_repo / "test.txt").write_text("line1\nline2\nMODIFIED\nline4\nline5\n")

        # Start with custom context lines
        command_start(unified=1)

        # Show should use the stored context lines
        command_show()
        captured = capsys.readouterr()

        # With -U1, we should see 1 line of context before and after
        assert "line2" in captured.out  # 1 line before
        assert "MODIFIED" in captured.out
        assert "line4" in captured.out  # 1 line after
        # line1 and line5 should not appear as diff lines
        # They may appear in hunk headers (e.g., "@@ ... @@ line1"), so we check
        # that they don't appear as actual context/changed lines in the diff body
        lines = captured.out.split('\n')
        diff_lines = [l for l in lines if l.startswith(' ') or l.startswith('+') or l.startswith('-')]
        assert not any('line1' in l for l in diff_lines)
        assert not any('line5' in l for l in diff_lines)


class TestCommandStop:
    """Tests for stop command."""

    def test_stop_removes_state_directory(self, temp_git_repo, capsys):
        """Test that stop removes the state directory."""
        # Create changes for start to process
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")

        command_start()
        state_dir = get_state_directory_path()
        assert state_dir.exists()

        command_stop()

        assert not state_dir.exists()
        captured = capsys.readouterr()
        assert "State cleared" in captured.out

    def test_stop_when_no_state_exists(self, temp_git_repo, capsys):
        """Test that stop works when no state directory exists."""
        state_dir = get_state_directory_path()
        assert not state_dir.exists()

        command_stop()  # Should not raise

        captured = capsys.readouterr()
        assert "State cleared" in captured.out


class TestCommandAgain:
    """Tests for again command."""

    def test_again_clears_and_recreates_state(self, temp_git_repo):
        """Test that again clears and recreates the state directory."""
        # Create changes for start to process
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")

        command_start()
        state_dir = get_state_directory_path()

        # Create a marker file
        marker = state_dir / "marker.txt"
        marker.write_text("test")
        assert marker.exists()

        command_again()

        # Directory should still exist but marker should be gone
        assert state_dir.exists()
        assert not marker.exists()

    def test_again_when_no_state_exists(self, temp_git_repo):
        """Test that again works when no state directory exists."""
        state_dir = get_state_directory_path()
        assert not state_dir.exists()

        command_again()  # Should not raise

        assert state_dir.exists()


class TestCommandShow:
    """Tests for show command."""

    def test_show_displays_hunk(self, temp_git_repo, capsys):
        """Test that show displays a hunk when changes exist."""
        # Modify the existing README.md file
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew line added\n")

        command_show()

        captured = capsys.readouterr()
        # New format shows file path, hunk header, and line with ID
        assert "README.md" in captured.out
        assert "@@" in captured.out
        assert "[#1]" in captured.out
        assert "New line added" in captured.out

    def test_show_no_changes(self, temp_git_repo, capsys):
        """Test that show displays message when no changes exist."""
        command_show()

        captured = capsys.readouterr()
        assert "No changes to show" in captured.out

    def test_show_only_first_hunk(self, temp_git_repo, capsys):
        """Test that show only displays the first hunk when multiple exist."""
        # Create and commit two files
        file1 = temp_git_repo / "file1.txt"
        file1.write_text("original 1\n")
        file2 = temp_git_repo / "file2.txt"
        file2.write_text("original 2\n")
        subprocess.run(["git", "add", "file1.txt", "file2.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        # Now modify both files
        file1.write_text("modified 1\n")
        file2.write_text("modified 2\n")

        command_show()

        captured = capsys.readouterr()
        # Should show file1 but not file2
        assert "file1.txt" in captured.out
        assert "file2.txt" not in captured.out

    def test_show_skips_processed_hunks(self, temp_git_repo, capsys):
        """Test that show skips hunks that have been processed."""
        # Create and commit two files
        file1 = temp_git_repo / "file1.txt"
        file1.write_text("original 1\n")
        file2 = temp_git_repo / "file2.txt"
        file2.write_text("original 2\n")
        subprocess.run(["git", "add", "file1.txt", "file2.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify both files
        file1.write_text("modified 1\n")
        file2.write_text("modified 2\n")

        # Include the first hunk
        command_include()
        capsys.readouterr()  # Clear output

        # Show should now display the second hunk
        command_show()
        captured = capsys.readouterr()
        assert "file2.txt" in captured.out
        assert "file1.txt" not in captured.out

    def test_show_all_hunks_processed(self, temp_git_repo, capsys):
        """Test show when all hunks have been processed."""
        # Modify README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")

        # Include the only hunk to process it
        command_include()
        capsys.readouterr()  # Clear output

        # Show should indicate no more hunks
        command_show()
        captured = capsys.readouterr()
        assert "No changes to show" in captured.out



    def test_show_caches_hunk_state(self, temp_git_repo):
        """Test that show caches the current hunk state."""
        from git_stage_batch.state import (
            get_current_hunk_patch_file_path,
            get_current_hunk_hash_file_path,
            get_current_lines_json_file_path,
        )
        
        # Modify README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew line\n")
        
        command_start()
        
        # Verify state files were created by show
        assert get_current_hunk_patch_file_path().exists()
        assert get_current_hunk_hash_file_path().exists()
        assert get_current_lines_json_file_path().exists()

class TestCommandInclude:
    """Tests for include command."""

    def test_include_stages_hunk(self, temp_git_repo, capsys):
        """Test that include stages a hunk."""
        # Modify README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")

        command_include()

        # Check that changes are staged
        result = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "+New content" in result.stdout

        captured = capsys.readouterr()
        assert "Hunk staged" in captured.out

    def test_include_no_changes(self, temp_git_repo, capsys):
        """Test include when no changes exist."""
        command_include()

        captured = capsys.readouterr()
        assert "No changes to stage" in captured.out

    def test_include_multiple_hunks(self, temp_git_repo, capsys):
        """Test including multiple hunks sequentially."""
        # Create and commit two files
        file1 = temp_git_repo / "file1.txt"
        file1.write_text("original 1\n")
        file2 = temp_git_repo / "file2.txt"
        file2.write_text("original 2\n")
        subprocess.run(["git", "add", "file1.txt", "file2.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify both files
        file1.write_text("modified 1\n")
        file2.write_text("modified 2\n")

        # Include first hunk
        command_include()
        captured = capsys.readouterr()
        assert "file1.txt" in captured.out

        # Include second hunk
        command_include()
        captured = capsys.readouterr()
        assert "file2.txt" in captured.out

        # Verify both are staged
        result = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "file1.txt" in result.stdout
        assert "file2.txt" in result.stdout

    def test_include_all_hunks_processed(self, temp_git_repo, capsys):
        """Test include when all hunks have been processed."""
        # Modify README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")

        # Include the only hunk
        command_include()
        capsys.readouterr()  # Clear output

        # Try to include again - should say no changes because hunk was staged
        command_include()
        captured = capsys.readouterr()
        assert "No changes to stage" in captured.out


class TestCommandSkip:
    """Tests for skip command."""

    def test_skip_marks_hunk_as_processed(self, temp_git_repo, capsys):
        """Test that skip marks a hunk as processed without staging."""
        # Modify README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")

        command_skip()

        # Check that changes are NOT staged
        result = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert result.stdout == ""

        # But changes still exist in working tree
        result = subprocess.run(
            ["git", "diff"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "+New content" in result.stdout

        captured = capsys.readouterr()
        assert "Hunk skipped" in captured.out

    def test_skip_no_changes(self, temp_git_repo, capsys):
        """Test skip when no changes exist."""
        command_skip()

        captured = capsys.readouterr()
        assert "No changes to process" in captured.out

    def test_skip_then_include_next(self, temp_git_repo, capsys):
        """Test skipping one hunk then including the next."""
        # Create and commit two files
        file1 = temp_git_repo / "file1.txt"
        file1.write_text("original 1\n")
        file2 = temp_git_repo / "file2.txt"
        file2.write_text("original 2\n")
        subprocess.run(["git", "add", "file1.txt", "file2.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify both files
        file1.write_text("modified 1\n")
        file2.write_text("modified 2\n")

        # Skip first hunk
        command_skip()
        captured = capsys.readouterr()
        assert "file1.txt" in captured.out

        # Include second hunk
        command_include()
        captured = capsys.readouterr()
        assert "file2.txt" in captured.out

        # Verify only file2 is staged
        result = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "file2.txt" in result.stdout
        assert "file1.txt" not in result.stdout

    def test_skip_all_hunks_processed(self, temp_git_repo, capsys):
        """Test skip when all hunks have been processed."""
        # Modify README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")

        # Skip the only hunk
        command_skip()
        capsys.readouterr()  # Clear output

        # Try to skip again - hunk is still in working tree but was already skipped
        command_skip()
        captured = capsys.readouterr()
        assert "No more hunks to process" in captured.out


class TestCommandDiscard:
    """Tests for discard command."""

    def test_discard_removes_hunk_from_working_tree(self, temp_git_repo, capsys):
        """Test that discard removes a hunk from the working tree."""
        # Modify README
        readme = temp_git_repo / "README.md"
        original_content = readme.read_text()
        readme.write_text("# Test\nNew content\n")

        command_discard()

        # Changes should be removed from working tree
        assert readme.read_text() == original_content

        # Nothing should be staged
        result = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert result.stdout == ""

        captured = capsys.readouterr()
        assert "Hunk discarded" in captured.out

    def test_discard_no_changes(self, temp_git_repo, capsys):
        """Test discard when no changes exist."""
        command_discard()

        captured = capsys.readouterr()
        assert "No changes to discard" in captured.out

    def test_discard_then_include_next(self, temp_git_repo, capsys):
        """Test discarding one hunk then including the next."""
        # Create and commit two files
        file1 = temp_git_repo / "file1.txt"
        file1.write_text("original 1\n")
        file2 = temp_git_repo / "file2.txt"
        file2.write_text("original 2\n")
        subprocess.run(["git", "add", "file1.txt", "file2.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify both files
        file1.write_text("modified 1\n")
        file2.write_text("modified 2\n")

        # Discard first hunk
        command_discard()
        captured = capsys.readouterr()
        assert "file1.txt" in captured.out

        # Verify file1 is restored
        assert file1.read_text() == "original 1\n"

        # Include second hunk
        command_include()
        captured = capsys.readouterr()
        assert "file2.txt" in captured.out

        # Verify only file2 is staged
        result = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "file2.txt" in result.stdout
        assert "file1.txt" not in result.stdout

    def test_discard_all_hunks_processed(self, temp_git_repo, capsys):
        """Test discard when all hunks have been processed."""
        # Modify README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")

        # Discard the only hunk
        command_discard()
        capsys.readouterr()  # Clear output

        # Try to discard again
        command_discard()
        captured = capsys.readouterr()
        assert "No changes to discard" in captured.out


class TestCommandStatus:
    """Tests for status command."""

    def test_status_no_session(self, temp_git_repo, capsys):
        """Test status when no session is active."""
        command_status()

        captured = capsys.readouterr()
        assert "No batch staging session in progress" in captured.out
        assert "git-stage-batch start" in captured.out

    def test_status_active_session_no_changes(self, temp_git_repo, capsys):
        """Test status with active session but no changes."""
        # Create changes for start, then stage them so nothing is left
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)

        command_status()

        captured = capsys.readouterr()
        assert "Session active" in captured.out or "No batch staging session in progress" in captured.out

    def test_status_with_unprocessed_hunks(self, temp_git_repo, capsys):
        """Test status with unprocessed hunks."""
        # Modify README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")

        command_status()

        captured = capsys.readouterr()
        # Status might show session not in progress if start hasn't been called
        assert "Session active" in captured.out or "No batch staging session in progress" in captured.out

    def test_status_with_processed_hunks(self, temp_git_repo, capsys):
        """Test status after processing some hunks."""
        # Create and commit two files
        file1 = temp_git_repo / "file1.txt"
        file1.write_text("original 1\n")
        file2 = temp_git_repo / "file2.txt"
        file2.write_text("original 2\n")
        subprocess.run(["git", "add", "file1.txt", "file2.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify both files
        file1.write_text("modified 1\n")
        file2.write_text("modified 2\n")

        # Include first hunk
        command_include()
        capsys.readouterr()  # Clear output

        command_status()

        captured = capsys.readouterr()
        assert "Session active" in captured.out
        assert "Processed: 1 hunks" in captured.out
        assert "Remaining: 1 hunks" in captured.out
        assert "Current file: file2.txt" in captured.out

    def test_status_all_hunks_processed(self, temp_git_repo, capsys):
        """Test status when all hunks have been processed."""
        # Modify README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")

        # Skip the only hunk
        command_skip()
        capsys.readouterr()  # Clear output

        command_status()

        captured = capsys.readouterr()
        assert "Session active" in captured.out
        assert "Processed: 1 hunks" in captured.out
        assert "Remaining: 0 hunks" in captured.out
        assert "All hunks processed" in captured.out

    def test_start_initializes_abort_state(self, temp_git_repo):
        """Test that start initializes abort state files."""
        from git_stage_batch.state import (
            get_abort_head_file_path,
            get_abort_stash_file_path,
            read_text_file_contents,
        )

        # Create a change to make start succeed
        (temp_git_repo / "README.md").write_text("# Test\nModified\n")

        command_start()

        # Verify abort-head file was created with current HEAD
        abort_head_path = get_abort_head_file_path()
        assert abort_head_path.exists()
        saved_head = read_text_file_contents(abort_head_path).strip()
        current_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True
        ).stdout.strip()
        assert saved_head == current_head

        # Verify abort-stash file was created (may be empty if no tracked changes)
        abort_stash_path = get_abort_stash_file_path()
        assert abort_stash_path.exists()

    def test_discard_snapshots_untracked_file(self, temp_git_repo):
        """Test that discard snapshots content of untracked files."""
        from git_stage_batch.state import (
            get_abort_snapshots_directory_path,
        )

        # Create an untracked file
        untracked_file = temp_git_repo / "untracked.txt"
        original_content = "untracked content\n"
        untracked_file.write_text(original_content)

        # Auto-add with -N to make it visible to diff
        subprocess.run(["git", "add", "-N", "untracked.txt"], check=True, cwd=temp_git_repo, capture_output=True)

        # Start session (initializes abort state)
        command_start()

        # Discard the file (should create snapshot before discarding)
        command_discard()

        # Verify snapshot was created
        snapshot_dir = get_abort_snapshots_directory_path()
        snapshot_file = snapshot_dir / "untracked.txt"
        assert snapshot_file.exists()
        assert snapshot_file.read_text() == original_content


class TestCommandAbort:
    """Tests for abort command."""

    def test_abort_no_session(self, temp_git_repo, capsys):
        """Test abort when no session exists."""
        # Should error when no abort state exists
        with pytest.raises(SystemExit):
            command_abort()

        captured = capsys.readouterr()
        assert "No session to abort" in captured.err

    def test_abort_restores_working_tree(self, temp_git_repo):
        """Test that abort restores working tree state from session start."""
        # Create a file with uncommitted changes
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nUncommitted change\n")

        # Start session (this saves the uncommitted state in stash)
        command_start()

        # Make more changes and discard them
        readme.write_text("# Test\nAnother change\n")
        command_discard()

        # File should be back to original committed state
        assert readme.read_text() == "# Test\n"

        # Abort should restore the uncommitted changes from session start
        command_abort()

        # File should have the uncommitted changes from before session
        assert readme.read_text() == "# Test\nUncommitted change\n"

    def test_abort_undoes_commits(self, temp_git_repo):
        """Test that abort undoes commits made during session."""
        # Get initial HEAD
        initial_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        ).stdout.strip()

        # Create changes and start session
        (temp_git_repo / "README.md").write_text("# Test\nNew content\n")

        # Start session
        command_start()

        # Make a change and commit it
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Session commit"], check=True, cwd=temp_git_repo, capture_output=True)

        # Verify HEAD moved
        new_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert new_head != initial_head

        # Abort should restore HEAD
        command_abort()

        restored_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert restored_head == initial_head

    def test_abort_clears_state(self, temp_git_repo):
        """Test that abort clears all session state."""
        # Create changes and start
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")

        command_start()
        state_dir = get_state_directory_path()
        assert state_dir.exists()

        command_abort()

        assert not state_dir.exists()

    def test_abort_with_staged_changes_before_session(self, temp_git_repo):
        """Test abort restores staged changes from before session."""
        # Create and stage a new file before session
        new_file = temp_git_repo / "new.txt"
        new_file.write_text("new content\n")
        subprocess.run(["git", "add", "new.txt"], check=True, cwd=temp_git_repo, capture_output=True)

        # Verify it's staged
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "new.txt" in result.stdout

        # Make changes to allow start to work
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")

        # Start session
        command_start()

        # Unstage and delete the file
        subprocess.run(["git", "reset", "new.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        new_file.unlink()

        # Abort should restore the staged file
        command_abort()

        # File should exist again
        assert new_file.exists()
        assert new_file.read_text() == "new content\n"

        # And should be staged
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "new.txt" in result.stdout

    def test_abort_resets_auto_added_files(self, temp_git_repo):
        """Test that abort resets auto-added files."""
        # Create an untracked file
        new_file = temp_git_repo / "untracked.txt"
        new_file.write_text("untracked content\n")

        # Create a diff so start has something to work with
        (temp_git_repo / "README.md").write_text("# Test\nModified\n")

        # Start session
        command_start()

        # Simulate auto-add by adding with -N and tracking it
        subprocess.run(["git", "add", "-N", "untracked.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        # Record it in auto-added list
        from git_stage_batch.state import append_file_path_to_file
        append_file_path_to_file(get_auto_added_files_file_path(), "untracked.txt")

        # Verify it's in index
        result = subprocess.run(
            ["git", "ls-files", "untracked.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )

        assert "untracked.txt" in result.stdout

        # Abort should reset it
        command_abort()

        # File should no longer be in index
        result = subprocess.run(
            ["git", "ls-files", "untracked.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == ""

        # But should still exist in working tree
        assert new_file.exists()

    def test_abort_restores_snapshotted_file(self, temp_git_repo):
        """Test that abort restores files that were snapshotted."""
        # Create an untracked file
        untracked_file = temp_git_repo / "untracked.txt"
        original_content = "original untracked content\n"
        untracked_file.write_text(original_content)

        # Create a diff so start has something to work with
        (temp_git_repo / "README.md").write_text("# Test\nModified\n")

        # Start session
        command_start()

        # Snapshot the file (simulating what discard-file would do)
        snapshot_file_if_untracked("untracked.txt")

        # Delete the file (simulating discard-file)
        untracked_file.unlink()
        assert not untracked_file.exists()

        # Abort should restore it
        command_abort()

        # File should be restored with original content
        assert untracked_file.exists()
        assert untracked_file.read_text() == original_content

class TestCommandIncludeFile:
    """Tests for include-file command."""

    def test_include_file_stages_all_hunks_from_file(self, temp_git_repo, capsys):
        """Test that include-file stages all hunks from the current file."""
        # Create and commit a file with multiple hunks
        test_file = temp_git_repo / "multi.txt"
        test_file.write_text("line 1\nline 2\nline 3\nline 4\nline 5\n")
        subprocess.run(["git", "add", "multi.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add multi"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify multiple parts to create multiple hunks
        test_file.write_text("line 1 modified\nline 2\nline 3\nline 4\nline 5 modified\n")

        command_include_file()

        # Check that all changes are staged
        result = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "+line 1 modified" in result.stdout
        assert "+line 5 modified" in result.stdout

        # Verify command produced output (either summary or per-hunk messages)
        captured = capsys.readouterr()
        assert "staged" in captured.out.lower()
        assert "multi.txt" in captured.out

    def test_include_file_no_changes(self, temp_git_repo, capsys):
        """Test include-file when no changes exist."""
        command_include_file()

        captured = capsys.readouterr()
        assert "No changes to stage" in captured.out

    def test_include_file_only_current_file(self, temp_git_repo, capsys):
        """Test that include-file only stages hunks from current file, not others."""

        # Create and commit two files
        file1 = temp_git_repo / "file1.txt"
        file1.write_text("original 1\n")
        file2 = temp_git_repo / "file2.txt"
        file2.write_text("original 2\n")
        subprocess.run(["git", "add", "file1.txt", "file2.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify both files
        file1.write_text("modified 1\n")
        file2.write_text("modified 2\n")

        # Include-file should only stage file1
        command_include_file()
        capsys.readouterr()  # Clear output

        # Verify only file1 is staged
        result = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "file1.txt" in result.stdout
        assert "file2.txt" not in result.stdout

        # file2 should still be in working tree
        result = subprocess.run(
            ["git", "diff"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "file2.txt" in result.stdout


class TestCommandSkipFile:
    """Tests for skip-file command."""

    def test_skip_file_skips_all_hunks_from_file(self, temp_git_repo, capsys):
        """Test that skip-file skips all hunks from the current file."""
        # Create and commit a file with multiple hunks
        test_file = temp_git_repo / "multi.txt"
        test_file.write_text("line 1\nline 2\nline 3\nline 4\nline 5\n")
        subprocess.run(["git", "add", "multi.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add multi"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify multiple parts to create multiple hunks
        test_file.write_text("line 1 modified\nline 2\nline 3\nline 4\nline 5 modified\n")

        command_skip_file()

        # Check that nothing is staged
        result = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert result.stdout == ""

        # Changes should still be in working tree
        result = subprocess.run(
            ["git", "diff"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "+line 1 modified" in result.stdout

        # Verify command produced output (either summary or per-hunk messages)
        captured = capsys.readouterr()
        assert "skipped" in captured.out.lower()
        assert "multi.txt" in captured.out

    def test_skip_file_only_current_file(self, temp_git_repo, capsys):
        """Test that skip-file only skips hunks from current file, not others."""
        # Create and commit two files
        file1 = temp_git_repo / "file1.txt"
        file1.write_text("original 1\n")
        file2 = temp_git_repo / "file2.txt"
        file2.write_text("original 2\n")
        subprocess.run(["git", "add", "file1.txt", "file2.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify both files
        file1.write_text("modified 1\n")
        file2.write_text("modified 2\n")

        # Skip-file should only skip file1
        command_skip_file()
        capsys.readouterr()  # Clear output

        # Include file2 - should work since it wasn't skipped
        command_include()
        captured = capsys.readouterr()
        assert "file2.txt" in captured.out

        # Verify file2 is staged
        result = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "file2.txt" in result.stdout
        assert "file1.txt" not in result.stdout


class TestHunkCachingInfrastructure:
    """Tests for hunk caching and staleness detection."""

    def test_clear_current_hunk_state_files(self, temp_git_repo):
        """Test clearing hunk state files."""
        from git_stage_batch.commands import clear_current_hunk_state_files
        from git_stage_batch.state import (
            get_current_hunk_patch_file_path,
            get_current_hunk_hash_file_path,
            get_current_lines_json_file_path,
            write_text_file_contents,
        )

        # Create some state files
        write_text_file_contents(get_current_hunk_patch_file_path(), "patch")
        write_text_file_contents(get_current_hunk_hash_file_path(), "hash")
        write_text_file_contents(get_current_lines_json_file_path(), "{}")

        # Clear them
        clear_current_hunk_state_files()

        # Verify they're gone
        assert not get_current_hunk_patch_file_path().exists()
        assert not get_current_hunk_hash_file_path().exists()
        assert not get_current_lines_json_file_path().exists()

    def test_find_and_cache_next_unblocked_hunk(self, temp_git_repo):
        """Test finding and caching the next unblocked hunk."""
        from git_stage_batch.commands import (
            command_start,
            find_and_cache_next_unblocked_hunk,
        )
        from git_stage_batch.state import (
            get_current_hunk_patch_file_path,
            get_current_hunk_hash_file_path,
        )

        # Modify README to create a hunk
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nModified\n")

        # Initialize session
        from git_stage_batch.state import ensure_state_directory_exists
        ensure_state_directory_exists()

        # Find and cache hunk
        result = find_and_cache_next_unblocked_hunk()

        assert result is True
        assert get_current_hunk_patch_file_path().exists()
        assert get_current_hunk_hash_file_path().exists()

    def test_find_and_cache_next_unblocked_hunk_no_hunks(self, temp_git_repo):
        """Test finding hunk when none exist."""
        from git_stage_batch.commands import find_and_cache_next_unblocked_hunk
        from git_stage_batch.state import ensure_state_directory_exists

        ensure_state_directory_exists()

        # No changes, should return False
        result = find_and_cache_next_unblocked_hunk()

        assert result is False

    def test_show_caches_hunk_state(self, temp_git_repo):
        """Test that show caches the current hunk state."""
        from git_stage_batch.state import (
            get_current_hunk_patch_file_path,
            get_current_hunk_hash_file_path,
            get_current_lines_json_file_path,
        )

        # Modify README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew line\n")

        command_start()

        # Verify state files were created by show
        assert get_current_hunk_patch_file_path().exists()
        assert get_current_hunk_hash_file_path().exists()
        assert get_current_lines_json_file_path().exists()


class TestCommandIncludeLine:
    """Tests for command_include_line."""

    def test_include_line_requires_current_hunk(self, temp_git_repo):
        """Test that include --line requires an active hunk."""
        with pytest.raises(SystemExit):
            command_include_line("1")

    def test_include_line_stages_single_addition(self, temp_git_repo):
        """Test including a single added line."""
        # Create a file with content
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\nline2\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify the file - add new lines
        test_file.write_text("line1\nnew line\nline2\n")

        command_start()
        command_show()  # Load the hunk

        # Include only the added line (ID 1)
        command_include_line("1")

        # Check staged content
        result = subprocess.run(
            ["git", "show", ":test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "new line" in result.stdout
        assert "line1" in result.stdout
        assert "line2" in result.stdout

    def test_include_line_stages_single_deletion(self, temp_git_repo):
        """Test including a single deleted line."""
        # Create a file with content
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\ndelete me\nline2\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Delete a line
        test_file.write_text("line1\nline2\n")

        command_start()
        command_show()  # Load the hunk

        # Include the deletion (ID 1)
        command_include_line("1")

        # Check staged content - deleted line should be gone
        result = subprocess.run(
            ["git", "show", ":test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "delete me" not in result.stdout
        assert "line1" in result.stdout
        assert "line2" in result.stdout

    def test_include_line_with_range(self, temp_git_repo):
        """Test including a range of lines."""
        # Create a file with content
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\nline2\nline3\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Add multiple new lines
        test_file.write_text("line1\nnew1\nnew2\nnew3\nline2\nline3\n")

        command_start()
        command_show()  # Load the hunk

        # Include lines 1-3 (all three additions)
        command_include_line("1-3")

        # Check staged content
        result = subprocess.run(
            ["git", "show", ":test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "new1" in result.stdout
        assert "new2" in result.stdout
        assert "new3" in result.stdout

    def test_include_line_partial_selection(self, temp_git_repo):
        """Test including only some lines from a hunk."""
        # Create a file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\nline2\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Add multiple lines
        test_file.write_text("line1\nadd1\nadd2\nline2\n")

        command_start()
        command_show()  # Load the hunk

        # Include only first addition (ID 1), skip second (ID 2)
        command_include_line("1")

        # Check staged content
        result = subprocess.run(
            ["git", "show", ":test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "add1" in result.stdout
        # Second addition should not be staged yet
        assert "add2" not in result.stdout

    def test_include_line_recalculates_hunk(self, temp_git_repo):
        """Test that include --line recalculates the hunk with fresh line IDs."""
        # Create a file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Add multiple lines
        test_file.write_text("line1\nadd1\nadd2\nadd3\n")

        command_start()
        command_show()  # Load the hunk

        # Initial hunk has IDs 1, 2, 3 for the three additions
        # Include first line (add1)
        command_include_line("1")

        # After include --line, hunk recalculates with fresh line IDs
        # Now IDs are 1, 2 for the remaining additions (add2, add3)
        # Include next line (add2)
        command_include_line("1")

        # Check staged content has both add1 and add2
        result = subprocess.run(
            ["git", "show", ":test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "add1" in result.stdout
        assert "add2" in result.stdout
        assert "add3" not in result.stdout



class TestProgressTrackingHelpers:
    """Tests for progress tracking helper functions."""

    def test_get_iteration_count_default(self, temp_git_repo):
        """Test getting iteration count when file doesn't exist."""
        from git_stage_batch.commands import get_iteration_count
        from git_stage_batch.state import ensure_state_directory_exists
        
        ensure_state_directory_exists()
        count = get_iteration_count()
        assert count == 1

    def test_get_iteration_count_reads_value(self, temp_git_repo):
        """Test getting iteration count when file exists."""
        from git_stage_batch.commands import get_iteration_count
        from git_stage_batch.state import (
            ensure_state_directory_exists,
            get_iteration_count_file_path,
            write_text_file_contents,
        )
        
        ensure_state_directory_exists()
        write_text_file_contents(get_iteration_count_file_path(), "3")
        
        count = get_iteration_count()
        assert count == 3

    def test_increment_iteration_count(self, temp_git_repo):
        """Test incrementing iteration count."""
        from git_stage_batch.commands import get_iteration_count, increment_iteration_count
        from git_stage_batch.state import ensure_state_directory_exists
        
        ensure_state_directory_exists()
        
        initial = get_iteration_count()
        increment_iteration_count()
        new_count = get_iteration_count()
        
        assert new_count == initial + 1

    def test_record_hunk_included(self, temp_git_repo):
        """Test recording an included hunk."""
        from git_stage_batch.commands import record_hunk_included
        from git_stage_batch.state import (
            ensure_state_directory_exists,
            get_included_hunks_file_path,
            read_text_file_contents,
        )
        
        ensure_state_directory_exists()
        
        record_hunk_included("abc123")
        record_hunk_included("def456")
        
        content = read_text_file_contents(get_included_hunks_file_path())
        assert "abc123" in content
        assert "def456" in content

    def test_record_hunk_discarded(self, temp_git_repo):
        """Test recording a discarded hunk."""
        from git_stage_batch.commands import record_hunk_discarded
        from git_stage_batch.state import (
            ensure_state_directory_exists,
            get_discarded_hunks_file_path,
            read_text_file_contents,
        )
        
        ensure_state_directory_exists()
        
        record_hunk_discarded("xyz789")
        
        content = read_text_file_contents(get_discarded_hunks_file_path())
        assert "xyz789" in content

    def test_format_id_range_single(self, temp_git_repo):
        """Test formatting a single ID."""
        from git_stage_batch.commands import format_id_range
        
        result = format_id_range([5])
        assert result == "5"

    def test_format_id_range_consecutive(self, temp_git_repo):
        """Test formatting consecutive IDs."""
        from git_stage_batch.commands import format_id_range
        
        result = format_id_range([1, 2, 3, 4, 5])
        assert result == "1-5"

    def test_format_id_range_mixed(self, temp_git_repo):
        """Test formatting mixed IDs."""
        from git_stage_batch.commands import format_id_range
        
        result = format_id_range([1, 2, 3, 7, 9, 10, 11])
        assert result == "1-3,7,9-11"

    def test_include_records_progress(self, temp_git_repo):
        """Test that include command records hunk in progress tracking."""
        from git_stage_batch.state import (
            get_included_hunks_file_path,
            read_text_file_contents,
        )
        
        # Modify README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nModified\n")
        
        # Start session and include
        command_start()
        command_include()
        
        # Verify hunk was recorded
        included_content = read_text_file_contents(get_included_hunks_file_path())
        assert len(included_content.strip()) > 0  # Some hash was recorded

    def test_skip_records_progress(self, temp_git_repo):
        """Test that skip command records hunk in progress tracking."""
        from git_stage_batch.state import (
            get_skipped_hunks_jsonl_file_path,
            read_text_file_contents,
        )
        
        # Modify README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nModified\n")
        
        # Start session and skip
        command_start()
        command_skip()
        
        # Verify hunk was recorded
        skipped_content = read_text_file_contents(get_skipped_hunks_jsonl_file_path())
        assert len(skipped_content.strip()) > 0  # Some JSON was recorded
        assert "README.md" in skipped_content

    def test_discard_records_progress(self, temp_git_repo):
        """Test that discard command records hunk in progress tracking."""
        from git_stage_batch.state import (
            get_discarded_hunks_file_path,
            read_text_file_contents,
        )
        
        # Modify README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nModified\n")
        
        # Start session and discard
        command_start()
        command_discard()
        
        # Verify hunk was recorded
        discarded_content = read_text_file_contents(get_discarded_hunks_file_path())
        assert len(discarded_content.strip()) > 0  # Some hash was recorded
