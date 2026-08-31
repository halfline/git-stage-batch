"""Regression coverage for replaying a whole tracked-file batch after a peel."""

import subprocess

from .conftest import git_stage_batch
def test_whole_tracked_file_replay_keeps_surviving_baseline_deletions(
    functional_repo,
):
    """Replay must not resurrect baseline lines removed outside the prior peel."""
    path = functional_repo / "output.c"
    baseline = (
        "static void output_init(void)\n"
        "{\n"
        "    old_lock_init();\n"
        "    old_composer_lock_init();\n"
        "    old_queue = alloc_old_queue();\n"
        "}\n"
    )
    target = (
        "static void output_init(void)\n"
        "{\n"
        "    old_lock_init();\n"
        "    dispatch_lock_init();\n"
        "    capture_output_init();\n"
        "    dispatch_queue = alloc_dispatch_queue();\n"
        "}\n"
    )
    after_prior_peel = (
        "static void output_init(void)\n"
        "{\n"
        "    capture_output_init();\n"
        "}\n"
    )

    path.write_text(baseline)
    subprocess.run(["git", "add", path.name], cwd=functional_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add output fixture"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
        text=True,
    )
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")

    peeled = git_stage_batch(
        "discard",
        "--to",
        "dispatch-demand",
        "--file",
        path.name,
        "--no-auto-advance",
        check=False,
    )
    assert peeled.returncode == 0, peeled.stderr
    replay = git_stage_batch(
        "apply",
        "--from",
        "dispatch-demand",
        "--file",
        path.name,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == target

    path.write_text(after_prior_peel)
    sifted = git_stage_batch(
        "sift",
        "--from",
        "dispatch-demand",
        "--to",
        "dispatch-demand",
        check=False,
    )
    assert sifted.returncode == 0, sifted.stderr
    assert path.read_text() == after_prior_peel

    whole = git_stage_batch(
        "discard",
        "--to",
        "capture-init",
        "--file",
        path.name,
        check=False,
    )
    assert whole.returncode == 0, whole.stderr
    assert path.read_text() == baseline

    replay = git_stage_batch(
        "apply",
        "--from",
        "capture-init",
        "--file",
        path.name,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == after_prior_peel
