"""Tests for CLI entry point."""

import sys
from argparse import Namespace
from contextlib import contextmanager
from importlib import import_module
from unittest.mock import patch

import pytest

main_module = import_module("git_stage_batch.cli.main")


def test_main_callable():
    """Test that main is callable."""
    assert main_module.main is not None
    assert callable(main_module.main)


def test_main_with_no_args():
    """Test main with no arguments exits with error."""
    with patch.object(sys, 'argv', ['git-stage-batch']):
        with patch.object(main_module, "dispatch_args", side_effect=main_module.CommandError("boom", exit_code=1)):
            with patch.object(main_module, "parse_command_line", return_value=Namespace(working_directory=None)):
                with pytest.raises(SystemExit) as exc_info:
                    main_module.main()
                assert exc_info.value.code == 1


def test_main_acquires_session_lock_before_dispatch():
    """Test main runs dispatch inside the session lock."""
    events = []

    @contextmanager
    def fake_lock():
        events.append("lock-enter")
        try:
            yield
        finally:
            events.append("lock-exit")

    def fake_dispatch(args):
        events.append("dispatch")

    args = Namespace(working_directory=None)

    with patch.object(sys, "argv", ["git-stage-batch", "status"]):
        with patch.object(main_module, "parse_command_line", return_value=args):
            with patch.object(main_module, "should_page_output", return_value=False):
                with patch.object(main_module, "acquire_session_lock", fake_lock):
                    with patch.object(main_module, "dispatch_args", side_effect=fake_dispatch):
                        main_module.main()

    assert events == ["lock-enter", "dispatch", "lock-exit"]


def test_main_skips_session_lock_for_prompt_status():
    """Prompt status should not create or lock session state before dispatch."""
    events = []

    def fake_dispatch(args):
        events.append(("dispatch", args.prompt_format))

    args = Namespace(working_directory=None, prompt_format="STAGING")

    with patch.object(sys, "argv", ["git-stage-batch", "status", "--for-prompt"]):
        with patch.object(main_module, "parse_command_line", return_value=args):
            with patch.object(main_module, "should_page_output", return_value=False):
                with patch.object(
                    main_module,
                    "acquire_session_lock",
                    side_effect=AssertionError("prompt status must not lock"),
                ):
                    with patch.object(main_module, "dispatch_args", side_effect=fake_dispatch):
                        main_module.main()

    assert events == [("dispatch", "STAGING")]


def test_main_handles_keyboard_interrupt_without_traceback(capsys):
    """Ctrl-C should exit cleanly without Python's KeyboardInterrupt traceback."""
    args = Namespace(working_directory=None)

    with patch.object(sys, "argv", ["git-stage-batch", "show"]):
        with patch.object(main_module, "parse_command_line", return_value=args):
            with patch.object(main_module, "should_page_output", return_value=False):
                with patch.object(main_module, "dispatch_args", side_effect=KeyboardInterrupt):
                    with pytest.raises(SystemExit) as exc_info:
                        main_module.main()

    assert exc_info.value.code == 130
    captured = capsys.readouterr()
    assert "Interrupted." in captured.err
    assert "Traceback" not in captured.err
