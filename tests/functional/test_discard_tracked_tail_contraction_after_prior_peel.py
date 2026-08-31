"""Regression coverage for a tracked tail contraction after an earlier peel."""

import subprocess

from .conftest import git_stage_batch


def _display_id_for_text(view, text):
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def test_tracked_tail_contraction_survives_prior_same_file_peel(functional_repo):
    """Restoring a tracked list tail must work after peeling an earlier entry."""
    path = functional_repo / "Kbuild"
    baseline = (
        "objects := \\\n"
        "\tbase.o \\\n"
        "\tluts.o\n"
        "\n"
        "flags += -Iinclude\n"
    )
    target = (
        "objects := \\\n"
        "\tbase.o \\\n"
        "\tother.o \\\n"
        "\tcapture_job.o \\\n"
        "\tluts.o \\\n"
        "\tsnapshot.o\n"
        "\n"
        "flags += -Iinclude\n"
    )
    after_prior_peel = target.replace("\tcapture_job.o \\\n", "")
    predecessor = baseline.replace("\tbase.o \\\n", "\tbase.o \\\n\tother.o \\\n")

    path.write_text(baseline)
    subprocess.run(
        ["git", "add", path.name],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add module build list"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    capture_job = _display_id_for_text(view, "capture_job.o")
    prior = git_stage_batch(
        "discard",
        "--to",
        "deferred-capture",
        "--line",
        str(capture_job),
        "--no-auto-advance",
        check=False,
    )
    assert prior.returncode == 0, prior.stderr
    assert path.read_text() == after_prior_peel

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    old_luts = _display_id_for_text(view, "- \tluts.o")
    new_luts = _display_id_for_text(view, "+ \tluts.o")
    snapshot = _display_id_for_text(view, "snapshot.o")
    assert old_luts < new_luts < snapshot

    contraction = git_stage_batch(
        "discard",
        "--to",
        "frame-snapshot",
        "--line",
        f"{old_luts}-{snapshot}",
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text="\tluts.o\n",
        check=False,
    )
    assert contraction.returncode == 0, contraction.stderr
    assert path.read_text() == predecessor

    replay = git_stage_batch(
        "apply",
        "--from",
        "frame-snapshot",
        "--file",
        path.name,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == after_prior_peel
