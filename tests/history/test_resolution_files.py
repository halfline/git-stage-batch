"""Tests for bounded, private rewrite-resolution artifact transport."""

from __future__ import annotations

from collections.abc import Callable
import ctypes
import errno
import gc
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import tracemalloc
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from git_stage_batch.exceptions import CommandError
from git_stage_batch.history import resolution_files
from git_stage_batch.history.resolution_files import (
    ResolutionArtifactDigest,
    copy_resolution_artifact_atomically,
    create_private_resolution_directory,
    digest_resolution_artifact,
    import_resolution_artifact_blob,
    list_resolution_directory,
    lock_resolution_directory,
    publish_private_resolution_directory,
    recover_interrupted_resolution_artifact_write,
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


def _write_private_bytes(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    path.chmod(0o600)


def _inject_darwin_privacy_failure(
    monkeypatch: pytest.MonkeyPatch,
    *,
    issue: str,
    affected: Callable[[os.stat_result], bool],
) -> list[int]:
    inspected_modes: list[int] = []

    def privacy_issue(descriptor: int) -> bool:
        metadata = os.fstat(descriptor)
        if not affected(metadata):
            return False
        inspected_modes.append(metadata.st_mode)
        return True

    monkeypatch.setattr(resolution_files.sys, "platform", "darwin")
    monkeypatch.setattr(
        resolution_files,
        "_darwin_descriptor_ignores_ownership",
        privacy_issue if issue == "unknown-permissions" else lambda _fd: False,
    )
    monkeypatch.setattr(
        resolution_files,
        "_darwin_descriptor_has_extended_acl",
        privacy_issue if issue == "extended-acl" else lambda _fd: False,
    )
    return inspected_modes


@pytest.mark.parametrize(
    ("entry_result", "entry_errno", "expected"),
    [(0, 0, True), (-1, errno.EINVAL, False)],
)
def test_darwin_acl_inspection_distinguishes_entry_from_empty_acl(
    monkeypatch,
    entry_result,
    entry_errno,
    expected,
):
    def get_entry(*_arguments):
        ctypes.set_errno(entry_errno)
        return entry_result

    get_acl = Mock(return_value=1234)
    free_acl = Mock(return_value=0)
    library = SimpleNamespace(
        acl_get_fd_np=get_acl,
        acl_get_entry=Mock(side_effect=get_entry),
        acl_free=free_acl,
    )
    monkeypatch.setattr(
        resolution_files.ctypes,
        "CDLL",
        lambda _name, *, use_errno: library,
    )

    assert resolution_files._darwin_descriptor_has_extended_acl(7) is expected
    get_acl.assert_called_once_with(7, resolution_files._DARWIN_ACL_TYPE_EXTENDED)
    free_acl.assert_called_once_with(1234)


def test_darwin_acl_inspection_fails_closed_on_retrieval_error(monkeypatch):
    def get_acl(*_arguments):
        ctypes.set_errno(errno.EOPNOTSUPP)
        return None

    library = SimpleNamespace(
        acl_get_fd_np=Mock(side_effect=get_acl),
        acl_get_entry=Mock(),
        acl_free=Mock(),
    )
    monkeypatch.setattr(
        resolution_files.ctypes,
        "CDLL",
        lambda _name, *, use_errno: library,
    )

    with pytest.raises(OSError) as caught:
        resolution_files._darwin_descriptor_has_extended_acl(7)

    assert caught.value.errno == errno.EOPNOTSUPP
    library.acl_free.assert_not_called()


def test_darwin_acl_inspection_treats_missing_extended_acl_as_empty(monkeypatch):
    def get_acl(*_arguments):
        ctypes.set_errno(errno.ENOENT)
        return None

    library = SimpleNamespace(
        acl_get_fd_np=Mock(side_effect=get_acl),
        acl_get_entry=Mock(),
        acl_free=Mock(),
    )
    monkeypatch.setattr(
        resolution_files.ctypes,
        "CDLL",
        lambda _name, *, use_errno: library,
    )

    assert resolution_files._darwin_descriptor_has_extended_acl(7) is False
    library.acl_get_entry.assert_not_called()
    library.acl_free.assert_not_called()


def test_darwin_mount_inspection_reads_unknown_permissions_flag(monkeypatch):
    def fstatfs(_descriptor, file_system_pointer):
        file_system = ctypes.cast(
            file_system_pointer,
            ctypes.POINTER(resolution_files._DarwinFileSystemStatistics),
        ).contents
        file_system.flags = resolution_files._DARWIN_MNT_UNKNOWNPERMISSIONS
        return 0

    library = SimpleNamespace(fstatfs=Mock(side_effect=fstatfs))
    monkeypatch.setattr(
        resolution_files.ctypes,
        "CDLL",
        lambda _name, *, use_errno: library,
    )

    assert resolution_files._darwin_descriptor_ignores_ownership(7)
    library.fstatfs.assert_called_once()


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


@pytest.mark.parametrize(
    ("issue", "message"),
    [
        ("extended-acl", "permissions must be 0700"),
        ("unknown-permissions", "owned by the current user"),
    ],
)
def test_darwin_private_directory_rejects_nonprivate_descriptor_state(
    tmp_path,
    monkeypatch,
    issue,
    message,
):
    workspace = _private_directory(tmp_path / "workspace")
    inspected_modes = _inject_darwin_privacy_failure(
        monkeypatch,
        issue=issue,
        affected=lambda metadata: stat.S_ISDIR(metadata.st_mode),
    )

    with pytest.raises(CommandError, match=message):
        require_private_resolution_directory(workspace)

    assert inspected_modes
    assert all(stat.S_ISDIR(mode) for mode in inspected_modes)


@pytest.mark.parametrize(
    ("issue", "message"),
    [
        ("extended-acl", "permissions must be 0700"),
        ("unknown-permissions", "owned by the current user"),
    ],
)
def test_darwin_directory_creation_rejects_inherited_nonprivate_state(
    tmp_path,
    monkeypatch,
    issue,
    message,
):
    workspace = tmp_path / "workspace"
    inspected_modes = _inject_darwin_privacy_failure(
        monkeypatch,
        issue=issue,
        affected=lambda metadata: stat.S_ISDIR(metadata.st_mode),
    )

    with pytest.raises(CommandError, match=message):
        create_private_resolution_directory(workspace)

    assert inspected_modes
    assert not workspace.exists()


@pytest.mark.parametrize(
    ("issue", "message"),
    [
        ("extended-acl", "permissions must be 0600"),
        ("unknown-permissions", "owned by the current user"),
    ],
)
def test_darwin_private_file_rejects_nonprivate_descriptor_state(
    tmp_path,
    monkeypatch,
    issue,
    message,
):
    workspace = _private_directory(tmp_path / "workspace")
    artifact = workspace / "result"
    _write_private_bytes(artifact, b"content\n")
    inspected_modes = _inject_darwin_privacy_failure(
        monkeypatch,
        issue=issue,
        affected=lambda metadata: stat.S_ISREG(metadata.st_mode),
    )

    with pytest.raises(CommandError, match=message):
        digest_resolution_artifact(artifact)

    assert inspected_modes
    assert all(stat.S_ISREG(mode) for mode in inspected_modes)


@pytest.mark.parametrize(
    ("issue", "message"),
    [
        ("extended-acl", "permissions must be 0600"),
        ("unknown-permissions", "owned by the current user"),
    ],
)
def test_darwin_file_creation_rejects_inherited_nonprivate_state(
    tmp_path,
    monkeypatch,
    issue,
    message,
):
    workspace = _private_directory(tmp_path / "workspace")
    artifact = workspace / "result"
    inspected_modes = _inject_darwin_privacy_failure(
        monkeypatch,
        issue=issue,
        affected=lambda metadata: stat.S_ISREG(metadata.st_mode),
    )

    with pytest.raises(CommandError, match=message):
        write_resolution_artifact_atomically(artifact, [b"content\n"])

    assert inspected_modes
    assert not artifact.exists()
    assert list(workspace.glob(".git-stage-batch-resolution-*.tmp")) == []


@pytest.mark.parametrize(
    ("issue", "message"),
    [
        ("extended-acl", "permissions must be 0600"),
        ("unknown-permissions", "owned by the current user"),
    ],
)
@pytest.mark.parametrize("create", [False, True])
def test_darwin_workspace_lock_rejects_nonprivate_descriptor_state(
    tmp_path,
    monkeypatch,
    issue,
    message,
    create,
):
    workspace = _private_directory(tmp_path / "workspace")
    lock_path = workspace / ".workspace.lock"
    if not create:
        _write_private_bytes(lock_path, b"")
    inspected_modes = _inject_darwin_privacy_failure(
        monkeypatch,
        issue=issue,
        affected=lambda metadata: stat.S_ISREG(metadata.st_mode),
    )

    with pytest.raises(CommandError, match=message):
        with lock_resolution_directory(workspace, create=create):
            pass

    assert inspected_modes
    assert lock_path.exists() == (not create)


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


def test_invalid_detail_escapes_terminal_controls(tmp_path):
    with pytest.raises(CommandError) as caught:
        resolution_files._invalid(tmp_path / "artifact", "bad\n\x1bdetail")

    assert "\n" not in str(caught.value)
    assert r"\n\u001b" in str(caught.value)


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


def test_directory_listing_is_sorted_and_entry_bounded(tmp_path):
    workspace = _private_directory(tmp_path / "workspace")
    for name in ("third", "first", "second"):
        _write_private_bytes(workspace / name, b"")

    assert list_resolution_directory(workspace, maximum_entries=3) == (
        "first",
        "second",
        "third",
    )
    with pytest.raises(CommandError, match="entry-count limit"):
        list_resolution_directory(workspace, maximum_entries=2)
    with pytest.raises(ValueError, match="positive integer"):
        list_resolution_directory(workspace, maximum_entries=True)


def test_lock_rejects_fifo_and_nonprivate_existing_file(tmp_path):
    if not hasattr(os, "mkfifo"):
        pytest.skip("requires POSIX FIFOs")
    workspace = _private_directory(tmp_path / "workspace")
    lock_path = workspace / ".workspace.lock"
    os.mkfifo(lock_path, mode=0o600)

    with pytest.raises(CommandError, match="regular file"):
        with lock_resolution_directory(workspace, create=False):
            pass

    lock_path.unlink()
    lock_path.write_bytes(b"")
    lock_path.chmod(0o644)
    with pytest.raises(CommandError, match="permissions must be 0600"):
        with lock_resolution_directory(workspace):
            pass
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o644


def test_locked_root_anchors_descendant_access_across_visible_swap(tmp_path):
    workspace = _private_directory(tmp_path / "workspace")
    moved_workspace = tmp_path / "moved-workspace"
    replacement_workspace = tmp_path / "replacement-workspace"
    artifact = workspace / "result"
    original_payload = b"original\n"
    _write_private_bytes(artifact, original_payload)

    with lock_resolution_directory(workspace):
        workspace.rename(moved_workspace)
        workspace.mkdir(mode=0o700)
        _write_private_bytes(workspace / "result", b"replacement\n")

        assert digest_resolution_artifact(artifact) == ResolutionArtifactDigest(
            size=len(original_payload),
            sha256=hashlib.sha256(original_payload).hexdigest(),
        )

        workspace.rename(replacement_workspace)
        moved_workspace.rename(workspace)


def test_locked_root_walk_uses_independent_directory_position(tmp_path):
    """Exact-root walks must not share the lock descriptor's stream position."""
    workspace = _private_directory(tmp_path / "workspace")

    with lock_resolution_directory(workspace):
        locked_descriptor = resolution_files._ACTIVE_LOCKED_ROOTS.get()[-1].descriptor
        original_position = os.lseek(locked_descriptor, 0, os.SEEK_CUR)
        try:
            try:
                os.lseek(locked_descriptor, 17, os.SEEK_SET)
            except OSError as error:
                pytest.skip(f"directory positions are unavailable: {error}")

            with resolution_files._walk_directory(workspace) as walked_descriptor:
                locked_metadata = os.fstat(locked_descriptor)
                walked_metadata = os.fstat(walked_descriptor)
                assert (walked_metadata.st_dev, walked_metadata.st_ino) == (
                    locked_metadata.st_dev,
                    locked_metadata.st_ino,
                )
                assert os.lseek(locked_descriptor, 0, os.SEEK_CUR) == 17
                assert os.lseek(walked_descriptor, 0, os.SEEK_CUR) == 0
        finally:
            os.lseek(locked_descriptor, original_position, os.SEEK_SET)


def test_nested_locks_keep_outer_descendant_access_descriptor_anchored(tmp_path):
    outer = _private_directory(tmp_path / "outer")
    inner = _private_directory(tmp_path / "inner")
    displaced = tmp_path / "displaced"
    replacement = tmp_path / "replacement"
    payload = b"copied through the outer descriptor\n"

    with lock_resolution_directory(outer):
        with lock_resolution_directory(inner):
            outer.rename(displaced)
            outer.mkdir(mode=0o700)
            try:
                write_resolution_artifact_atomically(outer / "result", [payload])
                assert (displaced / "result").read_bytes() == payload
                assert not (outer / "result").exists()
            finally:
                outer.rename(replacement)
                displaced.rename(outer)
                replacement.rmdir()


def test_lock_authenticates_moved_root_through_publication(tmp_path):
    staging = _private_directory(tmp_path / "staging")
    destination = tmp_path / "workspace"

    with lock_resolution_directory(staging, moved_to=destination):
        publish_private_resolution_directory(staging, destination)

    assert destination.is_dir()
    assert (destination / ".workspace.lock").is_file()
    assert not staging.exists()


def test_lock_exit_authentication_runs_when_body_raises(tmp_path):
    workspace = _private_directory(tmp_path / "workspace")

    with pytest.raises(CommandError, match="permissions must be 0600"):
        with lock_resolution_directory(workspace):
            (workspace / ".workspace.lock").chmod(0o644)
            raise RuntimeError("injected body failure")


def test_directory_publication_does_not_replace_raced_destination(
    tmp_path,
    monkeypatch,
):
    staging = _private_directory(tmp_path / "staging")
    destination = tmp_path / "workspace"
    original_rename_noreplace = resolution_files._rename_noreplace

    def racing_rename(parent, source_name, destination_name):
        os.mkdir(destination_name, mode=0o700, dir_fd=parent)
        original_rename_noreplace(parent, source_name, destination_name)

    monkeypatch.setattr(resolution_files, "_rename_noreplace", racing_rename)

    with pytest.raises(CommandError, match="destination already exists"):
        publish_private_resolution_directory(staging, destination)

    assert staging.is_dir()
    assert destination.is_dir()


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
    _write_private_bytes(artifact, payload)
    expected = ResolutionArtifactDigest(
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )

    assert digest_resolution_artifact(artifact, expected=expected) == expected

    wrong = ResolutionArtifactDigest(size=len(payload) + 1, sha256=expected.sha256)
    with pytest.raises(CommandError, match="expected size and SHA-256"):
        digest_resolution_artifact(artifact, expected=wrong)


def test_digest_rejects_nonprivate_artifact_mode(tmp_path):
    workspace = _private_directory(tmp_path / "workspace")
    artifact = workspace / "result"
    artifact.write_bytes(b"content\n")
    artifact.chmod(0o644)

    with pytest.raises(CommandError, match="permissions must be 0600"):
        digest_resolution_artifact(artifact)


def test_digest_rejects_symlink_and_nonregular_artifact(tmp_path):
    workspace = _private_directory(tmp_path / "workspace")
    target = workspace / "target"
    _write_private_bytes(target, b"content\n")
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
    _write_private_bytes(artifact, b"content\n")
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
    _write_private_bytes(artifact, b"a" * (256 * 1024))
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
    _write_private_bytes(artifact, b"a" * (64 * 1024))
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
    _write_private_bytes(artifact, b"original\n" * 16384)
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
    _write_private_bytes(source, payload)
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


def test_atomic_write_enforces_optional_size_cap_and_cleans_temp(tmp_path):
    workspace = _private_directory(tmp_path / "workspace")
    destination = workspace / "result"

    with pytest.raises(CommandError, match="supported size limit"):
        write_resolution_artifact_atomically(
            destination,
            [b"1234", b"5"],
            maximum_bytes=4,
        )

    assert not destination.exists()
    assert list(workspace.glob(".git-stage-batch-resolution-*.tmp")) == []
    with pytest.raises(ValueError, match="positive integer or None"):
        write_resolution_artifact_atomically(
            destination,
            [],
            maximum_bytes=True,
        )


def test_atomic_write_rejects_swapped_temporary_path(
    tmp_path,
    monkeypatch,
):
    workspace = _private_directory(tmp_path / "workspace")
    destination = workspace / "result"
    original_rename_noreplace = resolution_files._rename_noreplace
    hostile_payload = b"hostile replacement\n"

    def swapping_rename(parent, source_name, destination_name):
        saved_name = source_name + ".saved"
        os.rename(
            source_name,
            saved_name,
            src_dir_fd=parent,
            dst_dir_fd=parent,
        )
        descriptor = os.open(
            source_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=parent,
        )
        try:
            os.fchmod(descriptor, 0o600)
            os.write(descriptor, hostile_payload)
        finally:
            os.close(descriptor)
        original_rename_noreplace(parent, source_name, destination_name)

    monkeypatch.setattr(resolution_files, "_rename_noreplace", swapping_rename)

    with pytest.raises(CommandError, match="published artifact identity changed"):
        write_resolution_artifact_atomically(destination, [b"expected\n"])

    assert destination.read_bytes() == hostile_payload


def test_atomic_exchange_rolls_back_raced_existing_destination(
    tmp_path,
    monkeypatch,
):
    workspace = _private_directory(tmp_path / "workspace")
    destination = workspace / "result"
    original_payload = b"original\n"
    raced_payload = b"raced\n"
    _write_private_bytes(destination, original_payload)
    original_exchange = resolution_files._rename_exchange
    injected = False

    def racing_exchange(parent, source_name, destination_name):
        nonlocal injected
        if not injected:
            injected = True
            os.rename(
                destination_name,
                "original-away",
                src_dir_fd=parent,
                dst_dir_fd=parent,
            )
            descriptor = os.open(
                destination_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=parent,
            )
            try:
                os.fchmod(descriptor, 0o600)
                os.write(descriptor, raced_payload)
            finally:
                os.close(descriptor)
        original_exchange(parent, source_name, destination_name)

    monkeypatch.setattr(resolution_files, "_rename_exchange", racing_exchange)

    with pytest.raises(CommandError, match="changed during atomic replacement"):
        write_resolution_artifact_atomically(destination, [b"resolved\n"])

    assert destination.read_bytes() == raced_payload
    assert (workspace / "original-away").read_bytes() == original_payload
    assert list(workspace.glob(".git-stage-batch-resolution-*.tmp")) == []


def test_interrupted_write_recovery_removes_only_private_temp(tmp_path):
    workspace = _private_directory(tmp_path / "workspace")
    destination = workspace / "result"
    temporary = workspace / resolution_files._temporary_resolution_artifact_name(
        destination
    )
    _write_private_bytes(temporary, b"partial\n")

    recover_interrupted_resolution_artifact_write(destination)

    assert not temporary.exists()
    recover_interrupted_resolution_artifact_write(destination)


def test_atomic_write_does_not_publish_through_replaced_directory(
    tmp_path,
    monkeypatch,
):
    workspace = _private_directory(tmp_path / "workspace")
    moved_workspace = tmp_path / "moved-workspace"
    destination = workspace / "result"
    original_rename_noreplace = resolution_files._rename_noreplace

    def swapping_rename_noreplace(parent, source_name, destination_name):
        workspace.rename(moved_workspace)
        workspace.mkdir(mode=0o700)
        return original_rename_noreplace(parent, source_name, destination_name)

    monkeypatch.setattr(
        resolution_files,
        "_rename_noreplace",
        swapping_rename_noreplace,
    )

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
    _write_private_bytes(artifact, payload)

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
    _write_private_bytes(artifact, b"content\n")
    object_id = (
        subprocess.run(
            ["git", "hash-object", "--stdin"],
            check=True,
            input=b"content\n",
            capture_output=True,
        )
        .stdout.decode("ascii")
        .strip()
    )

    with pytest.raises(ValueError, match="quarantine environment is required"):
        import_resolution_artifact_blob(artifact, env={})  # type: ignore[arg-type]

    assert (
        subprocess.run(
            ["git", "cat-file", "-e", object_id],
            check=False,
            capture_output=True,
        ).returncode
        != 0
    )

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
        artifact.chmod(0o600)

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
