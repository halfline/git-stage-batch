"""Tests for exception classes."""

import pytest

from git_stage_batch.exceptions import CommandError, exit_with_error


class TestCommandError:
    """Tests for CommandError exception."""

    def test_command_error_with_default_exit_code(self):
        """Test CommandError initializes with default exit code 1."""
        error = CommandError("test message")
        assert error.message == "test message"
        assert error.exit_code == 1
        assert str(error) == "test message"

    def test_command_error_with_custom_exit_code(self):
        """Test CommandError initializes with custom exit code."""
        error = CommandError("test message", exit_code=2)
        assert error.message == "test message"
        assert error.exit_code == 2

    def test_command_error_inherits_from_exception(self):
        """Test CommandError is an Exception subclass."""
        error = CommandError("test")
        assert isinstance(error, Exception)


class TestExitWithError:
    """Tests for exit_with_error helper."""

    def test_exit_with_error_raises_command_error(self):
        """Test exit_with_error raises CommandError."""
        with pytest.raises(CommandError) as exc_info:
            exit_with_error("test error")
        assert exc_info.value.message == "test error"
        assert exc_info.value.exit_code == 1

    def test_exit_with_error_with_custom_exit_code(self):
        """Test exit_with_error raises CommandError with custom exit code."""
        with pytest.raises(CommandError) as exc_info:
            exit_with_error("test error", exit_code=3)
        assert exc_info.value.message == "test error"
        assert exc_info.value.exit_code == 3
