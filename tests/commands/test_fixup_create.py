"""Command tests for creating grouped fixup commits."""

from __future__ import annotations

import json
import stat
import subprocess

import pytest

import git_stage_batch.fixup.execution as fixup_execution
from git_stage_batch.commands.fixup_create import command_create_fixups
from git_stage_batch.exceptions import CommandError


def _git(*arguments: str, check: bool = True) -> str:
    return subprocess.run(
        ["git", *arguments],
        check=check,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write_plan(path, plan: dict[str, object]) -> None:
    path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")


def _create_plan(base: str, path, capsys) -> dict[str, object]:
    command_create_fixups(base, dry_run=True, porcelain=True)
    plan = json.loads(capsys.readouterr().out)
    _write_plan(path, plan)
    return plan


@pytest.fixture
def fixup_create_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _git("init")
    _git("config", "user.name", "Test User")
    _git("config", "user.email", "test@example.com")

    source = tmp_path / "example.txt"
    source.write_text("alpha\nbeta\ngamma\n")
    _git("add", "example.txt")
    _git("commit", "-m", "Base")
    base = _git("rev-parse", "HEAD")

    source.write_text("alpha topic\nbeta\ngamma\n")
    _git("add", "example.txt")
    _git("commit", "-m", "Change alpha")
    alpha_commit = _git("rev-parse", "HEAD")

    source.write_text("alpha topic\nbeta\ngamma topic\n")
    _git("add", "example.txt")
    _git("commit", "-m", "Change gamma")
    gamma_commit = _git("rev-parse", "HEAD")

    return tmp_path, source, base, alpha_commit, gamma_commit


def test_create_conserves_additions_assigned_to_different_targets(
    tmp_path,
    monkeypatch,
    capsys,
):
    monkeypatch.chdir(tmp_path)
    _git("init")
    _git("config", "user.name", "Test User")
    _git("config", "user.email", "test@example.com")

    source = tmp_path / "example.txt"
    source.write_text("alpha\nmiddle\nomega\ntail\n")
    _git("add", "example.txt")
    _git("commit", "-m", "Base")
    base = _git("rev-parse", "HEAD")

    source.write_text("alpha topic\nmiddle\nomega\ntail\n")
    _git("commit", "-am", "Change alpha")
    source.write_text("alpha topic\nmiddle\nomega topic\ntail\n")
    _git("commit", "-am", "Change omega")

    source.write_text(
        "alpha topic\n"
        "alpha fixed\n"
        "middle\n"
        "omega topic\n"
        "omega fixed\n"
        "tail\n"
    )
    _git("add", "example.txt")

    command_create_fixups(base, dry_run=True, porcelain=True)

    output = json.loads(capsys.readouterr().out)
    assert [unit["kind"] for unit in output["units"]] == [
        "text-addition",
        "text-addition",
    ]
    assert output["summary"]["assigned_units"] == 2
    assert len(output["groups"]) == 2


def test_create_makes_one_fixup_per_target_and_preserves_unstaged_work(
    fixup_create_repo,
    capsys,
):
    _repo, source, base, alpha_commit, gamma_commit = fixup_create_repo
    source.write_text("alpha fixed\nbeta\ngamma fixed\n")
    _git("add", "example.txt")
    source.write_text("alpha fixed\nbeta unstaged\ngamma fixed\n")
    unstaged_before = _git("diff")

    command_create_fixups(base, porcelain=True)

    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["created_commits"] == 2
    assert output["summary"]["remaining_units"] == 0
    assert output["recovery_ref"].startswith("refs/git-stage-batch/fixup/backups/")
    assert _git("log", "-2", "--format=%s").splitlines() == [
        "fixup! Change gamma",
        "fixup! Change alpha",
    ]
    assert [group["target"] for group in output["groups"]] == [
        alpha_commit,
        gamma_commit,
    ]
    assert _git("diff", "--cached") == ""
    assert _git("diff") == unstaged_before


def test_create_does_not_diff_an_exact_unmodified_prefix(
    fixup_create_repo,
    monkeypatch,
    capsys,
):
    _repo, source, base, alpha_commit, _gamma_commit = fixup_create_repo
    source.write_text("alpha topic\nbeta\ngamma fixed\n")
    _git("add", "example.txt")
    base_tree = _git("rev-parse", f"{base}^{{tree}}")
    alpha_tree = _git("rev-parse", f"{alpha_commit}^{{tree}}")
    load_tree_diff = fixup_execution.load_tree_diff_as_buffer

    def reject_exact_prefix_diff(old_tree, new_tree, *, env=None):
        if (old_tree, new_tree) == (base_tree, alpha_tree):
            pytest.fail("exact unmodified prefix must not be materialized")
        return load_tree_diff(old_tree, new_tree, env=env)

    monkeypatch.setattr(
        fixup_execution,
        "load_tree_diff_as_buffer",
        reject_exact_prefix_diff,
    )

    command_create_fixups(base, dry_run=True, porcelain=True)

    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["assigned_units"] == 1


def test_create_groups_multiple_exact_units_for_one_target(
    fixup_create_repo,
    capsys,
):
    _repo, source, base, _alpha_commit, _gamma_commit = fixup_create_repo
    source.write_text("alpha combined\nbeta\ngamma combined\n")
    _git("commit", "-am", "Change alpha and gamma")
    target = _git("rev-parse", "HEAD")
    source.write_text("alpha fixed\nbeta\ngamma fixed\n")
    _git("add", "example.txt")

    command_create_fixups(base, porcelain=True)

    output = json.loads(capsys.readouterr().out)
    assert len(output["units"]) == 2
    assert len(output["groups"]) == 1
    assert output["groups"][0]["target"] == target
    assert len(output["groups"][0]["unit_ids"]) == 2
    assert output["summary"]["created_commits"] == 1
    assert _git("diff", "--cached") == ""


def test_create_replays_target_relative_fixup_patch(
    fixup_create_repo,
    monkeypatch,
    capsys,
):
    _repo, source, base, alpha_commit, gamma_commit = fixup_create_repo
    source.write_text("alpha fixed\nbeta\ngamma topic\n")
    _git("add", "example.txt")
    alpha_tree = _git("rev-parse", f"{alpha_commit}^{{tree}}")
    head_tree = _git("rev-parse", f"{gamma_commit}^{{tree}}")
    index_tree = _git("write-tree")
    diff_pairs: list[tuple[str, str]] = []
    load_tree_diff = fixup_execution.load_tree_diff_as_buffer

    def record_tree_diff(old_tree, new_tree, *, env=None):
        diff_pairs.append((old_tree, new_tree))
        return load_tree_diff(old_tree, new_tree, env=env)

    monkeypatch.setattr(
        fixup_execution,
        "load_tree_diff_as_buffer",
        record_tree_diff,
    )

    command_create_fixups(base, dry_run=True, porcelain=True)

    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["assigned_units"] == 1
    assert diff_pairs.count((head_tree, index_tree)) == 1
    assert any(
        old_tree == alpha_tree and new_tree != head_tree
        for old_tree, new_tree in diff_pairs
    )


def test_create_dry_run_does_not_mutate(fixup_create_repo, capsys):
    _repo, source, base, _alpha_commit, _gamma_commit = fixup_create_repo
    source.write_text("alpha fixed\nbeta\ngamma topic\n")
    _git("add", "example.txt")
    head_before = _git("rev-parse", "HEAD")
    index_before = _git("write-tree")

    command_create_fixups(base, dry_run=True, porcelain=True)

    output = json.loads(capsys.readouterr().out)
    assert output["dry_run"] is True
    assert output["schema_version"] == 2
    assert output["range"]["commits_newest_first"][0] == output["range"]["head"]
    assert output["assignments"][0]["basis"] == "automatic"
    assert output["summary"]["assigned_units"] == 1
    assert output["summary"]["created_commits"] == 0
    assert _git("rev-parse", "HEAD") == head_before
    assert _git("write-tree") == index_before


def test_create_replays_an_unchanged_dry_run_plan(
    fixup_create_repo,
    capsys,
):
    repo, source, base, alpha_commit, _gamma_commit = fixup_create_repo
    source.write_text("alpha fixed\nbeta\ngamma topic\n")
    _git("add", "example.txt")
    plan_path = repo / "fixup-plan.json"
    plan = _create_plan(base, plan_path, capsys)

    command_create_fixups(plan_path=str(plan_path), porcelain=True)

    output = json.loads(capsys.readouterr().out)
    assert plan["assignments"][0]["target"] == alpha_commit
    assert output["assignments"] == plan["assignments"]
    assert output["summary"]["created_commits"] == 1
    assert _git("log", "-1", "--format=%s") == "fixup! Change alpha"
    assert _git("diff", "--cached") == ""


def test_create_accepts_a_mechanically_valid_explicit_assignment(
    fixup_create_repo,
    capsys,
):
    repo, source, base, alpha_commit, gamma_commit = fixup_create_repo
    source.write_text("alpha fixed\nbeta\ngamma topic\n")
    _git("add", "example.txt")
    plan_path = repo / "fixup-plan.json"
    plan = _create_plan(base, plan_path, capsys)
    assert plan["units"][0]["target"] == alpha_commit

    plan["assignments"][0]["target"] = gamma_commit
    plan["assignments"][0]["basis"] = "explicit"
    _write_plan(plan_path, plan)
    command_create_fixups(plan_path=str(plan_path), porcelain=True)

    output = json.loads(capsys.readouterr().out)
    assert output["units"][0]["target"] == alpha_commit
    assert output["assignments"][0] == {
        "unit_id": plan["units"][0]["id"],
        "target": gamma_commit,
        "basis": "explicit",
    }
    assert output["groups"][0]["target"] == gamma_commit
    assert _git("log", "-1", "--format=%s") == "fixup! Change gamma"


def test_create_requires_explicit_basis_for_a_reviewed_override(
    fixup_create_repo,
    capsys,
):
    repo, source, base, _alpha_commit, gamma_commit = fixup_create_repo
    source.write_text("alpha fixed\nbeta\ngamma topic\n")
    _git("add", "example.txt")
    plan_path = repo / "fixup-plan.json"
    plan = _create_plan(base, plan_path, capsys)
    plan["assignments"][0]["target"] = gamma_commit
    _write_plan(plan_path, plan)

    with pytest.raises(CommandError, match="not automatically eligible"):
        command_create_fixups(plan_path=str(plan_path))


def test_create_rejects_an_explicit_target_behind_a_barrier(
    fixup_create_repo,
    capsys,
):
    repo, source, base, alpha_commit, gamma_commit = fixup_create_repo
    source.write_text("alpha topic\nbeta\ngamma fixed\n")
    _git("add", "example.txt")
    plan_path = repo / "fixup-plan.json"
    plan = _create_plan(base, plan_path, capsys)
    assert plan["units"][0]["placement"]["barrier"] == gamma_commit
    plan["assignments"][0]["target"] = alpha_commit
    plan["assignments"][0]["basis"] = "explicit"
    _write_plan(plan_path, plan)

    with pytest.raises(CommandError, match="cannot cross the commits"):
        command_create_fixups(plan_path=str(plan_path))


def test_create_accepts_explicit_semantics_for_placement_only_evidence(
    fixup_create_repo,
    capsys,
):
    repo, source, base, _alpha_commit, gamma_commit = fixup_create_repo
    source.write_text("alpha fixed\nbeta fixed\ngamma topic\n")
    _git("add", "example.txt")
    plan_path = repo / "fixup-plan.json"
    plan = _create_plan(base, plan_path, capsys)
    unit = plan["units"][0]
    assert unit["status"] == "placement-only"
    assert unit["target"] == gamma_commit
    assert plan["assignments"] == []
    plan["assignments"] = [
        {
            "unit_id": unit["id"],
            "target": gamma_commit,
            "basis": "explicit",
        }
    ]
    _write_plan(plan_path, plan)

    command_create_fixups(plan_path=str(plan_path), porcelain=True)

    output = json.loads(capsys.readouterr().out)
    assert output["assignments"][0]["basis"] == "explicit"
    assert output["summary"]["created_commits"] == 1


def test_create_rejects_explicit_assignment_with_unknown_placement(
    fixup_create_repo,
    capsys,
):
    repo, _source, base, _alpha_commit, gamma_commit = fixup_create_repo
    (repo / "new.txt").write_text("new work\n")
    _git("add", "new.txt")
    plan_path = repo / "fixup-plan.json"
    plan = _create_plan(base, plan_path, capsys)
    unit = plan["units"][0]
    assert unit["placement"]["status"] == "unknown"
    plan["assignments"] = [
        {
            "unit_id": unit["id"],
            "target": gamma_commit,
            "basis": "explicit",
        }
    ]
    _write_plan(plan_path, plan)

    with pytest.raises(CommandError, match="no conclusive mechanical placement"):
        command_create_fixups(plan_path=str(plan_path))


def test_create_rejects_a_plan_after_the_index_changes(
    fixup_create_repo,
    capsys,
):
    repo, source, base, _alpha_commit, _gamma_commit = fixup_create_repo
    source.write_text("alpha fixed\nbeta\ngamma topic\n")
    _git("add", "example.txt")
    plan_path = repo / "fixup-plan.json"
    _create_plan(base, plan_path, capsys)
    source.write_text("alpha newer\nbeta\ngamma topic\n")
    _git("add", "example.txt")

    with pytest.raises(CommandError, match="does not match the current"):
        command_create_fixups(plan_path=str(plan_path))


def test_create_rejects_omitted_plan_units(
    fixup_create_repo,
    capsys,
):
    repo, source, base, _alpha_commit, _gamma_commit = fixup_create_repo
    source.write_text("alpha fixed\nbeta\ngamma topic\n")
    _git("add", "example.txt")
    plan_path = repo / "fixup-plan.json"
    plan = _create_plan(base, plan_path, capsys)
    plan["units"] = []
    plan["assignments"] = []
    _write_plan(plan_path, plan)

    with pytest.raises(CommandError, match="exact units or evidence"):
        command_create_fixups(plan_path=str(plan_path))


def test_create_reports_a_missing_unit_id_as_an_invalid_plan(
    fixup_create_repo,
    capsys,
):
    repo, source, base, _alpha_commit, _gamma_commit = fixup_create_repo
    source.write_text("alpha fixed\nbeta\ngamma topic\n")
    _git("add", "example.txt")
    plan_path = repo / "fixup-plan.json"
    plan = _create_plan(base, plan_path, capsys)
    del plan["units"][0]["id"]
    plan["assignments"] = []
    _write_plan(plan_path, plan)

    with pytest.raises(CommandError, match="missing field 'id'"):
        command_create_fixups(plan_path=str(plan_path))


def test_create_rejects_duplicate_json_fields(
    fixup_create_repo,
    capsys,
):
    repo, source, base, _alpha_commit, _gamma_commit = fixup_create_repo
    source.write_text("alpha fixed\nbeta\ngamma topic\n")
    _git("add", "example.txt")
    plan_path = repo / "fixup-plan.json"
    plan = _create_plan(base, plan_path, capsys)
    payload = json.dumps(plan).replace(
        '"operation": "fixup-create"',
        '"operation": "fixup-create", "operation": "fixup-create"',
        1,
    )
    plan_path.write_text(payload, encoding="utf-8")

    with pytest.raises(CommandError, match="duplicate field 'operation'"):
        command_create_fixups(plan_path=str(plan_path))


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("path", "forged.txt"),
        ("reason", "forged-rationale"),
    ],
)
def test_create_rejects_forged_plan_unit_evidence(
    fixup_create_repo,
    capsys,
    field,
    replacement,
):
    repo, source, base, _alpha_commit, _gamma_commit = fixup_create_repo
    source.write_text("alpha fixed\nbeta\ngamma topic\n")
    _git("add", "example.txt")
    plan_path = repo / "fixup-plan.json"
    plan = _create_plan(base, plan_path, capsys)
    plan["units"][0][field] = replacement
    _write_plan(plan_path, plan)

    with pytest.raises(CommandError, match="exact units or evidence"):
        command_create_fixups(plan_path=str(plan_path))


