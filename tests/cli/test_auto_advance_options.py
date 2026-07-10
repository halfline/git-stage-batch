"""Tests for shared CLI auto-advance options."""

import argparse

import pytest

from git_stage_batch.cli.auto_advance_options import add_auto_advance_arguments


def _parser_with_auto_advance() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(exit_on_error=False)
    add_auto_advance_arguments(parser)
    return parser


def test_add_auto_advance_arguments_defaults_to_none():
    args = _parser_with_auto_advance().parse_args([])

    assert args.auto_advance is None


def test_add_auto_advance_arguments_accepts_enabled_flag():
    args = _parser_with_auto_advance().parse_args(["--auto-advance"])

    assert args.auto_advance is True


def test_add_auto_advance_arguments_accepts_disabled_flag():
    args = _parser_with_auto_advance().parse_args(["--no-auto-advance"])

    assert args.auto_advance is False


def test_add_auto_advance_arguments_rejects_mixed_flags():
    with pytest.raises(argparse.ArgumentError):
        _parser_with_auto_advance().parse_args(
            ["--auto-advance", "--no-auto-advance"],
        )
