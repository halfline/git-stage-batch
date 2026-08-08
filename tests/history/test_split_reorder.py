"""Tests for split execution and dependency-aware history reordering."""

from __future__ import annotations

import copy
import json

import pytest

from git_stage_batch.exceptions import CommandError
from git_stage_batch.history import execution
from git_stage_batch.history.execution import (
    continue_history_operation,
    start_history_operation,
)
from git_stage_batch.history.models import HistoryPhase
from git_stage_batch.history.plan_files import read_and_validate_history_plan
from git_stage_batch.history.records import history_plan_document_record
from git_stage_batch.history.scan import acquire_history_plan_document
from git_stage_batch.history.state import (
    active_history_operation_id,
    load_active_history_operation,
)

from .conftest import git


def _initialize_repository(tmp_path, monkeypatch, files: dict[str, str]) -> str:
    monkeypatch.chdir(tmp_path)
    git("init", "-q", "-b", "topic")
    git("config", "user.name", "Test User")
    git("config", "user.email", "test@example.com")
    for path, contents in files.items():
        (tmp_path / path).write_text(contents, encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "Base")
    return git("rev-parse", "HEAD")


def _write_plan(tmp_path, base: str, mutation=None):
    plan = history_plan_document_record(acquire_history_plan_document(base))
    if mutation is not None:
        mutation(plan)
    path = tmp_path / "history-plan.json"
    path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    return path, plan


def _split_single_source(plan: dict[str, object]) -> None:
    original = plan["plan"]["outputs"][0]
    first = copy.deepcopy(original)
    second = copy.deepcopy(original)
    first["operation"] = "SPLIT"
    first["unit_ids"] = [original["unit_ids"][0]]
    first["message"] = "Change the first line\n"
    second["operation"] = "SPLIT"
    second["unit_ids"] = [original["unit_ids"][1]]
    second["message"] = "Change the last line\n"
    plan["plan"]["outputs"] = [first, second]


def test_split_builds_nonempty_commits_and_preserves_author(
    tmp_path,
    monkeypatch,
):
    lines = [f"line {number}\n" for number in range(20)]
    base = _initialize_repository(
        tmp_path,
        monkeypatch,
        {"example.txt": "".join(lines)},
    )
    lines.insert(0, "inserted first\n")
    lines[-1] = "last changed\n"
    (tmp_path / "example.txt").write_text("".join(lines), encoding="utf-8")
    git(
        "commit",
        "-am",
        "Change both ends",
        "--author=Original Author <original@example.com>",
    )
    original_tree = git("rev-parse", "HEAD^{tree}")
    path, plan = _write_plan(tmp_path, base, _split_single_source)

    assert len(plan["snapshot"]["commits"][0]["patch"]["units"]) == 2

    state = start_history_operation(str(path))

    assert state.phase is HistoryPhase.COMPLETE
    assert len(state.output_commits) == 2
    assert git("rev-list", "--count", f"{base}..HEAD") == "2"
    assert git("rev-parse", "HEAD^{tree}") == original_tree
    assert git("log", "--reverse", "--format=%s", f"{base}..HEAD").splitlines() == [
        "Change the first line",
        "Change the last line",
    ]
    assert git("log", "--format=%ae", f"{base}..HEAD").splitlines() == [
        "original@example.com",
        "original@example.com",
    ]


def test_split_resume_reconciles_a_published_output(
    tmp_path,
    monkeypatch,
):
    lines = [f"line {number}\n" for number in range(20)]
    base = _initialize_repository(
        tmp_path,
        monkeypatch,
        {"example.txt": "".join(lines)},
    )
    lines[0] = "first changed\n"
    lines[-1] = "last changed\n"
    (tmp_path / "example.txt").write_text("".join(lines), encoding="utf-8")
    git("commit", "-am", "Change both ends")
    original_tip = git("rev-parse", "HEAD")
    path, _plan = _write_plan(tmp_path, base, _split_single_source)
    real_update = execution.update_history_operation
    interrupted = False

    def interrupt_after_first_output(state):
        nonlocal interrupted
        if (
            not interrupted
            and state.completed_output_count == 1
            and state.pending_output_commit is None
        ):
            interrupted = True
            raise RuntimeError("stop after the first split output")
        real_update(state)

    monkeypatch.setattr(
        execution,
        "update_history_operation",
        interrupt_after_first_output,
    )
    with pytest.raises(RuntimeError, match="first split output"):
        start_history_operation(str(path))

    paused = load_active_history_operation()
    assert paused.phase is HistoryPhase.PAUSED
    assert paused.completed_output_count == 0
    assert paused.pending_output_commit is not None
    assert git("rev-parse", paused.output_ref) == paused.pending_output_commit
    assert git("rev-parse", "HEAD") == original_tip

    monkeypatch.setattr(execution, "update_history_operation", real_update)
    complete = continue_history_operation()

    assert complete.phase is HistoryPhase.COMPLETE
    assert len(complete.output_commits) == 2
    assert active_history_operation_id() is None