def test_create_rejects_a_forged_stable_unit_id(
    fixup_create_repo,
    capsys,
):
    repo, source, base, _alpha_commit, _gamma_commit = fixup_create_repo
    source.write_text("alpha fixed\nbeta\ngamma topic\n")
    _git("add", "example.txt")
    plan_path = repo / "fixup-plan.json"
    plan = _create_plan(base, plan_path, capsys)
    forged_id = "f" * 64
    plan["units"][0]["id"] = forged_id
    plan["assignments"][0]["unit_id"] = forged_id
    _write_plan(plan_path, plan)

    with pytest.raises(CommandError, match="exact units or evidence"):
        command_create_fixups(plan_path=str(plan_path))


def test_create_rejects_duplicate_plan_assignments(
    fixup_create_repo,
    capsys,
):
    repo, source, base, _alpha_commit, _gamma_commit = fixup_create_repo
    source.write_text("alpha fixed\nbeta\ngamma topic\n")
    _git("add", "example.txt")
    plan_path = repo / "fixup-plan.json"
    plan = _create_plan(base, plan_path, capsys)
    plan["assignments"].append(dict(plan["assignments"][0]))
    _write_plan(plan_path, plan)

    with pytest.raises(CommandError, match="duplicates a unit assignment"):
        command_create_fixups(plan_path=str(plan_path))


