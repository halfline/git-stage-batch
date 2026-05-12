"""Repository-scoped advisory lock for git-stage-batch session state."""

from __future__ import annotations

import fcntl
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from .git import get_git_directory_path
from .paths import ensure_state_directory_exists, get_session_lock_file_path

_LOCK_DEPTH = 0
_LOCK_HANDLE = None
_INDEX_LOCK_WAIT_SECONDS = 2.0
_INDEX_LOCK_POLL_SECONDS = 0.05


def wait_for_git_index_lock(
    *,
    timeout_seconds: float = _INDEX_LOCK_WAIT_SECONDS,
    poll_seconds: float = _INDEX_LOCK_POLL_SECONDS,
) -> None:
    """Wait briefly for a pre-existing Git index lock to disappear."""
    try:
        index_lock_path = get_git_directory_path() / "index.lock"
    except subprocess.CalledProcessError:
        return

    deadline = time.monotonic() + timeout_seconds
    while index_lock_path.exists():
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            return
        time.sleep(min(poll_seconds, remaining_seconds))


@contextmanager
def acquire_session_lock():
    """Hold an advisory lock covering the shared session-state directory.

    The lock is process-reentrant so nested dispatches in interactive mode do
    not deadlock. Because it uses `flock`, the kernel releases the lock
    automatically if the process exits or crashes.
    """
    global _LOCK_DEPTH, _LOCK_HANDLE

    if _LOCK_DEPTH > 0:
        _LOCK_DEPTH += 1
        try:
            yield
        finally:
            _LOCK_DEPTH -= 1
        return

    ensure_state_directory_exists()
    lock_path = get_session_lock_file_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = Path(lock_path).open("a+", encoding="utf-8")

    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        _LOCK_HANDLE = lock_handle
        _LOCK_DEPTH = 1
        yield
    finally:
        _LOCK_DEPTH = 0
        _LOCK_HANDLE = None
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            lock_handle.close()
