"""Tests for TUI interactive mode."""

from git_stage_batch.utils.file_io import write_text_file_contents
from git_stage_batch.utils.git import run_git_command
from git_stage_batch.utils.paths import (
    ensure_state_directory_exists,
    get_start_head_file_path,
    get_start_index_tree_file_path,
)
from git_stage_batch.core.models import LineLevelChange, HunkHeader, LineEntry

import subprocess
from unittest.mock import patch

import pytest

from git_stage_batch.tui.flow import FlowLocation, FlowState
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
        assert "u, undo" in captured.out
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
        # Set up start state matching selected state

        ensure_state_directory_exists()

        # Record selected state as start state
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

        header = HunkHeader(old_start=1, old_len=1, new_start=1, new_len=1)
        line_changes = LineLevelChange(
            path="test.txt",
            header=header,
            lines=[LineEntry(id=1, kind="+", old_line_number=None, new_line_number=1, text_bytes=b"test\n", text="test\n")]
        )

        with patch("git_stage_batch.tui.interactive.load_line_changes_from_state", return_value=line_changes):
            with patch("git_stage_batch.commands.include.command_include_file") as mock_include:
                with patch("git_stage_batch.tui.interactive.fetch_next_change", return_value=None):
                    with patch("builtins.input", return_value="i"):
                        handle_file_selection(FlowState(
                            source=FlowLocation.WORKING_TREE,
                            target=FlowLocation.STAGING_AREA
                        ))
                        mock_include.assert_called_once()

    def test_handle_file_selection_skip(self):
        """Test file selection with skip action."""

        header = HunkHeader(old_start=1, old_len=1, new_start=1, new_len=1)
        line_changes = LineLevelChange(
            path="test.txt",
            header=header,
            lines=[LineEntry(id=1, kind="+", old_line_number=None, new_line_number=1, text_bytes=b"test\n", text="test\n")]
        )

        with patch("git_stage_batch.tui.interactive.load_line_changes_from_state", return_value=line_changes):
            with patch("git_stage_batch.commands.skip.command_skip_file") as mock_skip:
                with patch("git_stage_batch.tui.interactive.fetch_next_change", return_value=None):
                    with patch("builtins.input", return_value="s"):
                        handle_file_selection(FlowState(
                            source=FlowLocation.WORKING_TREE,
                            target=FlowLocation.STAGING_AREA
                        ))
                        mock_skip.assert_called_once()

    def test_handle_file_selection_discard(self):
        """Test file selection with discard action."""

        header = HunkHeader(old_start=1, old_len=1, new_start=1, new_len=1)
        line_changes = LineLevelChange(
            path="test.txt",
            header=header,
            lines=[LineEntry(id=1, kind="+", old_line_number=None, new_line_number=1, text_bytes=b"test\n", text="test\n")]
        )

        with patch("git_stage_batch.tui.interactive.load_line_changes_from_state", return_value=line_changes):
            with patch("git_stage_batch.commands.discard.command_discard_file") as mock_discard:
                with patch("git_stage_batch.tui.interactive.fetch_next_change", return_value=None):
                    with patch("git_stage_batch.tui.interactive.confirm_destructive_operation", return_value=True):
                        with patch("builtins.input", return_value="d"):
                            handle_file_selection(FlowState(
                                source=FlowLocation.WORKING_TREE,
                                target=FlowLocation.STAGING_AREA
                            ))
                            mock_discard.assert_called_once()

    def test_handle_file_selection_discard_to_batch(self):
        """Test file selection with discard action when target is batch."""

        header = HunkHeader(old_start=1, old_len=1, new_start=1, new_len=1)
        line_changes = LineLevelChange(
            path="test.txt",
            header=header,
            lines=[LineEntry(id=1, kind="+", old_line_number=None, new_line_number=1, text_bytes=b"test\n", text="test\n")]
        )

        with patch("git_stage_batch.tui.interactive.load_line_changes_from_state", return_value=line_changes):
            with patch("git_stage_batch.commands.discard.command_discard_to_batch") as mock_discard:
                with patch("git_stage_batch.tui.interactive.fetch_next_change", return_value=None):
                    with patch("builtins.input", return_value="d"):
                        handle_file_selection(FlowState(
                            source=FlowLocation.WORKING_TREE,
                            target=FlowLocation.for_batch("mybatch")
                        ))
                        mock_discard.assert_called_once_with("mybatch", file="", quiet=True)

    def test_handle_file_selection_cancel(self):
        """Test file selection with Ctrl-C."""

        header = HunkHeader(old_start=1, old_len=1, new_start=1, new_len=1)
        line_changes = LineLevelChange(
            path="test.txt",
            header=header,
            lines=[LineEntry(id=1, kind="+", old_line_number=None, new_line_number=1, text_bytes=b"test\n", text="test\n")]
        )

        with patch("git_stage_batch.tui.interactive.load_line_changes_from_state", return_value=line_changes):
            with patch("builtins.input", side_effect=KeyboardInterrupt):
                # Should return without raising
                handle_file_selection(FlowState(
                            source=FlowLocation.WORKING_TREE,
                            target=FlowLocation.STAGING_AREA
                        ))


