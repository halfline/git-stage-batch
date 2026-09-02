"""Regression coverage for replaying a tracked multiline replacement."""

import subprocess

from .conftest import git_stage_batch


def _display_id_for_text(view: str, text: str) -> int:
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def _display_ids(view: str) -> list[int]:
    return [
        int(line.split("[#", 1)[1].split("]", 1)[0])
        for line in view.splitlines()
        if "[#" in line
    ]


def _index_text(repo, path) -> str:
    return subprocess.run(
        ["git", "show", f":{path.name}"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_include_tracked_multiline_replacement_removes_predecessor(
    functional_repo,
):
    """A full replay must not retain the replaced single-line suffix."""
    path = functional_repo / "driver.c"
    predecessor = (
        "static const struct driver driver = {\n"
        "\t.features = MODESET | ATOMIC,\n"
        "\t.ioctl = handle_ioctl,\n"
        "};\n"
    )
    after_inner = predecessor.replace(
        "\t.features = MODESET | ATOMIC,\n",
        "\t.features = MODESET | ATOMIC |\n"
        "\t\t    SYNCOBJ | SYNCOBJ_TIMELINE,\n",
    )
    target = after_inner.replace(
        "\t\t    SYNCOBJ | SYNCOBJ_TIMELINE,\n",
        "\t\t    SYNCOBJ | SYNCOBJ_TIMELINE |\n"
        "\t\t    CURSOR_HOTSPOT,\n",
    )

    path.write_text(predecessor)
    subprocess.run(
        ["git", "add", path.name],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add driver"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch(
        "show", "--file", path.name, "--page", "all"
    ).stdout
    ids = [
        _display_id_for_text(view, "SYNCOBJ_TIMELINE |"),
        _display_id_for_text(view, "CURSOR_HOTSPOT"),
    ]
    peeled = git_stage_batch(
        "discard",
        "--to",
        "cursor-hotspot",
        "--line",
        ",".join(str(display_id) for display_id in ids),
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text="\t\t    SYNCOBJ | SYNCOBJ_TIMELINE,\n",
        check=False,
    )
    assert peeled.returncode == 0, peeled.stderr
    assert path.read_text() == after_inner

    view = git_stage_batch(
        "show", "--file", path.name, "--page", "all"
    ).stdout
    inner_ids = _display_ids(view)
    neighbor = git_stage_batch(
        "discard",
        "--to",
        "inner-feature",
        "--line",
        f"{min(inner_ids)}-{max(inner_ids)}",
        "--no-auto-advance",
        check=False,
    )
    assert neighbor.returncode == 0, neighbor.stderr
    assert path.read_text() == predecessor
    git_stage_batch("stop")

    path.write_text(after_inner)
    subprocess.run(
        ["git", "add", path.name],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Modernize dispatch"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    (functional_repo / "future.txt").write_text("later concern\n")
    git_stage_batch("start", "--no-auto-advance")
    replay = git_stage_batch(
        "include",
        "--from",
        "cursor-hotspot",
        "--file",
        path.name,
        "--no-auto-advance",
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert _index_text(functional_repo, path) == target