def test_create_rejects_abbreviated_plan_target_ids(
    fixup_create_repo,
    capsys,
):
    repo, source, base, _alpha_commit, _gamma_commit = fixup_create_repo
    source.write_text("alpha fixed\nbeta\ngamma topic\n")
    _git("add", "example.txt")
    plan_path = repo / "fixup-plan.json"
    plan = _create_plan(base, plan_path, capsys)
    plan["assignments"][0]["target"] = plan["assignments"][0]["target"][:12]
    _write_plan(plan_path, plan)

    with pytest.raises(CommandError, match="full lowercase hexadecimal"):
        command_create_fixups(plan_path=str(plan_path))


def test_create_validates_and_refreshes_a_reviewed_plan_in_dry_run(
    fixup_create_repo,
    capsys,
):
    repo, source, base, _alpha_commit, gamma_commit = fixup_create_repo
    source.write_text("alpha fixed\nbeta\ngamma topic\n")
    _git("add", "example.txt")
    head_before = _git("rev-parse", "HEAD")
    plan_path = repo / "fixup-plan.json"
    plan = _create_plan(base, plan_path, capsys)
    plan["assignments"][0]["target"] = gamma_commit
    plan["assignments"][0]["basis"] = "explicit"
    _write_plan(plan_path, plan)

    command_create_fixups(
        plan_path=str(plan_path),
        dry_run=True,
        porcelain=True,
    )

    refreshed = json.loads(capsys.readouterr().out)
    assert refreshed["groups"][0]["target"] == gamma_commit
    assert refreshed["assignments"][0]["basis"] == "explicit"
    assert refreshed["dry_run"] is True
    assert _git("rev-parse", "HEAD") == head_before


