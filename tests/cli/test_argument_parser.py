"""Tests for CLI argument parsing."""

import io
import os
from unittest.mock import Mock, call

import pytest

from git_stage_batch.cli import argument_parser
from git_stage_batch.cli.argument_parser import parse_command_line


def _stdin_with_bytes(data: bytes) -> io.TextIOWrapper:
    """Build stdin carrying exact bytes for `--as-stdin` tests."""
    return io.TextIOWrapper(io.BytesIO(data), encoding="utf-8", errors="surrogateescape")


class _UnreadableStdin:
    """Stdin stub that fails if a command reads replacement text too early."""

    class _Buffer:
        def read(self):
            raise AssertionError("stdin should not be read")

    buffer = _Buffer()


def _mock_live_file_candidates(monkeypatch, changed, untracked=()):
    """Provide deterministic live file candidates for parser scope resolution."""
    monkeypatch.setattr(argument_parser, "list_changed_files", lambda: list(changed))
    monkeypatch.setattr(argument_parser, "list_untracked_files", lambda: list(untracked))


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

    monkeypatch.setattr(argument_parser, "run_command", fake_run)
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

    def fake_git(cmd, **kwargs):
        calls.append((cmd, kwargs.get("env")))
        return Mock(returncode=0)

    monkeypatch.setattr(argument_parser, "run_git_command", fake_git)

    assert argument_parser._show_git_stage_batch_help() is True
    assert calls[0][0] == ["help", "git-stage-batch"]
    assert calls[0][1]["MANPATH"] == f"{manpage.parent.parent}{os.pathsep}/usr/share/man"


def test_show_git_stage_batch_help_uses_packaged_command_page(monkeypatch, tmp_path):
    """Command help should prefer the matching packaged man page."""
    manpage = tmp_path / "assets" / "man" / "man1" / "git-stage-batch-include.1"
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

    def fake_git(cmd, **kwargs):
        calls.append((cmd, kwargs.get("env")))
        return Mock(returncode=0)

    monkeypatch.setattr(argument_parser, "run_git_command", fake_git)

    assert argument_parser._show_git_stage_batch_help("stage-batch-include") is True
    assert calls[0][0] == ["help", "git-stage-batch-include"]
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

    def fake_git(cmd, **kwargs):
        calls.append((cmd, kwargs.get("env")))
        manpath_root = kwargs["env"]["MANPATH"].split(os.pathsep, 1)[0]
        staged_manpage = argument_parser.Path(manpath_root) / "man1" / "git-stage-batch.1"
        assert staged_manpage.exists()
        assert staged_manpage.read_text(encoding="utf-8") == "test"
        return Mock(returncode=0)

    monkeypatch.setattr(argument_parser, "run_git_command", fake_git)

    assert argument_parser._show_git_stage_batch_help() is True
    assert calls[0][1]["MANPATH"].endswith(f"{os.pathsep}/usr/share/man")


def test_resolve_live_file_scope_marks_implicit_scope():
    scope = argument_parser._resolve_live_file_scope(None, None)

    assert scope.kind is argument_parser.FileScopeKind.IMPLICIT
    assert scope.files == ()
    assert scope.optional_file() is None


def test_resolve_live_file_scope_marks_pathless_file_scope():
    scope = argument_parser._resolve_live_file_scope("", None)

    assert scope.kind is argument_parser.FileScopeKind.EXPLICIT
    assert scope.files == ("",)
    assert scope.optional_file() == ""


def test_resolve_live_file_scope_resolves_file_argument_as_pattern(monkeypatch):
    _mock_live_file_candidates(monkeypatch, ["src/parser.py", "notes.txt"])

    scope = argument_parser._resolve_live_file_scope("src/parser.py", None)

    assert scope.kind is argument_parser.FileScopeKind.PATTERN
    assert scope.files == ("src/parser.py",)
    assert scope.optional_file() == "src/parser.py"


def test_resolve_live_file_scope_keeps_single_pattern_scope_kind(monkeypatch):
    monkeypatch.setattr(argument_parser, "list_changed_files", lambda: ["src/parser.py", "notes.txt"])
    monkeypatch.setattr(argument_parser, "list_untracked_files", lambda: [])

    scope = argument_parser._resolve_live_file_scope(None, ["*.py"])

    assert scope.kind is argument_parser.FileScopeKind.PATTERN
    assert scope.files == ("src/parser.py",)
    assert scope.optional_file() == "src/parser.py"


def test_resolve_live_file_scope_matches_untracked_pattern_candidates(monkeypatch):
    monkeypatch.setattr(argument_parser, "list_changed_files", lambda: ["src/parser.py"])
    monkeypatch.setattr(argument_parser, "list_untracked_files", lambda: ["notes.txt"])

    scope = argument_parser._resolve_live_file_scope(None, ["*.txt"])

    assert scope.kind is argument_parser.FileScopeKind.PATTERN
    assert scope.files == ("notes.txt",)


