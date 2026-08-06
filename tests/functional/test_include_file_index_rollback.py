"""Functional coverage for isolated index staging during include --file."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

from git_stage_batch.utils.index_transaction import active_git_index_path

from .conftest import git_stage_batch


def _git(repo: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    return subprocess.run(
        ["git", *arguments],
        cwd=repo,
        env=environment,
        check=True,
        capture_output=True,
    )


def _index_state(repo: Path) -> tuple[bytes, bytes]:
    debug_state = _git(repo, "ls-files", "--stage", "--debug", "-z").stdout
    index_bytes = active_git_index_path(cwd=str(repo)).read_bytes()
    return index_bytes, debug_state


def test_failed_whole_file_include_preserves_exact_unrelated_index_state(
    functional_repo,
):
    """A failed clean filter must preserve flags and intent-to-add entries."""
    (functional_repo / ".gitattributes").write_text("target.txt filter=fail\n")
    (functional_repo / "target.txt").write_text("base\n")
    _git(functional_repo, "config", "filter.fail.clean", "cat")
    _git(functional_repo, "config", "filter.fail.smudge", "cat")
    _git(functional_repo, "config", "filter.fail.required", "true")
    _git(functional_repo, "add", ".gitattributes", "target.txt")
    _git(functional_repo, "commit", "-m", "add filtered target")

    (functional_repo / "target.txt").write_text("changed\n")
    (functional_repo / "unrelated.txt").write_text("intent\n")
    git_stage_batch("start", "--no-auto-advance")
    _git(
        functional_repo,
        "update-index",
        "--assume-unchanged",
        "src/utils.py",
    )
    _git(
        functional_repo,
        "update-index",
        "--skip-worktree",
        "src/main.py",
    )
    _git(functional_repo, "config", "filter.fail.clean", "false")
    before = _index_state(functional_repo)

    result = git_stage_batch("include", "--file", "target.txt", check=False)

    assert result.returncode != 0
    assert "filter 'fail' failed" in result.stderr
    assert "No changes" not in result.stderr
    assert _index_state(functional_repo) == before