def test_create_plan_can_leave_reviewed_units_staged_with_partial(
    fixup_create_repo,
    capsys,
):
    repo, source, base, alpha_commit, _gamma_commit = fixup_create_repo
    source.write_text("alpha fixed\nbeta\ngamma fixed\n")
    _git("add", "example.txt")
    plan_path = repo / "fixup-plan.json"
    plan = _create_plan(base, plan_path, capsys)
    plan["assignments"] = [
        assignment
        for assignment in plan["assignments"]
        if assignment["target"] == alpha_commit
    ]
    _write_plan(plan_path, plan)

    with pytest.raises(CommandError, match="--partial"):
        command_create_fixups(plan_path=str(plan_path))
    capsys.readouterr()

    command_create_fixups(
        plan_path=str(plan_path),
        partial=True,
        porcelain=True,
    )

    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["assigned_units"] == 1
    assert output["summary"]["remaining_units"] == 1
    assert output["summary"]["created_commits"] == 1
    staged = _git("diff", "--cached", "--unified=0")
    assert "gamma fixed" in staged
    assert "alpha fixed" not in staged


def test_create_rejects_a_boundary_together_with_a_plan(
    fixup_create_repo,
    capsys,
):
    repo, source, base, _alpha_commit, _gamma_commit = fixup_create_repo
    source.write_text("alpha fixed\nbeta\ngamma topic\n")
    _git("add", "example.txt")
    plan_path = repo / "fixup-plan.json"
    _create_plan(base, plan_path, capsys)

    with pytest.raises(CommandError, match="both a fixup boundary and --plan"):
        command_create_fixups(base, plan_path=str(plan_path))