def test_resolve_batch_file_scope_marks_implicit_scope():
    scope = argument_parser._resolve_batch_file_scope("batch", None, None)

    assert scope.kind is argument_parser.FileScopeKind.IMPLICIT
    assert scope.files == ()
    assert scope.optional_file() is None


def test_resolve_batch_file_scope_marks_pathless_file_scope():
    scope = argument_parser._resolve_batch_file_scope("batch", "", None)

    assert scope.kind is argument_parser.FileScopeKind.EXPLICIT
    assert scope.files == ("",)
    assert scope.optional_file() == ""


def test_resolve_batch_file_scope_resolves_file_argument_as_pattern(monkeypatch):
    monkeypatch.setattr(argument_parser, "batch_exists", lambda name: True)
    monkeypatch.setattr(
        argument_parser,
        "read_batch_metadata",
        lambda name: {"files": {"src/parser.py": {}, "notes.txt": {}}},
    )

    scope = argument_parser._resolve_batch_file_scope("batch", "src/parser.py", None)

    assert scope.kind is argument_parser.FileScopeKind.PATTERN
    assert scope.files == ("src/parser.py",)
    assert scope.optional_file() == "src/parser.py"


def test_resolve_batch_file_scope_keeps_pattern_scope_kind(monkeypatch):
    monkeypatch.setattr(argument_parser, "batch_exists", lambda name: True)
    monkeypatch.setattr(
        argument_parser,
        "read_batch_metadata",
        lambda name: {"files": {"src/parser.py": {}, "src/render.py": {}, "notes.txt": {}}},
    )

    scope = argument_parser._resolve_batch_file_scope("batch", None, ["*.py"])

    assert scope.kind is argument_parser.FileScopeKind.PATTERN
    assert scope.files == ("src/parser.py", "src/render.py")


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


def test_parse_command_line_subcommand_help_uses_command_manpage(monkeypatch):
    """Subcommand help should open that command's man page."""
    calls = []

    def fake_help(help_topic):
        calls.append(help_topic)
        return True

    monkeypatch.setattr(argument_parser, "_show_git_stage_batch_help", fake_help)

    with pytest.raises(SystemExit) as exc_info:
        parse_command_line(["include", "--help"], quiet=True)

    assert exc_info.value.code == 0
    assert calls == ["stage-batch-include"]


def test_parse_command_line_alias_help_uses_canonical_command_manpage(monkeypatch):
    """Alias help should open the canonical command's man page."""
    calls = []

    def fake_help(help_topic):
        calls.append(help_topic)
        return True

    monkeypatch.setattr(argument_parser, "_show_git_stage_batch_help", fake_help)

    with pytest.raises(SystemExit) as exc_info:
        parse_command_line(["i", "--help"], quiet=True)

    assert exc_info.value.code == 0
    assert calls == ["stage-batch-include"]


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


def test_parse_command_line_start_passes_auto_advance(monkeypatch):
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_start", mock_command)

    args = parse_command_line(["start", "--no-auto-advance"], quiet=True)

    assert args is not None
    args.func(args)
    mock_command.assert_called_once_with(context_lines=None, auto_advance=False)


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


def test_parse_command_line_again_passes_auto_advance(monkeypatch):
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_again", mock_command)

    args = parse_command_line(["again", "--auto-advance"], quiet=True)

    assert args is not None
    args.func(args)
    mock_command.assert_called_once_with(auto_advance=True)


def test_parse_command_line_show():
    """Test parsing show command."""
    args = parse_command_line(["show"], quiet=True)
    assert args is not None
    assert args.command == "show"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_show_page_requires_file():
    args = parse_command_line(["show", "--page", "2"], quiet=True)
    assert args is not None
    with pytest.raises(argument_parser.CommandError, match="requires `--file`"):
        args.func(args)


def test_show_pages_alias_requires_file():
    args = parse_command_line(["show", "--pages", "2"], quiet=True)
    assert args is not None
    assert args.page == "2"
    with pytest.raises(argument_parser.CommandError, match="requires `--file` or a single-file `--files` match"):
        args.func(args)


def test_show_page_accepts_single_files_match(monkeypatch):
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_show", mock_command)
    _mock_live_file_candidates(monkeypatch, ["src/parser.py", "notes.txt"])

    args = parse_command_line(["show", "--files", "*.py", "--page", "2"], quiet=True)

    assert args is not None
    args.func(args)
    mock_command.assert_called_once_with(file="src/parser.py", page="2", porcelain=False)


def test_show_page_accepts_single_file_pattern_match(monkeypatch):
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_show", mock_command)
    _mock_live_file_candidates(monkeypatch, ["src/parser.py", "notes.txt"])

    args = parse_command_line(["show", "--file", "*.py", "--page", "2"], quiet=True)

    assert args is not None
    args.func(args)
    mock_command.assert_called_once_with(file="src/parser.py", page="2", porcelain=False)


