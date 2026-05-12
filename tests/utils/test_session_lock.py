"""Tests for repository session lock helpers."""

import subprocess

from git_stage_batch.utils import session_lock


def test_wait_for_git_index_lock_returns_when_lock_absent(tmp_path, monkeypatch):
    """Absent index locks should not delay startup."""
    sleep_calls = []

    monkeypatch.setattr(session_lock, "get_git_directory_path", lambda: tmp_path)
    monkeypatch.setattr(
        session_lock.time,
        "sleep",
        lambda duration: sleep_calls.append(duration),
    )

    session_lock.wait_for_git_index_lock()

    assert sleep_calls == []


def test_wait_for_git_index_lock_polls_until_lock_disappears(tmp_path, monkeypatch):
    """A transient index lock should delay startup until it disappears."""
    index_lock = tmp_path / "index.lock"
    index_lock.write_text("")
    sleep_calls = []

    def fake_sleep(duration):
        sleep_calls.append(duration)
        index_lock.unlink()

    monkeypatch.setattr(session_lock, "get_git_directory_path", lambda: tmp_path)
    monkeypatch.setattr(session_lock.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(session_lock.time, "sleep", fake_sleep)

    session_lock.wait_for_git_index_lock(
        timeout_seconds=1.0,
        poll_seconds=0.05,
    )

    assert sleep_calls == [0.05]


def test_wait_for_git_index_lock_stops_after_timeout(tmp_path, monkeypatch):
    """Persistent index locks should only block startup for the timeout."""
    index_lock = tmp_path / "index.lock"
    index_lock.write_text("")
    times = iter([0.0, 0.0, 0.2])
    sleep_calls = []

    monkeypatch.setattr(session_lock, "get_git_directory_path", lambda: tmp_path)
    monkeypatch.setattr(session_lock.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(
        session_lock.time,
        "sleep",
        lambda duration: sleep_calls.append(duration),
    )

    session_lock.wait_for_git_index_lock(
        timeout_seconds=0.1,
        poll_seconds=0.05,
    )

    assert index_lock.exists()
    assert sleep_calls == [0.05]


def test_wait_for_git_index_lock_ignores_non_repository(monkeypatch):
    """Startup lock waiting should defer non-repository errors to commands."""
    sleep_calls = []

    def fail_git_directory():
        raise subprocess.CalledProcessError(128, ["git", "rev-parse"])

    monkeypatch.setattr(session_lock, "get_git_directory_path", fail_git_directory)
    monkeypatch.setattr(
        session_lock.time,
        "sleep",
        lambda duration: sleep_calls.append(duration),
    )

    session_lock.wait_for_git_index_lock()

    assert sleep_calls == []