def test_create_requires_partial_when_some_units_are_unresolved(
    fixup_create_repo,
    capsys,
):
    repo, source, base, _alpha_commit, _gamma_commit = fixup_create_repo
    source.write_text("alpha fixed\nbeta\ngamma topic\n")
    (repo / "new.txt").write_text("new staged work\n")
    _git("add", "example.txt")
    _git("add", "new.txt")
    head_before = _git("rev-parse", "HEAD")

    with pytest.raises(CommandError, match="--partial"):
        command_create_fixups(base, porcelain=True)

    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["eligible_units"] == 1
    assert output["summary"]["remaining_units"] == 1
    assert _git("rev-parse", "HEAD") == head_before

    command_create_fixups(base, partial=True, porcelain=True)

    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["created_commits"] == 1
    assert output["summary"]["remaining_units"] == 1
    staged = _git("diff", "--cached", "--unified=0")
    assert "new staged work" in staged
    assert "alpha fixed" not in staged


def test_create_rolls_back_all_fixups_when_later_hook_fails(
    fixup_create_repo,
):
    repo, source, base, _alpha_commit, _gamma_commit = fixup_create_repo
    source.write_text("alpha fixed\nbeta\ngamma fixed\n")
    _git("add", "example.txt")
    head_before = _git("rev-parse", "HEAD")
    index_before = _git("write-tree")

    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        'count_file="$(git rev-parse --git-dir)/fixup-hook-count"\n'
        "count=0\n"
        'test ! -f "$count_file" || count=$(cat "$count_file")\n'
        "count=$((count + 1))\n"
        'printf \'%s\\n\' "$count" >"$count_file"\n'
        'test "$count" -lt 2\n'
    )
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)

    with pytest.raises(subprocess.CalledProcessError):
        command_create_fixups(base)

    assert _git("rev-parse", "HEAD") == head_before
    assert _git("write-tree") == index_before
    assert _git("log", "-1", "--format=%s") == "Change gamma"
    recovery_refs = _git(
        "for-each-ref",
        "--format=%(refname)",
        "refs/git-stage-batch/fixup/backups/",
    ).splitlines()
    assert len(recovery_refs) == 1
    assert _git("rev-parse", recovery_refs[0]) == head_before