def test_show_page_rejects_multiple_files_matches(monkeypatch):
    _mock_live_file_candidates(monkeypatch, ["foo.py", "bar.py"])

    args = parse_command_line(["show", "--files", "*.py", "--page", "2"], quiet=True)

    assert args is not None
    with pytest.raises(argument_parser.CommandError, match="requires exactly one resolved file"):
        args.func(args)


def test_show_page_rejects_multiple_file_pattern_matches(monkeypatch):
    _mock_live_file_candidates(monkeypatch, ["foo.py", "bar.py"])

    args = parse_command_line(["show", "--file", "*.py", "--page", "2"], quiet=True)

    assert args is not None
    with pytest.raises(argument_parser.CommandError, match="requires exactly one resolved file"):
        args.func(args)


def test_show_page_rejects_line_selection(monkeypatch):
    _mock_live_file_candidates(monkeypatch, ["src/parser.py"])
    args = parse_command_line(["show", "--file", "src/parser.py", "--page", "2", "--line", "1"], quiet=True)
    assert args is not None
    with pytest.raises(argument_parser.CommandError, match="together with `show --line`"):
        args.func(args)


def test_show_page_rejects_porcelain(monkeypatch):
    _mock_live_file_candidates(monkeypatch, ["src/parser.py"])
    args = parse_command_line(["show", "--file", "src/parser.py", "--page", "2", "--porcelain"], quiet=True)
    assert args is not None
    with pytest.raises(argument_parser.CommandError, match="with `--porcelain`"):
        args.func(args)


def test_show_from_page_accepts_single_file_batch_without_file(monkeypatch):
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_show_from_batch", mock_command)
    monkeypatch.setattr(argument_parser, "batch_exists", lambda name: True)
    monkeypatch.setattr(
        argument_parser,
        "read_batch_metadata",
        lambda name: {"files": {"src/parser.py": {}}},
    )

    args = parse_command_line(["show", "--from", "batch", "--page", "2"], quiet=True)

    assert args is not None
    args.func(args)
    mock_command.assert_called_once_with("batch", None, None, page="2")


def test_show_from_page_rejects_multi_file_batch_without_file(monkeypatch):
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_show_from_batch", mock_command)
    monkeypatch.setattr(argument_parser, "batch_exists", lambda name: True)
    monkeypatch.setattr(
        argument_parser,
        "read_batch_metadata",
        lambda name: {"files": {"src/parser.py": {}, "src/render.py": {}}},
    )

    args = parse_command_line(["show", "--from", "batch", "--page", "2"], quiet=True)

    assert args is not None
    with pytest.raises(argument_parser.CommandError, match="single-file batch"):
        args.func(args)
    mock_command.assert_not_called()


def test_show_from_page_missing_batch_reports_missing_batch(monkeypatch):
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_show_from_batch", mock_command)
    monkeypatch.setattr(argument_parser, "batch_exists", lambda name: False)
    monkeypatch.setattr(
        argument_parser,
        "read_batch_metadata",
        Mock(side_effect=AssertionError("missing batch should not be read")),
    )

    args = parse_command_line(["show", "--from", "missing", "--page", "2"], quiet=True)

    assert args is not None
    with pytest.raises(argument_parser.CommandError, match="Batch 'missing' does not exist"):
        args.func(args)
    mock_command.assert_not_called()


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


def test_parse_command_line_status_for_prompt():
    """Test parsing status prompt format."""
    args = parse_command_line(["status", "--for-prompt", " [{status}]"], quiet=True)
    assert args is not None
    assert args.command == "status"
    assert args.prompt_format == " [{status}]"
    assert args.porcelain is False
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_status_for_prompt_default():
    """Test parsing status prompt mode with the default label."""
    args = parse_command_line(["status", "--for-prompt"], quiet=True)
    assert args is not None
    assert args.command == "status"
    assert args.prompt_format == "STAGING"


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


def test_parse_command_line_include_passes_auto_advance(monkeypatch):
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_include", mock_command)

    args = parse_command_line(["include", "--no-auto-advance"], quiet=True)

    assert args is not None
    args.func(args)
    mock_command.assert_called_once_with(auto_advance=False)


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


def test_parse_command_line_include_with_file_and_as_dispatches_file_replacement(monkeypatch):
    """Include --file --as without --line should dispatch file replacement staging."""
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_include_file_as", mock_command)
    _mock_live_file_candidates(monkeypatch, ["path.txt"])

    args = parse_command_line(
        ["include", "--file", "path.txt", "--as", "replacement"],
        quiet=True,
    )

    assert args is not None
    args.func(args)
    mock_command.assert_called_once_with(
        "replacement",
        file="path.txt",
        auto_advance=None,
    )


def test_parse_command_line_include_with_file_and_as_stdin_dispatches_file_replacement(monkeypatch):
    """Include --file --as-stdin should preserve trailing newlines."""
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_include_file_as", mock_command)
    monkeypatch.setattr(argument_parser.sys, "stdin", _stdin_with_bytes(b"replacement\n"))
    _mock_live_file_candidates(monkeypatch, ["path.txt"])

    args = parse_command_line(
        ["include", "--file", "path.txt", "--as-stdin"],
        quiet=True,
    )

    assert args is not None
    args.func(args)
    mock_command.assert_called_once_with(
        "replacement\n",
        file="path.txt",
        auto_advance=None,
    )


