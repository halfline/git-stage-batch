"""Regression coverage for batch review after a tracked-line contraction."""

import subprocess

from .conftest import git_stage_batch


def _display_id_for_text(view, text):
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def test_batch_view_omits_contraction_predecessor_from_target_context(
    functional_repo,
):
    """A two-to-one replacement must not render its predecessor twice."""
    path = functional_repo / "driver.c"
    baseline = (
        "static const struct driver driver = {\n"
        "    .features = DRIVER_MODESET,\n"
        "    .ioctls = ioctls,\n"
        "};\n"
    )
    target = (
        "static const struct driver driver = {\n"
        "    .features = DRIVER_MODESET |\n"
        "                DRIVER_SYNCOBJ | DRIVER_SYNCOBJ_TIMELINE |\n"
        "                DRIVER_CURSOR_HOTSPOT,\n"
        "    .ioctls = ioctls,\n"
        "};\n"
    )
    predecessor = target.replace(
        "                DRIVER_SYNCOBJ | DRIVER_SYNCOBJ_TIMELINE |\n"
        "                DRIVER_CURSOR_HOTSPOT,\n",
        "                DRIVER_SYNCOBJ | DRIVER_SYNCOBJ_TIMELINE,\n",
    )

    path.write_text(baseline)
    subprocess.run(
        ["git", "add", path.name],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add driver baseline"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    syncobj = _display_id_for_text(
        view, "DRIVER_SYNCOBJ | DRIVER_SYNCOBJ_TIMELINE |"
    )
    hotspot = _display_id_for_text(view, "DRIVER_CURSOR_HOTSPOT")
    result = git_stage_batch(
        "discard",
        "--to",
        "cursor-plane-snapshot",
        "--file",
        path.name,
        "--line",
        f"{syncobj}-{hotspot}",
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text="                DRIVER_SYNCOBJ | DRIVER_SYNCOBJ_TIMELINE,\n",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert path.read_text() == predecessor

    batch_view = git_stage_batch(
        "show",
        "--from",
        "cursor-plane-snapshot",
        "--file",
        path.name,
        "--page",
        "all",
    ).stdout
    syncobj_lines = [
        line
        for line in batch_view.splitlines()
        if "DRIVER_SYNCOBJ | DRIVER_SYNCOBJ_TIMELINE" in line
    ]
    assert len(syncobj_lines) == 2, batch_view

    replay = git_stage_batch(
        "apply",
        "--from",
        "cursor-plane-snapshot",
        "--file",
        path.name,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == target