def test_create_treats_pathspec_looking_name_as_literal(
    fixup_create_repo,
    capsys,
):
    repo, _source, base, _alpha_commit, _gamma_commit = fixup_create_repo
    path = ":(top)literal.txt"
    literal = repo / path
    literal.write_text("topic\n")
    _git("--literal-pathspecs", "add", "--", path)
    _git("commit", "-m", "Add literal path")
    target = _git("rev-parse", "HEAD")

    literal.write_text("fixed\n")
    _git("--literal-pathspecs", "add", "--", path)

    command_create_fixups(base, porcelain=True)

    output = json.loads(capsys.readouterr().out)
    assert output["groups"][0]["target"] == target
    assert output["units"][0]["path"] == path
    assert _git("log", "-1", "--format=%s") == "fixup! Add literal path"


def test_create_keeps_subject_when_first_duplicate_is_the_target(
    fixup_create_repo,
    capsys,
):
    repo, source, base, alpha_commit, _gamma_commit = fixup_create_repo
    (repo / "unrelated.txt").write_text("later\n")
    _git("add", "unrelated.txt")
    _git("commit", "-m", "Change alpha")

    source.write_text("alpha fixed\nbeta\ngamma topic\n")
    _git("add", "example.txt")

    command_create_fixups(base, porcelain=True)

    output = json.loads(capsys.readouterr().out)
    expected_subject = "fixup! Change alpha"
    assert output["groups"][0]["target"] == alpha_commit
    assert output["groups"][0]["fixup_subject"] == expected_subject
    assert output["groups"][0]["hash_qualified"] is False
    assert _git("log", "-1", "--format=%s") == expected_subject

    _git("-c", "sequence.editor=:", "rebase", "-i", "--autosquash", base)

    assert _git("rev-list", "--count", f"{base}..HEAD") == "3"
    first_commit = _git("rev-list", "--reverse", f"{base}..HEAD").splitlines()[0]
    assert _git("show", f"{first_commit}:example.txt") == (
        "alpha fixed\nbeta\ngamma"
    )
    assert source.read_text() == "alpha fixed\nbeta\ngamma topic\n"
    assert "git-stage-batch-fixup-id:" not in _git(
        "log",
        "--format=%B",
        f"{base}..HEAD",
    )


