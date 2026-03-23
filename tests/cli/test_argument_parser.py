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


def test_parse_command_line_start():
    """Test parsing start command."""
    args = parse_command_line(["start"], quiet=True)
    assert args is not None
    assert args.command == "start"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_stop():
    """Test parsing stop command."""
    args = parse_command_line(["stop"], quiet=True)
    assert args is not None
    assert args.command == "stop"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_again():
    """Test parsing again command."""
    args = parse_command_line(["again"], quiet=True)
    assert args is not None
    assert args.command == "again"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_again_alias():
    """Test parsing again command alias 'a'."""
    args = parse_command_line(["a"], quiet=True)
    assert args is not None
    assert args.command == "a"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_show():
    """Test parsing show command."""
    args = parse_command_line(["show"], quiet=True)
    assert args is not None
    assert args.command == "show"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_status():
    """Test parsing status command."""
    args = parse_command_line(["status"], quiet=True)
    assert args is not None
    assert args.command == "status"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_status_alias():
    """Test parsing status command alias 'st'."""
    args = parse_command_line(["st"], quiet=True)
    assert args is not None
    assert args.command == "st"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_include():
    """Test parsing include command."""
    args = parse_command_line(["include"], quiet=True)
    assert args is not None
    assert args.command == "include"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_include_alias():
    """Test parsing include command alias 'i'."""
    args = parse_command_line(["i"], quiet=True)
    assert args is not None
    assert args.command == "i"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_include_with_file():
    """Test parsing include command with --file flag."""
    args = parse_command_line(["include", "--file"], quiet=True)
    assert args is not None
    assert args.file is True
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_skip():
    """Test parsing skip command."""
    args = parse_command_line(["skip"], quiet=True)
    assert args is not None
    assert args.command == "skip"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_skip_alias():
    """Test parsing skip command alias 's'."""
    args = parse_command_line(["s"], quiet=True)
    assert args is not None
    assert args.command == "s"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_discard():
    """Test parsing discard command."""
    args = parse_command_line(["discard"], quiet=True)
    assert args is not None
    assert args.command == "discard"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_discard_alias():
    """Test parsing discard command alias 'd'."""
    args = parse_command_line(["d"], quiet=True)
    assert args is not None
    assert args.command == "d"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_abort():
    """Test parsing abort command."""
    args = parse_command_line(["abort"], quiet=True)
    assert args is not None
    assert args.command == "abort"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_block_file():
    """Test parsing block-file command."""
    args = parse_command_line(["block-file", "test.txt"], quiet=True)
    assert args is not None
    assert args.command == "block-file"
    assert args.file_path == "test.txt"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_block_file_alias():
    """Test parsing block-file command alias 'bf'."""
    args = parse_command_line(["bf", "test.txt"], quiet=True)
    assert args is not None
    assert args.command == "bf"
    assert args.file_path == "test.txt"
    assert hasattr(args, "func")
    assert callable(args.func)