class TestHandleLineSelection:
    """Tests for handle_line_selection function."""

    def test_handle_line_selection_include(self):
        """Test line selection with include action."""

        header = HunkHeader(old_start=1, old_len=2, new_start=1, new_len=2)
        line_changes = LineLevelChange(
            path="test.txt",
            header=header,
            lines=[
                LineEntry(id=1, kind="+", old_line_number=None, new_line_number=1, text_bytes=b"line1\n", text="line1\n"),
                LineEntry(id=2, kind="+", old_line_number=None, new_line_number=2, text_bytes=b"line2\n", text="line2\n"),
            ]
        )

        with patch("git_stage_batch.tui.interactive.load_line_changes_from_state", return_value=line_changes):
            with patch("git_stage_batch.commands.include.command_include_line") as mock_include:
                with patch("git_stage_batch.tui.interactive.prompt_line_ids", return_value="1"):
                    with patch("builtins.input", return_value="i"):
                        handle_line_selection(FlowState(
                            source=FlowLocation.WORKING_TREE,
                            target=FlowLocation.STAGING_AREA
                        ))
                        mock_include.assert_called_once_with("1")

    def test_handle_line_selection_no_changed_lines(self):
        """Test line selection when no changed lines exist."""

        header = HunkHeader(old_start=1, old_len=1, new_start=1, new_len=1)
        line_changes = LineLevelChange(
            path="test.txt",
            header=header,
            lines=[LineEntry(id=None, kind=" ", old_line_number=1, new_line_number=1, text_bytes=b" unchanged\n", text=" unchanged\n")]
        )

        with patch("git_stage_batch.tui.interactive.load_line_changes_from_state", return_value=line_changes):
            handle_line_selection(FlowState(
                            source=FlowLocation.WORKING_TREE,
                            target=FlowLocation.STAGING_AREA
                        ))
            # Should return early without prompting

    def test_handle_line_selection_cancel(self):
        """Test line selection with Ctrl-C."""

        header = HunkHeader(old_start=1, old_len=1, new_start=1, new_len=1)
        line_changes = LineLevelChange(
            path="test.txt",
            header=header,
            lines=[LineEntry(id=1, kind="+", old_line_number=None, new_line_number=1, text_bytes=b"test\n", text="test\n")]
        )

        with patch("git_stage_batch.tui.interactive.load_line_changes_from_state", return_value=line_changes):
            with patch("builtins.input", side_effect=KeyboardInterrupt):
                # Should return without raising
                handle_line_selection(FlowState(
                            source=FlowLocation.WORKING_TREE,
                            target=FlowLocation.STAGING_AREA
                        ))


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
            with patch("git_stage_batch.batch.query.list_batch_names", return_value=["existing-batch"]):
                with patch("git_stage_batch.batch.query.read_batch_metadata", return_value={"note": "", "created_at": ""}):
                    with patch("builtins.input", side_effect=["c", "my-batch", "my note", KeyboardInterrupt]):
                        with patch("git_stage_batch.commands.new.command_new_batch") as mock_new:
                            with patch("git_stage_batch.tui.interactive.handle_quit"):
                                start_interactive_mode()
                                mock_new.assert_called_once_with(batch_name="my-batch", note="my note")

    def test_batch_create_no_note(self, temp_git_repo):
        """Test creating a batch without note from submenu."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=["b", "q"]):
            with patch("git_stage_batch.batch.query.list_batch_names", return_value=["existing-batch"]):
                with patch("git_stage_batch.batch.query.read_batch_metadata", return_value={"note": "", "created_at": ""}):
                    with patch("builtins.input", side_effect=["c", "my-batch", "", KeyboardInterrupt]):
                        with patch("git_stage_batch.commands.new.command_new_batch") as mock_new:
                            with patch("git_stage_batch.tui.interactive.handle_quit"):
                                start_interactive_mode()
                                mock_new.assert_called_once_with(batch_name="my-batch", note=None)

    def test_batch_create_cancel(self, temp_git_repo):
        """Test cancelling batch creation."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=["b", "q"]):
            with patch("git_stage_batch.batch.query.list_batch_names", return_value=["existing-batch"]):
                with patch("git_stage_batch.batch.query.read_batch_metadata", return_value={"note": "", "created_at": ""}):
                    with patch("builtins.input", side_effect=["c", "", KeyboardInterrupt]):
                        with patch("git_stage_batch.commands.new.command_new_batch") as mock_new:
                            with patch("git_stage_batch.tui.interactive.handle_quit"):
                                start_interactive_mode()
                                mock_new.assert_not_called()

    def test_batch_edit(self, temp_git_repo):
        """Test editing batch note from submenu with multiple batches."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=["b", "q"]):
            with patch("git_stage_batch.batch.query.list_batch_names", return_value=["test-batch", "other-batch"]):
                with patch("git_stage_batch.batch.query.read_batch_metadata", return_value={"note": "old note", "created_at": ""}):
                    with patch("builtins.input", side_effect=["e", "1", "new note", KeyboardInterrupt]):
                        with patch("git_stage_batch.commands.annotate.command_annotate_batch") as mock_annotate:
                            with patch("git_stage_batch.tui.interactive.handle_quit"):
                                start_interactive_mode()
                                mock_annotate.assert_called_once_with("test-batch", "new note")

    def test_batch_drop(self, temp_git_repo):
        """Test dropping a batch from submenu with multiple batches."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=["b", "q"]):
            with patch("git_stage_batch.batch.query.list_batch_names", return_value=["test-batch", "other-batch"]):
                with patch("git_stage_batch.batch.query.read_batch_metadata", return_value={"note": "some note", "created_at": ""}):
                    with patch("builtins.input", side_effect=["d", "1", KeyboardInterrupt]):
                        with patch("git_stage_batch.commands.drop.command_drop_batch") as mock_drop:
                            with patch("git_stage_batch.tui.interactive.handle_quit"):
                                start_interactive_mode()
                                mock_drop.assert_called_once_with("test-batch")

    def test_batch_apply(self, temp_git_repo):
        """Test applying a batch from submenu."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=["b", "q"]):
            with patch("git_stage_batch.batch.query.list_batch_names", return_value=["test-batch"]):
                with patch("git_stage_batch.batch.query.read_batch_metadata", return_value={"note": "some note", "created_at": ""}):
                    with patch("builtins.input", side_effect=["a", "1", KeyboardInterrupt]):
                        with patch("git_stage_batch.commands.apply_from.command_apply_from_batch") as mock_apply:
                            with patch("git_stage_batch.tui.interactive.handle_quit"):
                                start_interactive_mode()
                                mock_apply.assert_called_once_with("test-batch")

    def test_batch_select_no_batches(self, temp_git_repo, capsys):
        """Test auto-create when no batches exist."""
        # Use side_effect to mock list_batch_names:
        # - First call returns [] (triggers auto-create)
        # - After create, return ["my-batch"] so menu shows
        batch_list_mock = [[], ["my-batch"]]
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=["b", "q"]):
            with patch("git_stage_batch.batch.query.list_batch_names", side_effect=batch_list_mock):
                with patch("git_stage_batch.batch.query.read_batch_metadata", return_value={"note": "my note", "created_at": ""}):
                    with patch("builtins.input", side_effect=["my-batch", "my note", KeyboardInterrupt]):
                        with patch("git_stage_batch.commands.new.command_new_batch") as mock_new:
                            with patch("git_stage_batch.tui.interactive.handle_quit"):
                                start_interactive_mode()
                                captured = capsys.readouterr()
                                assert "No batches found. Create one now." in captured.out
                                mock_new.assert_called_once_with(batch_name="my-batch", note="my note")

    def test_batch_select_invalid(self, temp_git_repo, capsys):
        """Test invalid batch selection with multiple batches."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=["b", "q"]):
            with patch("git_stage_batch.batch.query.list_batch_names", return_value=["test-batch", "other-batch"]):
                with patch("git_stage_batch.batch.query.read_batch_metadata", return_value={"note": "note", "created_at": ""}):
                    with patch("builtins.input", side_effect=["e", "999", KeyboardInterrupt]):
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
            with patch("git_stage_batch.batch.query.list_batch_names", return_value=["existing-batch"]):
                with patch("git_stage_batch.batch.query.read_batch_metadata", return_value={"note": "", "created_at": ""}):
                    with patch("builtins.input", side_effect=["x", KeyboardInterrupt]):
                        with patch("git_stage_batch.tui.interactive.handle_quit"):
                            start_interactive_mode()
                            captured = capsys.readouterr()
                            assert "Unknown action" in captured.out

    def test_batch_select_by_name(self, temp_git_repo):
        """Test selecting batch by full name instead of number."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=["b", "q"]):
            with patch("git_stage_batch.batch.query.list_batch_names", return_value=["first-batch", "second-batch"]):
                with patch("git_stage_batch.batch.query.read_batch_metadata", return_value={"note": "note", "created_at": ""}):
                    with patch("builtins.input", side_effect=["d", "second-batch", KeyboardInterrupt]):
                        with patch("git_stage_batch.commands.drop.command_drop_batch") as mock_drop:
                            with patch("git_stage_batch.tui.interactive.handle_quit"):
                                start_interactive_mode()
                                mock_drop.assert_called_once_with("second-batch")

    def test_batch_edit_single_batch_skips_prompt(self, temp_git_repo):
        """Test editing when only one batch exists skips selection prompt."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=["b", "q"]):
            with patch("git_stage_batch.batch.query.list_batch_names", return_value=["only-batch"]):
                with patch("git_stage_batch.batch.query.read_batch_metadata", return_value={"note": "old", "created_at": ""}):
                    # Only "e" and "new note" in input - no batch selection prompt
                    with patch("builtins.input", side_effect=["e", "new note", KeyboardInterrupt]):
                        with patch("git_stage_batch.commands.annotate.command_annotate_batch") as mock_annotate:
                            with patch("git_stage_batch.tui.interactive.handle_quit"):
                                start_interactive_mode()
                                mock_annotate.assert_called_once_with("only-batch", "new note")

    def test_batch_drop_single_batch_skips_prompt(self, temp_git_repo):
        """Test dropping when only one batch exists skips selection prompt."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=["b", "q"]):
            with patch("git_stage_batch.batch.query.list_batch_names", return_value=["only-batch"]):
                with patch("git_stage_batch.batch.query.read_batch_metadata", return_value={"note": "note", "created_at": ""}):
                    # Only "d" in input - no batch selection prompt
                    with patch("builtins.input", side_effect=["d", KeyboardInterrupt]):
                        with patch("git_stage_batch.commands.drop.command_drop_batch") as mock_drop:
                            with patch("git_stage_batch.tui.interactive.handle_quit"):
                                start_interactive_mode()
                                mock_drop.assert_called_once_with("only-batch")

    def test_batch_apply_multiple_batches_shows_prompt(self, temp_git_repo):
        """Test applying with multiple batches shows selection prompt."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=["b", "q"]):
            with patch("git_stage_batch.batch.query.list_batch_names", return_value=["first", "second"]):
                with patch("git_stage_batch.batch.query.read_batch_metadata", return_value={"note": "", "created_at": ""}):
                    # Apply always prompts, even with one batch
                    with patch("builtins.input", side_effect=["a", "1", KeyboardInterrupt]):
                        with patch("git_stage_batch.commands.apply_from.command_apply_from_batch") as mock_apply:
                            with patch("git_stage_batch.tui.interactive.handle_quit"):
                                start_interactive_mode()
                                mock_apply.assert_called_once_with("first")

