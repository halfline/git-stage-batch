"""Regression coverage for replacing an unchanged tracked file."""

import subprocess

from .conftest import git_stage_batch


def test_include_file_as_stdin_stages_unchanged_tracked_path(functional_repo):
    """Explicit replacement text should make a clean path selectable."""
    path = functional_repo / "README.md"
    original = path.read_text()
    replacement = "# Test Project\n\nUpdated from historical content.\n"

    # Keep a live review session active without changing the replacement path.
    (functional_repo / "src" / "utils.py").write_text(
        "def helper():\n"
        "    return 43\n"
    )
    git_stage_batch("start", "--no-auto-advance")

    result = git_stage_batch(
        "include",
        "--file",
        path.name,
        "--as-stdin",
        "--no-auto-advance",
        input_text=replacement,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    staged = subprocess.run(
        ["git", "show", f":{path.name}"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert staged == replacement
    assert path.read_text() == original
