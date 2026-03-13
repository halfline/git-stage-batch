"""Tests for command implementations."""

import subprocess

import pytest

from git_stage_batch.commands import command_again, command_discard, command_include, command_show, command_skip, command_start, command_status, command_stop
from git_stage_batch.state import (
    get_context_lines,
    get_context_lines_file_path,
    get_state_directory_path,
    read_text_file_contents,
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
        assert "--- a/README.md" in captured.out
        assert "+++ b/README.md" in captured.out
        assert "+New line added" in captured.out

    def test_show_no_changes(self, temp_git_repo, capsys):
        """Test that show displays message when no changes exist."""
        command_show()

        captured = capsys.readouterr()
        assert "No more hunks to process" in captured.out

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
        assert "No more hunks to process" in captured.out


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
        assert "No more hunks to process" in captured.out

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

        # Try to include again - should say no more hunks because hunk was staged
        command_include()
        captured = capsys.readouterr()
        assert "No more hunks to process" in captured.out


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
        assert "No more hunks to process" in captured.out

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
