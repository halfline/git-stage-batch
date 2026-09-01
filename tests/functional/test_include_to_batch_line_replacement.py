"""Regression coverage for saving a line replacement directly to a batch."""

import subprocess

from .conftest import git_stage_batch


def test_include_to_batch_accepts_line_replacement(functional_repo):
    """A replacement batch view should not alter the live file or index."""
    path = functional_repo / "README.md"
    baseline = path.read_text()
    working = baseline.replace("A test project.", "A future project.")
    replacement = "A test project with focused metadata.\n"
    path.write_text(working)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch(
        "show", "--file", path.name, "--page", "all"
    ).stdout
    ids = [
        line.split("[#", 1)[1].split("]", 1)[0]
        for line in view.splitlines()
        if "[#" in line
    ]
    assert ids

    result = git_stage_batch(
        "include",
        "--to",
        "focused-metadata",
        "--line",
        ",".join(ids),
        "--as-stdin",
        "--no-auto-advance",
        input_text=replacement,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    batch_file = subprocess.run(
        [
            "git",
            "show",
            "refs/git-stage-batch/batches/focused-metadata:README.md",
        ],
        cwd=functional_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert batch_file == baseline.replace("A test project.", replacement.rstrip())
    assert path.read_text() == working
    assert subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=functional_repo,
        check=False,
    ).returncode == 0
