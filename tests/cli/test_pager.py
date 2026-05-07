"""Tests for CLI pager activation."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from importlib import import_module

from git_stage_batch.cli import pager as pager_module
from git_stage_batch.cli.pager import should_page_output

main_module = import_module("git_stage_batch.cli.main")


def _make_args(**overrides) -> argparse.Namespace:
    defaults = {
        "command": None,
        "interactive_flag": False,
        "porcelain": False,
        "working_directory": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_should_page_show_when_stdout_is_tty(monkeypatch):
    """Show output should page when attached to a tty."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    assert should_page_output(_make_args(command="show")) is True


def test_should_page_include_when_stdout_is_tty(monkeypatch):
    """Include should page because it advances and prints the next hunk."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    assert should_page_output(_make_args(command="include")) is True


def test_should_not_page_porcelain_output(monkeypatch):
    """Machine-readable output should bypass the pager."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    assert should_page_output(_make_args(command="status", porcelain=True)) is False


def test_should_not_page_prompt_output(monkeypatch):
    """Prompt output should bypass the pager."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    assert should_page_output(_make_args(command="status", prompt_format="STAGING")) is False


def test_should_not_page_interactive_mode(monkeypatch):
    """Interactive mode manages its own terminal output."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    assert should_page_output(_make_args(command="interactive")) is False
    assert should_page_output(_make_args(command=None, interactive_flag=True)) is False


def test_resolve_git_pager_treats_cat_as_disabled(monkeypatch):
    """Git's sentinel pager value should bypass pager startup."""
    monkeypatch.setattr(
        pager_module,
        "run_git_command",
        lambda _args, check=False: argparse.Namespace(returncode=0, stdout="cat\n"),
    )

    assert pager_module._resolve_git_pager() is None


def test_build_pager_environment_sets_git_compatible_defaults(monkeypatch):
    """Pager environment should include Git-style defaults when unset."""
    monkeypatch.delenv("LESS", raising=False)
    monkeypatch.delenv("LV", raising=False)

    env = pager_module._build_pager_environment()

    assert env["LESS"] == "FRX"
    assert env["LV"] == "-c"


def test_build_pager_environment_preserves_user_overrides(monkeypatch):
    """Pager environment should not override explicit user settings."""
    monkeypatch.setenv("LESS", "SX")
    monkeypatch.setenv("LV", "-abc")

    env = pager_module._build_pager_environment()

    assert env["LESS"] == "SX"
    assert env["LV"] == "-abc"


def test_pager_output_starts_pager_with_git_compatible_environment(monkeypatch):
    """Pager startup should pass the environment needed for less color/quit behavior."""
    captured: dict[str, object] = {}

    class DummyProcess:
        def wait(self) -> int:
            return 0

    monkeypatch.delenv("LESS", raising=False)
    monkeypatch.delenv("LV", raising=False)
    monkeypatch.setattr(pager_module, "_resolve_git_pager", lambda: "less")

    def fake_start_command(arguments, **kwargs):
        captured["arguments"] = arguments
        captured["env"] = kwargs.get("env")
        return DummyProcess()

    monkeypatch.setattr(pager_module, "start_command", fake_start_command)

    with pager_module.pager_output():
        pass

    assert captured["arguments"] == ["sh", "-c", "less"]
    assert captured["env"]["LESS"] == "FRX"
    assert captured["env"]["LV"] == "-c"


def test_main_wraps_dispatch_with_pager(monkeypatch):
    """Main should activate the pager for pageable commands."""
    events: list[str] = []
    args = _make_args(command="show")

    monkeypatch.setattr(main_module, "parse_command_line", lambda _argv, quiet=False: args)
    monkeypatch.setattr(main_module, "should_page_output", lambda _args: True)

    @contextmanager
    def fake_pager_output():
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    monkeypatch.setattr(main_module, "pager_output", fake_pager_output)
    monkeypatch.setattr(main_module, "dispatch_args", lambda _args: events.append("dispatch"))

    main_module.main()

    assert events == ["enter", "dispatch", "exit"]


def test_main_skips_pager_when_command_is_not_pageable(monkeypatch):
    """Main should dispatch directly when paging is disabled."""
    events: list[str] = []
    args = _make_args(command="annotate")

    monkeypatch.setattr(main_module, "parse_command_line", lambda _argv, quiet=False: args)
    monkeypatch.setattr(main_module, "should_page_output", lambda _args: False)
    monkeypatch.setattr(main_module, "dispatch_args", lambda _args: events.append("dispatch"))

    main_module.main()

    assert events == ["dispatch"]