def test_reorder_moves_only_across_proven_independent_units(
    tmp_path,
    monkeypatch,
):
    base = _initialize_repository(
        tmp_path,
        monkeypatch,
        {"alpha.txt": "alpha\n", "beta.txt": "beta\n"},
    )
    (tmp_path / "alpha.txt").write_text("alpha changed\n", encoding="utf-8")
    git("commit", "-am", "Change alpha")
    (tmp_path / "beta.txt").write_text("beta changed\n", encoding="utf-8")
    git("commit", "-am", "Change beta")
    original_tree = git("rev-parse", "HEAD^{tree}")

    def reorder(plan):
        alpha, beta = plan["plan"]["outputs"]
        beta["operation"] = "REORDER"
        plan["plan"]["outputs"] = [beta, alpha]

    path, plan = _write_plan(tmp_path, base, reorder)
    dependencies = plan["snapshot"]["dependency_graph"]["units"]
    assert dependencies[1]["earliest_position"] == 0
    assert dependencies[1]["barrier"] is None

    state = start_history_operation(str(path))

    assert state.phase is HistoryPhase.COMPLETE
    assert git("rev-parse", "HEAD^{tree}") == original_tree
    assert git("log", "--reverse", "--format=%s", f"{base}..HEAD").splitlines() == [
        "Change beta",
        "Change alpha",
    ]


def test_reorder_rejects_a_blocked_same_line_crossing(
    tmp_path,
    monkeypatch,
):
    base = _initialize_repository(
        tmp_path,
        monkeypatch,
        {"example.txt": "original\n"},
    )
    (tmp_path / "example.txt").write_text("first\n", encoding="utf-8")
    git("commit", "-am", "First value")
    (tmp_path / "example.txt").write_text("second\n", encoding="utf-8")
    git("commit", "-am", "Second value")
    original_tip = git("rev-parse", "HEAD")

    def reorder(plan):
        first, second = plan["plan"]["outputs"]
        second["operation"] = "REORDER"
        plan["plan"]["outputs"] = [second, first]

    path, plan = _write_plan(tmp_path, base, reorder)
    dependency = plan["snapshot"]["dependency_graph"]["units"][1]
    assert dependency["barrier"] == "BLOCKED"
    assert dependency["earliest_position"] == 1

    with pytest.raises(CommandError, match="BLOCKED dependency"):
        read_and_validate_history_plan(str(path))

    assert git("rev-parse", "HEAD") == original_tip
    assert active_history_operation_id() is None


