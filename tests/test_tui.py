"""Tests for TUI interactive mode."""

import subprocess
from unittest.mock import patch

import pytest

from git_stage_batch.tui import handle_quit, print_help, start_interactive_mode


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


class TestPrintHelp:
    """Tests for print_help function."""

    def test_print_help_without_color(self, capsys):
        """Test help printing without colors."""
        with patch("sys.stdout.isatty", return_value=False):
            print_help()
            captured = capsys.readouterr()

        assert "Interactive Mode Commands:" in captured.out
        assert "i, include" in captured.out
        assert "s, skip" in captured.out
        assert "d, discard" in captured.out
        assert "q, quit" in captured.out
        assert "?, help" in captured.out

    def test_print_help_with_color(self, capsys):
        """Test help printing with colors enabled."""
        with patch("sys.stdout.isatty", return_value=True):
            print_help()
            captured = capsys.readouterr()

        assert "Interactive Mode Commands:" in captured.out


class TestHandleQuit:
    """Tests for handle_quit function."""

    def test_handle_quit_no_start_state(self, temp_git_repo):
        """Test quit when no start state exists."""
        with patch("git_stage_batch.tui.command_stop") as mock_stop:
            handle_quit()
            mock_stop.assert_called_once()

    def test_handle_quit_no_changes_silent(self, temp_git_repo):
        """Test quit silently stops when no changes made."""
        # Set up start state matching current state
        from git_stage_batch.state import (
            ensure_state_directory_exists,
            get_start_head_file_path,
            get_start_index_tree_file_path,
            run_git_command,
            write_text_file_contents,
        )

        ensure_state_directory_exists()

        # Record current state as start state
        head = run_git_command(["rev-parse", "HEAD"]).stdout.strip()
        index_tree = run_git_command(["write-tree"]).stdout.strip()

        write_text_file_contents(get_start_head_file_path(), head)
        write_text_file_contents(get_start_index_tree_file_path(), index_tree)

        # Quit should call stop without prompting
        with patch("git_stage_batch.tui.command_stop") as mock_stop:
            with patch("git_stage_batch.tui.prompt_quit_session") as mock_prompt:
                handle_quit()
                mock_stop.assert_called_once()
                mock_prompt.assert_not_called()

    def test_handle_quit_with_changes_keep(self, temp_git_repo):
        """Test quit with changes and user chooses to keep."""
        from git_stage_batch.state import (
            ensure_state_directory_exists,
            get_start_head_file_path,
            get_start_index_tree_file_path,
            run_git_command,
            write_text_file_contents,
        )

        ensure_state_directory_exists()

        # Record start state
        head = run_git_command(["rev-parse", "HEAD"]).stdout.strip()
        old_index_tree = run_git_command(["write-tree"]).stdout.strip()

        write_text_file_contents(get_start_head_file_path(), head)
        write_text_file_contents(get_start_index_tree_file_path(), old_index_tree)

        # Make a change to the index
        (temp_git_repo / "test.txt").write_text("new file\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)

        # Quit with "keep" choice
        with patch("git_stage_batch.tui.prompt_quit_session", return_value="keep"):
            with patch("git_stage_batch.tui.command_stop") as mock_stop:
                with patch("git_stage_batch.tui.command_abort") as mock_abort:
                    handle_quit()
                    mock_stop.assert_called_once()
                    mock_abort.assert_not_called()

    def test_handle_quit_with_changes_undo(self, temp_git_repo):
        """Test quit with changes and user chooses to undo."""
        from git_stage_batch.state import (
            ensure_state_directory_exists,
            get_start_head_file_path,
            get_start_index_tree_file_path,
            run_git_command,
            write_text_file_contents,
        )

        ensure_state_directory_exists()

        # Record start state
        head = run_git_command(["rev-parse", "HEAD"]).stdout.strip()
        old_index_tree = run_git_command(["write-tree"]).stdout.strip()

        write_text_file_contents(get_start_head_file_path(), head)
        write_text_file_contents(get_start_index_tree_file_path(), old_index_tree)

        # Make a change to the index
        (temp_git_repo / "test.txt").write_text("new file\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)

        # Quit with "undo" choice
        with patch("git_stage_batch.tui.prompt_quit_session", return_value="undo"):
            with patch("git_stage_batch.tui.command_stop") as mock_stop:
                with patch("git_stage_batch.tui.command_abort") as mock_abort:
                    handle_quit()
                    mock_stop.assert_not_called()
                    mock_abort.assert_called_once()

    def test_handle_quit_with_changes_cancel(self, temp_git_repo):
        """Test quit with changes and user cancels."""
        from git_stage_batch.state import (
            ensure_state_directory_exists,
            get_start_head_file_path,
            get_start_index_tree_file_path,
            run_git_command,
            write_text_file_contents,
        )

        ensure_state_directory_exists()

        # Record start state
        head = run_git_command(["rev-parse", "HEAD"]).stdout.strip()
        old_index_tree = run_git_command(["write-tree"]).stdout.strip()

        write_text_file_contents(get_start_head_file_path(), head)
        write_text_file_contents(get_start_index_tree_file_path(), old_index_tree)

        # Make a change to the index
        (temp_git_repo / "test.txt").write_text("new file\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)

        # Quit with "cancel" choice
        with patch("git_stage_batch.tui.prompt_quit_session", return_value="cancel"):
            with patch("git_stage_batch.tui.command_stop") as mock_stop:
                with patch("git_stage_batch.tui.command_abort") as mock_abort:
                    handle_quit()
                    # Neither should be called on cancel
                    mock_stop.assert_not_called()
                    mock_abort.assert_not_called()


class TestStartInteractiveMode:
    """Tests for start_interactive_mode function."""

    def test_start_interactive_exits_cleanly_when_no_hunks_at_startup(self, temp_git_repo, capsys):
        """Test that interactive mode exits cleanly when command_start finds no hunks."""
        from git_stage_batch.state import CommandError

        # Test the actual bug scenario: command_start raises CommandError when no hunks
        with patch("git_stage_batch.tui.command_start") as mock_start:
            mock_start.side_effect = CommandError("", exit_code=2)

            with pytest.raises(SystemExit) as exc_info:
                start_interactive_mode()

            assert exc_info.value.code == 2

    def test_start_interactive_no_hunks_real_integration(self, temp_git_repo, capsys):
        """Test that 'No pending hunks' message appears exactly once."""
        # Don't mock anything - test the real integration
        with pytest.raises(SystemExit) as exc_info:
            start_interactive_mode()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        # Message should appear exactly once (to stderr)
        assert captured.err.count("No pending hunks") == 1
        # And not to stdout
        assert "No pending hunks" not in captured.out

    def test_start_interactive_no_hunks(self, temp_git_repo, capsys):
        """Test starting interactive mode with no pending hunks after initialization."""
        # Mock command_start to succeed without finding hunks
        with patch("git_stage_batch.tui.command_start"):
            with patch("git_stage_batch.tui.load_current_lines_from_state", return_value=None):
                start_interactive_mode()
                captured = capsys.readouterr()

        # When main loop exits early (no hunks after start), no message is printed
        assert captured.out == ""

    def test_start_interactive_records_start_state(self, temp_git_repo):
        """Test that interactive mode records start HEAD and index tree."""
        from git_stage_batch.state import (
            get_start_head_file_path,
            get_start_index_tree_file_path,
        )

        # Mock to exit immediately
        with patch("git_stage_batch.tui.command_start"):
            with patch("git_stage_batch.tui.load_current_lines_from_state", return_value=None):
                start_interactive_mode()

        # Verify start state was recorded
        assert get_start_head_file_path().exists()
        assert get_start_index_tree_file_path().exists()
