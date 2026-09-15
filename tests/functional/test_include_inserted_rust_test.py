"""Selecting an inserted Rust test preserves its neighboring braces and attribute."""

import re
import subprocess
from pathlib import Path

from .conftest import git_stage_batch


FIXTURES = Path(__file__).parent / "fixtures"


def _git(*args):
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


def _fixture(name):
    return (FIXTURES / f"pronk_source_geometry_{name}.rs").read_text()


def test_include_first_inserted_test_after_staging_an_earlier_hunk(functional_repo):
    # Reduced from Pronk's checked-source-geometry split. The earlier decoder
    # replacements and second new test must remain unstaged. Repeated closing
    # braces and test attributes must not move the selected insertion backward.
    path = functional_repo / "source.rs"
    path.write_text(_fixture("before"))
    _git("add", "source.rs")
    _git("commit", "-m", "Record source tests")
    live = _fixture("working")
    path.write_text(live)

    git_stage_batch("start")
    shown = git_stage_batch("show", "--file", "source.rs", "--page", "all").stdout
    assert "Change 1/4   lines 1–19" in shown
    git_stage_batch("include", "--line", "1-19")
    shown = git_stage_batch("show", "--file", "source.rs", "--page", "all").stdout
    start = re.search(r"\[#(\d+)\]\s+\+\s+fn geometry_preserves_", shown)
    end = re.search(r"\[#(\d+)\]\s+\+\s+fn source_result_decodes_", shown)
    assert start and end, shown
    git_stage_batch("include", "--line", f"{start[1]}-{int(end[1]) - 1}")

    assert path.read_text() == live
    assert _git("show", ":source.rs") == _fixture("expected")
