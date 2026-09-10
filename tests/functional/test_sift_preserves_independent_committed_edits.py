"""Sifting an applied batch must not adopt an unrelated committed edit."""

import subprocess

from .conftest import git_stage_batch


def test_sift_applied_batch_preserves_independent_committed_edit(functional_repo):
    path = functional_repo / "objects.txt"
    baseline = "scope\nalpha\nbeta\ngamma\ndelta\nepsilon\nzeta\nowner\n"
    path.write_text(baseline)
    subprocess.run(["git", "add", path.name], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add object list"], check=True, capture_output=True)

    path.write_text(baseline + "authority\n")
    git_stage_batch("start", "--no-auto-advance")
    git_stage_batch("discard", "--to", "authority", "--file", path.name)
    git_stage_batch("stop")

    renamed = baseline.replace("scope\n", "outputs\n")
    path.write_text(renamed)
    subprocess.run(["git", "add", path.name], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Rename output list"], check=True, capture_output=True)
    git_stage_batch("apply", "--from", "authority")
    expected = renamed + "authority\n"
    assert path.read_text() == expected

    git_stage_batch("sift", "--from", "authority", "--to", "remaining")
    assert path.read_text() == expected
    git_stage_batch("apply", "--from", "remaining")
    assert path.read_text() == expected
