"""Regression coverage for mutations after discontiguous file-review pages."""

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


def test_discard_accepts_ids_shown_on_discontiguous_pages(functional_repo):
    """Every ID shown by one multi-page review must remain selectable."""
    path = functional_repo / "long-added.txt"
    original = "".join(f"line {number:03d}\n" for number in range(1, 161))
    path.write_text(original)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch(
        "show", "--file", path.name, "--page", "1,3"
    ).stdout
    first_id = _display_id_for_text(view, "line 045")
    third_page_id = _display_id_for_text(view, "line 145")

    result = git_stage_batch(
        "discard",
        "--to",
        "selected-lines",
        "--line",
        f"{first_id},{third_page_id}",
        "--no-auto-advance",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "line 045\n" not in path.read_text()
    assert "line 145\n" not in path.read_text()