def test_create_hash_qualifies_later_duplicate_for_autosquash(
    fixup_create_repo,
    capsys,
):
    _repo, source, base, _alpha_commit, _gamma_commit = fixup_create_repo
    source.write_text("alpha topic\nbeta topic\ngamma topic\n")
    _git("add", "example.txt")
    _git("commit", "-m", "Change alpha")
    later_duplicate = _git("rev-parse", "HEAD")

    source.write_text("alpha topic\nbeta fixed\ngamma topic\n")
    _git("add", "example.txt")

    command_create_fixups(base, porcelain=True)

    output = json.loads(capsys.readouterr().out)
    expected_subject = f"fixup! {later_duplicate}"
    assert output["groups"][0]["target"] == later_duplicate
    assert output["groups"][0]["fixup_subject"] == expected_subject
    assert output["groups"][0]["hash_qualified"] is True
    assert _git("log", "-1", "--format=%s") == expected_subject

    _git("-c", "sequence.editor=:", "rebase", "-i", "--autosquash", base)

    assert _git("rev-list", "--count", f"{base}..HEAD") == "3"
    first_commit = _git("rev-list", "--reverse", f"{base}..HEAD").splitlines()[0]
    assert _git("show", f"{first_commit}:example.txt") == (
        "alpha topic\nbeta\ngamma"
    )
    assert source.read_text() == "alpha topic\nbeta fixed\ngamma topic\n"