class TestFlowMenus:
    """Tests for flow menus (from and to)."""

    def test_from_menu_select_working_tree(self, temp_git_repo):
        """Test selecting working tree as source."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=["<", "q"]):
            with patch("git_stage_batch.batch.query.list_batch_names", return_value=["test-batch"]):
                with patch("git_stage_batch.batch.query.read_batch_metadata", return_value={"note": "note", "created_at": ""}):
                    with patch("builtins.input", side_effect=["1", KeyboardInterrupt]):
                        with patch("git_stage_batch.tui.interactive.handle_quit"):
                            start_interactive_mode()
                            # Should not raise, flow changed to working tree

    def test_from_menu_select_batch(self, temp_git_repo):
        """Test selecting batch as source."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=["<", "q"]):
            with patch("git_stage_batch.batch.query.list_batch_names", return_value=["my-batch", "other-batch"]):
                with patch("git_stage_batch.batch.query.read_batch_metadata", return_value={"note": "note", "created_at": ""}):
                    with patch("builtins.input", side_effect=["2", KeyboardInterrupt]):
                        with patch("git_stage_batch.tui.interactive.handle_quit"):
                            start_interactive_mode()
                            # Should not raise, flow changed to batch

    def test_from_menu_cancel(self, temp_git_repo):
        """Test cancelling from menu."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=["<", "q"]):
            with patch("git_stage_batch.batch.query.list_batch_names", return_value=["test-batch"]):
                with patch("git_stage_batch.batch.query.read_batch_metadata", return_value={"note": "", "created_at": ""}):
                    with patch("builtins.input", side_effect=[KeyboardInterrupt, KeyboardInterrupt]):
                        with patch("git_stage_batch.tui.interactive.handle_quit"):
                            start_interactive_mode()
                            # Should not raise, source unchanged

    def test_to_menu_select_staging(self, temp_git_repo):
        """Test selecting staging as target."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=[">", "q"]):
            with patch("git_stage_batch.batch.query.list_batch_names", return_value=["test-batch"]):
                with patch("git_stage_batch.batch.query.read_batch_metadata", return_value={"note": "", "created_at": ""}):
                    with patch("builtins.input", side_effect=["1", KeyboardInterrupt]):
                        with patch("git_stage_batch.tui.interactive.handle_quit"):
                            start_interactive_mode()
                            # Should not raise, target changed to staging

    def test_to_menu_select_existing_batch(self, temp_git_repo):
        """Test selecting existing batch as target."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=[">", "q"]):
            with patch("git_stage_batch.batch.query.list_batch_names", return_value=["batch1", "batch2"]):
                with patch("git_stage_batch.batch.query.read_batch_metadata", return_value={"note": "test note", "created_at": ""}):
                    with patch("builtins.input", side_effect=["2", KeyboardInterrupt]):
                        with patch("git_stage_batch.tui.interactive.handle_quit"):
                            start_interactive_mode()
                            # Should not raise, target changed to batch1

    def test_to_menu_create_new_batch(self, temp_git_repo):
        """Test creating new batch from to menu."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=[">", "q"]):
            with patch("git_stage_batch.batch.query.list_batch_names", return_value=["existing"]):
                with patch("git_stage_batch.batch.query.read_batch_metadata", return_value={"note": "", "created_at": ""}):
                    # Select "New Batch..." option (3), then provide batch ID and note
                    with patch("builtins.input", side_effect=["3", "new-batch", "my note", KeyboardInterrupt]):
                        with patch("git_stage_batch.commands.new.command_new_batch") as mock_new:
                            with patch("git_stage_batch.tui.interactive.handle_quit"):
                                start_interactive_mode()
                                mock_new.assert_called_once_with(batch_name="new-batch", note="my note")

    def test_to_menu_create_new_batch_no_note(self, temp_git_repo):
        """Test creating new batch without note from to menu."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=[">", "q"]):
            with patch("git_stage_batch.batch.query.list_batch_names", return_value=[]):
                with patch("git_stage_batch.batch.query.read_batch_metadata", return_value={"note": "", "created_at": ""}):
                    # Select "New Batch..." option (2 when no batches), then provide batch ID with no note
                    with patch("builtins.input", side_effect=["2", "new-batch", "", KeyboardInterrupt]):
                        with patch("git_stage_batch.commands.new.command_new_batch") as mock_new:
                            with patch("git_stage_batch.tui.interactive.handle_quit"):
                                start_interactive_mode()
                                mock_new.assert_called_once_with(batch_name="new-batch", note=None)

    def test_to_menu_cancel_new_batch(self, temp_git_repo):
        """Test cancelling new batch creation from to menu."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=[">", "q"]):
            with patch("git_stage_batch.batch.query.list_batch_names", return_value=[]):
                with patch("git_stage_batch.batch.query.read_batch_metadata", return_value={"note": "", "created_at": ""}):
                    # Select "New Batch...", then cancel with empty batch ID
                    with patch("builtins.input", side_effect=["2", "", KeyboardInterrupt]):
                        with patch("git_stage_batch.commands.new.command_new_batch") as mock_new:
                            with patch("git_stage_batch.tui.interactive.handle_quit"):
                                start_interactive_mode()
                                mock_new.assert_not_called()

    def test_direct_source_batch_shortcut(self, temp_git_repo):
        """Test direct source selection with <batch-name shortcut."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=["<my-batch", "q"]):
            with patch("git_stage_batch.tui.interactive.handle_quit"):
                start_interactive_mode()
                # Should set source to my-batch without showing menu

    def test_direct_target_batch_shortcut(self, temp_git_repo):
        """Test direct target selection with >batch-name shortcut."""
        with patch("git_stage_batch.tui.interactive.prompt_action", side_effect=[">ci", "q"]):
            with patch("git_stage_batch.tui.interactive.handle_quit"):
                start_interactive_mode()
                # Should set target to ci batch without showing menu
