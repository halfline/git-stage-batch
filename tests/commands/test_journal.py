"""Tests for diagnostic journal management."""

from __future__ import annotations

import json
import subprocess

import pytest

from git_stage_batch.commands.journal import command_journal
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils import journal
from git_stage_batch.utils.journal import JOURNAL_LEVEL_ENV, JOURNAL_PATH_ENV, flush_journal, log_journal


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    monkeypatch.setenv(JOURNAL_PATH_ENV, str(tmp_path / "state" / "journal.jsonl"))
    monkeypatch.setenv(JOURNAL_LEVEL_ENV, "metadata-only")
    journal._reset_journal_state_for_tests()
    yield tmp_path
    journal._reset_journal_state_for_tests()


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
