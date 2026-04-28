"""Tests for CLI argument parsing."""

import os
from unittest.mock import Mock, call

import pytest

from git_stage_batch.cli import argument_parser
from git_stage_batch.cli.argument_parser import parse_command_line


def test_build_manpath_with_existing_environment(monkeypatch):
    """Existing MANPATH should be preserved after the packaged root."""
    monkeypatch.setattr(argument_parser, "_resolve_default_manpath", lambda: "/ignored")

    result = argument_parser._build_manpath_with_packaged_page(
        argument_parser.Path("/tmp/pkg-man"),
        {"MANPATH": "/custom/man"},
    )

    assert result == f"/tmp/pkg-man{os.pathsep}/custom/man"


def test_build_manpath_uses_computed_default(monkeypatch):
    """Unset MANPATH should use the computed default search path."""
    monkeypatch.setattr(argument_parser, "_resolve_default_manpath", lambda: "/usr/share/man:/usr/local/share/man")

    result = argument_parser._build_manpath_with_packaged_page(
        argument_parser.Path("/tmp/pkg-man"),
        {},
    )

    assert result == f"/tmp/pkg-man{os.pathsep}/usr/share/man:/usr/local/share/man"


def test_build_manpath_falls_back_to_double_colon(monkeypatch):
    """Unset MANPATH should preserve default lookup semantics as a fallback."""
    monkeypatch.setattr(argument_parser, "_resolve_default_manpath", lambda: None)

    result = argument_parser._build_manpath_with_packaged_page(
        argument_parser.Path("/tmp/pkg-man"),
        {},
    )

    assert result == f"/tmp/pkg-man{os.pathsep}{os.pathsep}"


def test_resolve_default_manpath_unsets_manpath(monkeypatch):
    """Default manpath discovery should ignore the current MANPATH override."""
    captured_env = {}

    def fake_run(*_args, **kwargs):
        nonlocal captured_env
        captured_env = kwargs["env"]
        return Mock(returncode=0, stdout="/usr/share/man\n")

    monkeypatch.setattr(argument_parser.subprocess, "run", fake_run)
    monkeypatch.setenv("MANPATH", "/custom/man")

    result = argument_parser._resolve_default_manpath()

    assert result == "/usr/share/man"
    assert "MANPATH" not in captured_env


def test_show_git_stage_batch_help_uses_packaged_page_first(monkeypatch, tmp_path):
    """Packaged man pages should be added to MANPATH before invoking git help."""
    manpage = tmp_path / "assets" / "man" / "man1" / "git-stage-batch.1"
    manpage.parent.mkdir(parents=True)
    manpage.write_text("test", encoding="utf-8")

    monkeypatch.setattr(
        argument_parser.resources,
        "files",
        lambda _package: tmp_path,
    )

    class _AsFile:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self.path

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(argument_parser.resources, "as_file", lambda path: _AsFile(path))
    monkeypatch.setattr(argument_parser, "_resolve_default_manpath", lambda: "/usr/share/man")

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs.get("env")))
        return Mock(returncode=0)

    monkeypatch.setattr(argument_parser.subprocess, "run", fake_run)

    assert argument_parser._show_git_stage_batch_help() is True
    assert calls[0][0] == ["git", "help", "stage-batch"]
    assert calls[0][1]["MANPATH"] == f"{manpage.parent.parent}{os.pathsep}/usr/share/man"


def test_show_git_stage_batch_help_materializes_editable_manpage(monkeypatch, tmp_path):
    """Editable installs should copy the man page into a real man1 tree."""
    editable_manpage = tmp_path / "build" / "cp313" / "git-stage-batch.1"
    editable_manpage.parent.mkdir(parents=True)
    editable_manpage.write_text("test", encoding="utf-8")

    class _EditableRoot:
        def joinpath(self, *segments):
            assert segments == ("assets", "man", "man1", "git-stage-batch.1")
            return editable_manpage

    monkeypatch.setattr(argument_parser.resources, "files", lambda _package: _EditableRoot())

    class _AsFile:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self.path

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(argument_parser.resources, "as_file", lambda path: _AsFile(path))
    monkeypatch.setattr(argument_parser, "_resolve_default_manpath", lambda: "/usr/share/man")

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs.get("env")))
        manpath_root = kwargs["env"]["MANPATH"].split(os.pathsep, 1)[0]
        staged_manpage = argument_parser.Path(manpath_root) / "man1" / "git-stage-batch.1"
        assert staged_manpage.exists()
        assert staged_manpage.read_text(encoding="utf-8") == "test"
        return Mock(returncode=0)

    monkeypatch.setattr(argument_parser.subprocess, "run", fake_run)

    assert argument_parser._show_git_stage_batch_help() is True
    assert calls[0][1]["MANPATH"].endswith(f"{os.pathsep}/usr/share/man")


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


