"""Repository-scoped advisory lock for git-stage-batch session state."""

from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path

from .paths import ensure_common_state_directory_exists, get_session_lock_file_path

_LOCK_DEPTH = 0
_LOCK_HANDLE = None
_LOCK_GENERATION: int | None = None


class SessionLockChangedDuringPrompt(RuntimeError):
    """Another process acquired the session lock while a prompt was open."""


def _read_lock_generation(lock_handle) -> int:
    """Return the generation stored in an acquired lock file."""
    lock_handle.seek(0)
    try:
        return int(lock_handle.read().strip() or "0")
    except ValueError:
        return 0


def _advance_lock_generation(lock_handle) -> int:
    """Advance and durably publish the acquired lock generation."""
    generation = _read_lock_generation(lock_handle) + 1
    lock_handle.seek(0)
    lock_handle.truncate()
    lock_handle.write(f"{generation}\n")
    lock_handle.flush()
    os.fsync(lock_handle.fileno())
    return generation


@contextmanager
def acquire_session_lock():
    """Hold an advisory lock covering the shared session-state directory.

    The lock is process-reentrant so nested dispatches in interactive mode do
    not deadlock. Because it uses `flock`, the kernel releases the lock
    automatically if the process exits or crashes.
    """
    global _LOCK_DEPTH, _LOCK_GENERATION, _LOCK_HANDLE

    if _LOCK_DEPTH > 0:
        _LOCK_DEPTH += 1
        try:
            yield
        finally:
            _LOCK_DEPTH -= 1
        return

    ensure_common_state_directory_exists()
    lock_path = get_session_lock_file_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = Path(lock_path).open("a+", encoding="utf-8")

    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        _LOCK_GENERATION = _advance_lock_generation(lock_handle)
        _LOCK_HANDLE = lock_handle
        _LOCK_DEPTH = 1
        yield
    finally:
        _LOCK_DEPTH = 0
        _LOCK_GENERATION = None
        _LOCK_HANDLE = None
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            lock_handle.close()


@contextmanager
def temporarily_release_session_lock():
    """Release an acquired session lock while waiting for user input.

    Interactive actions run under the repository lock, but prompts must not
    prevent another process from making progress.  Restore the full reentrant
    depth before returning so the action resumes with the same lock contract.
    """
    global _LOCK_DEPTH, _LOCK_GENERATION, _LOCK_HANDLE

    if _LOCK_DEPTH == 0 or _LOCK_HANDLE is None:
        yield
        return

    lock_handle = _LOCK_HANDLE
    lock_depth = _LOCK_DEPTH
    lock_generation = _LOCK_GENERATION
    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    _LOCK_DEPTH = 0
    _LOCK_GENERATION = None
    _LOCK_HANDLE = None
    prompt_raised = False
    try:
        yield
    except BaseException:
        prompt_raised = True
        raise
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        current_generation = _read_lock_generation(lock_handle)
        _LOCK_HANDLE = lock_handle
        _LOCK_DEPTH = lock_depth
        _LOCK_GENERATION = current_generation
        if not prompt_raised and current_generation != lock_generation:
            raise SessionLockChangedDuringPrompt