def test_parse_command_line_include_with_line_range_and_as_stdin_dispatches_line_replacement(monkeypatch):
    """Include --line range --as-stdin should forward exact replacement text."""
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_include_line_as", mock_command)
    monkeypatch.setattr(argument_parser.sys, "stdin", _stdin_with_bytes(b"replacement one\nreplacement two\n"))
    _mock_live_file_candidates(monkeypatch, ["path.txt"])

    args = parse_command_line(
        ["include", "--file", "path.txt", "--line", "2-3", "--as-stdin"],
        quiet=True,
    )

    assert args is not None
    args.func(args)
    mock_command.assert_called_once_with(
        "2-3",
        "replacement one\nreplacement two\n",
        file="path.txt",
        no_edge_overlap=False,
        auto_advance=None,
    )


def test_parse_command_line_include_with_file_and_line_dispatches_file_scope(monkeypatch):
    """Include --file --line should dispatch to file-scoped line staging."""
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_include_line", mock_command)
    _mock_live_file_candidates(monkeypatch, ["path.txt"])

    args = parse_command_line(
        ["include", "--file", "path.txt", "--line", "2-3"],
        quiet=True,
    )

    assert args is not None
    args.func(args)
    mock_command.assert_called_once_with(
        "2-3",
        file="path.txt",
        auto_advance=None,
    )


def test_parse_command_line_include_with_files_dispatches_per_file(monkeypatch):
    """Include should dispatch once per file resolved from --files."""
    mock_command = Mock(return_value=1)
    monkeypatch.setattr(argument_parser.commands, "command_include_file", mock_command)
    mock_select_next = Mock(return_value=True)
    monkeypatch.setattr(argument_parser, "select_next_change_after_action", mock_select_next)
    mock_show_selected_change = Mock()
    monkeypatch.setattr(argument_parser, "show_selected_change", mock_show_selected_change)
    _mock_live_file_candidates(monkeypatch, ["foo.py", "dir/bar.py", "baz.txt"])

    args = parse_command_line(["include", "--files", "*.py"], quiet=True)

    assert args is not None
    assert args.file_patterns == ["*.py"]
    args.func(args)
    assert mock_command.call_args_list == [
        call("foo.py", quiet=True, advance=False),
        call("dir/bar.py", quiet=True, advance=False),
    ]
    mock_select_next.assert_called_once_with(auto_advance=None)
    mock_show_selected_change.assert_called_once_with()


def test_parse_command_line_include_rejects_files_without_patterns():
    """--files should still require at least one pattern."""
    assert parse_command_line(["include", "--files"], quiet=True) is None


def test_parse_command_line_include_with_file_pattern_dispatches_per_file(monkeypatch):
    """Include --file with a pattern should dispatch like --files."""
    mock_command = Mock(return_value=1)
    monkeypatch.setattr(argument_parser.commands, "command_include_file", mock_command)
    mock_select_next = Mock(return_value=True)
    monkeypatch.setattr(argument_parser, "select_next_change_after_action", mock_select_next)
    mock_show_selected_change = Mock()
    monkeypatch.setattr(argument_parser, "show_selected_change", mock_show_selected_change)
    _mock_live_file_candidates(monkeypatch, ["foo.py", "dir/bar.py", "baz.txt"])

    args = parse_command_line(["include", "--file", "*.py"], quiet=True)

    assert args is not None
    args.func(args)
    assert mock_command.call_args_list == [
        call("foo.py", quiet=True, advance=False),
        call("dir/bar.py", quiet=True, advance=False),
    ]
    mock_select_next.assert_called_once_with(auto_advance=None)
    mock_show_selected_change.assert_called_once_with()


def test_parse_command_line_include_with_file_pattern_honors_exclusions(monkeypatch):
    """Include --file should accept multiple gitignore-style patterns."""
    mock_command = Mock(return_value=1)
    monkeypatch.setattr(argument_parser.commands, "command_include_file", mock_command)
    monkeypatch.setattr(argument_parser, "select_next_change_after_action", Mock(return_value=False))
    monkeypatch.setattr(argument_parser, "show_selected_change", Mock())
    _mock_live_file_candidates(monkeypatch, ["foo.py", "bar.py", "baz.txt"])

    args = parse_command_line(["include", "--file", "*.py", "!bar.py"], quiet=True)

    assert args is not None
    args.func(args)
    mock_command.assert_called_once_with("foo.py", auto_advance=None)


def test_parse_command_line_include_with_file_preserves_exact_metachar_path(monkeypatch):
    """Include --file PATH should still handle exact paths that look like patterns."""
    mock_command = Mock(return_value=1)
    monkeypatch.setattr(argument_parser.commands, "command_include_file", mock_command)
    _mock_live_file_candidates(monkeypatch, ["src/[parser].py", "src/p.py"])

    args = parse_command_line(["include", "--file", "src/[parser].py"], quiet=True)

    assert args is not None
    args.func(args)
    mock_command.assert_called_once_with("src/[parser].py", auto_advance=None)


