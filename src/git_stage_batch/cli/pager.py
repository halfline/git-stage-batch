"""Pager support for non-interactive CLI commands."""

from __future__ import annotations

import argparse
import io
import os
import sys
from contextlib import contextmanager
from typing import Iterator

from ..utils.command import start_command
from ..utils.git import run_git_command


_PAGEABLE_COMMANDS = {
    None,
    "again",
    "block-file",
    "discard",
    "include",
    "list",
    "show",
    "skip",
    "start",
    "status",
    "unblock-file",
    "__complete-files",
}


def should_page_output(args: argparse.Namespace) -> bool:
    """Return whether the selected CLI command should write through a pager."""
    if not sys.stdout.isatty():
        return False

    if getattr(args, "interactive_flag", False):
        return False

    command = getattr(args, "command", None)
    if command == "interactive":
        return False

    if getattr(args, "porcelain", False):
        return False

    if getattr(args, "prompt_format", None) is not None:
        return False

    return command in _PAGEABLE_COMMANDS


def _resolve_git_pager() -> str | None:
    """Resolve the pager command using Git's own precedence rules."""
    result = run_git_command(["var", "GIT_PAGER"], check=False, requires_index_lock=False)
    if result.returncode != 0:
        return None

    pager = result.stdout.strip()
    if pager == "cat":
        return None
    return pager or None


def _build_pager_environment() -> dict[str, str]:
    """Build the pager environment with Git-compatible defaults."""
    env = os.environ.copy()
    env.setdefault("LESS", "FRX")
    env.setdefault("LV", "-c")
    return env


class _PagerStdout(io.TextIOBase):
    """Text stream proxy that preserves tty detection while writing to a pager."""

    def __init__(self, stream: io.TextIOBase, original_stdout: io.TextIOBase) -> None:
        self._stream = stream
        self._original_stdout = original_stdout
        self._broken_pipe = False

    def write(self, text: str) -> int:
        if self._broken_pipe:
            return len(text)

        try:
            written = self._stream.write(text)
            return len(text) if written is None else written
        except BrokenPipeError:
            self._broken_pipe = True
            return len(text)

    def flush(self) -> None:
        if self._broken_pipe:
            return

        try:
            self._stream.flush()
        except BrokenPipeError:
            self._broken_pipe = True

    def writable(self) -> bool:
        return True

    def close(self) -> None:
        if self._stream.closed:
            return

        try:
            self._stream.close()
        except BrokenPipeError:
            self._broken_pipe = True

    def isatty(self) -> bool:
        return self._original_stdout.isatty()

    @property
    def encoding(self) -> str | None:
        return getattr(self._original_stdout, "encoding", None)

    @property
    def errors(self) -> str | None:
        return getattr(self._original_stdout, "errors", None)

    def fileno(self) -> int:
        return self._original_stdout.fileno()


@contextmanager
def pager_output() -> Iterator[None]:
    """Route stdout through the configured Git pager when one is available."""
    pager = _resolve_git_pager()
    if pager is None:
        yield
        return

    read_fd, write_fd = os.pipe()
    original_stdout = sys.stdout
    pager_process = None

    try:
        pager_process = start_command(
            ["sh", "-c", pager],
            stdin_fd=read_fd,
            env=_build_pager_environment(),
            capture_stdout=False,
            capture_stderr=False,
        )
    except OSError:
        os.close(read_fd)
        os.close(write_fd)
        yield
        return

    writer = io.TextIOWrapper(
        os.fdopen(write_fd, "wb", closefd=True),
        encoding=getattr(original_stdout, "encoding", None) or "utf-8",
        errors=getattr(original_stdout, "errors", None) or "strict",
        write_through=True,
    )
    proxy = _PagerStdout(writer, original_stdout)
    sys.stdout = proxy
    try:
        yield
    finally:
        try:
            proxy.flush()
        finally:
            sys.stdout = original_stdout
            proxy.close()
            if pager_process is not None:
                pager_process.wait()
