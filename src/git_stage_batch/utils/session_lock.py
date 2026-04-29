"""Repository-scoped advisory lock for git-stage-batch session state."""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path

from .paths import ensure_state_directory_exists, get_session_lock_file_path

_LOCK_DEPTH = 0
_LOCK_HANDLE = None


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
