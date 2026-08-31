"""Regression coverage for the first narrow replacement in an added file."""

from .conftest import git_stage_batch


def _display_id_for_text(view, text):
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def test_first_added_file_replacement_does_not_claim_unselected_lines(
    functional_repo,
):
    """Replacing one line must not make the batch own the whole added file."""
    path = functional_repo / "capture-grants.md"
    target = (
        "# Capture grants\n"
        "\n"
        "| Right | Operations |\n"
        "|---|---|\n"
        "| `MANAGE_ATTACHMENT` | Attach or detach a remote monitor. |\n"
        "\n"
        "End.\n"
    )
    predecessor = target.replace(
        "Attach or detach a remote monitor.",
        "Attach a remote monitor.",
    )
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    rights = _display_id_for_text(view, "Attach or detach")
    result = git_stage_batch(
        "discard",
        "--to",
        "monitor-detach",
        "--file",
        path.name,
        "--line",
        str(rights),
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text="| `MANAGE_ATTACHMENT` | Attach a remote monitor. |\n",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert path.read_text() == predecessor

    batch_view = git_stage_batch(
        "show",
        "--from",
        "monitor-detach",
        "--file",
        path.name,
        "--page",
        "all",
    ).stdout
    selected_heading = [
        line
        for line in batch_view.splitlines()
        if "[#" in line and "# Capture grants" in line
    ]
    selected_footer = [
        line for line in batch_view.splitlines() if "[#" in line and "End." in line
    ]
    selected_ids = [
        line.split("[#", 1)[1].split("]", 1)[0]
        for line in batch_view.splitlines()
        if "[#" in line
    ]
    revised_predecessor = predecessor.replace("# Capture grants", "# Revised grants")
    revised_target = target.replace("# Capture grants", "# Revised grants")
    path.write_text(revised_predecessor)
    replay = git_stage_batch(
        "apply",
        "--from",
        "monitor-detach",
        "--file",
        path.name,
        "--line",
        ",".join(selected_ids),
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == revised_target
    assert not selected_heading, batch_view
    assert not selected_footer, batch_view
