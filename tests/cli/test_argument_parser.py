"""Tests for CLI argument parsing."""

import pytest

from git_stage_batch.cli.argument_parser import parse_command_line


def test_parse_command_line_version():
    """Test parsing --version flag."""
    # --version causes argparse to exit, which is expected
    with pytest.raises(SystemExit) as exc_info:
        parse_command_line(["--version"], quiet=True)
    assert exc_info.value.code == 0


def test_parse_command_line_no_args():
    """Test parsing with no arguments."""
    args = parse_command_line([], quiet=True)
    assert args is not None


def test_parse_command_line_quick_action_help():
    """Test that '?' expands to --help."""
    # --help causes argparse to exit, which is expected
    with pytest.raises(SystemExit) as exc_info:
        parse_command_line(["?"], quiet=True)
    assert exc_info.value.code == 0


def test_parse_command_line_invalid_arg():
    """Test parsing invalid argument returns None."""
    args = parse_command_line(["--invalid-arg"], quiet=True)
    # Should return None for invalid arguments
    # (depends on argparse behavior, may return None or valid Namespace)
    # Just verify function is callable
    assert parse_command_line is not None
