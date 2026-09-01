"""Regression coverage for negated apply-from file scopes."""

import subprocess

from .conftest import git_stage_batch


def test_apply_from_batch_files_skips_incompatible_negated_nested_path(
    functional_repo,
):
    """An excluded nested file must not participate in merge validation."""
    included = functional_repo / "included.txt"
    docs = functional_repo / "docs"
    docs.mkdir(exist_ok=True)
    excluded = docs / "excluded.md"

    included.write_text("included baseline\n")
    excluded.write_text("excluded baseline\n")
    git_stage_batch("stop", check=False)

    subprocess.run(
        ["git", "add", included.name, "docs/excluded.md"],
        cwd=functional_repo,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "add apply fixtures"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
        text=True,
    )

    included.write_text("included batch target\n")
    excluded.write_text("excluded batch target\n")
    git_stage_batch("start", "--no-auto-advance")
    git_stage_batch(
        "include",
        "--to",
        "saved",
        "--files",
        "included.txt",
        "docs/excluded.md",
    )
    git_stage_batch("discard", "--from", "saved", "--files", "**")

    excluded.write_text("incompatible live edit\n")
    result = git_stage_batch(
        "apply",
        "--from",
        "saved",
        "--files",
        "**",
        "!docs/excluded.md",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert included.read_text() == "included batch target\n"
    assert excluded.read_text() == "incompatible live edit\n"
