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


def test_nested_repository_capture_uses_repository_relative_paths_from_subdirectory(
    tmp_path,
    monkeypatch,
):
    """Nested Git commands resolve paths from the outer repository root."""
    repository = _initialize_repository(tmp_path, monkeypatch)
    nested = repository / "nested"
    nested.mkdir()
    subprocess.run(["git", "init"], cwd=nested, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=nested,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=nested,
        check=True,
        capture_output=True,
    )
    (nested / "file.txt").write_text("nested\n")
    subprocess.run(["git", "add", "file.txt"], cwd=nested, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Add nested file"],
        cwd=nested,
        check=True,
        capture_output=True,
    )
    nested_oid = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=nested,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subdirectory = repository / "elsewhere"
    subdirectory.mkdir()
    monkeypatch.chdir(subdirectory)

    entry = undo_worktree.snapshot_worktree_paths(["nested"])[0]

    assert entry["exists"] is True
    assert entry["worktree_oid"] == nested_oid


def test_gitlink_capture_uses_repository_relative_paths_from_subdirectory(
    tmp_path,
    monkeypatch,
):
    """Index and HEAD gitlink lookups run from the superproject root."""
    repository = _initialize_repository(tmp_path, monkeypatch)
    nested = repository / "nested"
    nested.mkdir()
    subprocess.run(["git", "init"], cwd=nested, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=nested,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=nested,
        check=True,
        capture_output=True,
    )
    (nested / "file.txt").write_text("nested\n")
    subprocess.run(["git", "add", "file.txt"], cwd=nested, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Add nested file"],
        cwd=nested,
        check=True,
        capture_output=True,
    )
    nested_oid = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=nested,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        [
            "git",
            "update-index",
            "--add",
            "--cacheinfo",
            "160000",
            nested_oid,
            "nested",
        ],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add gitlink"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    subdirectory = repository / "elsewhere"
    subdirectory.mkdir()
    monkeypatch.chdir(subdirectory)

    entry = undo_worktree.snapshot_worktree_paths(["nested"])[0]

    assert entry["kind"] == "gitlink"
    assert entry["index_oid"] == nested_oid
    assert entry["head_oid"] == nested_oid
    assert entry["archive"] is True
    assert entry["blob"]


def test_dirty_gitlink_capture_archives_exact_worktree_bytes(tmp_path, monkeypatch):
    """Dirty gitlinks retain content identity as well as commit identity."""
    repository = _initialize_repository(tmp_path, monkeypatch)
    nested = repository / "nested"
    nested.mkdir()
    subprocess.run(["git", "init"], cwd=nested, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=nested,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=nested,
        check=True,
    )
    nested_file = nested / "file.txt"
    nested_file.write_text("base\n")
    subprocess.run(["git", "add", "file.txt"], cwd=nested, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Add nested file"],
        cwd=nested,
        check=True,
        capture_output=True,
    )
    nested_oid = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=nested,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        [
            "git",
            "update-index",
            "--add",
            "--cacheinfo",
            "160000",
            nested_oid,
            "nested",
        ],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add gitlink"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    nested_file.write_text("first dirty state\n")

    first = undo_worktree.snapshot_worktree_paths(["nested"])[0]
    nested_file.write_text("second dirty state\n")
    second = undo_worktree.snapshot_worktree_paths(["nested"])[0]
    unchanged = undo_worktree.snapshot_worktree_paths(["nested"])[0]

    assert first["dirty"] is True
    assert first["archive"] is True
    assert first["blob"]
    assert second["blob"] != first["blob"]
    assert unchanged["blob"] == second["blob"]


def test_broken_embedded_repository_is_snapshotted_as_present(tmp_path, monkeypatch):
    """A present nested directory remains restorable even without a valid HEAD."""
    repository = _initialize_repository(tmp_path, monkeypatch)
    nested = repository / "broken"
    nested.mkdir()
    (nested / ".git").write_text("not a gitdir\n")
    (nested / "private.txt").write_text("keep me\n")

    entry = undo_worktree.snapshot_worktree_paths(["broken"])[0]

    assert entry["kind"] == "embedded-repo"
    assert entry["exists"] is True
    assert entry["worktree_oid"] is None
    assert entry["archive"] is True
    assert entry["blob"]
