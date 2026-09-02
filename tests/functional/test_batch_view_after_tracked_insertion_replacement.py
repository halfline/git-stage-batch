"""Regression coverage for replacements inside a tracked-file insertion."""

import subprocess

from .conftest import git_stage_batch


def _display_id_for_text(view, text):
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def test_layered_replacement_in_tracked_insertion_stays_narrow(functional_repo):
    """A replacement in a large insertion must not claim the surrounding file."""
    path = functional_repo / "guide.md"
    baseline = "# Project\n\n## Build\n\nBuild it.\n"
    target = (
        "# Project\n"
        "\n"
        "Introduction.\n"
        "\n"
        "## How a session works\n"
        "\n"
        "1. The card appears with `max_outputs` disconnected virtual\n"
        "   connectors. The upper bound is feature-dependent because\n"
        "   cursor planes and writeback consume object-mask slots.\n"
        "   Invalid combinations are rejected before creation. Nothing is\n"
        "   plugged in yet.\n"
        "2. The agent attaches a monitor.\n"
        "   Lab helper guidance.\n"
        "3. The compositor renders.\n"
        "\n"
        "## Build\n"
        "\n"
        "Build it.\n"
    )
    after_upper = target.replace("   Lab helper guidance.\n", "")
    predecessor = after_upper.replace(
        "1. The card appears with `max_outputs` disconnected virtual\n"
        "   connectors. The upper bound is feature-dependent because\n"
        "   cursor planes and writeback consume object-mask slots.\n"
        "   Invalid combinations are rejected before creation. Nothing is\n"
        "   plugged in yet.\n",
        "1. The card appears with one disconnected virtual connector.\n"
        "   Nothing is plugged in yet.\n",
    )

    path.write_text(baseline)
    subprocess.run(
        ["git", "add", path.name],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add guide baseline"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    upper = _display_id_for_text(view, "Lab helper guidance")
    peeled = git_stage_batch(
        "discard",
        "--to",
        "grant-launcher",
        "--line",
        str(upper),
        "--no-auto-advance",
        check=False,
    )
    assert peeled.returncode == 0, peeled.stderr
    assert path.read_text() == after_upper

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    first = _display_id_for_text(view, "1. The card appears")
    last = _display_id_for_text(view, "plugged in yet")
    replaced = git_stage_batch(
        "discard",
        "--to",
        "multi-output",
        "--line",
        f"{first}-{last}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=(
            "1. The card appears with one disconnected virtual connector.\n"
            "   Nothing is plugged in yet.\n"
        ),
        check=False,
    )
    assert replaced.returncode == 0, replaced.stderr
    assert path.read_text() == predecessor

    batch_view = git_stage_batch(
        "show",
        "--from",
        "multi-output",
        "--file",
        path.name,
        "--page",
        "all",
    ).stdout
    unrelated = [
        line
        for line in batch_view.splitlines()
        if "[#" in line
        and any(
            text in line
            for text in ("# Project", "Introduction.", "2. The agent", "## Build")
        )
    ]
    assert not unrelated, batch_view

    replay = git_stage_batch(
        "apply", "--from", "multi-output", "--file", path.name, check=False
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == after_upper
