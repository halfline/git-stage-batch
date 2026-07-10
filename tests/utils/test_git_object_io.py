"""Tests for Git object IO helpers."""

import subprocess

import pytest

from git_stage_batch.utils.git_command import run_git_command
from git_stage_batch.utils.git_object_io import create_git_blobs_from_paths


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
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


def test_create_git_blobs_from_paths_hashes_path_bytes(temp_git_repo):
    """Path-based blob creation should store exact file bytes."""
    files = [
        temp_git_repo / "alpha.txt",
        temp_git_repo / "nested" / "beta.bin",
        temp_git_repo / "name,with,commas.txt",
    ]
    files[1].parent.mkdir()
    files[0].write_bytes(b"alpha\n")
    files[1].write_bytes(b"\x00\x01beta\n")
    files[2].write_bytes(b"comma\n")

    blobs = create_git_blobs_from_paths([files[0], files[1], files[0], files[2]])

    assert set(blobs) == set(files)
    for file_path in files:
        result = run_git_command(
            ["cat-file", "blob", blobs[file_path]],
            text_output=False,
            requires_index_lock=False,
        )
        assert result.stdout == file_path.read_bytes()
