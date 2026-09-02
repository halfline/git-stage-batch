"""Live file-review filtering by display-line ID."""

import re

from .conftest import git_stage_batch


def _display_id_for_text(view: str, text: str) -> int:
    for line in view.splitlines():
        if text not in line:
            continue
        match = re.search(r"\[#(\d+)\]", line)
        if match:
            return int(match.group(1))
    raise AssertionError(f"no display ID for {text!r} in:\n{view}")


def test_show_live_file_line_filters_to_requested_display_id(functional_repo):
    """``show --file --line`` must not silently show the first review page."""
    path = functional_repo / "long-added.txt"
    path.write_text("".join(f"line {number:02d}\n" for number in range(1, 81)))

    git_stage_batch("start", "--no-auto-advance")
    full_view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    requested_id = _display_id_for_text(full_view, "line 72")

    filtered_view = git_stage_batch(
        "show", "--file", path.name, "--line", str(requested_id)
    ).stdout

    assert f"[#{requested_id}]" in filtered_view
    assert "line 72" in filtered_view
    assert "line 01" not in filtered_view
    assert path.read_text() == "".join(
        f"line {number:02d}\n" for number in range(1, 81)
    )
