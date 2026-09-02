"""Prompt cache reads and background refreshes."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import os
from pathlib import Path
import stat
import subprocess
import sys

from ..data.session_marker import session_is_active
from ..data.status_summary import (
    read_prompt_status_cache_seed,
    read_prompt_status_summary,
)
from ..data.status_summary_cache import (
    CachedPromptStatus,
    mark_prompt_status_cache_requested,
    prompt_status_cache_requested,
    read_cached_prompt_status,
    read_session_marker_identity,
    write_cached_prompt_status,
)
from ..data.status_types import PromptStatusSummary
from ..exceptions import CommandError
from ..utils.paths import (
    ensure_common_state_directory_exists,
    get_status_summary_refresh_lock_file_path,
)
from ..utils.session_lock import (
    current_session_lock_generation,
    read_session_lock_generation_if_available,
)


@dataclass(frozen=True, slots=True)
class PromptStatusSnapshot:
    """Prompt values plus whether an exact refresh still needs starting."""

    summary: PromptStatusSummary | None
    needs_refresh: bool


def read_prompt_status_from_cache(
    git_dir: Path,
) -> PromptStatusSnapshot:
    """Return a cached prompt snapshot, seeding one without a full scan."""
    session_marker = read_session_marker_identity(git_dir)
    if session_marker is None:
        return PromptStatusSnapshot(None, False)
    if not mark_prompt_status_cache_requested(git_dir):
        return PromptStatusSnapshot(None, False)

    cached = read_cached_prompt_status(
        git_dir,
        session_marker=session_marker,
    )
    if cached is not None:
        current_generation = read_session_lock_generation_if_available()
        needs_refresh = (
            not cached.exact
            or current_generation != cached.lock_generation
        )
        return PromptStatusSnapshot(cached.summary, needs_refresh)

    generation_before = read_session_lock_generation_if_available()
    if generation_before is None:
        return PromptStatusSnapshot(None, False)
    summary = read_prompt_status_cache_seed()
    generation_after = read_session_lock_generation_if_available()
    marker_after = read_session_marker_identity(git_dir)
    if generation_after != generation_before or marker_after != session_marker:
        return PromptStatusSnapshot(None, False)
    write_cached_prompt_status(
        summary,
        lock_generation=generation_after,
        exact=False,
        session_marker=session_marker,
        git_dir=git_dir,
    )
    return PromptStatusSnapshot(summary, True)


def request_status_summary_cache_refresh() -> None:
    """Start one detached refresh when an active prompt cache is stale."""
    try:
        if not session_is_active() or not prompt_status_cache_requested():
            return
        session_marker = read_session_marker_identity()
        generation = read_session_lock_generation_if_available()
        if session_marker is None or generation is None:
            return
        cached = read_cached_prompt_status(session_marker=session_marker)
        if _cache_is_current(cached, generation):
            return
        if _status_refresh_is_running():
            return
        _spawn_status_refresh(Path.cwd())
    except (OSError, subprocess.SubprocessError):
        # Prompt caching is an optimization and cannot change command success.
        return


def refresh_status_summary_cache() -> None:
    """Publish an exact prompt snapshot if session state stays unchanged."""
    with _acquire_status_refresh_lock() as acquired:
        if not acquired:
            return
        if not session_is_active() or not prompt_status_cache_requested():
            return
        session_marker = read_session_marker_identity()
        generation_before = read_session_lock_generation_if_available()
        if session_marker is None or generation_before is None:
            return

        try:
            summary = read_prompt_status_summary()
        except (CommandError, OSError, subprocess.SubprocessError):
            return

        generation_after = read_session_lock_generation_if_available()
        marker_after = read_session_marker_identity()
        if generation_after != generation_before or marker_after != session_marker:
            return
        write_cached_prompt_status(
            summary,
            lock_generation=generation_after,
            exact=True,
            session_marker=session_marker,
        )


def cache_exact_prompt_status_if_requested(
    summary: PromptStatusSummary,
) -> None:
    """Keep a regular status result for later prompt reads."""
    if not prompt_status_cache_requested():
        return
    session_marker = read_session_marker_identity()
    generation = current_session_lock_generation()
    if generation is None:
        generation = read_session_lock_generation_if_available()
    if session_marker is None or generation is None:
        return
    write_cached_prompt_status(
        summary,
        lock_generation=generation,
        exact=True,
        session_marker=session_marker,
    )


def _cache_is_current(
    cached: CachedPromptStatus | None,
    generation: int,
) -> bool:
    return (
        cached is not None
        and cached.exact
        and cached.lock_generation == generation
    )


@contextmanager
def _acquire_status_refresh_lock() -> Iterator[bool]:
    """Try to own the cache worker slot without waiting."""
    ensure_common_state_directory_exists()
    path = get_status_summary_refresh_lock_file_path()
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            yield False
            return
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _status_refresh_is_running() -> bool:
    with _acquire_status_refresh_lock() as acquired:
        return not acquired


def _spawn_status_refresh(working_directory: Path) -> None:
    arguments = [
        sys.executable,
        "-B",
        "-m",
        "git_stage_batch.cli.main",
        "-C",
        str(working_directory),
        "status",
        "--refresh-cache",
    ]
    null_descriptor = os.open(
        os.devnull,
        os.O_RDWR | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        file_actions: list[tuple[int, int] | tuple[int, int, int]] = [
            (os.POSIX_SPAWN_DUP2, null_descriptor, standard_descriptor)
            for standard_descriptor in range(3)
        ]
        if null_descriptor >= 3:
            file_actions.append((os.POSIX_SPAWN_CLOSE, null_descriptor))
        os.posix_spawn(
            sys.executable,
            arguments,
            os.environ.copy(),
            file_actions=file_actions,
        )
    finally:
        os.close(null_descriptor)
