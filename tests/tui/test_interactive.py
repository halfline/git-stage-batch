"""Tests for TUI interactive mode."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from git_stage_batch.tui.interactive import (
    handle_file_selection,
    handle_line_selection,
    handle_quit,
    print_help,
    start_interactive_mode,
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
        with patch("git_stage_batch.commands.stop.command_stop") as mock_stop:
            handle_quit()
            mock_stop.assert_called_once()

    def test_handle_quit_no_changes_silent(self, temp_git_repo):
        """Test quit silently stops when no changes made."""
        # Set up start state matching current state
        from git_stage_batch.utils.file_io import write_text_file_contents
        from git_stage_batch.utils.git import run_git_command
        from git_stage_batch.utils.paths import (
            ensure_state_directory_exists,
            get_start_head_file_path,
            get_start_index_tree_file_path,
        )

        ensure_state_directory_exists()

        # Record current state as start state
        head = run_git_command(["rev-parse", "HEAD"]).stdout.strip()
        index_tree = run_git_command(["write-tree"]).stdout.strip()

        write_text_file_contents(get_start_head_file_path(), head)
        write_text_file_contents(get_start_index_tree_file_path(), index_tree)

        # Quit should call stop without prompting
        with patch("git_stage_batch.commands.stop.command_stop") as mock_stop:
            with patch("git_stage_batch.tui.interactive.prompt_quit_session") as mock_prompt:
                handle_quit()
                mock_stop.assert_called_once()
                mock_prompt.assert_not_called()

    def test_handle_quit_with_changes_keep(self, temp_git_repo):
        """Test quit with changes and user chooses to keep."""
        from git_stage_batch.utils.file_io import write_text_file_contents
        from git_stage_batch.utils.git import run_git_command
        from git_stage_batch.utils.paths import (
            ensure_state_directory_exists,
            get_start_head_file_path,
            get_start_index_tree_file_path,
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
        with patch("git_stage_batch.tui.interactive.prompt_quit_session", return_value="keep"):
            with patch("git_stage_batch.commands.stop.command_stop") as mock_stop:
                with patch("git_stage_batch.commands.abort.command_abort") as mock_abort:
                    handle_quit()
                    mock_stop.assert_called_once()
                    mock_abort.assert_not_called()

    def test_handle_quit_with_changes_undo(self, temp_git_repo):
        """Test quit with changes and user chooses to undo."""
        from git_stage_batch.utils.file_io import write_text_file_contents
        from git_stage_batch.utils.git import run_git_command
        from git_stage_batch.utils.paths import (
            ensure_state_directory_exists,
            get_start_head_file_path,
            get_start_index_tree_file_path,
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
        with patch("git_stage_batch.tui.interactive.prompt_quit_session", return_value="undo"):
            with patch("git_stage_batch.commands.stop.command_stop") as mock_stop:
                with patch("git_stage_batch.commands.abort.command_abort") as mock_abort:
                    handle_quit()
                    mock_stop.assert_not_called()
                    mock_abort.assert_called_once()

    def test_handle_quit_with_changes_cancel(self, temp_git_repo):
        """Test quit with changes and user cancels."""
        from git_stage_batch.utils.file_io import write_text_file_contents
        from git_stage_batch.utils.git import run_git_command
        from git_stage_batch.utils.paths import (
            ensure_state_directory_exists,
            get_start_head_file_path,
            get_start_index_tree_file_path,
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
        with patch("git_stage_batch.tui.interactive.prompt_quit_session", return_value="cancel"):
            with patch("git_stage_batch.commands.stop.command_stop") as mock_stop:
                with patch("git_stage_batch.commands.abort.command_abort") as mock_abort:
                    handle_quit()
                    # Neither should be called on cancel
                    mock_stop.assert_not_called()
                    mock_abort.assert_not_called()


class TestHandleFileSelection:
    """Tests for handle_file_selection function."""

    def test_handle_file_selection_include(self):
        """Test file selection with include action."""
        from git_stage_batch.core.models import CurrentLines, HunkHeader, LineEntry

        header = HunkHeader(old_start=1, old_len=1, new_start=1, new_len=1)
        current_lines = CurrentLines(
            path="test.txt",
            header=header,
            lines=[LineEntry(id=1, kind="+", old_line_number=None, new_line_number=1, text="test\n")]
        )

        with patch("git_stage_batch.tui.interactive.load_current_lines_from_state", return_value=current_lines):
            with patch("git_stage_batch.commands.include.command_include_file") as mock_include:
                with patch("git_stage_batch.tui.interactive.find_and_cache_next_unblocked_hunk", return_value=None):
                    with patch("builtins.input", return_value="i"):
                        handle_file_selection()
                        mock_include.assert_called_once()

    def test_handle_file_selection_skip(self):
        """Test file selection with skip action."""
        from git_stage_batch.core.models import CurrentLines, HunkHeader, LineEntry

        header = HunkHeader(old_start=1, old_len=1, new_start=1, new_len=1)
        current_lines = CurrentLines(
            path="test.txt",
            header=header,
            lines=[LineEntry(id=1, kind="+", old_line_number=None, new_line_number=1, text="test\n")]
        )

        with patch("git_stage_batch.tui.interactive.load_current_lines_from_state", return_value=current_lines):
            with patch("git_stage_batch.commands.skip.command_skip_file") as mock_skip:
                with patch("git_stage_batch.tui.interactive.find_and_cache_next_unblocked_hunk", return_value=None):
                    with patch("builtins.input", return_value="s"):
                        handle_file_selection()
                        mock_skip.assert_called_once()

    def test_handle_file_selection_cancel(self):
        """Test file selection with Ctrl-C."""
        from git_stage_batch.core.models import CurrentLines, HunkHeader, LineEntry

        header = HunkHeader(old_start=1, old_len=1, new_start=1, new_len=1)
        current_lines = CurrentLines(
            path="test.txt",
            header=header,
            lines=[LineEntry(id=1, kind="+", old_line_number=None, new_line_number=1, text="test\n")]
        )

        with patch("git_stage_batch.tui.interactive.load_current_lines_from_state", return_value=current_lines):
            with patch("builtins.input", side_effect=KeyboardInterrupt):
                # Should return without raising
                handle_file_selection()


class TestHandleLineSelection:
    """Tests for handle_line_selection function."""

    def test_handle_line_selection_include(self):
        """Test line selection with include action."""
        from git_stage_batch.core.models import CurrentLines, HunkHeader, LineEntry

        header = HunkHeader(old_start=1, old_len=2, new_start=1, new_len=2)
        current_lines = CurrentLines(
            path="test.txt",
            header=header,
            lines=[
                LineEntry(id=1, kind="+", old_line_number=None, new_line_number=1, text="line1\n"),
                LineEntry(id=2, kind="+", old_line_number=None, new_line_number=2, text="line2\n"),
            ]
        )

        with patch("git_stage_batch.tui.interactive.load_current_lines_from_state", return_value=current_lines):
            with patch("git_stage_batch.commands.include.command_include_line") as mock_include:
                with patch("git_stage_batch.tui.interactive.prompt_line_ids", return_value="1"):
                    with patch("builtins.input", return_value="i"):
                        handle_line_selection()
                        mock_include.assert_called_once_with("1")

    def test_handle_line_selection_no_changed_lines(self):
        """Test line selection when no changed lines exist."""
        from git_stage_batch.core.models import CurrentLines, HunkHeader, LineEntry

        header = HunkHeader(old_start=1, old_len=1, new_start=1, new_len=1)
        current_lines = CurrentLines(
            path="test.txt",
            header=header,
            lines=[LineEntry(id=None, kind=" ", old_line_number=1, new_line_number=1, text=" unchanged\n")]
        )

        with patch("git_stage_batch.tui.interactive.load_current_lines_from_state", return_value=current_lines):
            handle_line_selection()
            # Should return early without prompting

    def test_handle_line_selection_cancel(self):
        """Test line selection with Ctrl-C."""
        from git_stage_batch.core.models import CurrentLines, HunkHeader, LineEntry

        header = HunkHeader(old_start=1, old_len=1, new_start=1, new_len=1)
        current_lines = CurrentLines(
            path="test.txt",
            header=header,
            lines=[LineEntry(id=1, kind="+", old_line_number=None, new_line_number=1, text="test\n")]
        )

        with patch("git_stage_batch.tui.interactive.load_current_lines_from_state", return_value=current_lines):
            with patch("builtins.input", side_effect=KeyboardInterrupt):
                # Should return without raising
                handle_line_selection()


class TestDegradedMode:
    """Tests for degraded mode (no changes available)."""

    def test_start_interactive_mode_no_changes_shows_degraded_message(self, temp_git_repo, capsys):
        """Test that interactive mode starts when no changes exist."""
        # Repo has no changes - should start without error
        with patch("git_stage_batch.tui.interactive.prompt_action", return_value="q"):
            with patch("git_stage_batch.tui.interactive.handle_quit"):
                start_interactive_mode()

        captured = capsys.readouterr()
        assert "No changes to stage" in captured.err

    def test_degraded_mode_help_works(self, temp_git_repo, capsys):
        """Test that help action works in degraded mode."""
        # No changes, try help action
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=["?", "q"]):
            with patch("git_stage_batch.tui.interactive.handle_quit"):
                start_interactive_mode()

        captured = capsys.readouterr()
        assert "Interactive Mode Commands:" in captured.out

    def test_degraded_mode_hunk_actions_show_error(self, temp_git_repo, capsys):
        """Test that hunk-based actions show error in degraded mode."""
        # No changes, try include action
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=["i", "q"]):
            with patch("git_stage_batch.tui.interactive.handle_quit"):
                start_interactive_mode()

        captured = capsys.readouterr()
        assert "No changes to process" in captured.err

    def test_degraded_mode_shell_command_works(self, temp_git_repo, capsys):
        """Test that shell commands work in degraded mode."""
        # No changes, run shell command
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=["!echo test", "q"]):
            with patch("git_stage_batch.tui.interactive.handle_quit"):
                with patch("builtins.input"):  # Mock the "Press Enter" prompt
                    start_interactive_mode()

        # Command should have run (we'll see the output)
        # This is a basic test that the shell handler was called

    def test_degraded_mode_quit_works(self, temp_git_repo):
        """Test that quit works in degraded mode."""
        # No changes, quit immediately
        with patch("git_stage_batch.tui.interactive.prompt_action", return_value="q"):
            with patch("git_stage_batch.tui.interactive.handle_quit") as mock_quit:
                start_interactive_mode()
                mock_quit.assert_called_once()


class TestBatchSubmenu:
    """Tests for batch management submenu."""

    def test_batch_create(self, temp_git_repo):
        """Test creating a batch from submenu."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=["b", "q"]):
            with patch("builtins.input", side_effect=["c", "my-batch", "my note"]):
                with patch("git_stage_batch.commands.new.command_new_batch") as mock_new:
                    with patch("git_stage_batch.tui.interactive.handle_quit"):
                        start_interactive_mode()
                        mock_new.assert_called_once_with(batch_id="my-batch", note="my note")

    def test_batch_create_no_note(self, temp_git_repo):
        """Test creating a batch without note from submenu."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=["b", "q"]):
            with patch("builtins.input", side_effect=["c", "my-batch", ""]):
                with patch("git_stage_batch.commands.new.command_new_batch") as mock_new:
                    with patch("git_stage_batch.tui.interactive.handle_quit"):
                        start_interactive_mode()
                        mock_new.assert_called_once_with(batch_id="my-batch", note=None)

    def test_batch_create_cancel(self, temp_git_repo):
        """Test cancelling batch creation."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=["b", "q"]):
            with patch("builtins.input", side_effect=["c", ""]):
                with patch("git_stage_batch.commands.new.command_new_batch") as mock_new:
                    with patch("git_stage_batch.tui.interactive.handle_quit"):
                        start_interactive_mode()
                        mock_new.assert_not_called()

    def test_batch_edit(self, temp_git_repo):
        """Test editing batch note from submenu."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=["b", "q"]):
            with patch("git_stage_batch.commands.list.command_list_batches", return_value=[("test-batch", "old note")]):
                with patch("builtins.input", side_effect=["e", "1", "new note"]):
                    with patch("git_stage_batch.commands.annotate.command_annotate_batch") as mock_annotate:
                        with patch("git_stage_batch.tui.interactive.handle_quit"):
                            start_interactive_mode()
                            mock_annotate.assert_called_once_with(name="test-batch", note="new note")

    def test_batch_drop(self, temp_git_repo):
        """Test dropping a batch from submenu."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=["b", "q"]):
            with patch("git_stage_batch.commands.list.command_list_batches", return_value=[("test-batch", "some note")]):
                with patch("builtins.input", side_effect=["d", "1"]):
                    with patch("git_stage_batch.commands.drop.command_drop_batch") as mock_drop:
                        with patch("git_stage_batch.tui.interactive.handle_quit"):
                            start_interactive_mode()
                            mock_drop.assert_called_once_with(name="test-batch")

    def test_batch_apply(self, temp_git_repo):
        """Test applying a batch from submenu."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=["b", "q"]):
            with patch("git_stage_batch.commands.list.command_list_batches", return_value=[("test-batch", "some note")]):
                with patch("builtins.input", side_effect=["a", "1"]):
                    with patch("git_stage_batch.commands.apply_from.command_apply_from_batch") as mock_apply:
                        with patch("git_stage_batch.tui.interactive.handle_quit"):
                            start_interactive_mode()
                            mock_apply.assert_called_once_with(name="test-batch")

    def test_batch_select_no_batches(self, temp_git_repo, capsys):
        """Test selecting batch when none exist."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=["b", "q"]):
            with patch("git_stage_batch.commands.list.command_list_batches", return_value=[]):
                with patch("builtins.input", side_effect=["e"]):
                    with patch("git_stage_batch.tui.interactive.handle_quit"):
                        start_interactive_mode()
                        captured = capsys.readouterr()
                        assert "No batches found" in captured.err

    def test_batch_select_invalid(self, temp_git_repo, capsys):
        """Test invalid batch selection."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=["b", "q"]):
            with patch("git_stage_batch.commands.list.command_list_batches", return_value=[("test-batch", "note")]):
                with patch("builtins.input", side_effect=["e", "999"]):
                    with patch("git_stage_batch.commands.annotate.command_annotate_batch") as mock_annotate:
                        with patch("git_stage_batch.tui.interactive.handle_quit"):
                            start_interactive_mode()
                            mock_annotate.assert_not_called()
                            captured = capsys.readouterr()
                            assert "Invalid selection" in captured.err

    def test_batch_submenu_cancel(self, temp_git_repo):
        """Test cancelling batch submenu."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=["b", "q"]):
            with patch("builtins.input", side_effect=KeyboardInterrupt):
                with patch("git_stage_batch.tui.interactive.handle_quit"):
                    start_interactive_mode()
                    # Should not raise, just return to main menu

    def test_batch_submenu_unknown_action(self, temp_git_repo, capsys):
        """Test unknown action in batch submenu."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=["b", "q"]):
            with patch("builtins.input", side_effect=["x"]):
                with patch("git_stage_batch.tui.interactive.handle_quit"):
                    start_interactive_mode()
                    captured = capsys.readouterr()
                    assert "Unknown action" in captured.out


class TestFlowState:
    """Tests for source/target flow state tracking."""

    def test_default_flow_state(self, temp_git_repo, capsys):
        """Test that flow state defaults to working tree -> staging."""
        # Create a change to enter normal mode
        (temp_git_repo / "test.txt").write_text("change")

        with patch("git_stage_batch.tui.interactive.prompt_action", return_value="q"):
            with patch("git_stage_batch.tui.interactive.handle_quit"):
                start_interactive_mode()

        captured = capsys.readouterr()
        assert "Source: working tree" in captured.out
        assert "Target: staging" in captured.out
