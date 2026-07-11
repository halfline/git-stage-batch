"""End-to-end coverage for Git path normalization."""

import os
import subprocess

import pytest

from .conftest import git_stage_batch


SPECIAL_PATHS = [
    "space name.txt",
    "quote\"name.txt",
    "tab\tname.txt",
    "line\nname.txt",
    "old b/component.txt",
    "trailing-space.txt ",
]
if os.name == "posix":
    SPECIAL_PATHS.append(os.fsdecode(b"non-utf8-\xff.txt"))


@pytest.mark.parametrize("file_path", SPECIAL_PATHS)
def test_special_path_include_ignores_diff_configuration(functional_repo, file_path):
    """Every filesystem pathname survives discovery, display, and inclusion."""
    path = functional_repo / file_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"old\n")
    subprocess.run(
        ["git", "add", "--", file_path],
        check=True,
        cwd=functional_repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add special path"],
        check=True,
        cwd=functional_repo,
        capture_output=True,
    )
    path.write_bytes(b"new\n")
    subprocess.run(
        ["git", "config", "diff.noprefix", "true"],
        check=True,
        cwd=functional_repo,
    )
    subprocess.run(
        ["git", "config", "diff.external", "false"],
        check=True,
        cwd=functional_repo,
    )
    subprocess.run(
        ["git", "config", "core.quotePath", "false"],
        check=True,
        cwd=functional_repo,
    )

    git_stage_batch("start")
    git_stage_batch("show", "--file", file_path)
    git_stage_batch("include", "--file", file_path)

    staged = subprocess.run(
        ["git", "show", f":{file_path}"],
        check=True,
        cwd=functional_repo,
        capture_output=True,
    )
    assert staged.stdout == b"new\n"
