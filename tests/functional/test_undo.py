"""Functional tests for undo support."""

import subprocess

from .conftest import git_stage_batch, get_staged_diff


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True)


def _commit_file(path, content: str) -> None:
    path.write_text(content)
    _git("add", str(path))
    _git("commit", "-m", f"Add {path.name}")


def test_undo_with_empty_stack_fails(repo_with_changes):
    """Undo reports an error when no checkpoint exists."""
    git_stage_batch("start")

    result = git_stage_batch("undo", check=False)

    assert result.returncode != 0
    assert "Nothing to undo" in result.stderr
