"""Tests for repository-backed buffer loading."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import git_stage_batch.core.mapped_storage as mapped_storage_module
from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.exceptions import RepositoryDataInvalid
import git_stage_batch.utils.repository_buffers as repository_buffers
from git_stage_batch.utils.repository_buffers import (
    git_object_name_is_batch_protocol_safe,
    load_git_blob_as_buffer,
    read_git_object_buffer_or_none,
    read_git_object_buffer_or_empty,
    load_git_tree_files_as_buffers,
    load_working_tree_file_as_buffer,
    stream_git_blob_buffers,
)
from git_stage_batch.utils.git_object_io import (
    GitBlobStream,
    GitObjectInfo,
    GitTreeBlob,
)


def test_load_git_blob_as_buffer_loads_streamed_blob(monkeypatch):
    """Git blob buffers are loaded from streamed blob chunks."""
    calls = []

    def fake_read_git_blob(blob_sha):
        calls.append(blob_sha)
        return iter([b"alpha\nbe", b"ta\n"])

    monkeypatch.setattr(repository_buffers, "read_git_blob", fake_read_git_blob)

    with load_git_blob_as_buffer("abc123") as buffer:
        assert calls == ["abc123"]
        assert buffer.uses_mapped_storage is False
        assert buffer[1] == b"beta\n"


@pytest.mark.parametrize(
    "object_name",
    ["HEAD:path\nname", "HEAD:path\rname", "HEAD:path\udcffname"],
)
def test_git_object_name_rejects_batch_protocol_unsafe_paths(object_name):
    """Line delimiters and surrogateescaped bytes require argv-safe reads."""
    assert git_object_name_is_batch_protocol_safe(object_name) is False


def test_git_object_name_accepts_normal_unicode_path():
    assert git_object_name_is_batch_protocol_safe("HEAD:naïve file.txt") is True


def test_stream_git_blob_buffers_spools_and_closes_each_source(
    monkeypatch,
    tmp_path,
):
    """Batch-loaded source buffers should be mmap-backed and file-local."""
    payload = b"line\n" * 2_000
    temporary_directories = []
    real_temporary_file = mapped_storage_module._temporary_file

    def recording_temporary_file(spool_dir=None):
        temporary_directories.append(spool_dir)
        return real_temporary_file(spool_dir)

    def fake_stream_git_blobs(blob_names):
        for blob_name in blob_names:
            yield GitBlobStream(
                requested_name=blob_name,
                object_id=f"oid-{blob_name}",
                size=len(payload),
                content_chunks=iter((payload[:4_000], payload[4_000:])),
            )

    monkeypatch.setattr(
        repository_buffers,
        "stream_git_blobs",
        fake_stream_git_blobs,
    )
    monkeypatch.setattr(
        mapped_storage_module,
        "_temporary_file",
        recording_temporary_file,
    )
    spool_dir = tmp_path / "scratch"
    spool_dir.mkdir()

    buffers = stream_git_blob_buffers(
        ["first", "second"],
        spool_dir=spool_dir,
    )
    first = next(buffers)
    assert first.buffer.uses_mapped_storage is True
    assert first.buffer[0] == b"line\n"

    second = next(buffers)
    with pytest.raises(ValueError, match="closed"):
        len(first.buffer)
    assert second.buffer.uses_mapped_storage is True

    buffers.close()
    with pytest.raises(ValueError, match="closed"):
        len(second.buffer)
    assert temporary_directories
    assert all(
        directory is not None
        and directory.resolve() == spool_dir.resolve()
        for directory in temporary_directories
    )


def test_load_git_tree_files_as_buffers_loads_tree_blobs(monkeypatch, tmp_path):
    """Files from a Git tree are loaded by blob SHA."""
    calls = []
    loaded_blobs = []

    def fake_list_git_tree_blobs(treeish, file_paths):
        calls.append((treeish, list(file_paths)))
        return {
            "alpha.txt": GitTreeBlob("alpha.txt", "100644", "alpha-sha"),
            "beta.txt": GitTreeBlob("beta.txt", "100644", "beta-sha"),
        }

    def fake_load_git_blob_as_buffer(blob_sha, *, spool_dir=None):
        loaded_blobs.append((blob_sha, spool_dir))
        return LineBuffer.from_bytes(
            blob_sha.encode("ascii") + b"\n",
            spool_dir=spool_dir,
        )

    monkeypatch.setattr(
        repository_buffers,
        "list_git_tree_blobs",
        fake_list_git_tree_blobs,
    )
    monkeypatch.setattr(
        repository_buffers,
        "load_git_blob_as_buffer",
        fake_load_git_blob_as_buffer,
    )

    spool_dir = tmp_path / "scratch"
    spool_dir.mkdir()
    buffers = load_git_tree_files_as_buffers(
        "HEAD",
        ["alpha.txt", "beta.txt"],
        spool_dir=spool_dir,
    )
    try:
        assert calls == [("HEAD", ["alpha.txt", "beta.txt"])]
        assert loaded_blobs == [
            ("alpha-sha", spool_dir),
            ("beta-sha", spool_dir),
        ]
        assert buffers["alpha.txt"][0] == b"alpha-sha\n"
        assert buffers["beta.txt"][0] == b"beta-sha\n"
    finally:
        for buffer in buffers.values():
            buffer.close()


def test_read_git_object_buffer_or_none_loads_streamed_output(monkeypatch):
    """Git object buffers are loaded from precisely resolved blobs."""
    calls = []

    monkeypatch.setattr(
        repository_buffers,
        "resolve_git_objects",
        lambda names: {
            names[0]: GitObjectInfo("abc123", "blob", 11),
        },
    )

    def fake_load(blob_sha, **_kwargs):
        calls.append(blob_sha)
        return LineBuffer.from_chunks([b"alpha\nbe", b"ta\n"])

    monkeypatch.setattr(repository_buffers, "load_git_blob_as_buffer", fake_load)

    with read_git_object_buffer_or_none("HEAD:file.txt") as buffer:
        assert calls == ["abc123"]
        assert buffer.uses_mapped_storage is False
        assert buffer[1] == b"beta\n"


def test_read_git_object_buffer_or_none_returns_none_for_missing_object(monkeypatch):
    """Missing Git objects return None instead of a buffer."""

    monkeypatch.setattr(repository_buffers, "resolve_git_objects", lambda _names: {})

    assert read_git_object_buffer_or_none("HEAD:missing.txt") is None


def test_read_git_object_buffer_or_none_rejects_unsafe_non_blob(monkeypatch):
    """The unusual-path fallback must retain the normal blob-only contract."""
    calls = []

    def fake_run(arguments, **_kwargs):
        calls.append(arguments)
        if arguments == ["rev-parse", "--verify", "HEAD^{tree}"]:
            return SimpleNamespace(returncode=0, stdout="tree-id\n")
        if arguments[0] == "rev-parse":
            return SimpleNamespace(returncode=0, stdout="object-id\n")
        assert arguments[:2] == ["cat-file", "-t"]
        return SimpleNamespace(returncode=0, stdout="tree\n")

    monkeypatch.setattr(repository_buffers, "run_git_command", fake_run)

    with pytest.raises(RepositoryDataInvalid, match="tree, not a blob"):
        read_git_object_buffer_or_none("HEAD:directory\nname")

    assert calls == [
        ["rev-parse", "--verify", "HEAD^{tree}"],
        ["rev-parse", "--verify", "HEAD:directory\nname"],
        ["cat-file", "-t", "object-id"],
    ]


def test_read_git_object_buffer_or_empty_returns_empty_for_missing_object(
    monkeypatch,
):
    """Missing Git objects can be loaded as empty file buffers."""

    monkeypatch.setattr(repository_buffers, "resolve_git_objects", lambda _names: {})

    with read_git_object_buffer_or_empty("HEAD:missing.txt") as buffer:
        assert len(buffer) == 0


def test_load_working_tree_file_as_buffer_uses_repository_root(monkeypatch, tmp_path):
    """Working-tree buffers are loaded relative to the repository root."""
    file_path = tmp_path / "dir" / "file.txt"
    file_path.parent.mkdir()
    file_path.write_bytes(b"alpha\nbeta\n")

    monkeypatch.setattr(
        repository_buffers,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )

    with load_working_tree_file_as_buffer("dir/file.txt") as buffer:
        assert buffer.uses_mapped_storage is False
        assert buffer[0] == b"alpha\n"
        assert buffer[1] == b"beta\n"


def test_load_working_tree_file_as_buffer_returns_empty_for_missing_file(
    monkeypatch,
    tmp_path,
):
    """Missing working-tree files return an empty buffer."""
    monkeypatch.setattr(
        repository_buffers,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )

    with load_working_tree_file_as_buffer("missing.txt") as buffer:
        assert len(buffer) == 0
