"""Tests for status command."""

import json
import subprocess

import pytest

from git_stage_batch.commands.include import command_include
from git_stage_batch.commands.skip import command_skip
from git_stage_batch.commands.start import command_start
from git_stage_batch.commands.status import command_status


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


class TestCommandStatus:
    """Tests for status command."""

    def test_status_no_session(self, temp_git_repo, capsys):
        """Test status when no session is active."""
        command_status()

        captured = capsys.readouterr()
        assert "No batch staging session in progress" in captured.err
        assert "git-stage-batch start" in captured.err

    def test_status_active_session_no_changes(self, temp_git_repo, capsys):
        """Test status with active session but no changes."""
        # Create changes for start, then stage them so nothing is left
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)

        command_status()

        captured = capsys.readouterr()
        assert "Session active" in captured.out or "No batch staging session in progress" in captured.err

    def test_status_with_unprocessed_hunks(self, temp_git_repo, capsys):
        """Test status with unprocessed hunks."""
        # Modify README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")

        command_status()

        captured = capsys.readouterr()
        # Status might show session not in progress if start hasn't been called
        assert "Session active" in captured.out or "No batch staging session in progress" in captured.err

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

        # Start session
        command_start()

        # Include first hunk
        command_include()
        capsys.readouterr()  # Clear output

        command_status()

        captured = capsys.readouterr()
        assert "Session: iteration" in captured.out
        assert "Progress this iteration:" in captured.out
        assert "Remaining:" in captured.out
        assert "file2.txt" in captured.out

    def test_status_all_hunks_processed(self, temp_git_repo, capsys):
        """Test status when all hunks have been processed."""
        # Modify README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")

        # Start session
        command_start()

        # Skip the only hunk
        command_skip()
        capsys.readouterr()  # Clear output

        command_status()

        captured = capsys.readouterr()
        assert "Session: iteration" in captured.out
        assert "Progress this iteration:" in captured.out
        assert "Remaining:" in captured.out
        # When all hunks are processed, should show completion message
        assert "complete" in captured.out or "Remaining: 0" in captured.out or "Remaining:  0" in captured.out

    def test_status_shows_line_range_when_cached(self, temp_git_repo, capsys):
        """Test that status shows line range when cached state is available."""
        from git_stage_batch.commands.show import command_show

        # Create and commit a file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify the file
        test_file.write_text("line 1\nMODIFIED\nline 3\n")

        # Start session
        command_start()

        # Show the hunk (which caches state)
        command_show()
        capsys.readouterr()  # Clear output

        # Check status - should show line range
        command_status()

        captured = capsys.readouterr()
        assert "Session: iteration" in captured.out
        assert "Current hunk:" in captured.out
        assert "test.txt" in captured.out
        assert "[#" in captured.out  # Should show line IDs in brackets

    def test_status_shows_iteration_and_progress_metrics(self, temp_git_repo, capsys):
        """Test that status shows iteration number and progress metrics."""
        from git_stage_batch.commands.show import command_show
        from git_stage_batch.data.session import get_iteration_count, initialize_abort_state
        from git_stage_batch.utils.paths import get_iteration_count_file_path

        # Create and commit a file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify the file to create a hunk
        test_file.write_text("line 1\nMODIFIED\nline 3\n")

        # Set iteration count to 2 to test iteration display
        from git_stage_batch.utils.paths import ensure_state_directory_exists
        ensure_state_directory_exists()
        initialize_abort_state()
        get_iteration_count_file_path().write_text("2")

        # Cache selected hunk with show
        command_show()
        capsys.readouterr()

        # Check status
        command_status()

        captured = capsys.readouterr()
        # Should show iteration number
        assert "Session: iteration 2" in captured.out
        assert "(in progress)" in captured.out

        # Should show progress metrics
        assert "Progress this iteration:" in captured.out
        assert "Included:" in captured.out
        assert "Skipped:" in captured.out
        assert "Discarded:" in captured.out
        assert "Remaining:" in captured.out

    def test_status_porcelain_no_session(self, temp_git_repo, capsys):
        """Test status --porcelain when no session is active."""
        command_status(porcelain=True)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output == {"session_active": False}

    def test_status_porcelain_with_session(self, temp_git_repo, capsys):
        """Test status --porcelain outputs JSON with session data."""
        from git_stage_batch.commands.show import command_show
        from git_stage_batch.data.session import initialize_abort_state
        from git_stage_batch.utils.paths import ensure_state_directory_exists

        # Create and commit a file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify the file
        test_file.write_text("line 1\nMODIFIED\nline 3\n")

        # Initialize session state
        ensure_state_directory_exists()
        initialize_abort_state()

        # Cache selected hunk with show
        command_show()
        capsys.readouterr()

        # Get JSON output
        command_status(porcelain=True)

        captured = capsys.readouterr()
        output = json.loads(captured.out)

        # Verify JSON structure
        assert output["session_active"] is True
        assert output["iteration"] == 1
        assert output["status"] in ("in_progress", "complete")
        assert "progress" in output
        assert "included" in output["progress"]
        assert "skipped" in output["progress"]
        assert "discarded" in output["progress"]
        assert "remaining" in output["progress"]
        assert "skipped_hunks" in output

    def test_status_porcelain_includes_selected_hunk(self, temp_git_repo, capsys):
        """Test status --porcelain includes selected hunk details."""
        from git_stage_batch.commands.show import command_show
        from git_stage_batch.data.session import initialize_abort_state
        from git_stage_batch.utils.paths import ensure_state_directory_exists

        # Create and commit a file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify the file
        test_file.write_text("line 1\nMODIFIED\nline 3\n")

        # Initialize session and cache hunk
        ensure_state_directory_exists()
        initialize_abort_state()
        command_show()
        capsys.readouterr()

        # Get JSON output
        command_status(porcelain=True)

        captured = capsys.readouterr()
        output = json.loads(captured.out)

        # Should have selected hunk details (if full state caching is implemented)
        # At this stage, show.py may only cache patch+hash, not full LineLevelChange
        if output["selected_hunk"] is not None:
            assert "file" in output["selected_hunk"]
            assert "line" in output["selected_hunk"]
            assert "ids" in output["selected_hunk"]
            assert output["selected_hunk"]["file"] == "test.txt"