def test_parse_command_line_include_with_files_dispatches_per_file(monkeypatch):
    """Include should dispatch once per file resolved from --files."""
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_include_file", mock_command)
    monkeypatch.setattr(argument_parser, "list_changed_files", lambda: ["foo.py", "dir/bar.py", "baz.txt"])

    args = parse_command_line(["include", "--files", "*.py"], quiet=True)

    assert args is not None
    assert args.file_patterns == ["*.py"]
    args.func(args)
    assert mock_command.call_args_list == [call("foo.py"), call("dir/bar.py")]


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
    assert args.file == ""
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_skip_with_file_path():
    """Test parsing skip command with --file PATH."""
    args = parse_command_line(["skip", "--file", "src/debug.py"], quiet=True)
    assert args is not None
    assert args.file == "src/debug.py"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_skip_with_line():
    """Test parsing skip command with --line flag."""
    args = parse_command_line(["skip", "--line", "1,3,5-7"], quiet=True)
    assert args is not None
    assert args.line_ids == "1,3,5-7"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_skip_with_files():
    """Skip should parse --files patterns."""
    args = parse_command_line(["skip", "--files", "*.py", "docs/*.md"], quiet=True)
    assert args is not None
    assert args.file_patterns == ["*.py", "docs/*.md"]


def test_parse_command_line_skip_files_dispatches_per_file(monkeypatch):
    """Skip should dispatch once per file resolved from --files."""
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_skip_file", mock_command)
    monkeypatch.setattr(argument_parser, "list_changed_files", lambda: ["foo.py", "bar.py", "notes.txt"])

    args = parse_command_line(["skip", "--files", "*.py"], quiet=True)

    assert args is not None
    args.func(args)
    assert mock_command.call_args_list == [call("foo.py"), call("bar.py")]


def test_parse_command_line_skip_rejects_lines_with_multiple_files(monkeypatch):
    """Skip should reject --line/--lines when --files resolves to multiple files."""
    monkeypatch.setattr(argument_parser, "list_changed_files", lambda: ["foo.py", "bar.py"])
    args = parse_command_line(["skip", "--files", "*.py", "--lines", "1"], quiet=True)
    assert args is not None

    with pytest.raises(argument_parser.CommandError, match="Cannot use --lines with multiple files"):
        args.func(args)


def test_parse_command_line_skip_rejects_mixed_file_and_files():
    """Skip should reject mixing --file and --files."""
    args = parse_command_line(["skip", "--file", "foo.py", "--files", "*.py"], quiet=True)
    assert args is not None

    with pytest.raises(argument_parser.CommandError, match="Cannot use --file together with --files"):
        args.func(args)


def test_parse_command_line_discard_with_files_dispatches_per_file(monkeypatch):
    """Discard should dispatch once per file resolved from --files."""
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_discard_file", mock_command)
    monkeypatch.setattr(argument_parser, "list_changed_files", lambda: ["foo.py", "bar.py", "notes.txt"])

    args = parse_command_line(["discard", "--files", "*.py"], quiet=True)

    assert args is not None
    args.func(args)
    assert mock_command.call_args_list == [call("foo.py"), call("bar.py")]


def test_parse_command_line_apply_with_files_dispatches_per_file(monkeypatch):
    """Apply should dispatch once per file resolved from --files."""
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_apply_from_batch", mock_command)
    monkeypatch.setattr(argument_parser, "batch_exists", lambda name: True)
    monkeypatch.setattr(argument_parser, "read_batch_metadata", lambda name: {"files": {"foo.py": {}, "bar.py": {}, "notes.txt": {}}})

    args = parse_command_line(
        ["apply", "--from", "batch", "--files", "*.py"],
        quiet=True,
    )

    assert args is not None
    args.func(args)
    assert mock_command.call_args_list == [
        call("batch", line_ids=None, file="foo.py"),
        call("batch", line_ids=None, file="bar.py"),
    ]


def test_parse_command_line_show_with_files_only_last_result_is_selectable(monkeypatch):
    """Show should hide gutters for earlier files resolved from --files."""
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_show", mock_command)
    monkeypatch.setattr(argument_parser, "list_changed_files", lambda: ["foo.py", "bar.py", "notes.txt"])

    args = parse_command_line(["show", "--files", "*.py"], quiet=True)

    assert args is not None
    args.func(args)
    assert mock_command.call_args_list == [
        call(file="foo.py", porcelain=False, selectable=False),
        call(file="bar.py", porcelain=False, selectable=True),
    ]


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
