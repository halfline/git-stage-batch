"""Regression coverage for inverse replay beside unowned replacements."""

import subprocess

from .conftest import git_stage_batch


def _display_id_for_text(view, text):
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return matches[0].split("[#", 1)[1].split("]", 1)[0]


def test_discard_applied_insertion_beside_unowned_replacement(functional_repo):
    """Inverse replay must preserve an adjacent unselected replacement."""
    path = functional_repo / "Buildfile"
    baseline = "items := \\\n\tbase \\\n\tlegacy\n\nfooter\n"
    predecessor = "items := \\\n\tbase\n\nfooter\n"
    target = predecessor.replace(
        "\nfooter\n",
        "\nitems += feature\n\nfooter\n",
    )
    realized_predecessor = predecessor.replace(
        "\nfooter\n",
        "\n\nfooter\n",
    )
    path.write_text(baseline)
    subprocess.run(
        ["git", "add", "Buildfile"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add build list"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch(
        "show",
        "--file",
        "Buildfile",
        "--page",
        "all",
    ).stdout
    feature_id = _display_id_for_text(view, "items += feature")
    git_stage_batch(
        "discard",
        "--to",
        "feature",
        "--line",
        feature_id,
        "--no-auto-advance",
    )
    assert path.read_text() == realized_predecessor

    replay = git_stage_batch(
        "apply",
        "--from",
        "feature",
        "--file",
        "Buildfile",
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == target

    inverse = git_stage_batch(
        "discard",
        "--from",
        "feature",
        "--file",
        "Buildfile",
        check=False,
    )
    assert inverse.returncode == 0, inverse.stderr
    assert path.read_text() == realized_predecessor
