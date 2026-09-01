"""Tests for the shell-prompt status cache."""

from __future__ import annotations

import json
import stat
import subprocess

import pytest

import git_stage_batch.data.status_summary_cache as status_summary_cache

from git_stage_batch.data.status_summary_cache import (
    mark_prompt_status_cache_requested,
    prompt_status_cache_requested,
    read_cached_prompt_status,
    read_session_marker_identity,
    write_cached_prompt_status,
)
from git_stage_batch.data.status_types import PromptStatusSummary
from git_stage_batch.utils.paths import (
    get_abort_head_file_path,
    get_session_directory_path,
    get_status_summary_cache_file_path,
)


@pytest.fixture
def status_cache_repo(tmp_path, monkeypatch):
    """Create a repository with only the active marker needed by the cache."""
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init"], cwd=repository, check=True, capture_output=True)
    monkeypatch.chdir(repository)
    marker = get_abort_head_file_path()
    marker.parent.mkdir(parents=True)
    marker.write_text("head\n", encoding="utf-8")
    return repository


def _summary() -> PromptStatusSummary:
    return {
        "session": {
            "active": True,
            "iteration": 4,
            "status": "in_progress",
            "in_progress": True,
        },
        "selected_change": {
            "kind": "hunk",
            "file": "README.md",
            "line": 7,
            "ids": [2, 3],
        },
        "file_review": None,
        "progress": {
            "included": 3,
            "skipped": 2,
            "discarded": 1,
            "remaining": 5,
        },
    }


def test_status_summary_cache_round_trips_small_prompt_snapshot(status_cache_repo):
    """The cache should retain prompt fields without full status-only lists."""
    marker = read_session_marker_identity()
    assert marker is not None

    assert write_cached_prompt_status(
        _summary(),
        lock_generation=12,
        exact=True,
        session_marker=marker,
    )

    cached = read_cached_prompt_status(session_marker=marker)
    assert cached is not None
    assert cached.summary == _summary()
    assert cached.lock_generation == 12
    assert cached.exact is True
    payload = json.loads(get_status_summary_cache_file_path().read_text())
    assert "skipped_hunks" not in payload["summary"]
    assert stat.S_IMODE(get_status_summary_cache_file_path().stat().st_mode) == 0o600


def test_status_summary_cache_rejects_previous_session_marker(status_cache_repo):
    """A replacement active marker must not inherit the prior session summary."""
    old_marker = read_session_marker_identity()
    assert old_marker is not None
    assert write_cached_prompt_status(
        _summary(),
        lock_generation=3,
        exact=True,
        session_marker=old_marker,
    )

    marker_path = get_abort_head_file_path()
    marker_path.unlink()
    marker_path.write_text("new session\n", encoding="utf-8")

    assert read_cached_prompt_status() is None


def test_status_summary_cache_rejects_malformed_values(status_cache_repo):
    """A corrupt optimization record should behave like a cache miss."""
    cache_path = get_status_summary_cache_file_path()
    cache_path.write_text('{"schema_version":1,"exact":"yes"}', encoding="utf-8")

    assert read_cached_prompt_status() is None


def test_status_summary_cache_does_not_publish_oversized_values(
    status_cache_repo,
    monkeypatch,
):
    """The writer should use the same fixed size limit as the reader."""
    marker = read_session_marker_identity()
    assert marker is not None
    monkeypatch.setattr(status_summary_cache, "_MAXIMUM_CACHE_BYTES", 32)

    assert not write_cached_prompt_status(
        _summary(),
        lock_generation=1,
        exact=True,
        session_marker=marker,
    )
    assert not get_status_summary_cache_file_path().exists()


def test_status_summary_cache_does_not_recreate_removed_session(status_cache_repo):
    """A late refresh must not resurrect a session removed by stop."""
    marker = read_session_marker_identity()
    assert marker is not None
    session_directory = get_session_directory_path()
    for path in sorted(session_directory.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        else:
            path.rmdir()
    session_directory.rmdir()

    assert not write_cached_prompt_status(
        _summary(),
        lock_generation=4,
        exact=True,
        session_marker=marker,
    )
    assert not session_directory.exists()


def test_prompt_marker_is_private_and_idempotent(status_cache_repo):
    """Rich prompt use should enable later refreshes once per session."""
    assert not prompt_status_cache_requested()
    assert mark_prompt_status_cache_requested()
    assert mark_prompt_status_cache_requested()
    assert prompt_status_cache_requested()