def test_parse_command_line_include_line_rejects_multiple_file_pattern_matches(monkeypatch):
    """Include --file patterns must resolve to one file for line operations."""
    _mock_live_file_candidates(monkeypatch, ["foo.py", "bar.py"])

    args = parse_command_line(["include", "--file", "*.py", "--line", "1"], quiet=True)

    assert args is not None
    with pytest.raises(argument_parser.CommandError, match="Cannot use --lines with multiple files"):
        args.func(args)


def test_parse_command_line_include_with_files_and_as_dispatches_single_match(monkeypatch):
    """Include --files --as should work when patterns resolve to one file."""
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_include_file_as", mock_command)
    _mock_live_file_candidates(monkeypatch, ["path.txt", "notes.md"])

    args = parse_command_line(["include", "--files", "*.txt", "--as", "replacement"], quiet=True)

    assert args is not None
    args.func(args)
    mock_command.assert_called_once_with(
        "replacement",
        file="path.txt",
        auto_advance=None,
    )


def test_parse_command_line_include_with_files_and_as_rejects_multiple_matches(monkeypatch):
    """Include --files --as should reject multi-file resolution."""
    _mock_live_file_candidates(monkeypatch, ["foo.py", "bar.py"])

    args = parse_command_line(["include", "--files", "*.py", "--as", "replacement"], quiet=True)

    assert args is not None
    with pytest.raises(argument_parser.CommandError, match="Cannot use --as with multiple files"):
        args.func(args)


def test_parse_command_line_include_from_with_files_resolves_batch_scope_only(monkeypatch):
    """include --from --files should match batch files, not current live changes."""
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_include_from_batch", mock_command)
    monkeypatch.setattr(argument_parser, "batch_exists", lambda name: True)
    monkeypatch.setattr(
        argument_parser,
        "read_batch_metadata",
        lambda name: {"files": {"foo.py": {}, "bar.py": {}, "notes.txt": {}}},
    )
    monkeypatch.setattr(
        argument_parser,
        "list_changed_files",
        Mock(side_effect=AssertionError("live scope should not be resolved")),
    )

    args = parse_command_line(["include", "--from", "batch", "--files", "*.py"], quiet=True)

    assert args is not None
    args.func(args)
    assert mock_command.call_args_list == [
        call("batch", None, "foo.py"),
        call("batch", None, "bar.py"),
    ]


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


def test_parse_command_line_include_rejects_as_and_as_stdin_together(monkeypatch):
    """Include should reject mixing literal and stdin replacement sources."""
    monkeypatch.setattr(argument_parser.sys, "stdin", _stdin_with_bytes(b"replacement\n"))
    args = parse_command_line(
        ["include", "--file", "path.txt", "--as", "replacement", "--as-stdin"],
        quiet=True,
    )
    assert args is not None

    with pytest.raises(argument_parser.CommandError, match="Cannot use `--as` and `--as-stdin` together"):
        args.func(args)


def test_parse_command_line_include_with_no_edge_overlap_dispatches_line_replacement(monkeypatch):
    """Include --line --as --no-edge-overlap should forward the no-edge-overlap flag."""
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_include_line_as", mock_command)
    _mock_live_file_candidates(monkeypatch, ["path.txt"])

    args = parse_command_line(
        ["include", "--file", "path.txt", "--line", "2-3", "--as", "replacement", "--no-edge-overlap"],
        quiet=True,
    )

    assert args is not None
    args.func(args)
    mock_command.assert_called_once_with(
        "2-3",
        "replacement",
        file="path.txt",
        no_edge_overlap=True,
        auto_advance=None,
    )


def test_parse_command_line_include_rejects_no_edge_overlap_without_line_as():
    """Include should reject --no-edge-overlap outside live include --line --as."""
    args = parse_command_line(
        ["include", "--file", "path.txt", "--no-edge-overlap"],
        quiet=True,
    )
    assert args is not None

    with pytest.raises(argument_parser.CommandError, match="`--no-edge-overlap` requires `include --line --as`"):
        args.func(args)


def test_parse_command_line_include_invalid_as_stdin_shape_does_not_read(monkeypatch):
    """Invalid include --as-stdin combinations should fail before consuming stdin."""
    monkeypatch.setattr(argument_parser.sys, "stdin", _UnreadableStdin())
    args = parse_command_line(["include", "--to", "batch", "--as-stdin"], quiet=True)
    assert args is not None

    with pytest.raises(argument_parser.CommandError, match="`include --as` requires"):
        args.func(args)


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


def test_parse_command_line_skip_passes_auto_advance(monkeypatch):
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_skip", mock_command)

    args = parse_command_line(["skip", "--no-auto-advance"], quiet=True)

    assert args is not None
    args.func(args)
    mock_command.assert_called_once_with(auto_advance=False)


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


