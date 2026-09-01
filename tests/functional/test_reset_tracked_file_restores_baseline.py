"""Regression reproducer for resetting a tracked file from a batch."""

import json
import subprocess

from .conftest import git_stage_batch


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        capture_output=True,
        text=True,
    )


def test_reset_tracked_file_restores_baseline_entry(functional_repo):
    """Resetting the last claim must not turn the tracked file into a deletion."""
    route = functional_repo / "route.c"
    baseline = "int route = 0;\n"
    selected = "int route = 1;\n"

    route.write_text(baseline)
    _git(functional_repo, "add", "route.c")
    _git(functional_repo, "commit", "-m", "Add route")
    route.write_text(selected)

    git_stage_batch("start", "--no-auto-advance")
    captured = git_stage_batch(
        "discard",
        "--to",
        "route-admission",
        "--file",
        "route.c",
        "--no-auto-advance",
        check=False,
    )
    assert captured.returncode == 0, captured.stderr
    batch_ref = "refs/git-stage-batch/batches/route-admission"
    assert _git(functional_repo, "show", f"{batch_ref}:route.c").stdout == selected

    git_stage_batch(
        "reset",
        "--from",
        "route-admission",
        "--file",
        "route.c",
    )

    state = json.loads(
        _git(
            functional_repo,
            "show",
            "refs/git-stage-batch/state/route-admission:batch.json",
        ).stdout
    )
    assert "route.c" not in state["files"]

    restored = _git(
        functional_repo,
        "show",
        f"{batch_ref}:route.c",
        check=False,
    )
    assert restored.returncode == 0, restored.stderr
    assert restored.stdout == baseline


def test_reset_added_file_removes_batch_entry(functional_repo):
    """Resetting an added file must leave it absent from the batch tree."""
    added = functional_repo / "added.c"
    added.write_text("int added = 1;\n")

    git_stage_batch("start", "--no-auto-advance")
    captured = git_stage_batch(
        "discard",
        "--to",
        "added-file",
        "--file",
        "added.c",
        "--no-auto-advance",
        check=False,
    )
    assert captured.returncode == 0, captured.stderr

    git_stage_batch("reset", "--from", "added-file", "--file", "added.c")

    restored = _git(
        functional_repo,
        "show",
        "refs/git-stage-batch/batches/added-file:added.c",
        check=False,
    )
    assert restored.returncode != 0
