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


def test_special_added_and_deleted_paths(functional_repo):
    """Added and deleted special paths retain their complete names."""
    deleted_name = 'deleted"name.txt'
    deleted_path = functional_repo / deleted_name
    deleted_path.write_text("deleted\n")
    subprocess.run(["git", "add", "--", deleted_name], check=True, cwd=functional_repo)
    subprocess.run(["git", "commit", "-m", "Add deleted path"], check=True, cwd=functional_repo)
    deleted_path.unlink()

    added_name = "added\nname.txt"
    (functional_repo / added_name).write_text("added\n")

    git_stage_batch("start")
    git_stage_batch("include", "--file", added_name)
    git_stage_batch("include", "--file", deleted_name)

    staged_names = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "-z"],
        check=True,
        cwd=functional_repo,
        capture_output=True,
    ).stdout.split(b"\0")
    assert os.fsencode(added_name) in staged_names
    assert os.fsencode(deleted_name) in staged_names


def test_special_path_rename_and_binary_include(functional_repo):
    """Quoted rename metadata and binary headers retain canonical paths."""
    old_name = "old b/name.txt"
    new_name = "new\nname.txt"
    old_path = functional_repo / old_name
    old_path.parent.mkdir(parents=True)
    old_path.write_text("content\n")
    binary_name = 'binary"name.bin'
    binary_path = functional_repo / binary_name
    binary_path.write_bytes(b"old\0bytes")
    subprocess.run(
        ["git", "add", "--", old_name, binary_name],
        check=True,
        cwd=functional_repo,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add special changes"],
        check=True,
        cwd=functional_repo,
    )
    old_path.rename(functional_repo / new_name)
    binary_path.write_bytes(b"new\0bytes")

    git_stage_batch("start")
    git_stage_batch("include", "--file", new_name)
    git_stage_batch("include", "--file", binary_name)

    staged_names = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "-z"],
        check=True,
        cwd=functional_repo,
        capture_output=True,
    ).stdout.split(b"\0")
    assert os.fsencode(new_name) in staged_names
    assert os.fsencode(binary_name) in staged_names
