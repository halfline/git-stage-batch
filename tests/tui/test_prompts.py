"""Tests for TUI prompt utilities."""

from git_stage_batch.tui.prompts import _shell_command_history

from io import StringIO
from unittest.mock import patch

from git_stage_batch.output.colors import Colors
from git_stage_batch.tui.prompts import (
    confirm_destructive_operation,
    prompt_action,
    prompt_fixup_action,
    prompt_line_ids,
    prompt_quit_session,
    prompt_shell_command,
)


class TestPromptAction:
    """Tests for prompt_action function."""

    def test_prompt_action_include(self):
        """Test selecting include action."""
        with patch("builtins.input", return_value="i"):
            assert prompt_action(use_color=False) == "i"

    def test_prompt_action_skip(self):
        """Test selecting skip action."""
        with patch("builtins.input", return_value="s"):
            assert prompt_action(use_color=False) == "s"

    def test_prompt_action_discard(self):
        """Test selecting discard action."""
        with patch("builtins.input", return_value="d"):
            assert prompt_action(use_color=False) == "d"

    def test_prompt_action_quit(self):
        """Test selecting quit action."""
        with patch("builtins.input", return_value="q"):
            assert prompt_action(use_color=False) == "q"

    def test_prompt_action_full_word(self):
        """Test using full word instead of single letter."""
        with patch("builtins.input", return_value="include"):
            assert prompt_action(use_color=False) == "i"

        with patch("builtins.input", return_value="skip"):
            assert prompt_action(use_color=False) == "s"

        with patch("builtins.input", return_value="quit"):
            assert prompt_action(use_color=False) == "q"

    def test_prompt_action_secondary_options(self):
        """Test selecting secondary options."""
        with patch("builtins.input", return_value="a"):
            assert prompt_action(use_color=False) == "a"

        with patch("builtins.input", return_value="l"):
            assert prompt_action(use_color=False) == "l"

        with patch("builtins.input", return_value="f"):
            assert prompt_action(use_color=False) == "f"

        with patch("builtins.input", return_value="?"):
            assert prompt_action(use_color=False) == "?"

    def test_prompt_action_case_insensitive(self):
        """Test that input is case-insensitive."""
        with patch("builtins.input", return_value="I"):
            assert prompt_action(use_color=False) == "i"

        with patch("builtins.input", return_value="INCLUDE"):
            assert prompt_action(use_color=False) == "i"

    def test_prompt_action_ctrl_c(self):
        """Test Ctrl-C returns quit."""
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            assert prompt_action(use_color=False) == "q"

    def test_prompt_action_ctrl_d(self):
        """Test Ctrl-D (EOF) returns quit."""
        with patch("builtins.input", side_effect=EOFError):
            assert prompt_action(use_color=False) == "q"

    def test_prompt_action_unknown_returns_as_is(self):
        """Test unknown input is returned as-is."""
        with patch("builtins.input", return_value="xyz"):
            assert prompt_action(use_color=False) == "xyz"

    def test_prompt_action_with_color(self):
        """Test prompt with colors enabled."""
        with patch("builtins.input", return_value="i"):
            with patch("sys.stdout", new=StringIO()) as fake_out:
                with patch("sys.stdout.isatty", return_value=True):
                    result = prompt_action(use_color=True)
                    output = fake_out.getvalue()

        assert result == "i"
        assert Colors.GREEN in output  # include hotkey should be green
        assert Colors.RED in output    # discard hotkey should be red
        assert Colors.CYAN in output   # "More options" should be cyan

    def test_prompt_action_shows_flow_options(self):
        """Test that flow options appear in menu."""
        with patch("builtins.input", return_value="i"):
            with patch("sys.stdout", new=StringIO()) as fake_out:
                with patch("sys.stdout.isatty", return_value=False):
                    result = prompt_action(use_color=False)
                    output = fake_out.getvalue()

        assert result == "i"
        assert "from" in output.lower() or "<" in output
        assert "to" in output.lower() or ">" in output

    def test_prompt_action_from_normalized(self):
        """Test that 'from' normalizes to '<'."""
        with patch("builtins.input", return_value="from"):
            assert prompt_action(use_color=False) == "<"

    def test_prompt_action_to_normalized(self):
        """Test that 'to' normalizes to '>'."""
        with patch("builtins.input", return_value="to"):
            assert prompt_action(use_color=False) == ">"


