"""Tests for low-overhead, privacy-conscious journal logging."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from git_stage_batch.commands.start import command_start
from git_stage_batch.utils import journal
from git_stage_batch.utils.journal import (
    JOURNAL_LEVEL_ENV,
    JOURNAL_MAX_BYTES_ENV,
    JOURNAL_PATH_ENV,
    JOURNAL_RETENTION_DAYS_ENV,
    JournalLevel,
    flush_journal,
    get_journal_path,
    journal_enabled,
    log_journal,
)


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary Git repository with an isolated journal."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
    (tmp_path / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True)
    subprocess.run(["git", "commit", "-m", "initial"], check=True, capture_output=True)
    monkeypatch.setenv(JOURNAL_PATH_ENV, str(tmp_path / "private" / "journal.jsonl"))
    journal._reset_journal_state_for_tests()
    yield tmp_path
    journal._reset_journal_state_for_tests()


def _entries(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_journal_is_disabled_without_opt_in(temp_git_repo, monkeypatch):
    path = get_journal_path()
    monkeypatch.delenv(JOURNAL_LEVEL_ENV, raising=False)
    monkeypatch.delenv("GIT_STAGE_BATCH_DEBUG", raising=False)
    monkeypatch.setattr(
        journal.sys,
        "_getframe",
        lambda _depth: pytest.fail("inspected frame"),
    )

    for _ in range(1000):
        log_journal("disabled_event", path="secret.txt")
    flush_journal()

    assert not path.exists()
    assert not path.parent.exists()


def test_metadata_journal_redacts_paths_content_and_command_output(
    temp_git_repo,
    monkeypatch,
):
    monkeypatch.setenv(JOURNAL_LEVEL_ENV, "metadata-only")
    path = get_journal_path()

    log_journal(
        "metadata_event",
        file_path="private/customer.txt",
        buffer_preview=b"account-token",
        stderr="password=secret",
        object_id="abc123",
        content_len=13,
    )
    assert not path.exists(), "small entries should remain buffered until a boundary"
    flush_journal()

    entry = _entries(path)[0]
    assert entry["level"] == "metadata-only"
    assert entry["source"].endswith(
        ":test_metadata_journal_redacts_paths_content_and_command_output"
    )
    assert entry["fields"]["file_path"]["path_id"]
    assert entry["fields"]["buffer_preview"] == {"redacted": True, "byte_count": 13}
    assert entry["fields"]["stderr"] == {"redacted": True, "byte_count": 15}
    assert entry["fields"]["object_id"] == "abc123"
    serialized = path.read_text()
    assert "customer.txt" not in serialized
    assert "account-token" not in serialized
    assert "password" not in serialized
    assert "stack" not in entry


def test_metadata_start_workflow_does_not_record_repository_content(
    temp_git_repo,
    monkeypatch,
):
    monkeypatch.setenv(JOURNAL_LEVEL_ENV, "metadata-only")
    secret_path = temp_git_repo / "customer-secret.txt"
    secret_path.write_text("API_KEY=do-not-record\n")

    command_start()
    flush_journal()

    serialized = get_journal_path().read_text()
    assert "customer-secret.txt" not in serialized
    assert "do-not-record" not in serialized


def test_verbose_adds_bounded_stack_without_exposing_content(temp_git_repo, monkeypatch):
    monkeypatch.setenv(JOURNAL_LEVEL_ENV, "verbose")

    log_journal("verbose_event", path="private.txt", index_preview=b"secret")
    flush_journal()

    entry = _entries(get_journal_path())[0]
    assert 0 < len(entry["stack"]) <= 6
    assert entry["fields"]["index_preview"]["redacted"] is True
    assert "private.txt" not in get_journal_path().read_text()


def test_error_event_adds_stack_at_metadata_level(temp_git_repo, monkeypatch):
    monkeypatch.setenv(JOURNAL_LEVEL_ENV, "metadata-only")

    log_journal("operation_failed", reason="test")
    flush_journal()

    assert _entries(get_journal_path())[0]["stack"]


def test_content_debug_is_a_separate_raw_content_opt_in(temp_git_repo, monkeypatch):
    monkeypatch.setenv(JOURNAL_LEVEL_ENV, "content-debug")

    log_journal(
        "content_event",
        file_path="private/customer.txt",
        buffer_preview=b"\x00secret",
    )
    flush_journal()

    entry = _entries(get_journal_path())[0]
    assert entry["fields"]["file_path"] == "private/customer.txt"
    assert entry["fields"]["buffer_preview"] == {
        "encoding": "base64",
        "data": "AHNlY3JldA==",
    }


def test_content_debug_bounds_individual_content_fields(temp_git_repo, monkeypatch):
    monkeypatch.setenv(JOURNAL_LEVEL_ENV, "content-debug")

    log_journal("large_output", stderr="x" * 10_000)
    flush_journal()

    stderr = _entries(get_journal_path())[0]["fields"]["stderr"]
    assert stderr["truncated"] is True
    assert stderr["original_byte_count"] == 10_000
    assert len(stderr["text"].encode()) == journal.MAX_CONTENT_FIELD_BYTES


def test_journal_uses_private_permissions(temp_git_repo, monkeypatch):
    monkeypatch.setenv(JOURNAL_LEVEL_ENV, "metadata-only")
    old_umask = os.umask(0)
    try:
        log_journal("permissions")
        flush_journal()
    finally:
        os.umask(old_umask)

    path = get_journal_path()
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_rotation_bounds_journal_files(temp_git_repo, monkeypatch):
    monkeypatch.setenv(JOURNAL_LEVEL_ENV, "metadata-only")
    monkeypatch.setenv(JOURNAL_MAX_BYTES_ENV, "300")

    for number in range(8):
        log_journal("rotation_event", number=number, padding="x" * 100)
        flush_journal()

    path = get_journal_path()
    files = [
        candidate
        for candidate in path.parent.iterdir()
        if ".jsonl" in candidate.name and not candidate.name.endswith(".lock")
    ]
    assert len(files) <= 4
    assert path.exists()


def test_expired_rotated_journals_are_removed(temp_git_repo, monkeypatch):
    monkeypatch.setenv(JOURNAL_LEVEL_ENV, "metadata-only")
    monkeypatch.setenv(JOURNAL_RETENTION_DAYS_ENV, "1")
    path = get_journal_path()
    path.parent.mkdir()
    expired = path.with_name(f"{path.name}.1")
    expired.write_text('{"operation":"expired"}\n')
    old_time = time.time() - 2 * 24 * 60 * 60
    os.utime(expired, (old_time, old_time))

    log_journal("current")
    flush_journal()

    assert not expired.exists()
    assert path.exists()


def test_default_path_is_private_per_user_and_uses_repository_id(
    temp_git_repo,
    monkeypatch,
):
    state_home = temp_git_repo / "user-state"
    monkeypatch.delenv(JOURNAL_PATH_ENV)
    monkeypatch.delenv(journal.GLOBAL_JOURNAL_PATH_ENV, raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))

    path = get_journal_path()

    assert path.parent == state_home / "git-stage-batch" / "journals"
    assert path.suffix == ".jsonl"
    assert len(path.stem) == 24
    assert str(temp_git_repo) not in path.name


def test_legacy_debug_switch_selects_verbose_not_content(temp_git_repo, monkeypatch):
    monkeypatch.delenv(JOURNAL_LEVEL_ENV, raising=False)
    monkeypatch.setenv("GIT_STAGE_BATCH_DEBUG", "1")

    assert journal_enabled(JournalLevel.VERBOSE)
    assert not journal_enabled(JournalLevel.CONTENT_DEBUG)


def test_journal_logging_failure_never_breaks_operation(temp_git_repo, monkeypatch):
    monkeypatch.setenv(JOURNAL_LEVEL_ENV, "metadata-only")
    path = get_journal_path()
    path.parent.mkdir()
    path.mkdir()

    log_journal("unwritable_destination")
    flush_journal()


def test_concurrent_processes_write_complete_json_lines(temp_git_repo, monkeypatch):
    monkeypatch.setenv(JOURNAL_LEVEL_ENV, "metadata-only")
    path = get_journal_path()
    environment = os.environ.copy()
    code = (
        "from git_stage_batch.utils.journal import log_journal, flush_journal; "
        "[log_journal('child', number=n) for n in range(20)]; flush_journal()"
    )

    processes = [
        subprocess.Popen([sys.executable, "-c", code], env=environment)
        for _ in range(4)
    ]
    assert all(process.wait() == 0 for process in processes)

    entries = _entries(path)
    assert len(entries) == 80
    assert all(entry["operation"] == "child" for entry in entries)


def test_abrupt_exit_may_lose_events_since_last_boundary(temp_git_repo, monkeypatch):
    """Journal buffering is not part of repository-state crash durability."""
    monkeypatch.setenv(JOURNAL_LEVEL_ENV, "metadata-only")
    path = get_journal_path()
    environment = os.environ.copy()
    code = (
        "import os; "
        "from git_stage_batch.utils.journal import log_journal; "
        "log_journal('not_flushed'); os._exit(0)"
    )

    result = subprocess.run([sys.executable, "-c", code], env=environment)

    assert result.returncode == 0
    assert not path.exists()