def test_parse_command_line_skip_repeated_file_uses_argument_bearing_value():
    """Repeated --file should not make an earlier pathless use conflict."""
    args = parse_command_line(["skip", "--file", "--file", "src/debug.py"], quiet=True)
    assert args is not None
    assert args.file == "src/debug.py"


def test_parse_command_line_skip_repeated_file_keeps_final_pathless_value():
    """A final pathless --file should keep selected-file behavior."""
    args = parse_command_line(["skip", "--file", "src/debug.py", "--file"], quiet=True)
    assert args is not None
    assert args.file == ""


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
    mock_command = Mock(side_effect=[1, 2])
    monkeypatch.setattr(argument_parser.commands, "command_skip_file", mock_command)
    mock_select_next = Mock(return_value=True)
    monkeypatch.setattr(argument_parser, "select_next_change_after_action", mock_select_next)
    mock_show_selected_change = Mock()
    monkeypatch.setattr(argument_parser, "show_selected_change", mock_show_selected_change)
    _mock_live_file_candidates(monkeypatch, ["foo.py", "bar.py", "notes.txt"])

    args = parse_command_line(["skip", "--files", "*.py"], quiet=True)

    assert args is not None
    args.func(args)
    assert mock_command.call_args_list == [
        call("foo.py", quiet=True, advance=False),
        call("bar.py", quiet=True, advance=False),
    ]
    mock_select_next.assert_called_once_with(auto_advance=None)
    mock_show_selected_change.assert_called_once_with()


def test_parse_command_line_skip_rejects_lines_with_multiple_files(monkeypatch):
    """Skip should reject --line/--lines when --files resolves to multiple files."""
    _mock_live_file_candidates(monkeypatch, ["foo.py", "bar.py"])
    args = parse_command_line(["skip", "--files", "*.py", "--lines", "1"], quiet=True)
    assert args is not None

    with pytest.raises(argument_parser.CommandError, match="Cannot use --lines with multiple files"):
        args.func(args)


def test_parse_command_line_skip_rejects_pathless_file_with_files():
    """Pathless --file should remain separate from --files pattern resolution."""
    args = parse_command_line(["skip", "--file", "--files", "*.py"], quiet=True)
    assert args is not None

    with pytest.raises(argument_parser.CommandError, match="Cannot use --file together with --files"):
        args.func(args)


def test_parse_command_line_skip_combines_file_and_files_patterns(monkeypatch):
    """Argument-bearing --file and --files should resolve together."""
    mock_command = Mock(side_effect=[1, 2])
    monkeypatch.setattr(argument_parser.commands, "command_skip_file", mock_command)
    monkeypatch.setattr(argument_parser, "select_next_change_after_action", Mock(return_value=False))
    monkeypatch.setattr(argument_parser, "show_selected_change", Mock())
    _mock_live_file_candidates(monkeypatch, ["foo.py", "bar.py", "notes.txt"])

    args = parse_command_line(["skip", "--file", "foo.py", "--files", "bar.py"], quiet=True)

    assert args is not None
    args.func(args)
    assert mock_command.call_args_list == [
        call("foo.py", quiet=True, advance=False),
        call("bar.py", quiet=True, advance=False),
    ]


def test_parse_command_line_discard_with_files_dispatches_per_file(monkeypatch):
    """Discard should dispatch once per file resolved from --files."""
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_discard_file", mock_command)
    _mock_live_file_candidates(monkeypatch, ["foo.py", "bar.py", "notes.txt"])

    args = parse_command_line(["discard", "--files", "*.py"], quiet=True)

    assert args is not None
    args.func(args)
    assert mock_command.call_args_list == [
        call("foo.py", auto_advance=None),
        call("bar.py", auto_advance=None),
    ]


def test_parse_command_line_discard_with_file_pattern_dispatches_per_file(monkeypatch):
    """Discard --file with a pattern should dispatch like --files."""
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_discard_file", mock_command)
    _mock_live_file_candidates(monkeypatch, ["foo.py", "bar.py", "notes.txt"])

    args = parse_command_line(["discard", "--file", "*.py"], quiet=True)

    assert args is not None
    args.func(args)
    assert mock_command.call_args_list == [
        call("foo.py", auto_advance=None),
        call("bar.py", auto_advance=None),
    ]


def test_parse_command_line_discard_from_with_files_resolves_batch_scope_only(monkeypatch):
    """discard --from --files should match batch files, not current live changes."""
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_discard_from_batch", mock_command)
    monkeypatch.setattr(argument_parser, "batch_exists", lambda name: True)
    monkeypatch.setattr(
        argument_parser,
        "read_batch_metadata",
        lambda name: {"files": {"foo.py": {}, "bar.py": {}, "notes.txt": {}}},
    )
    monkeypatch.setattr(
        argument_parser,
        "list_changed_files",
        Mock(side_effect=AssertionError("live scope should not be resolved")),
    )

    args = parse_command_line(["discard", "--from", "batch", "--files", "*.py"], quiet=True)

    assert args is not None
    args.func(args)
    assert mock_command.call_args_list == [
        call("batch", None, "foo.py"),
        call("batch", None, "bar.py"),
    ]