class TestConfirmDestructiveOperation:
    """Tests for confirm_destructive_operation function."""

    def test_confirm_with_yes(self):
        """Test confirmation with 'yes'."""
        with patch("builtins.input", return_value="yes"):
            assert confirm_destructive_operation("discard", "This will remove the hunk") is True

    def test_confirm_with_no(self):
        """Test cancellation with 'no'."""
        with patch("builtins.input", return_value="no"):
            assert confirm_destructive_operation("discard", "This will remove the hunk") is False

    def test_confirm_with_empty(self):
        """Test empty input defaults to no."""
        with patch("builtins.input", return_value=""):
            assert confirm_destructive_operation("discard", "This will remove the hunk") is False

    def test_confirm_case_insensitive(self):
        """Test that confirmation is case-insensitive."""
        with patch("builtins.input", return_value="YES"):
            assert confirm_destructive_operation("discard", "This will remove the hunk") is True

        with patch("builtins.input", return_value="Yes"):
            assert confirm_destructive_operation("discard", "This will remove the hunk") is True

    def test_confirm_only_full_yes_accepted(self):
        """Test that only full 'yes' is accepted, not 'y'."""
        with patch("builtins.input", return_value="y"):
            assert confirm_destructive_operation("discard", "This will remove the hunk") is False

    def test_confirm_ctrl_c(self):
        """Test Ctrl-C returns False."""
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            assert confirm_destructive_operation("discard", "This will remove the hunk") is False

    def test_confirm_ctrl_d(self):
        """Test Ctrl-D returns False."""
        with patch("builtins.input", side_effect=EOFError):
            assert confirm_destructive_operation("discard", "This will remove the hunk") is False

    def test_confirm_with_color(self):
        """Test confirmation prompt with colors."""
        with patch("builtins.input", return_value="yes"):
            with patch("sys.stdout", new=StringIO()) as fake_out:
                with patch("sys.stdout.isatty", return_value=True):
                    result = confirm_destructive_operation("discard", "Warning message")
                    output = fake_out.getvalue()

        assert result is True
        assert "⚠️" in output
        assert Colors.RED in output  # Warning should be red


class TestPromptLineIds:
    """Tests for prompt_line_ids function."""

    def test_prompt_line_ids_simple(self):
        """Test prompting for line IDs."""
        with patch("builtins.input", return_value="1,3,5"):
            assert prompt_line_ids() == "1,3,5"

    def test_prompt_line_ids_with_ranges(self):
        """Test prompting for line IDs with ranges."""
        with patch("builtins.input", return_value="1,3,5-7"):
            assert prompt_line_ids() == "1,3,5-7"

    def test_prompt_line_ids_ctrl_c(self):
        """Test Ctrl-C returns empty string."""
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            assert prompt_line_ids() == ""

    def test_prompt_line_ids_ctrl_d(self):
        """Test Ctrl-D returns empty string."""
        with patch("builtins.input", side_effect=EOFError):
            assert prompt_line_ids() == ""


class TestPromptQuitSession:
    """Tests for prompt_quit_session function."""

    def test_prompt_quit_yes(self):
        """Test quitting with yes (keep changes)."""
        with patch("builtins.input", return_value="y"):
            assert prompt_quit_session() == "keep"

        with patch("builtins.input", return_value="yes"):
            assert prompt_quit_session() == "keep"

    def test_prompt_quit_no(self):
        """Test quitting with no (undo changes)."""
        with patch("builtins.input", return_value="n"):
            assert prompt_quit_session() == "undo"

        with patch("builtins.input", return_value="no"):
            assert prompt_quit_session() == "undo"

    def test_prompt_quit_case_insensitive(self):
        """Test quit prompt is case-insensitive."""
        with patch("builtins.input", return_value="Y"):
            assert prompt_quit_session() == "keep"

        with patch("builtins.input", return_value="N"):
            assert prompt_quit_session() == "undo"

    def test_prompt_quit_ctrl_c(self):
        """Test Ctrl-C returns cancel."""
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            assert prompt_quit_session() == "cancel"

    def test_prompt_quit_ctrl_d(self):
        """Test Ctrl-D returns cancel."""
        with patch("builtins.input", side_effect=EOFError):
            assert prompt_quit_session() == "cancel"

    def test_prompt_quit_invalid_returns_cancel(self):
        """Test invalid input returns cancel."""
        with patch("builtins.input", return_value="maybe"):
            assert prompt_quit_session() == "cancel"

    def test_prompt_quit_with_color(self):
        """Test quit prompt with colors enabled (TTY)."""
        # Just verify it works with TTY - input() prompts aren't captured in tests
        with patch("builtins.input", return_value="y"):
            with patch("sys.stdout.isatty", return_value=True):
                result = prompt_quit_session()

        assert result == "keep"


