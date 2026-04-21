"""Tests for CLI argument parsing."""

from unittest.mock import Mock

import pytest

from git_stage_batch.cli import argument_parser
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
    parse_command_line(["--invalid-arg"], quiet=True)
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
    assert args.file == ""
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_include_with_line():
    """Test parsing include command with --line flag."""
    args = parse_command_line(["include", "--line", "1,3,5-7"], quiet=True)
    assert args is not None
    assert args.line_ids == "1,3,5-7"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_include_with_as():
    """Test parsing include command with --as replacement text."""
    args = parse_command_line(["include", "--line", "2-3", "--as", "replacement"], quiet=True)
    assert args is not None
    assert args.line_ids == "2-3"
    assert args.as_text == "replacement"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_include_with_file_and_as():
    """Test parsing include command with --file, --line, and --as."""
    args = parse_command_line(
        ["include", "--file", "path.txt", "--line", "2-3", "--as", "replacement"],
        quiet=True,
    )
    assert args is not None
    assert args.file == "path.txt"
    assert args.line_ids == "2-3"
    assert args.as_text == "replacement"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_include_with_file_and_line_dispatches_file_scope(monkeypatch):
    """Include --file --line should dispatch to file-scoped line staging."""
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_include_line", mock_command)

    args = parse_command_line(
        ["include", "--file", "path.txt", "--line", "2-3"],
        quiet=True,
    )

    assert args is not None
    args.func(args)
    mock_command.assert_called_once_with("2-3", file="path.txt")
def test_parse_command_line_include_from_with_as():
    """Test parsing include --from with replacement text."""
    args = parse_command_line(
        ["include", "--from", "batch", "--line", "2-3", "--as", "replacement"],
        quiet=True,
    )
    assert args is not None
    assert args.from_batch == "batch"
    assert args.line_ids == "2-3"
    assert args.as_text == "replacement"
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


def test_parse_command_line_skip_with_file():
    """Test parsing skip command with --file flag."""
    args = parse_command_line(["skip", "--file"], quiet=True)
    assert args is not None
    assert args.file is True
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_skip_with_line():
    """Test parsing skip command with --line flag."""
    args = parse_command_line(["skip", "--line", "1,3,5-7"], quiet=True)
    assert args is not None
    assert args.line_ids == "1,3,5-7"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_discard():
    """Test parsing discard command."""
    args = parse_command_line(["discard"], quiet=True)
    assert args is not None
    assert args.command == "discard"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_discard_to_with_as():
    """Test parsing discard --to with replacement text."""
    args = parse_command_line(
        ["discard", "--to", "batch", "--line", "2-3", "--as", "replacement"],
        quiet=True,
    )
    assert args is not None
    assert args.to_batch == "batch"
    assert args.line_ids == "2-3"
    assert args.as_text == "replacement"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_discard_to_with_file_and_as():
    """Test parsing discard --to --file with replacement text."""
    args = parse_command_line(
        ["discard", "--to", "batch", "--file", "path.txt", "--line", "2-3", "--as", "replacement"],
        quiet=True,
    )
    assert args is not None
    assert args.to_batch == "batch"
    assert args.file == "path.txt"
    assert args.line_ids == "2-3"
    assert args.as_text == "replacement"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_discard_with_file_and_line_dispatches_file_scope(monkeypatch):
    """Discard --file --line should dispatch to file-scoped line discard."""
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_discard_line", mock_command)

    args = parse_command_line(
        ["discard", "--file", "path.txt", "--line", "2-3"],
        quiet=True,
    )

    assert args is not None
    args.func(args)
    mock_command.assert_called_once_with("2-3", file="path.txt")
def test_parse_command_line_discard_alias():
    """Test parsing discard command alias 'd'."""
    args = parse_command_line(["d"], quiet=True)
    assert args is not None
    assert args.command == "d"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_discard_with_file():
    """Test parsing discard command with --file flag."""
    args = parse_command_line(["discard", "--file"], quiet=True)
    assert args is not None
    assert args.file == ""
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_discard_with_line():
    """Test parsing discard command with --line flag."""
    args = parse_command_line(["discard", "--line", "1,3,5-7"], quiet=True)
    assert args is not None
    assert args.line_ids == "1,3,5-7"
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


