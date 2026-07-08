"""Tests for repository file-mode detection."""

import os
import subprocess
import stat

import pytest

from git_stage_batch.data.file_modes import (
    apply_git_file_mode,
    detect_file_mode,
    detect_file_mode_in_commit,
    detect_file_mode_from_root,
)


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        check=True,
        cwd=repo,
        capture_output=True,
    )

    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True, cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        check=True,
        cwd=repo,
        capture_output=True,
    )

    return repo


def test_detects_regular_file_mode(temp_git_repo):
    test_file = temp_git_repo / "regular.py"
    test_file.write_text("print('regular')\n")

    assert detect_file_mode("regular.py") == "100644"


def test_detects_executable_file_mode(temp_git_repo):
    test_file = temp_git_repo / "script.sh"
    test_file.write_text("#!/bin/sh\n")
    test_file.chmod(0o755)

    assert detect_file_mode("script.sh") == "100755"


def test_detects_symbolic_link_mode(temp_git_repo):
    target_file = temp_git_repo / "target.txt"
    link_path = temp_git_repo / "linked.txt"
    target_file.write_text("target\n")
    try:
        os.symlink("target.txt", link_path)
    except OSError as error:
        pytest.skip(f"symlink creation failed: {error}")

    assert detect_file_mode("linked.txt") == "120000"


def test_uses_index_mode_for_missing_worktree_path(temp_git_repo):
    script_file = temp_git_repo / "script.sh"
    script_file.write_text("#!/bin/sh\n")
    script_file.chmod(0o755)
    subprocess.run(["git", "add", "script.sh"], check=True, cwd=temp_git_repo)
    subprocess.run(
        ["git", "commit", "-m", "Add script"],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
    )
    script_file.unlink()

    assert detect_file_mode_from_root(temp_git_repo, "script.sh") == "100755"


def test_defaults_to_regular_file_mode_for_unknown_path(temp_git_repo):
    assert detect_file_mode_from_root(temp_git_repo, "missing.txt") == "100644"


def test_detects_file_mode_in_commit_tree(temp_git_repo):
    script_file = temp_git_repo / "script.sh"
    script_file.write_text("#!/bin/sh\n")
    script_file.chmod(0o755)
    subprocess.run(["git", "add", "script.sh"], check=True, cwd=temp_git_repo)
    subprocess.run(
        ["git", "commit", "-m", "Add script"],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
    )

    assert detect_file_mode_in_commit("HEAD", "script.sh") == "100755"
    assert detect_file_mode_in_commit("HEAD", "missing.txt") is None


def test_applies_git_file_mode_to_worktree_path(temp_git_repo):
    target_file = temp_git_repo / "target.txt"
    target_file.write_text("content\n")
    target_file.chmod(0o644)

    apply_git_file_mode(target_file, "100755")

    executable_bits = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    assert target_file.stat().st_mode & executable_bits == executable_bits

    apply_git_file_mode(target_file, "100644")

    assert target_file.stat().st_mode & executable_bits == 0
