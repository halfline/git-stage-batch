"""Tests for Git object IO helpers."""

import subprocess

import pytest

from git_stage_batch.data import undo_checkpoints
from git_stage_batch.utils.git_command import run_git_command
from git_stage_batch.utils.git_index import git_write_tree, temp_git_index
from git_stage_batch.utils.git_object_io import (
    create_git_blob,
    create_git_blobs_from_paths,
    get_empty_git_tree_object_id,
    read_git_blobs_as_bytes,
    resolve_git_objects,
    stream_git_blobs,
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
    subprocess.run(
        ["git", "add", "README.md"], check=True, cwd=repo, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        check=True,
        cwd=repo,
        capture_output=True,
    )

    return repo


def test_empty_tree_helper_returns_a_tree_object(temp_git_repo):
    """The shared empty-tree helper should create a repository tree object."""
    object_id = get_empty_git_tree_object_id()

    assert run_git_command(["cat-file", "-t", object_id]).stdout.strip() == "tree"
    assert run_git_command(["ls-tree", object_id]).stdout == ""


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


def test_read_git_blobs_as_bytes_can_ignore_non_blob_objects(temp_git_repo):
    """Tolerant batch readers should skip wrong object types without desync."""
    blob = create_git_blob([b"content\n"])
    tree = run_git_command(["write-tree"]).stdout.strip()
    missing = "HEAD:path does not exist"

    with pytest.raises(RuntimeError, match="Unexpected git cat-file"):
        read_git_blobs_as_bytes([tree, blob])

    blobs = read_git_blobs_as_bytes(
        [tree, missing, blob],
        ignore_non_blobs=True,
    )

    assert tree not in blobs
    assert missing not in blobs
    assert blobs[blob] == b"content\n"


def test_resolve_git_objects_canonicalizes_revision_paths(temp_git_repo):
    """Object checks should expose the shared canonical blob identity."""
    blob = run_git_command(["rev-parse", "HEAD:README.md"]).stdout.strip()
    tree = run_git_command(["write-tree"]).stdout.strip()
    expressions = ["HEAD:README.md", blob, tree, "missing-object"]

    resolved = resolve_git_objects(expressions)

    assert resolved["HEAD:README.md"].object_id == blob
    assert resolved[blob].object_id == blob
    assert resolved[blob].object_type == "blob"
    assert resolved[blob].size == len(b"# Test\n")
    assert resolved[tree].object_type == "tree"
    assert "missing-object" not in resolved


def test_stream_git_blobs_preserves_binary_object_boundaries(
    temp_git_repo,
):
    """Streaming batch reads should yield exact payloads one object at a time."""
    first = create_git_blob([b"first\x00payload\n"])
    second = create_git_blob([b"second\nline\n"])

    blobs = [
        (blob.requested_name, blob.object_id, b"".join(blob.content_chunks))
        for blob in stream_git_blobs([first, second])
    ]

    assert blobs == [
        (first, first, b"first\x00payload\n"),
        (second, second, b"second\nline\n"),
    ]


def test_stream_git_blobs_does_not_materialize_large_payloads(temp_git_repo):
    """Blob streams should expose bounded chunks instead of one payload value."""
    payload_size = 256 * 1024
    blob_id = create_git_blob([b"x" * payload_size])

    streamed_size = 0
    largest_chunk = 0
    for blob in stream_git_blobs([blob_id]):
        assert blob.size == payload_size
        for chunk in blob.content_chunks:
            streamed_size += len(chunk)
            largest_chunk = max(largest_chunk, len(chunk))

    assert streamed_size == payload_size
    assert largest_chunk < payload_size


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
