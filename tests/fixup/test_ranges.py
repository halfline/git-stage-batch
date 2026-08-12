"""Tests for canonical fixup target ranges."""

from __future__ import annotations

import subprocess

import pytest

from git_stage_batch.exceptions import CommandError
from git_stage_batch.fixup.ranges import resolve_fixup_range


def _git(*arguments: str, check: bool = True) -> str:
    return subprocess.run(
        ["git", *arguments],
        check=check,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def range_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _git("init")
    _git("config", "user.name", "Test User")
    _git("config", "user.email", "test@example.com")
    source = tmp_path / "file.txt"
    source.write_text("base\n")
    _git("add", "file.txt")
    _git("commit", "-m", "Base")
    base = _git("rev-parse", "HEAD")
    return tmp_path, source, base


def test_explicit_range_is_canonical_and_newest_first(range_repo):
    _repo, source, base = range_repo
    source.write_text("one\n")
    _git("commit", "-am", "One")
    first = _git("rev-parse", "HEAD")
    source.write_text("two\n")
    _git("commit", "-am", "Two")
    second = _git("rev-parse", "HEAD")

    commit_range = resolve_fixup_range(base[:12])

    assert commit_range.base_commit == base
    assert commit_range.head_commit == second
    assert commit_range.commits_newest_first == (second, first)


def test_default_range_uses_upstream_fork_point(range_repo):
    _repo, source, base = range_repo
    branch = _git("branch", "--show-current")
    _git("branch", "upstream", base)
    _git("branch", "--set-upstream-to=upstream", branch)
    source.write_text("topic\n")
    _git("commit", "-am", "Topic")

    commit_range = resolve_fixup_range(None)

    assert commit_range.base_commit == base
    assert commit_range.head_commit == _git("rev-parse", "HEAD")


def test_range_rejects_merge_commits(range_repo):
    _repo, source, base = range_repo
    _git("checkout", "-b", "side")
    (source.parent / "side.txt").write_text("side\n")
    _git("add", "side.txt")
    _git("commit", "-m", "Side")
    _git("checkout", "-")
    source.write_text("main\n")
    _git("commit", "-am", "Main")
    _git("merge", "--no-ff", "side", "-m", "Merge side")

    with pytest.raises(CommandError, match="linear range"):
        resolve_fixup_range(base)


def test_range_rejects_nonancestor_base(range_repo):
    _repo, source, base = range_repo
    _git("checkout", "-b", "side")
    source.write_text("side\n")
    _git("commit", "-am", "Side")
    side = _git("rev-parse", "HEAD")
    _git("checkout", "-")
    source.write_text("main\n")
    _git("commit", "-am", "Main")

    with pytest.raises(CommandError, match="not an ancestor"):
        resolve_fixup_range(side)

    assert _git("merge-base", base, "HEAD") == base
