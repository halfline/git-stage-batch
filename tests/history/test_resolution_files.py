"""Tests for bounded, private rewrite-resolution artifact transport."""

from __future__ import annotations

import gc
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import tracemalloc

import pytest

from git_stage_batch.exceptions import CommandError
from git_stage_batch.history import resolution_files
from git_stage_batch.history.resolution_files import (
    ResolutionArtifactDigest,
    copy_resolution_artifact_atomically,
    create_private_resolution_directory,
    digest_resolution_artifact,
    import_resolution_artifact_blob,
    require_private_resolution_directory,
    resolution_artifact_name,
    write_resolution_artifact_atomically,
)
from git_stage_batch.utils.git_command import run_git_command
from git_stage_batch.utils.git_object_io import (
    stream_git_blobs,
    temporary_git_object_environment,
)


def _private_directory(path: Path) -> Path:
    return create_private_resolution_directory(path)


def test_private_resolution_directory_has_exact_permissions(tmp_path):
    workspace = tmp_path / "workspace"
    old_umask = os.umask(0)
    try:
        created = create_private_resolution_directory(workspace)
    finally:
        os.umask(old_umask)

    assert created == workspace
    assert stat.S_IMODE(workspace.stat().st_mode) == 0o700
    assert require_private_resolution_directory(workspace) == workspace