@pytest.mark.parametrize(
    "target_subject",
    ["fixup! Change alpha", "amend! Change alpha", "squash! Change alpha"],
)
def test_create_hash_qualifies_fixupish_target_subject(
    fixup_create_repo,
    capsys,
    target_subject,
):
    _repo, source, base, _alpha_commit, _gamma_commit = fixup_create_repo
    source.write_text("alpha topic\nbeta topic\ngamma topic\n")
    _git("add", "example.txt")
    _git("commit", "-m", target_subject)
    fixupish_target = _git("rev-parse", "HEAD")

    source.write_text("alpha topic\nbeta fixed\ngamma topic\n")
    _git("add", "example.txt")

    command_create_fixups(base, porcelain=True)

    output = json.loads(capsys.readouterr().out)
    expected_subject = f"fixup! {fixupish_target}"
    assert output["groups"][0]["fixup_subject"] == expected_subject
    assert output["groups"][0]["hash_qualified"] is True


def test_create_rolls_back_when_commit_msg_hook_changes_subject(
    fixup_create_repo,
):
    repo, source, base, _alpha_commit, _gamma_commit = fixup_create_repo
    source.write_text("alpha fixed\nbeta\ngamma topic\n")
    _git("add", "example.txt")
    head_before = _git("rev-parse", "HEAD")
    index_before = _git("write-tree")

    hook = repo / ".git" / "hooks" / "commit-msg"
    hook.write_text(
        "#!/bin/sh\n"
        "{\n"
        "  printf '%s\\n' 'changed by hook'\n"
        "  sed -n '2,$p' \"$1\"\n"
        "} >\"$1.tmp\"\n"
        "mv \"$1.tmp\" \"$1\"\n"
    )
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)

    with pytest.raises(CommandError, match="changed the planned fixup subject"):
        command_create_fixups(base)

    assert _git("rev-parse", "HEAD") == head_before
    assert _git("write-tree") == index_before


def test_create_preserves_head_moved_by_post_commit_hook(
    fixup_create_repo,
):
    repo, source, base, _alpha_commit, _gamma_commit = fixup_create_repo
    source.write_text("alpha fixed\nbeta\ngamma topic\n")
    _git("add", "example.txt")
    head_before = _git("rev-parse", "HEAD")
    index_before = _git("write-tree")
    head_tree = _git("rev-parse", "HEAD^{tree}")
    external_commit = _git(
        "commit-tree",
        head_tree,
        "-p",
        head_before,
        "-m",
        "External movement",
    )

    hook = repo / ".git" / "hooks" / "post-commit"
    hook.write_text(
        "#!/bin/sh\n"
        f"git update-ref HEAD {external_commit}\n"
    )
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)

    with pytest.raises(CommandError, match="HEAD moved unexpectedly"):
        command_create_fixups(base)

    assert _git("rev-parse", "HEAD") == external_commit
    assert _git("write-tree") == index_before
    recovery_refs = _git(
        "for-each-ref",
        "--format=%(refname)",
        "refs/git-stage-batch/fixup/backups/",
    ).splitlines()
    assert len(recovery_refs) == 1
    assert _git("rev-parse", recovery_refs[0]) == head_before


def test_create_rejects_an_active_bisect(fixup_create_repo):
    repo, source, base, _alpha_commit, _gamma_commit = fixup_create_repo
    source.write_text("alpha fixed\nbeta\ngamma topic\n")
    _git("add", "example.txt")
    (repo / ".git" / "BISECT_START").write_text(f"{base}\n")

    with pytest.raises(CommandError, match="BISECT_START"):
        command_create_fixups(base)


def test_create_human_output_escapes_controls_in_commit_subjects(
    fixup_create_repo,
    capsys,
):
    _repo, source, base, _alpha_commit, _gamma_commit = fixup_create_repo
    source.write_text("alpha topic\nbeta topic\ngamma topic\n")
    _git("commit", "-am", "Topic \x1b[2J")
    source.write_text("alpha topic\nbeta fixed\ngamma topic\n")
    _git("add", "example.txt")

    command_create_fixups(base, dry_run=True)

    output = capsys.readouterr().out
    assert "\x1b" not in output
    assert r"\u001b" in output
