"""Tests for editor Git buffer loading."""

from __future__ import annotations

import subprocess

import git_stage_batch.editor.git as editor_git
from git_stage_batch.editor import (
    EditorBuffer,
    load_git_blob_as_buffer,
    load_git_object_as_buffer,
    load_git_object_as_buffer_or_empty,
    load_git_tree_files_as_buffers,
    load_working_tree_file_as_buffer,
)
from git_stage_batch.utils.git import GitTreeBlob


def test_load_git_blob_as_buffer_loads_streamed_blob(monkeypatch):
    """Git blob buffers are loaded from streamed blob chunks."""
    calls = []

    def fake_read_git_blob(blob_sha):
        calls.append(blob_sha)
        return iter([b"alpha\nbe", b"ta\n"])

    monkeypatch.setattr(editor_git, "read_git_blob", fake_read_git_blob)

    with load_git_blob_as_buffer("abc123") as buffer:
        assert calls == ["abc123"]
        assert buffer.is_mmap_backed is False
        assert buffer[1] == b"beta\n"


def test_load_git_tree_files_as_buffers_loads_tree_blobs(monkeypatch):
    """Files from a Git tree are loaded by blob SHA."""
    calls = []

    def fake_list_git_tree_blobs(treeish, file_paths):
        calls.append((treeish, list(file_paths)))
        return {
            "alpha.txt": GitTreeBlob("alpha.txt", "100644", "alpha-sha"),
            "beta.txt": GitTreeBlob("beta.txt", "100644", "beta-sha"),
        }

    def fake_load_git_blob_as_buffer(blob_sha):
        return EditorBuffer.from_bytes(blob_sha.encode("ascii") + b"\n")

    monkeypatch.setattr(editor_git, "list_git_tree_blobs", fake_list_git_tree_blobs)
    monkeypatch.setattr(editor_git, "load_git_blob_as_buffer", fake_load_git_blob_as_buffer)

    buffers = load_git_tree_files_as_buffers("HEAD", ["alpha.txt", "beta.txt"])
    try:
        assert calls == [("HEAD", ["alpha.txt", "beta.txt"])]
        assert buffers["alpha.txt"][0] == b"alpha-sha\n"
        assert buffers["beta.txt"][0] == b"beta-sha\n"
    finally:
        for buffer in buffers.values():
            buffer.close()


def test_load_git_object_as_buffer_loads_streamed_output(monkeypatch):
    """Git object buffers are loaded from streamed command output."""
    calls = []

    def fake_stream_git_object(revision_path):
        calls.append(revision_path)
        return iter([b"alpha\nbe", b"ta\n"])

    monkeypatch.setattr(editor_git, "_stream_git_object", fake_stream_git_object)

    with load_git_object_as_buffer("HEAD:file.txt") as buffer:
        assert calls == ["HEAD:file.txt"]
        assert buffer.is_mmap_backed is False
        assert buffer[1] == b"beta\n"


def test_load_git_object_as_buffer_returns_none_for_missing_object(monkeypatch):
    """Missing Git objects return None instead of a buffer."""

    def fake_stream_git_object(revision_path):
        raise subprocess.CalledProcessError(
            128,
            ["git", "show", revision_path],
        )

    monkeypatch.setattr(editor_git, "_stream_git_object", fake_stream_git_object)

    assert load_git_object_as_buffer("HEAD:missing.txt") is None


def test_load_git_object_as_buffer_or_empty_returns_empty_for_missing_object(
    monkeypatch,
):
    """Missing Git objects can be loaded as empty file buffers."""

    def fake_stream_git_object(revision_path):
        raise subprocess.CalledProcessError(
            128,
            ["git", "show", revision_path],
        )

    monkeypatch.setattr(editor_git, "_stream_git_object", fake_stream_git_object)

    with load_git_object_as_buffer_or_empty("HEAD:missing.txt") as buffer:
        assert len(buffer) == 0


def test_load_working_tree_file_as_buffer_uses_repository_root(monkeypatch, tmp_path):
    """Working-tree buffers are loaded relative to the repository root."""
    file_path = tmp_path / "dir" / "file.txt"
    file_path.parent.mkdir()
    file_path.write_bytes(b"alpha\nbeta\n")

    monkeypatch.setattr(editor_git, "get_git_repository_root_path", lambda: tmp_path)

    with load_working_tree_file_as_buffer("dir/file.txt") as buffer:
        assert buffer.is_mmap_backed is False
        assert buffer[0] == b"alpha\n"
        assert buffer[1] == b"beta\n"


def test_load_working_tree_file_as_buffer_returns_empty_for_missing_file(
    monkeypatch,
    tmp_path,
):
    """Missing working-tree files return an empty buffer."""
    monkeypatch.setattr(editor_git, "get_git_repository_root_path", lambda: tmp_path)

    with load_working_tree_file_as_buffer("missing.txt") as buffer:
        assert len(buffer) == 0
