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
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils import repository_path as repository_path_module


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


def test_apply_git_file_mode_refuses_symlink_without_changing_referent(
    temp_git_repo,
):
    """Executable-bit updates must never follow a worktree symlink."""
    target_file = temp_git_repo / "target.txt"
    link_path = temp_git_repo / "linked.txt"
    target_file.write_text("target\n")
    target_file.chmod(0o644)
    try:
        os.symlink(target_file.name, link_path)
    except OSError as error:
        pytest.skip(f"symlink creation failed: {error}")

    with pytest.raises(CommandError, match="Cannot safely apply Git file mode"):
        apply_git_file_mode(link_path, "100755")

    assert stat.S_IMODE(target_file.stat().st_mode) == 0o644
    assert link_path.is_symlink()


def test_apply_git_file_mode_refuses_symlinked_parent_without_changing_referent(
    temp_git_repo,
):
    """Executable-bit updates must not traverse a worktree parent symlink."""
    outside_directory = temp_git_repo.parent / "outside"
    outside_directory.mkdir()
    target_file = outside_directory / "target.txt"
    target_file.write_text("target\n")
    target_file.chmod(0o644)
    linked_parent = temp_git_repo / "linked-parent"
    try:
        os.symlink(outside_directory, linked_parent, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation failed: {error}")

    with pytest.raises(CommandError, match="Cannot safely apply Git file mode"):
        apply_git_file_mode(linked_parent / target_file.name, "100755")

    assert stat.S_IMODE(target_file.stat().st_mode) == 0o644
    assert linked_parent.is_symlink()


def test_apply_git_file_mode_uses_write_descriptor_when_read_is_denied(
    temp_git_repo,
    monkeypatch,
):
    """Mode restoration should still work for a write-only regular file."""
    target_file = temp_git_repo / "write-only.txt"
    target_file.write_text("content\n")
    target_file.chmod(0o200)
    original_open = repository_path_module.os.open
    attempted_access_modes = []

    def open_with_read_denied(path, flags, *, dir_fd=None):
        if path == target_file.name and dir_fd is not None:
            access_mode = flags & os.O_ACCMODE
            attempted_access_modes.append(access_mode)
            if access_mode == os.O_RDONLY:
                raise PermissionError("read access denied")
        if dir_fd is None:
            return original_open(path, flags)
        return original_open(path, flags, dir_fd=dir_fd)

    monkeypatch.setattr(repository_path_module.os, "open", open_with_read_denied)

    apply_git_file_mode(target_file, "100755")

    assert attempted_access_modes == [os.O_RDONLY, os.O_WRONLY]
    assert stat.S_IMODE(target_file.stat().st_mode) == 0o311
