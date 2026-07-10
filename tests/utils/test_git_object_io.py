"""Tests for Git object IO helpers."""

import subprocess

import pytest

from git_stage_batch.data import undo_checkpoints
from git_stage_batch.utils.git_command import run_git_command
from git_stage_batch.utils.git_index import git_write_tree, temp_git_index
from git_stage_batch.utils.git_object_io import (
    create_git_blob,
    create_git_blobs_from_paths,
    read_git_blobs_as_bytes,
)


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


def test_read_git_blobs_as_bytes_accepts_revision_paths(temp_git_repo):
    """Batch object reads should support Git revision:path expressions."""
    file_path = temp_git_repo / "unicodé.txt"
    file_path.write_text("accented\n")
    subprocess.run(["git", "add", file_path.name], check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add unicode path"],
        check=True,
        capture_output=True,
    )

    blobs = read_git_blobs_as_bytes([f"HEAD:{file_path.name}"])

    assert blobs[f"HEAD:{file_path.name}"] == b"accented\n"


def test_directory_snapshot_hashes_normal_files_in_one_batch(
    temp_git_repo,
    monkeypatch,
):
    """Undo directory snapshots should not spawn one hash-object per file."""
    source_dir = temp_git_repo / "session"
    source_dir.mkdir()
    files = [
        source_dir / "one.txt",
        source_dir / "nested" / "two.txt",
        source_dir / "three.txt",
    ]
    files[1].parent.mkdir()
    for file_path in files:
        file_path.write_text(f"{file_path.name}\n")

    blob_sha = create_git_blob([b"snapshot\n"])
    calls = []

    def fake_create_git_blobs_from_paths(paths):
        paths = tuple(paths)
        calls.append(paths)
        return {path: blob_sha for path in paths}

    monkeypatch.setattr(
        undo_checkpoints,
        "create_git_blobs_from_paths",
        fake_create_git_blobs_from_paths,
    )

    with temp_git_index() as env:
        undo_checkpoints._add_directory_to_index(
            env,
            source_dir=source_dir,
            tree_prefix="session",
        )
        git_write_tree(env=env)

    assert calls == [tuple(sorted(files))]