class TestPromptShellCommand:
    """Tests for prompt_shell_command function."""

    def test_prompt_shell_command_simple(self):
        """Test prompting for a shell command."""
        with patch("builtins.input", return_value="ls -la"):
            assert prompt_shell_command() == "ls -la"

    def test_prompt_shell_command_empty(self):
        """Test empty command returns empty string."""
        with patch("builtins.input", return_value=""):
            assert prompt_shell_command() == ""

    def test_prompt_shell_command_ctrl_c(self):
        """Test Ctrl-C returns empty string."""
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            assert prompt_shell_command() == ""

    def test_prompt_shell_command_ctrl_d(self):
        """Test Ctrl-D returns empty string."""
        with patch("builtins.input", side_effect=EOFError):
            assert prompt_shell_command() == ""

    def test_prompt_shell_command_with_readline(self):
        """Test command is added to shell command history."""

        # Clear any existing history from previous tests
        _shell_command_history.clear()

        with patch("builtins.input", return_value="git status"):
            with patch("git_stage_batch.tui.prompts.HAS_READLINE", True):
                with patch("git_stage_batch.tui.prompts.INPUT_USES_LIBEDIT", False):  # Test GNU readline path
                    with patch("git_stage_batch.tui.prompts.readline") as mock_readline:
                        result = prompt_shell_command()
                        assert result == "git status"
                        # Verify command was added to module-level history
                        assert "git status" in _shell_command_history
                        # Verify readline.clear_history was called (to isolate shell history)
                        assert mock_readline.clear_history.called
                        # Verify Ctrl-R was enabled then disabled (GNU readline only)
                        parse_and_bind_calls = [call[0][0] for call in mock_readline.parse_and_bind.call_args_list]
                        assert "bind ^R em-inc-search-prev" in parse_and_bind_calls
                        assert "bind -r ^R" in parse_and_bind_calls

    def test_prompt_shell_command_with_libedit(self):
        """Test command history with libedit (Ctrl-R disabled)."""

        # Clear any existing history from previous tests
        _shell_command_history.clear()

        with patch("builtins.input", return_value="git status"):
            with patch("git_stage_batch.tui.prompts.HAS_READLINE", True):
                with patch("git_stage_batch.tui.prompts.INPUT_USES_LIBEDIT", True):  # Test libedit path
                    with patch("git_stage_batch.tui.prompts.readline") as mock_readline:
                        result = prompt_shell_command()
                        assert result == "git status"
                        # Verify command was added to module-level history
                        assert "git status" in _shell_command_history
                        # Verify readline.clear_history was called
                        assert mock_readline.clear_history.called
                        # Verify Ctrl-R remains disabled for libedit.
                        parse_and_bind_calls = [call[0][0] for call in mock_readline.parse_and_bind.call_args_list]
                        assert "bind ^R em-inc-search-prev" not in parse_and_bind_calls

    def test_prompt_shell_command_without_readline(self):
        """Test command works without readline available."""
        with patch("builtins.input", return_value="git status"):
            with patch("git_stage_batch.tui.prompts.HAS_READLINE", False):
                result = prompt_shell_command()
                assert result == "git status"


class TestPromptFixupAction:
    """Tests for prompt_fixup_action function."""

    def test_prompt_fixup_action_yes(self):
        """Test selecting yes."""
        with patch("builtins.input", return_value="y"):
            assert prompt_fixup_action(use_color=False) == "y"

        with patch("builtins.input", return_value="yes"):
            assert prompt_fixup_action(use_color=False) == "y"

    def test_prompt_fixup_action_next(self):
        """Test selecting next."""
        with patch("builtins.input", return_value="n"):
            assert prompt_fixup_action(use_color=False) == "n"

        with patch("builtins.input", return_value="next"):
            assert prompt_fixup_action(use_color=False) == "n"

    def test_prompt_fixup_action_reset(self):
        """Test selecting reset."""
        with patch("builtins.input", return_value="r"):
            assert prompt_fixup_action(use_color=False) == "r"

        with patch("builtins.input", return_value="reset"):
            assert prompt_fixup_action(use_color=False) == "r"

    def test_prompt_fixup_action_ctrl_c(self):
        """Test Ctrl-C returns cancel."""
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            assert prompt_fixup_action(use_color=False) == "q"

    def test_prompt_fixup_action_ctrl_d(self):
        """Test Ctrl-D returns cancel."""
        with patch("builtins.input", side_effect=EOFError):
            assert prompt_fixup_action(use_color=False) == "q"

    def test_prompt_fixup_action_unknown_returns_as_is(self):
        """Test unknown input is returned as-is."""
        with patch("builtins.input", return_value="xyz"):
            assert prompt_fixup_action(use_color=False) == "xyz"

    def test_prompt_fixup_action_case_insensitive(self):
        """Test that input is case-insensitive."""
        with patch("builtins.input", return_value="Y"):
            assert prompt_fixup_action(use_color=False) == "y"

        with patch("builtins.input", return_value="NEXT"):
            assert prompt_fixup_action(use_color=False) == "n"