def test_private_resolution_directory_rejects_alias_and_loose_permissions(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    workspace.chmod(0o755)

    with pytest.raises(CommandError, match="permissions must be 0700"):
        require_private_resolution_directory(workspace)

    workspace.chmod(0o700)
    link = tmp_path / "workspace-link"
    link.symlink_to(workspace, target_is_directory=True)
    with pytest.raises(CommandError, match="must not contain.*symlinks"):
        require_private_resolution_directory(link)

    with pytest.raises(CommandError, match="path must be absolute"):
        require_private_resolution_directory(Path("relative-workspace"))


def test_private_directory_failure_does_not_remove_replacement(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    original_workspace = tmp_path / "original-workspace"
    swapped = False

    def swapping_fsync(_file_descriptor: int) -> None:
        nonlocal swapped
        if not swapped:
            swapped = True
            workspace.rename(original_workspace)
            workspace.mkdir(mode=0o700)
        raise OSError("injected fsync failure")

    monkeypatch.setattr(resolution_files.os, "fsync", swapping_fsync)

    with pytest.raises(CommandError, match="cannot create private directory"):
        create_private_resolution_directory(workspace)

    assert workspace.is_dir()
    assert original_workspace.is_dir()


def test_artifact_name_is_opaque_and_bound_to_output_and_raw_path():
    unusual_path = "../line\nwith\udcff-byte.txt"

    first = resolution_artifact_name(3, unusual_path)

    assert first == resolution_artifact_name(3, unusual_path)
    assert first != resolution_artifact_name(4, unusual_path)
    assert first != resolution_artifact_name(3, unusual_path + "x")
    assert first.startswith("artifact-")
    assert len(first) == len("artifact-") + 64
    assert "line" not in first
    assert "/" not in first


def test_digest_identifies_exact_bytes_and_checks_expected_value(tmp_path):
    workspace = _private_directory(tmp_path / "workspace")
    artifact = workspace / "result"
    payload = b"first\x00line\nsecond\xffline\n"
    artifact.write_bytes(payload)
    expected = ResolutionArtifactDigest(
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )

    assert digest_resolution_artifact(artifact, expected=expected) == expected

    wrong = ResolutionArtifactDigest(size=len(payload) + 1, sha256=expected.sha256)
    with pytest.raises(CommandError, match="expected size and SHA-256"):
        digest_resolution_artifact(artifact, expected=wrong)


def test_digest_rejects_symlink_and_nonregular_artifact(tmp_path):
    workspace = _private_directory(tmp_path / "workspace")
    target = workspace / "target"
    target.write_text("content\n", encoding="utf-8")
    link = workspace / "link"
    link.symlink_to(target)

    with pytest.raises(CommandError, match="must not contain.*symlinks"):
        digest_resolution_artifact(link)

    if not hasattr(os, "mkfifo"):
        pytest.skip("requires POSIX FIFOs")
    fifo = workspace / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(CommandError, match="regular file"):
        digest_resolution_artifact(fifo)


def test_digest_rejects_hard_linked_and_foreign_owned_artifact(
    tmp_path,
    monkeypatch,
):
    workspace = _private_directory(tmp_path / "workspace")
    artifact = workspace / "result"
    artifact.write_text("content\n", encoding="utf-8")
    hard_link = workspace / "other-name"
    os.link(artifact, hard_link)

    with pytest.raises(CommandError, match="must not have hard links"):
        digest_resolution_artifact(artifact)

    hard_link.unlink()
    artifact_owner = artifact.stat().st_uid
    monkeypatch.setattr(resolution_files.os, "geteuid", lambda: artifact_owner + 1)
    with pytest.raises(CommandError, match="owned by the current user"):
        digest_resolution_artifact(artifact)


def test_digest_rejects_file_changed_during_read(tmp_path, monkeypatch):
    workspace = _private_directory(tmp_path / "workspace")
    artifact = workspace / "result"
    artifact.write_bytes(b"a" * (256 * 1024))
    original_read = os.read
    raced = False

    def racing_read(file_descriptor: int, size: int) -> bytes:
        nonlocal raced
        chunk = original_read(file_descriptor, size)
        if chunk and not raced:
            raced = True
            artifact.write_bytes(b"b" * (256 * 1024))
        return chunk

    monkeypatch.setattr(resolution_files.os, "read", racing_read)

    with pytest.raises(CommandError, match="changed while it was read"):
        digest_resolution_artifact(artifact)


def test_digest_caps_reads_at_initial_size_when_artifact_grows(
    tmp_path,
    monkeypatch,
):
    workspace = _private_directory(tmp_path / "workspace")
    artifact = workspace / "result"
    artifact.write_bytes(b"a" * (64 * 1024))
    original_read = os.read
    read_calls = 0

    def growing_read(file_descriptor: int, size: int) -> bytes:
        nonlocal read_calls
        read_calls += 1
        chunk = original_read(file_descriptor, size)
        if chunk:
            with artifact.open("ab") as destination:
                destination.write(b"b" * (64 * 1024))
        return chunk

    monkeypatch.setattr(resolution_files.os, "read", growing_read)

    with pytest.raises(CommandError, match="file grew while it was read"):
        digest_resolution_artifact(artifact)
    assert read_calls == 2


def test_digest_rejects_private_directory_replacement_during_read(
    tmp_path,
    monkeypatch,
):
    workspace = _private_directory(tmp_path / "workspace")
    moved_workspace = tmp_path / "moved-workspace"
    artifact = workspace / "result"
    artifact.write_bytes(b"original\n" * 16384)
    original_read = os.read
    swapped = False

    def swapping_read(file_descriptor: int, size: int) -> bytes:
        nonlocal swapped
        chunk = original_read(file_descriptor, size)
        if chunk and not swapped:
            swapped = True
            workspace.rename(moved_workspace)
            workspace.mkdir(mode=0o700)
            (workspace / "result").write_bytes(b"replacement\n")
        return chunk

    monkeypatch.setattr(resolution_files.os, "read", swapping_read)

    with pytest.raises(CommandError, match="directory path changed"):
        digest_resolution_artifact(artifact)

    assert (moved_workspace / "result").read_bytes().startswith(b"original\n")
    assert (workspace / "result").read_bytes() == b"replacement\n"


def test_atomic_copy_is_private_and_digest_guarded(tmp_path):
    source_workspace = _private_directory(tmp_path / "source")
    destination_workspace = _private_directory(tmp_path / "destination")
    source = source_workspace / "result"
    destination = destination_workspace / "accepted"
    payload = b"resolved payload\n" * 1000
    source.write_bytes(payload)
    destination.write_bytes(b"previous\n")
    destination.chmod(0o666)
    expected = ResolutionArtifactDigest(
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )

    copied = copy_resolution_artifact_atomically(
        source,
        destination,
        expected=expected,
    )

    assert copied == expected
    assert destination.read_bytes() == payload
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600

    source.write_bytes(b"unexpected replacement\n")
    with pytest.raises(CommandError, match="expected size and SHA-256"):
        copy_resolution_artifact_atomically(
            source,
            destination,
            expected=expected,
        )
    assert destination.read_bytes() == payload
    assert list(destination_workspace.glob(".git-stage-batch-resolution-*.tmp")) == []


def test_atomic_write_rejects_nonregular_destination(tmp_path):
    workspace = _private_directory(tmp_path / "workspace")
    destination = workspace / "result"
    if not hasattr(os, "mkfifo"):
        pytest.skip("requires POSIX FIFOs")
    os.mkfifo(destination)

    with pytest.raises(CommandError, match="regular file"):
        write_resolution_artifact_atomically(destination, [b"replacement\n"])

    assert stat.S_ISFIFO(destination.lstat().st_mode)


def test_atomic_write_does_not_publish_through_replaced_directory(
    tmp_path,
    monkeypatch,
):
    workspace = _private_directory(tmp_path / "workspace")
    moved_workspace = tmp_path / "moved-workspace"
    destination = workspace / "result"
    original_replace = os.replace

    def swapping_replace(source, target, **kwargs):
        workspace.rename(moved_workspace)
        workspace.mkdir(mode=0o700)
        return original_replace(source, target, **kwargs)

    monkeypatch.setattr(resolution_files.os, "replace", swapping_replace)

    with pytest.raises(CommandError, match="directory path changed"):
        write_resolution_artifact_atomically(destination, [b"resolved\n"])

    assert not destination.exists()
    assert (moved_workspace / "result").read_bytes() == b"resolved\n"


def test_artifact_import_stays_in_selected_git_object_quarantine(
    tmp_path,
    monkeypatch,
):
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.chdir(repository)
    subprocess.run(["git", "init", "-q"], check=True)
    workspace = _private_directory(tmp_path / "workspace")
    artifact = workspace / "result"
    payload = b"candidate content\n"
    artifact.write_bytes(payload)

    with temporary_git_object_environment() as quarantine:
        imported = import_resolution_artifact_blob(artifact, env=quarantine)
        streamed = [
            b"".join(blob.content_chunks)
            for blob in stream_git_blobs(
                [imported.blob_object_id],
                env=quarantine.environment(),
            )
        ]
        assert streamed == [payload]
        assert imported.digest == ResolutionArtifactDigest(
            size=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
        )

    result = run_git_command(
        ["cat-file", "-e", imported.blob_object_id],
        check=False,
        requires_index_lock=False,
    )
    assert result.returncode != 0


def test_artifact_import_requires_quarantine_and_disallows_filters(
    tmp_path,
    monkeypatch,
):
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.chdir(repository)
    subprocess.run(["git", "init", "-q"], check=True)
    workspace = _private_directory(tmp_path / "workspace")
    artifact = workspace / "result"
    artifact.write_text("content\n", encoding="utf-8")
    object_id = subprocess.run(
        ["git", "hash-object", "--stdin"],
        check=True,
        input=b"content\n",
        capture_output=True,
    ).stdout.decode("ascii").strip()

    with pytest.raises(ValueError, match="quarantine environment is required"):
        import_resolution_artifact_blob(artifact, env={})  # type: ignore[arg-type]

    assert subprocess.run(
        ["git", "cat-file", "-e", object_id],
        check=False,
        capture_output=True,
    ).returncode != 0

    with pytest.raises(TypeError, match="unexpected keyword argument 'git_path'"):
        import_resolution_artifact_blob(  # type: ignore[call-arg]
            artifact,
            env={},
            git_path="filtered.txt",
        )


def test_large_artifact_digest_has_bounded_python_heap(tmp_path):
    workspace = _private_directory(tmp_path / "workspace")
    heap_peaks: list[int] = []

    for payload_size in (1024 * 1024, 16 * 1024 * 1024):
        artifact = workspace / f"artifact-{payload_size}"
        with artifact.open("wb") as destination:
            remaining = payload_size
            chunk = b"resolution payload\n" * 2048
            while remaining:
                written = min(remaining, len(chunk))
                destination.write(chunk[:written])
                remaining -= written

        gc.collect()
        tracemalloc.start()
        try:
            digest = digest_resolution_artifact(artifact)
            _current_heap, peak_heap = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        heap_peaks.append(peak_heap)
        assert digest.size == payload_size

    small_peak, large_peak = heap_peaks
    assert large_peak < small_peak + 128 * 1024