def test_blocked_same_file_sibling_does_not_suppress_independent_repair(
    tmp_path,
    monkeypatch,
):
    lines = [f"line {number}\n" for number in range(20)]
    base = _initialize_repository(
        tmp_path,
        monkeypatch,
        {"example.txt": "".join(lines)},
    )
    lines[0] = "first topic\n"
    (tmp_path / "example.txt").write_text("".join(lines), encoding="utf-8")
    git("commit", "-am", "Change first")
    first = git("rev-parse", "HEAD")
    lines[-1] = "last topic\n"
    (tmp_path / "example.txt").write_text("".join(lines), encoding="utf-8")
    git("commit", "-am", "Change last")
    middle = git("rev-parse", "HEAD")
    lines[0] = "first repaired\n"
    lines[-1] = "last repaired\n"
    (tmp_path / "example.txt").write_text("".join(lines), encoding="utf-8")
    git("commit", "-am", "Repair both")
    repair = git("rev-parse", "HEAD")
    original_tree = git("rev-parse", "HEAD^{tree}")

    def integrate_repair_units(plan):
        first_output, middle_output, repair_output = plan["plan"]["outputs"]
        first_unit = first_output["unit_ids"][0]
        middle_unit = middle_output["unit_ids"][0]
        repair_first, repair_last = repair_output["unit_ids"]
        first_output["operation"] = "INTEGRATE"
        first_output["source_commits"] = [first, repair]
        first_output["unit_ids"] = [first_unit, repair_first]
        middle_output["operation"] = "INTEGRATE"
        middle_output["source_commits"] = [middle, repair]
        middle_output["unit_ids"] = [middle_unit, repair_last]
        plan["plan"]["outputs"] = [first_output, middle_output]

    path, plan = _write_plan(tmp_path, base, integrate_repair_units)
    dependencies = plan["snapshot"]["dependency_graph"]["units"]
    first_unit, middle_unit, repair_first, repair_last = dependencies
    assert repair_first["barrier"] == "BLOCKED"
    assert repair_first["barrier_unit_id"] == first_unit["unit_id"]
    assert repair_first["earliest_position"] == 1
    assert repair_last["barrier"] == "BLOCKED"
    assert repair_last["barrier_unit_id"] == middle_unit["unit_id"]
    assert repair_last["earliest_position"] == 2

    validated = read_and_validate_history_plan(str(path))
    assert [output.operation for output in validated.plan.outputs] == [
        "INTEGRATE",
        "INTEGRATE",
    ]

    state = start_history_operation(str(path))

    assert state.phase is HistoryPhase.COMPLETE
    assert len(state.output_commits) == 2
    assert git("rev-parse", "HEAD^{tree}") == original_tree


def test_integrate_moves_dependent_repair_hunks_as_one_output(
    tmp_path,
    monkeypatch,
):
    base = _initialize_repository(
        tmp_path,
        monkeypatch,
        {
            "render.py": "def render(action, phase, verified):\n    return phase\n",
            "other.txt": "original\n",
        },
    )
    flawed = """\
def render(action, phase, verified):
    output = [f"phase: {phase}"]
    if phase == "ABORTED":
        output.append("restored")
    else:
        output.append("verified")
    output.append("recovery")
    return output
"""
    (tmp_path / "render.py").write_text(flawed, encoding="utf-8")
    git("commit", "-am", "Render operation outcome")
    target = git("rev-parse", "HEAD")

    (tmp_path / "other.txt").write_text("middle\n", encoding="utf-8")
    git("commit", "-am", "Change other file")

    corrected = """\
def render(action, phase, verified):
    output = [f"phase: {phase}"]
    if action == "abort" and phase == "COMPLETE":
        output.append("already complete")
    elif phase == "ABORTED":
        output.append("restored")
    elif verified:
        output.append("verified")
    else:
        output.append("next action")
    output.append("recovery")
    return output
"""
    (tmp_path / "render.py").write_text(corrected, encoding="utf-8")
    git("commit", "-am", "Repair operation outcome")
    repair = git("rev-parse", "HEAD")
    original_tree = git("rev-parse", "HEAD^{tree}")

    def integrate_repair(plan):
        target_output, middle_output, repair_output = plan["plan"]["outputs"]
        target_output["operation"] = "INTEGRATE"
        target_output["source_commits"] = [target, repair]
        target_output["unit_ids"].extend(repair_output["unit_ids"])
        plan["plan"]["outputs"] = [target_output, middle_output]

    path, plan = _write_plan(tmp_path, base, integrate_repair)
    repair_units = plan["snapshot"]["commits"][-1]["patch"]["units"]
    repair_unit_ids = {unit["id"] for unit in repair_units}
    assert any(
        dependency["barrier"] == "BLOCKED"
        and dependency["barrier_unit_id"] in repair_unit_ids
        for dependency in plan["snapshot"]["dependency_graph"]["units"]
        if dependency["unit_id"] in repair_unit_ids
    )

    validated = read_and_validate_history_plan(str(path))
    assert [output.operation for output in validated.plan.outputs] == [
        "INTEGRATE",
        "KEEP",
    ]

    state = start_history_operation(str(path))

    assert state.phase is HistoryPhase.COMPLETE
    assert len(state.output_commits) == 2
    assert git("rev-parse", "HEAD^{tree}") == original_tree


