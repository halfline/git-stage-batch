"""Regression coverage for extending a transformed batch after another peel."""

from .conftest import git_stage_batch


def _display_id_for_text(view, text):
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def test_replacement_extends_whole_file_transform_after_independent_peel(
    functional_repo,
):
    """The initial whole-file transform stores both untracked snapshots."""
    path = functional_repo / "tool.c"
    target = (
        "caps = ASYNC_TX |\n"
        "       RX_INJECT |\n"
        "       TRANSPORT_STATE |\n"
        "       EDID;\n"
        "request path\n"
        "tail\n"
    )
    predecessor = target.replace("request path\n", "")
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "tool.c", "--page", "all").stdout
    first = _display_id_for_text(view, "caps = ASYNC_TX")
    last = _display_id_for_text(view, "tail")
    transformed = git_stage_batch(
        "discard",
        "--to",
        "transmit-request",
        "--line",
        f"{first}-{last}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=predecessor,
        check=False,
    )
    assert transformed.returncode == 0, transformed.stderr
    assert path.read_text() == predecessor