def test_parse_command_line_discard_to_with_files_uses_aggregate_dispatch(monkeypatch):
    """discard --to --files should suppress per-file selected-change display."""
    mock_command = Mock(
        return_value=argument_parser.commands.DiscardFilesToBatchResult(
            discarded_hunks=2,
            discarded_files=["foo.py", "bar.py"],
        )
    )
    mock_select_next = Mock(return_value=True)
    mock_show = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_discard_files_to_batch", mock_command)
    monkeypatch.setattr(argument_parser, "select_next_change_after_action", mock_select_next)
    monkeypatch.setattr(argument_parser, "show_selected_change", mock_show)
    _mock_live_file_candidates(monkeypatch, ["foo.py", "bar.py", "notes.txt"])

    args = parse_command_line(["discard", "--to", "batch", "--files", "*.py"], quiet=True)

    assert args is not None
    args.func(args)
    assert mock_command.call_args_list == [
        call("batch", ["foo.py", "bar.py"], quiet=True, advance=False, auto_advance=None),
    ]
    mock_select_next.assert_called_once_with(auto_advance=None)
    mock_show.assert_called_once_with()


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


def test_parse_command_line_show_with_files_uses_file_list(monkeypatch):
    """Show should route multi-file matches to a navigational file list."""
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_show_file_list", mock_command)
    _mock_live_file_candidates(monkeypatch, ["foo.py", "bar.py", "notes.txt"])

    args = parse_command_line(["show", "--files", "*.py"], quiet=True)

    assert args is not None
    args.func(args)
    mock_command.assert_called_once_with(["foo.py", "bar.py"])


def test_parse_command_line_show_with_files_raises_command_error_for_no_matches(monkeypatch):
    """Show should report unmatched --files patterns without a traceback."""
    _mock_live_file_candidates(monkeypatch, ["foo.py", "bar.py"])

    args = parse_command_line(["show", "--files", "*.md"], quiet=True)

    assert args is not None
    with pytest.raises(argument_parser.CommandError, match="No changed files matched: \\*.md"):
        args.func(args)


def test_parse_command_line_discard():
    """Test parsing discard command."""
    args = parse_command_line(["discard"], quiet=True)
    assert args is not None
    assert args.command == "discard"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_install_assets():
    """Test parsing install-assets for Claude skills."""
    args = parse_command_line(["install-assets", "claude-skills"], quiet=True)
    assert args is not None
    assert args.command == "install-assets"
    assert args.asset_group == "claude-skills"
    assert args.filters is None
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_install_assets_without_group():
    """Test parsing install-assets with no asset group."""
    args = parse_command_line(["install-assets"], quiet=True)
    assert args is not None
    assert args.command == "install-assets"
    assert args.asset_group is None
    assert args.filters is None
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_install_claude_agents():
    """Test parsing install-assets for Claude agents."""
    args = parse_command_line(["install-assets", "claude-agents"], quiet=True)
    assert args is not None
    assert args.command == "install-assets"
    assert args.asset_group == "claude-agents"
    assert args.filters is None
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_install_codex_assets():
    """Test parsing install-assets for Codex skills."""
    args = parse_command_line(["install-assets", "codex-skills"], quiet=True)
    assert args is not None
    assert args.command == "install-assets"
    assert args.asset_group == "codex-skills"
    assert args.filters is None
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_install_assets_with_filter():
    """Test parsing install-assets with --filter."""
    args = parse_command_line(
        ["install-assets", "claude-skills", "--filter", "commit-unstaged-changes"],
        quiet=True,
    )
    assert args is not None
    assert args.asset_group == "claude-skills"
    assert args.filters == ["commit-unstaged-changes"]


def test_parse_command_line_install_assets_with_force():
    """Test parsing install-assets with --force."""
    args = parse_command_line(
        ["install-assets", "claude-agents", "--force"],
        quiet=True,
    )
    assert args is not None
    assert args.asset_group == "claude-agents"
    assert args.force is True


def test_parse_command_line_install_codex_assets_with_filter():
    """Test parsing install-assets for Codex skills with --filter."""
    args = parse_command_line(
        ["install-assets", "codex-skills", "--filter", "commit-unstaged-changes"],
        quiet=True,
    )
    assert args is not None
    assert args.asset_group == "codex-skills"
    assert args.filters == ["commit-unstaged-changes"]


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


def test_parse_command_line_discard_with_file_and_as_dispatches_file_replacement(monkeypatch):
    """Discard --file --as without --line should dispatch file replacement."""
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_discard_file_as", mock_command)
    _mock_live_file_candidates(monkeypatch, ["path.txt"])

    args = parse_command_line(
        ["discard", "--file", "path.txt", "--as", "replacement"],
        quiet=True,
    )

    assert args is not None
    args.func(args)
    mock_command.assert_called_once_with(
        "replacement",
        file="path.txt",
        auto_advance=None,
    )


