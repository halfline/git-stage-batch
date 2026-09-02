"""Regression coverage for a paragraph replacement with a shared tail."""

import subprocess

from .conftest import git_stage_batch


def test_discard_replacement_keeps_shared_paragraph_tail(functional_repo):
    """A three-line batch replacement must not leak its last line live."""
    path = functional_repo / "README.md"
    baseline = (
        "Grant coverage:\n"
        "drop/reacquire, residual-frame denial for a replacement compositor, and\n"
        "revocation including creator-close and final-holder cleanup.\n"
        "\n"
        "## Next\n"
    )
    working = (
        "Grant coverage:\n"
        "drop/reacquire, residual-frame denial for a replacement compositor, grant\n"
        "`fdinfo`, and revocation including creator-close and final-holder cleanup.\n"
        "\n"
        "## Next\n"
    )
    replacement = (
        "drop/reacquire, residual-frame denial through pixel-export paths,\n"
        "capture-destination ownership, grant `fdinfo`, and revocation including\n"
        "creator-close and final-holder cleanup.\n"
    )
    expected_batch = "Grant coverage:\n" + replacement + "\n## Next\n"

    path.write_text(baseline)
    subprocess.run(
        ["git", "add", path.name],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add paragraph fixture"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    path.write_text(working)

    git_stage_batch("start", "--no-auto-advance")
    git_stage_batch("new", "coverage", "--note", "Capture coverage wording")
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
        "discard",
        "--to",
        "coverage",
        "--line",
        ",".join(ids),
        "--as-stdin",
        "--no-auto-advance",
        input_text=replacement,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    batch_file = subprocess.run(
        ["git", "show", "refs/git-stage-batch/batches/coverage:README.md"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert batch_file == expected_batch
    assert path.read_text() == baseline
