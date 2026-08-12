"""Tests for staged fixup attribution and tree commutation."""

from __future__ import annotations

import subprocess

import pytest

from git_stage_batch.fixup.planning import acquire_fixup_create_plan


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def linear_fixup_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _git("init")
    _git("config", "user.name", "Test User")
    _git("config", "user.email", "test@example.com")

    source = tmp_path / "example.txt"
    source.write_text("alpha\nbeta\ngamma\n")
    _git("add", "example.txt")
    _git("commit", "-m", "Base")
    base = _git("rev-parse", "HEAD")

    source.write_text("alpha\nbeta topic\ngamma\n")
    _git("add", "example.txt")
    _git("commit", "-m", "Change beta")
    beta_commit = _git("rev-parse", "HEAD")

    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("later\n")
    _git("add", "unrelated.txt")
    _git("commit", "-m", "Add unrelated file")

    return tmp_path, source, base, beta_commit


def test_plan_agrees_on_modified_line_target(linear_fixup_repo):
    _repo, source, base, beta_commit = linear_fixup_repo
    source.write_text("alpha\nbeta fixed\ngamma\n")
    _git("add", "example.txt")

    with acquire_fixup_create_plan(base) as plan:
        assert len(plan.units) == 1
        analysis = plan.units[0]
        assert analysis.status == "agreed"
        assert analysis.target == beta_commit
        assert analysis.eligible is True
        assert analysis.lineage.candidates == (beta_commit,)
        assert analysis.placement.barrier == beta_commit
        assert analysis.placement.commuted_across == (
            plan.commit_range.commits_newest_first[0],
        )


def test_plan_neutralizes_repository_blame_ignore_configuration(
    linear_fixup_repo,
):
    _repo, source, base, beta_commit = linear_fixup_repo
    _git("config", "blame.ignoreRevsFile", "missing-ignore-revs-file")
    source.write_text("alpha\nbeta fixed\ngamma\n")
    _git("add", "example.txt")

    with acquire_fixup_create_plan(base) as plan:
        analysis = plan.units[0]
        assert analysis.status == "agreed"
        assert analysis.target == beta_commit
        assert analysis.lineage.candidates == (beta_commit,)


def test_plan_assigns_adjacent_addition_by_lineage(linear_fixup_repo):
    _repo, source, base, beta_commit = linear_fixup_repo
    source.write_text("alpha\nbeta topic\ninserted fix\ngamma\n")
    _git("add", "example.txt")

    with acquire_fixup_create_plan(base) as plan:
        assert len(plan.units) == 1
        analysis = plan.units[0]
        assert analysis.status == "agreed"
        assert analysis.target == beta_commit
        assert analysis.eligible is True
        assert analysis.placement.barrier == beta_commit


def test_plan_reports_whole_file_addition_as_unsupported(linear_fixup_repo):
    repo, _source, base, _beta_commit = linear_fixup_repo
    (repo / "new.txt").write_text("new work\n")
    _git("add", "new.txt")

    with acquire_fixup_create_plan(base) as plan:
        assert len(plan.units) == 1
        analysis = plan.units[0]
        assert analysis.status == "unsupported"
        assert analysis.unit.kind == "text-file-addition"
        assert analysis.reason_code == "whole-file-addition"
        assert analysis.eligible is False


def test_plan_does_not_ignore_out_of_range_owners_in_one_hunk(
    linear_fixup_repo,
):
    _repo, source, base, beta_commit = linear_fixup_repo
    source.write_text("alpha fixed\nbeta fixed\ngamma\n")
    _git("add", "example.txt")

    with acquire_fixup_create_plan(base) as plan:
        assert len(plan.units) == 1
        analysis = plan.units[0]
        assert analysis.lineage.candidates == (beta_commit,)
        assert analysis.lineage.conclusive is False
        assert analysis.status == "placement-only"
        assert analysis.target == beta_commit
        assert analysis.eligible is False
