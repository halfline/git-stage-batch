"""Functional coverage for isolated index staging during include --file."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

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


@pytest.mark.parametrize("scope_option", ("--file", "--files"))
def test_failed_whole_file_include_preserves_exact_unrelated_index_state(
    functional_repo,
    scope_option,
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

    result = git_stage_batch("include", scope_option, "target.txt", check=False)

    assert result.returncode != 0
    assert "filter 'fail' failed" in result.stderr
    assert "No changes" not in result.stderr
    assert _index_state(functional_repo) == before


@pytest.mark.parametrize("scope_option", ("--file", "--files"))
def test_failed_whole_file_include_blocks_concurrent_git_update(
    functional_repo,
    scope_option,
):
    """A clean-filter failure must retain the real-index lock throughout."""
    (functional_repo / ".gitattributes").write_text("target.txt filter=fail\n")
    (functional_repo / "target.txt").write_text("base\n")
    (functional_repo / "concurrent.txt").write_text("base\n")
    _git(functional_repo, "config", "filter.fail.clean", "cat")
    _git(functional_repo, "config", "filter.fail.smudge", "cat")
    _git(functional_repo, "config", "filter.fail.required", "true")
    _git(
        functional_repo,
        "add",
        ".gitattributes",
        "target.txt",
        "concurrent.txt",
    )
    _git(functional_repo, "commit", "-m", "add filtered target")

    (functional_repo / "target.txt").write_text("transaction\n")
    (functional_repo / "concurrent.txt").write_text("external\n")
    git_stage_batch("start", "--no-auto-advance")
    _git(
        functional_repo,
        "config",
        "filter.fail.clean",
        "if test -z \"$GIT_INDEX_FILE\"; then cat; exit 0; fi; "
        "unset GIT_INDEX_FILE; "
        "if test -e \"$(git rev-parse --git-path index).lock\"; "
        "then lock=locked; else lock=unlocked; fi; "
        "if git add concurrent.txt; then status=published; else status=blocked; fi; "
        "printf '%s-%s\\n' \"$lock\" \"$status\" >> filter-result; exit 1",
    )

    result = git_stage_batch("include", scope_option, "target.txt", check=False)

    assert result.returncode != 0
    assert "clean filter" in result.stderr
    assert "fail" in result.stderr
    assert "Traceback" not in result.stderr
    filter_results = (functional_repo / "filter-result").read_text().splitlines()
    assert filter_results
    assert set(filter_results) == {"locked-blocked"}
    staged_paths = _git(
        functional_repo,
        "diff",
        "--cached",
        "--name-only",
        "-z",
    ).stdout.split(b"\0")
    assert b"concurrent.txt" not in staged_paths
    assert b"target.txt" not in staged_paths
