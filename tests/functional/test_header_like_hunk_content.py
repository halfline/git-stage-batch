"""Functional coverage for changed content resembling patch headers."""

import re
import subprocess

from .conftest import git_stage_batch


def _prepare_header_like_change(repo):
    path = repo / "header-like.txt"
    path.write_text("before\n--old\nafter\n")
    subprocess.run(
        ["git", "add", path.name],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add header-like content"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    path.write_text("before\n++new\nafter\n")
    return path


def _displayed_line_ids(output: str) -> str:
    line_ids = re.findall(r"\[#(\d+)\]", output)
    assert line_ids
    return ",".join(line_ids)


def test_start_show_and_include_header_like_change(functional_repo):
    """Header-looking replacement content can be displayed and included."""
    path = _prepare_header_like_change(functional_repo)

    git_stage_batch("start")
    shown = git_stage_batch("show")
    assert "--old" in shown.stdout
    assert "++new" in shown.stdout

    git_stage_batch("include", "--line", _displayed_line_ids(shown.stdout))

    staged = subprocess.run(
        ["git", "show", f":{path.name}"],
        check=True,
        cwd=functional_repo,
        capture_output=True,
        text=True,
    )
    assert staged.stdout == "before\n++new\nafter\n"


def test_discard_header_like_change(functional_repo):
    """Header-looking replacement content can be discarded end to end."""
    path = _prepare_header_like_change(functional_repo)

    git_stage_batch("start")
    shown = git_stage_batch("show")
    git_stage_batch("discard", "--line", _displayed_line_ids(shown.stdout))

    assert path.read_text() == "before\n--old\nafter\n"