def test_reorder_fails_closed_for_an_unsupported_rename_unit(
    tmp_path,
    monkeypatch,
):
    base = _initialize_repository(
        tmp_path,
        monkeypatch,
        {"anchor.txt": "anchor\n", "old.txt": "contents\n"},
    )
    (tmp_path / "anchor.txt").write_text("changed\n", encoding="utf-8")
    git("commit", "-am", "Change anchor")
    git("mv", "old.txt", "new.txt")
    git("commit", "-m", "Rename file")

    def reorder(plan):
        anchor, rename = plan["plan"]["outputs"]
        rename["operation"] = "REORDER"
        plan["plan"]["outputs"] = [rename, anchor]

    path, plan = _write_plan(tmp_path, base, reorder)
    dependency = plan["snapshot"]["dependency_graph"]["units"][1]
    assert dependency["barrier"] == "UNKNOWN"
    assert dependency["detail"] == "rename"

    with pytest.raises(CommandError, match="UNKNOWN dependency"):
        read_and_validate_history_plan(str(path))


def test_split_keeps_an_unmoved_atomic_source_on_whole_patch_replay(
    tmp_path,
    monkeypatch,
):
    lines = [f"line {number}\n" for number in range(20)]
    base = _initialize_repository(
        tmp_path,
        monkeypatch,
        {
            "example.txt": "".join(lines),
            "old.txt": "renamed contents\n",
        },
    )
    git("mv", "old.txt", "new.txt")
    git("commit", "-m", "Rename file")
    lines[0] = "first changed\n"
    lines[-1] = "last changed\n"
    (tmp_path / "example.txt").write_text("".join(lines), encoding="utf-8")
    git("commit", "-am", "Change both ends")
    original_tree = git("rev-parse", "HEAD^{tree}")

    def split_second_source(plan):
        rename, original = plan["plan"]["outputs"]
        first = copy.deepcopy(original)
        second = copy.deepcopy(original)
        first["operation"] = "SPLIT"
        first["unit_ids"] = [original["unit_ids"][0]]
        first["message"] = "Change the first line\n"
        second["operation"] = "SPLIT"
        second["unit_ids"] = [original["unit_ids"][1]]
        second["message"] = "Change the last line\n"
        plan["plan"]["outputs"] = [rename, first, second]

    path, plan = _write_plan(tmp_path, base, split_second_source)
    dependencies = plan["snapshot"]["dependency_graph"]["units"]
    assert dependencies[0]["barrier"] == "UNKNOWN"
    assert all(
        dependency["barrier"] == "UNKNOWN"
        for dependency in dependencies[1:]
    )

    state = start_history_operation(str(path))

    assert state.phase is HistoryPhase.COMPLETE
    assert len(state.output_commits) == 3
    assert git("rev-parse", "HEAD^{tree}") == original_tree
    assert git("show", "HEAD:new.txt") == "renamed contents"


def test_later_units_regain_reorder_proofs_after_an_unknown_segment(
    tmp_path,
    monkeypatch,
):
    base = _initialize_repository(
        tmp_path,
        monkeypatch,
        {
            "alpha.txt": "alpha\n",
            "beta.txt": "beta\n",
            "old.txt": "renamed contents\n",
        },
    )
    git("mv", "old.txt", "new.txt")
    git("commit", "-m", "Rename file")
    (tmp_path / "alpha.txt").write_text("alpha changed\n", encoding="utf-8")
    git("commit", "-am", "Change alpha")
    (tmp_path / "beta.txt").write_text("beta changed\n", encoding="utf-8")
    git("commit", "-am", "Change beta")
    original_tree = git("rev-parse", "HEAD^{tree}")

    def reorder_later_sources(plan):
        rename, alpha, beta = plan["plan"]["outputs"]
        beta["operation"] = "REORDER"
        plan["plan"]["outputs"] = [rename, beta, alpha]

    path, plan = _write_plan(tmp_path, base, reorder_later_sources)
    rename_dependency, alpha_dependency, beta_dependency = plan["snapshot"][
        "dependency_graph"
    ]["units"]
    assert rename_dependency["barrier"] == "UNKNOWN"
    assert alpha_dependency["earliest_position"] == 1
    assert beta_dependency["earliest_position"] == 1
    assert beta_dependency["barrier_unit_id"] == rename_dependency["unit_id"]

    state = start_history_operation(str(path))

    assert state.phase is HistoryPhase.COMPLETE
    assert git("rev-parse", "HEAD^{tree}") == original_tree
    assert git("log", "--reverse", "--format=%s", f"{base}..HEAD").splitlines() == [
        "Rename file",
        "Change beta",
        "Change alpha",
    ]
