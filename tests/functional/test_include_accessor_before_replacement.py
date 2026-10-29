"""Regression coverage for an accessor inserted before a method replacement."""

from pathlib import Path
import re
import subprocess

import pytest

from .conftest import git_stage_batch


@pytest.mark.parametrize("selection_scope", ["file", "group"])
def test_include_line_ids_preserves_accessor_before_replacement(
    functional_repo, selection_scope,
):
    """Selecting a complete group must preserve its inserted lines' order."""
    fixture = Path(__file__).parent / "fixtures" / "accessor_before_replacement"
    path = functional_repo / "dbus.js"
    before = (fixture / "before.js").read_bytes()
    path.write_bytes(before)
    subprocess.run(
        ["git", "add", path.name],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add D-Bus service"],
        check=True,
        capture_output=True,
    )
    expected = (fixture / "after.js").read_bytes()
    path.write_bytes(expected)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    display_ids = [int(value) for value in re.findall(r"\[#(\d+)\]", view)]
    assert display_ids
    selected_expected = expected
    if selection_scope == "group":
        first = re.search(r"\[#(\d+)\]\s+-\s+_logActivity\(methodName\)", view)
        last = re.search(r"\[#(\d+)\]\s+\+\s+\};", view)
        assert first is not None and last is not None
        display_ids = list(range(int(first[1]) - 1, int(last[1]) + 1))
        body = b"        this._activityLog.push(entry);"
        selected_expected = (
            before[:before.index(b"\n    _logActivity(")]
            + expected[expected.index(b"    set onEnabledChanged("):expected.index(body)]
            + before[before.index(body):]
        )
    git_stage_batch(
        "include",
        "--file",
        path.name,
        "--line",
        f"{min(display_ids)}-{max(display_ids)}",
        "--no-auto-advance",
    )
    selected_content = subprocess.run(
        ["git", "show", f":{path.name}"],
        check=True,
        capture_output=True,
    ).stdout
    assert selected_content == selected_expected
    assert path.read_bytes() == expected

    subprocess.run(["git", "reset", "--quiet"], check=True, capture_output=True)
    git_stage_batch("again")
    git_stage_batch("include", "--file", path.name, "--no-auto-advance")
    whole_file_content = subprocess.run(
        ["git", "show", f":{path.name}"],
        check=True,
        capture_output=True,
    ).stdout
    assert whole_file_content == expected
    if selection_scope == "file":
        assert whole_file_content == selected_content
