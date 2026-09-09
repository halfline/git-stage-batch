"""Tests for prompt cache refresh orchestration."""

from __future__ import annotations

from pathlib import Path

import git_stage_batch.commands.status_cache as status_cache
from git_stage_batch.data.status_summary_cache import (
    mark_prompt_status_cache_requested,
    read_cached_prompt_status,
    read_session_marker_identity,
    write_cached_prompt_status,
)
from git_stage_batch.data.status_types import PromptStatusSummary, StatusSummary
from git_stage_batch.utils.paths import get_status_summary_cache_file_path
from git_stage_batch.utils.session_lock import acquire_session_lock


def _summary(*, remaining: int = 2) -> PromptStatusSummary:
    return {
        "session": {
            "active": True,
            "iteration": 1,
            "status": "in_progress" if remaining else "complete",
            "in_progress": bool(remaining),
        },
        "selected_change": None,
        "file_review": None,
        "progress": {
            "included": 1,
            "skipped": 0,
            "discarded": 0,
            "remaining": remaining,
        },
    }


def _enable_cache() -> None:
    assert mark_prompt_status_cache_requested()
    assert read_session_marker_identity() is not None


def test_refresh_publishes_exact_summary(temp_git_repo_with_session, monkeypatch):
    """A stable background read should replace the provisional snapshot."""
    _enable_cache()
    monkeypatch.setattr(
        status_cache,
        "read_prompt_status_summary",
        lambda: _summary(remaining=6),
    )

    status_cache.refresh_status_summary_cache()

    cached = read_cached_prompt_status()
    assert cached is not None
    assert cached.exact is True
    assert cached.summary["progress"]["remaining"] == 6


def test_refresh_rejects_summary_when_session_generation_changes(
    temp_git_repo_with_session,
    monkeypatch,
):
    """A command overlapping each scan must prevent stale publication."""
    _enable_cache()
    calls = 0

    def changing_summary():
        nonlocal calls
        calls += 1
        with acquire_session_lock():
            pass
        return _summary()

    monkeypatch.setattr(
        status_cache,
        "read_prompt_status_summary",
        changing_summary,
    )

    status_cache.refresh_status_summary_cache()

    assert calls == 1
    assert not get_status_summary_cache_file_path().exists()


def test_request_spawns_refresh_for_provisional_cache(
    temp_git_repo_with_session,
    monkeypatch,
):
    """A first prompt snapshot should request the exact remaining count."""
    _enable_cache()
    marker = read_session_marker_identity()
    assert marker is not None
    assert write_cached_prompt_status(
        _summary(),
        lock_generation=0,
        exact=False,
        session_marker=marker,
    )
    spawned: list[Path] = []
    monkeypatch.setattr(status_cache, "_status_refresh_is_running", lambda: False)
    monkeypatch.setattr(status_cache, "_spawn_status_refresh", spawned.append)

    status_cache.request_status_summary_cache_refresh()

    assert spawned == [Path.cwd()]


def test_request_skips_current_exact_cache(
    temp_git_repo_with_session,
    monkeypatch,
):
    """An unchanged exact snapshot should not start another scan."""
    _enable_cache()
    marker = read_session_marker_identity()
    assert marker is not None
    assert write_cached_prompt_status(
        _summary(),
        lock_generation=0,
        exact=True,
        session_marker=marker,
    )
    def fail_spawn(_path: Path) -> None:
        raise AssertionError("unexpected refresh")

    monkeypatch.setattr(status_cache, "_spawn_status_refresh", fail_spawn)

    status_cache.request_status_summary_cache_refresh()


def test_prompt_requests_refresh_after_completed_command(
    temp_git_repo_with_session,
):
    """The next prompt should notice an exact cache from an older command."""
    _enable_cache()
    marker = read_session_marker_identity()
    assert marker is not None
    assert write_cached_prompt_status(
        _summary(),
        lock_generation=0,
        exact=True,
        session_marker=marker,
    )
    with acquire_session_lock():
        pass

    snapshot = status_cache.read_prompt_status_from_cache(
        temp_git_repo_with_session / ".git"
    )

    assert snapshot.summary == _summary()
    assert snapshot.needs_refresh is True


def test_request_never_waits_for_session_lock(
    temp_git_repo_with_session,
    monkeypatch,
):
    """A foreground lock holder should make cache maintenance defer."""
    _enable_cache()
    spawned: list[Path] = []
    monkeypatch.setattr(status_cache, "_spawn_status_refresh", spawned.append)

    with acquire_session_lock():
        status_cache.request_status_summary_cache_refresh()

    assert spawned == []


def test_exact_cache_accepts_full_status_response(temp_git_repo_with_session):
    """Regular status must publish a readable prompt subset of its response."""
    _enable_cache()
    summary: StatusSummary = {**_summary(), "skipped_hunks": []}

    with acquire_session_lock():
        status_cache.cache_exact_prompt_status_if_requested(summary)

    cached = read_cached_prompt_status()
    assert cached is not None
    assert cached.summary == _summary()
