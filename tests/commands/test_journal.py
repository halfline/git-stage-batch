"""Tests for diagnostic journal management."""

from __future__ import annotations

import json
import subprocess
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from git_stage_batch.commands.journal import command_journal
import git_stage_batch.commands.apply_from as apply_from
from git_stage_batch.commands.selection import discard_line_batching
from git_stage_batch.commands.selection import discard_to_batch_action
from git_stage_batch.data.undo.checkpoints import UndoCheckpointStatus
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.journal import JOURNAL_LEVEL_ENV, JOURNAL_PATH_ENV, flush_journal, log_journal
from tests.journal_helpers import reset_journal_state


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    monkeypatch.setenv(JOURNAL_PATH_ENV, str(tmp_path / "state" / "journal.jsonl"))
    monkeypatch.setenv(JOURNAL_LEVEL_ENV, "metadata-only")
    reset_journal_state()
    yield tmp_path
    reset_journal_state()


def test_journal_command_reports_json_summary(temp_git_repo, capsys):
    log_journal("sample")
    flush_journal()

    command_journal(porcelain=True)

    report = json.loads(capsys.readouterr().out)
    assert report["entry_count"] == 1
    assert report["level"] == "metadata-only"


def test_journal_command_prints_path(temp_git_repo, capsys):
    command_journal(path_only=True)

    assert capsys.readouterr().out.strip().endswith("state/journal.jsonl")


def test_journal_command_purges_data(temp_git_repo, capsys):
    log_journal("sample")
    flush_journal()

    command_journal(purge=True, porcelain=True)

    assert json.loads(capsys.readouterr().out) == {"removed_file_count": 1}


def test_all_requires_purge(temp_git_repo):
    with pytest.raises(CommandError, match="--all"):
        command_journal(all_repositories=True)


def test_apply_success_has_correlated_terminal_journal_event(monkeypatch):
    """Apply start and success events must share an operation identifier."""
    events = []
    context = SimpleNamespace(
        selector=SimpleNamespace(candidate_ordinal=None),
        batch_name="resolved",
    )
    selection = SimpleNamespace(
        file=None,
        files={"file.txt": {}},
        selected_ids=None,
        selection_ids=None,
    )
    monkeypatch.setattr(apply_from, "require_git_repository", lambda: None)
    monkeypatch.setattr(
        apply_from._action_context,
        "resolve_batch_source_action_context",
        lambda *_args, **_kwargs: context,
    )
    monkeypatch.setattr(
        apply_from._action_selection,
        "resolve_apply_action_selection",
        lambda *_args, **_kwargs: selection,
    )

    def apply_action(*, journal_progress, **_kwargs):
        journal_progress("completion", "delegated")

    monkeypatch.setattr(apply_from._apply_action, "execute_apply_action", apply_action)
    monkeypatch.setattr(
        apply_from,
        "log_journal",
        lambda operation, **fields: events.append((operation, fields)),
    )

    apply_from.command_apply_from_batch("saved")

    assert [operation for operation, _fields in events] == [
        "apply_from_batch_start",
        "apply_from_batch_success",
    ]
    assert events[0][1]["operation_id"] == events[1][1]["operation_id"]
    assert events[0][1]["batch_selector"] == "saved"
    assert events[1][1]["batch_selector"] == "saved"
    assert events[1][1]["resolved_batch_name"] == "resolved"
    assert events[1][1]["stage"] == "complete"
    assert events[1][1]["rollback"] == "delegated"


def test_apply_start_redacts_requested_file_path(temp_git_repo, monkeypatch):
    """Metadata-only apply diagnostics must not retain a requested path."""
    requested_path = "secret/customer.txt"
    monkeypatch.setattr(
        apply_from,
        "require_git_repository",
        lambda: (_ for _ in ()).throw(CommandError("stop after start")),
    )

    with pytest.raises(CommandError, match="stop after start"):
        apply_from.command_apply_from_batch(
            "saved",
            file=requested_path,
        )

    flush_journal()
    journal_path = temp_git_repo / "state" / "journal.jsonl"
    serialized = journal_path.read_text()
    entries = [json.loads(line) for line in serialized.splitlines()]

    requested_file = entries[0]["fields"]["requested_file_path"]
    assert requested_file["path_id"]
    assert requested_path not in serialized


