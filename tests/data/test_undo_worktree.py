"""Tests for scoped undo worktree capture."""

from __future__ import annotations

import subprocess

from git_stage_batch.data import undo_worktree


def _initialize_repository(tmp_path, monkeypatch):
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.chdir(repository)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
    )
    return repository


def test_regular_path_capture_uses_bulk_git_queries(tmp_path, monkeypatch):
    """Normal files should share index, HEAD, and hash-object processes."""
    repository = _initialize_repository(tmp_path, monkeypatch)
    paths = ["one.txt", "two.txt", "nested/three.txt"]
    for path in paths:
        file_path = repository / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(f"{path}\n")
    subprocess.run(["git", "add", "--", *paths], check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add files"],
        check=True,
        capture_output=True,
    )

    git_calls = []
    original_run_git_command = undo_worktree.run_git_command

    def recording_run_git_command(args, **kwargs):
        git_calls.append(tuple(args))
        return original_run_git_command(args, **kwargs)

    hash_batches = []
    original_create_blobs = undo_worktree.create_git_blobs_from_paths

    def recording_create_blobs(file_paths):
        file_paths = tuple(file_paths)
        hash_batches.append(file_paths)
        return original_create_blobs(file_paths)

    monkeypatch.setattr(undo_worktree, "run_git_command", recording_run_git_command)
    monkeypatch.setattr(
        undo_worktree,
        "create_git_blobs_from_paths",
        recording_create_blobs,
    )

    entries = undo_worktree.snapshot_worktree_paths(paths)

    assert [entry["path"] for entry in entries] == sorted(paths)
    assert [call[0] for call in git_calls] == ["ls-files", "ls-tree"]
    assert len(hash_batches) == 1
    assert set(hash_batches[0]) == {repository / path for path in paths}


def test_scoped_capture_does_not_inspect_unrelated_dirty_paths(tmp_path, monkeypatch):
    """Capture cost should depend on declared scope, not worktree dirtiness."""
    repository = _initialize_repository(tmp_path, monkeypatch)
    target = repository / "target.txt"
    target.write_text("target\n")
    subprocess.run(["git", "add", "target.txt"], check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add target"],
        check=True,
        capture_output=True,
    )
    for index in range(100):
        (repository / f"unrelated-{index}.txt").write_text("private\n")

    entries = undo_worktree.snapshot_worktree_paths(["target.txt"])

    assert [entry["path"] for entry in entries] == ["target.txt"]