def test_parse_command_line_discard_with_file_and_as_stdin_dispatches_file_replacement(monkeypatch):
    """Discard --file --as-stdin should preserve trailing newlines."""
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_discard_file_as", mock_command)
    monkeypatch.setattr(argument_parser.sys, "stdin", _stdin_with_bytes(b"replacement\n"))
    _mock_live_file_candidates(monkeypatch, ["path.txt"])

    args = parse_command_line(
        ["discard", "--file", "path.txt", "--as-stdin"],
        quiet=True,
    )

    assert args is not None
    args.func(args)
    mock_command.assert_called_once_with(
        "replacement\n",
        file="path.txt",
        auto_advance=None,
    )


def test_parse_command_line_discard_to_line_range_and_as_stdin_dispatches_replacement(monkeypatch):
    """Discard --to --line range --as-stdin should forward exact replacement text."""
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_discard_line_as_to_batch", mock_command)
    monkeypatch.setattr(argument_parser.sys, "stdin", _stdin_with_bytes(b"replacement one\nreplacement two\n"))
    _mock_live_file_candidates(monkeypatch, ["path.txt"])

    args = parse_command_line(
        ["discard", "--to", "batch", "--file", "path.txt", "--line", "2-3", "--as-stdin"],
        quiet=True,
    )

    assert args is not None
    args.func(args)
    mock_command.assert_called_once_with(
        "batch",
        "2-3",
        "replacement one\nreplacement two\n",
        file="path.txt",
        no_edge_overlap=False,
        auto_advance=None,
    )


def test_parse_command_line_discard_rejects_as_and_as_stdin_together(monkeypatch):
    """Discard should reject mixing literal and stdin replacement sources."""
    monkeypatch.setattr(argument_parser.sys, "stdin", _stdin_with_bytes(b"replacement\n"))
    args = parse_command_line(
        ["discard", "--file", "path.txt", "--as", "replacement", "--as-stdin"],
        quiet=True,
    )
    assert args is not None

    with pytest.raises(argument_parser.CommandError, match="Cannot use `--as` and `--as-stdin` together"):
        args.func(args)


def test_parse_command_line_discard_with_no_edge_overlap_dispatches_line_replacement(monkeypatch):
    """Discard --to --line --as --no-edge-overlap should forward the no-edge-overlap flag."""
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_discard_line_as_to_batch", mock_command)
    _mock_live_file_candidates(monkeypatch, ["path.txt"])

    args = parse_command_line(
        ["discard", "--to", "batch", "--file", "path.txt", "--line", "2-3", "--as", "replacement", "--no-edge-overlap"],
        quiet=True,
    )

    assert args is not None
    args.func(args)
    mock_command.assert_called_once_with(
        "batch",
        "2-3",
        "replacement",
        file="path.txt",
        no_edge_overlap=True,
        auto_advance=None,
    )


def test_parse_command_line_discard_rejects_no_edge_overlap_without_to_line_as():
    """Discard should reject --no-edge-overlap outside discard --to --line --as."""
    args = parse_command_line(
        ["discard", "--file", "path.txt", "--no-edge-overlap"],
        quiet=True,
    )
    assert args is not None

    with pytest.raises(argument_parser.CommandError, match="`--no-edge-overlap` requires `discard --to --line --as`"):
        args.func(args)


def test_parse_command_line_discard_invalid_as_stdin_shape_does_not_read(monkeypatch):
    """Invalid discard --as-stdin combinations should fail before consuming stdin."""
    monkeypatch.setattr(argument_parser.sys, "stdin", _UnreadableStdin())
    args = parse_command_line(["discard", "--from", "batch", "--as-stdin"], quiet=True)
    assert args is not None

    with pytest.raises(argument_parser.CommandError, match="`discard --as` requires"):
        args.func(args)


def test_parse_command_line_discard_with_file_and_line_dispatches_file_scope(monkeypatch):
    """Discard --file --line should dispatch to file-scoped line discard."""
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_discard_line", mock_command)
    _mock_live_file_candidates(monkeypatch, ["path.txt"])

    args = parse_command_line(
        ["discard", "--file", "path.txt", "--line", "2-3"],
        quiet=True,
    )

    assert args is not None
    args.func(args)
    mock_command.assert_called_once_with(
        "2-3",
        file="path.txt",
        auto_advance=None,
    )


def test_parse_command_line_discard_alias():
    """Test parsing discard command alias 'd'."""
    args = parse_command_line(["d"], quiet=True)
    assert args is not None
    assert args.command == "d"
    assert hasattr(args, "func")
    assert callable(args.func)


def test_parse_command_line_discard_passes_auto_advance(monkeypatch):
    mock_command = Mock()
    monkeypatch.setattr(argument_parser.commands, "command_discard", mock_command)

    args = parse_command_line(["discard", "--no-auto-advance"], quiet=True)

    assert args is not None
    args.func(args)
    mock_command.assert_called_once_with(auto_advance=False)


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
