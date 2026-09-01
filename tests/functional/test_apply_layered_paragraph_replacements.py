"""Regression coverage for replaying layered paragraph replacements."""

import subprocess

from .conftest import git_stage_batch


def _changed_ids(view):
    return [
        line.split("[#", 1)[1].split("]", 1)[0]
        for line in view.splitlines()
        if "[#" in line
    ]


def _peel_replacement(path, batch, target):
    path.write_text(target)
    git_stage_batch("new", batch, "--note", f"Record {batch} wording")
    view = git_stage_batch(
        "show", "--file", path.name, "--page", "all"
    ).stdout
    ids = _changed_ids(view)
    assert ids
    result = git_stage_batch(
        "discard",
        "--to",
        batch,
        "--line",
        ",".join(ids),
        "--no-auto-advance",
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_apply_layered_paragraph_replacements(functional_repo):
    """A later replay must augment an already replayed paragraph rewrite."""
    path = functional_repo / "contract.md"
    baseline = (
        "## Grant lifetime test\n"
        "\n"
        "Coverage includes delegated-helper lifetime, master\n"
        "drop/reacquire, residual-frame denial for a replacement compositor, and\n"
        "revocation including creator-close and final-holder cleanup.\n"
        "\n"
        "## Graphical testing\n"
    )
    fdinfo = (
        "## Grant lifetime test\n"
        "\n"
        "Coverage includes delegated-helper lifetime, master\n"
        "drop/reacquire, residual-frame denial for a replacement compositor, grant\n"
        "`fdinfo`, and revocation including creator-close and final-holder cleanup.\n"
        "\n"
        "## Graphical testing\n"
    )
    ownership = (
        "## Grant lifetime test\n"
        "\n"
        "Coverage includes delegated-helper lifetime, master\n"
        "drop/reacquire, residual-frame denial through CastKMS pixel-export paths,\n"
        "capture-destination ownership, and revocation including\n"
        "creator-close and final-holder cleanup.\n"
        "\n"
        "## Graphical testing\n"
    )
    expected = (
        "## Grant lifetime test\n"
        "\n"
        "Coverage includes delegated-helper lifetime, master\n"
        "drop/reacquire, residual-frame denial through CastKMS pixel-export paths,\n"
        "capture-destination ownership, grant `fdinfo`, and revocation including\n"
        "creator-close and final-holder cleanup.\n"
        "\n"
        "## Graphical testing\n"
    )

    path.write_text(baseline)
    subprocess.run(
        ["git", "add", path.name],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add contract fixture"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )

    path.write_text(fdinfo)
    git_stage_batch("start", "--no-auto-advance")
    _peel_replacement(path, "fdinfo", fdinfo)
    assert path.read_text() == baseline
    _peel_replacement(path, "ownership", ownership)
    assert path.read_text() == baseline

    first = git_stage_batch(
        "apply", "--from", "ownership", "--file", path.name, check=False
    )
    assert first.returncode == 0, first.stderr
    second = git_stage_batch(
        "apply", "--from", "fdinfo", "--file", path.name, check=False
    )
    assert second.returncode == 0, second.stderr
    assert path.read_text() == expected