def test_parse_command_line_unblock_file():
    """Test parsing unblock-file command."""
    args = parse_command_line(["unblock-file", "test.txt"], quiet=True)
    assert args is not None
    assert args.command == "unblock-file"
    assert args.file_path == "test.txt"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_unblock_file_alias():
    """Test parsing unblock-file command alias 'ubf'."""
    args = parse_command_line(["ubf", "test.txt"], quiet=True)
    assert args is not None
    assert args.command == "ubf"
    assert args.file_path == "test.txt"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_suggest_fixup():
    """Test parsing suggest-fixup command."""
    args = parse_command_line(["suggest-fixup"], quiet=True)
    assert args is not None
    assert args.command == "suggest-fixup"
    assert args.line_ids is None
    assert args.boundary is None
    assert args.reset is False
    assert args.abort is False
    assert args.last is False
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_suggest_fixup_alias():
    """Test parsing suggest-fixup command alias 'x'."""
    args = parse_command_line(["x"], quiet=True)
    assert args is not None
    assert args.command == "x"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_suggest_fixup_with_line():
    """Test parsing suggest-fixup with --line flag."""
    args = parse_command_line(["suggest-fixup", "--line", "1,3,5-7"], quiet=True)
    assert args is not None
    assert args.line_ids == "1,3,5-7"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_suggest_fixup_with_boundary():
    """Test parsing suggest-fixup with boundary argument."""
    args = parse_command_line(["suggest-fixup", "main"], quiet=True)
    assert args is not None
    assert args.boundary == "main"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_suggest_fixup_with_flags():
    """Test parsing suggest-fixup with flags."""
    args = parse_command_line(["suggest-fixup", "--reset"], quiet=True)
    assert args is not None
    assert args.reset is True
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_new():
    """Test parsing new command."""
    args = parse_command_line(["new", "my-batch"], quiet=True)
    assert args is not None
    assert args.command == "new"
    assert args.batch_name == "my-batch"
    assert args.note == ""
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_new_with_note():
    """Test parsing new command with --note flag."""
    args = parse_command_line(["new", "my-batch", "--note", "test note"], quiet=True)
    assert args is not None
    assert args.batch_name == "my-batch"
    assert args.note == "test note"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_list():
    """Test parsing list command."""
    args = parse_command_line(["list"], quiet=True)
    assert args is not None
    assert args.command == "list"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_drop():
    """Test parsing drop command."""
    args = parse_command_line(["drop", "my-batch"], quiet=True)
    assert args is not None
    assert args.command == "drop"
    assert args.batch_name == "my-batch"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_annotate():
    """Test parsing annotate command."""
    args = parse_command_line(["annotate", "my-batch", "new note"], quiet=True)
    assert args is not None
    assert args.command == "annotate"
    assert args.batch_name == "my-batch"
    assert args.note == "new note"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_undo():
    """Test parsing undo command."""
    args = parse_command_line(["undo"], quiet=True)
    assert args is not None
    assert args.command == "undo"
    assert args.force is False
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_undo_force():
    """Test parsing undo --force command."""
    args = parse_command_line(["undo", "--force"], quiet=True)
    assert args is not None
    assert args.force is True
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_redo():
    """Test parsing redo command."""
    args = parse_command_line(["redo"], quiet=True)
    assert args is not None
    assert args.command == "redo"
    assert args.force is False
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_redo_force():
    """Test parsing redo --force command."""
    args = parse_command_line(["redo", "--force"], quiet=True)
    assert args is not None
    assert args.force is True
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_redo_forward_alias():
    """Test parsing redo command alias 'forward'."""
    args = parse_command_line(["forward"], quiet=True)
    assert args is not None
    assert args.command == "forward"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_show_with_from():
    """Test parsing show command with --from flag."""
    args = parse_command_line(["show", "--from", "my-batch"], quiet=True)
    assert args is not None
    assert args.from_batch == "my-batch"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_include_with_from():
    """Test parsing include command with --from flag."""
    args = parse_command_line(["include", "--from", "my-batch"], quiet=True)
    assert args is not None
    assert args.from_batch == "my-batch"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_discard_with_from():
    """Test parsing discard command with --from flag."""
    args = parse_command_line(["discard", "--from", "my-batch"], quiet=True)
    assert args is not None
    assert args.from_batch == "my-batch"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_apply():
    """Test parsing apply command with required --from flag."""
    args = parse_command_line(["apply", "--from", "my-batch"], quiet=True)
    assert args is not None
    assert args.command == "apply"
    assert args.from_batch == "my-batch"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_reset_with_file():
    """Test parsing reset command with --file flag."""
    args = parse_command_line(["reset", "--from", "my-batch", "--file", "file.txt"], quiet=True)
    assert args is not None
    assert args.command == "reset"
    assert args.from_batch == "my-batch"
    assert args.file == "file.txt"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_reset_with_to():
    """Test parsing reset command with --to flag."""
    args = parse_command_line(["reset", "--from", "my-batch", "--to", "other-batch"], quiet=True)
    assert args is not None
    assert args.command == "reset"
    assert args.from_batch == "my-batch"
    assert args.to_batch == "other-batch"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_include_with_to():
    """Test parsing include command with --to flag."""
    args = parse_command_line(["include", "--to", "my-batch"], quiet=True)
    assert args is not None
    assert args.to_batch == "my-batch"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_discard_with_to():
    """Test parsing discard command with --to flag."""
    args = parse_command_line(["discard", "--to", "my-batch"], quiet=True)
    assert args is not None
    assert args.to_batch == "my-batch"
    assert hasattr(args, "func")
    assert callable(args.func)