def test_apply_failure_logs_stage_and_rollback_outcome(monkeypatch):
    """Apply failures must report where they stopped and whether rollback ran."""
    events = []
    context = SimpleNamespace(
        selector=SimpleNamespace(candidate_ordinal=None),
        batch_name="resolved",
    )
    selection = SimpleNamespace(
        file=None,
        files={"file.txt": {}},
        selected_ids=None,
        selection_ids=None,
    )
    monkeypatch.setattr(apply_from, "require_git_repository", lambda: None)
    monkeypatch.setattr(
        apply_from._action_context,
        "resolve_batch_source_action_context",
        lambda *_args, **_kwargs: context,
    )
    monkeypatch.setattr(
        apply_from._action_selection,
        "resolve_apply_action_selection",
        lambda *_args, **_kwargs: selection,
    )

    def fail_apply(*, journal_progress, **_kwargs):
        journal_progress("publication", "completed")
        raise CommandError("injected failure")

    monkeypatch.setattr(apply_from._apply_action, "execute_apply_action", fail_apply)
    monkeypatch.setattr(
        apply_from,
        "log_journal",
        lambda operation, **fields: events.append((operation, fields)),
    )

    with pytest.raises(CommandError, match="injected failure"):
        apply_from.command_apply_from_batch("saved")

    assert [operation for operation, _fields in events] == [
        "apply_from_batch_start",
        "apply_from_batch_failed",
    ]
    assert events[0][1]["operation_id"] == events[1][1]["operation_id"]
    assert events[1][1]["batch_selector"] == "saved"
    assert events[1][1]["resolved_batch_name"] == "resolved"
    assert events[1][1]["stage"] == "publication"
    assert events[1][1]["rollback"] == "completed"
    assert events[1][1]["error_type"] == "CommandError"


def test_discard_line_failure_has_terminal_journal_event(
    temp_git_repo,
    monkeypatch,
):
    """A failed line discard must not leave an unmatched start event."""
    monkeypatch.setattr(
        discard_line_batching,
        "require_selected_hunk",
        lambda: (_ for _ in ()).throw(RuntimeError("selection failed")),
    )

    with pytest.raises(RuntimeError, match="selection failed"):
        discard_line_batching.discard_selected_lines_to_batch(
            "saved",
            "1",
            quiet=True,
        )

    flush_journal()
    journal_path = temp_git_repo / "state" / "journal.jsonl"
    entries = [json.loads(line) for line in journal_path.read_text().splitlines()]
    operations = [entry["operation"] for entry in entries]

    assert operations == [
        "discard_lines_to_batch_start",
        "discard_lines_to_batch_failed",
    ]
    assert entries[-1]["fields"]["stage"] == "selection"
    assert entries[-1]["fields"]["rollback"] == "not-started"


def test_discard_line_terminal_event_waits_for_transaction_rollback(
    monkeypatch,
):
    """Command-level failure logging must observe the completed rollback."""
    events = []

    @contextmanager
    def transactional_checkpoint(*_args, **_kwargs):
        status = UndoCheckpointStatus(rollback="pending")
        try:
            yield status
        except BaseException:
            status.rollback = "completed"
            raise

    def fail_discard(*_args, journal_state, **_kwargs):
        journal_state.stage = "worktree-publication"
        raise OSError("injected write failure")

    monkeypatch.setattr(
        discard_to_batch_action,
        "validate_batch_name",
        lambda _name: None,
    )
    monkeypatch.setattr(
        discard_to_batch_action,
        "load_selected_change",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(
        discard_to_batch_action,
        "checkpoint_paths_for_file_scope",
        lambda *_args: [],
    )
    monkeypatch.setattr(
        discard_to_batch_action,
        "undo_checkpoint",
        transactional_checkpoint,
    )
    monkeypatch.setattr(
        discard_line_batching,
        "discard_selected_lines_to_batch",
        fail_discard,
    )
    monkeypatch.setattr(
        discard_line_batching,
        "log_journal",
        lambda operation, **fields: events.append((operation, fields)),
    )

    with pytest.raises(OSError, match="injected write failure"):
        discard_to_batch_action.execute_discard_to_batch_action(
            batch_name="saved",
            line_ids="1",
            file=None,
            original_file_scope=None,
            review_state=None,
            quiet=True,
            advance=True,
            auto_advance=None,
        )

    assert [operation for operation, _fields in events] == [
        "discard_lines_to_batch_start",
        "discard_lines_to_batch_failed",
    ]
    assert events[1][1]["stage"] == "worktree-publication"
    assert events[1][1]["rollback"] == "completed"
