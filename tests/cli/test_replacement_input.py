"""Tests for CLI replacement input handling."""

import argparse
import io

import pytest

from git_stage_batch.cli import replacement_input
from git_stage_batch.exceptions import CommandError


def _stdin_with_bytes(data: bytes) -> io.TextIOWrapper:
    """Build stdin carrying exact bytes for `--as-stdin` tests."""
    return io.TextIOWrapper(io.BytesIO(data), encoding="utf-8", errors="surrogateescape")


def test_resolve_replacement_text_returns_literal_payload():
    args = argparse.Namespace(as_text="replacement", as_stdin=False)

    result = replacement_input.resolve_replacement_text(args)

    assert result == "replacement"
    assert result.data == b"replacement"
    assert result.exact is True


def test_resolve_replacement_text_reads_exact_stdin(monkeypatch):
    monkeypatch.setattr(
        replacement_input.sys,
        "stdin",
        _stdin_with_bytes(b"replacement\n"),
    )
    args = argparse.Namespace(as_text=None, as_stdin=True)

    result = replacement_input.resolve_replacement_text(args)

    assert result == "replacement\n"
    assert result.data == b"replacement\n"
    assert result.exact is True


def test_resolve_replacement_text_rejects_mixed_sources():
    args = argparse.Namespace(as_text="replacement", as_stdin=True)

    with pytest.raises(CommandError, match="Cannot use `--as` and `--as-stdin`"):
        replacement_input.resolve_replacement_text(args)


def test_resolve_replacement_text_returns_none_without_source():
    args = argparse.Namespace(as_text=None, as_stdin=False)

    assert replacement_input.resolve_replacement_text(args) is None
