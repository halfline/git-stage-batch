"""Strict, bounded promotion of verified Git object closures."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import stat
import sys
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import chain
from typing import BinaryIO

from . import command_events, command_streaming
from .command import start_command
from .git_object_io import (
    GitObjectQuarantine,
    resolve_git_objects,
)
from .git_descriptor_exec import DARWIN_OBJECT_DIRECTORY_DESCRIPTOR
from .session_lock import acquire_session_lock, acquire_session_lock_descriptor


_MAXIMUM_GIT_PIPE_DIAGNOSTIC_BYTES = 1024 * 1024
_MAXIMUM_LEASE_ID_BYTES = 128
_PACK_READ_CHUNK_BYTES = 64 * 1024
_LEASE_ID_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)
_LINUX_RENAME_NOREPLACE = 1
_DARWIN_RENAME_EXCL = 0x00000004


@dataclass(frozen=True, slots=True)
class GitObjectPromotionLease:
    """One authenticated Git pack lease awaiting post-ref release."""

    lease_id: str
    pack_hash: str
    object_format: str
    keep_device: int
    keep_inode: int
    keep_changed_ns: int
    prior_released_device: int | None
    prior_released_inode: int | None


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    device: int
    inode: int
    mode: int
    links: int
    owner: int
    group: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True, slots=True)
class _OpenedPackLease:
    pack_descriptor: int
    pack_identity: _FileIdentity
    index_descriptor: int
    index_identity: _FileIdentity
    keep_descriptor: int
    keep_identity: _FileIdentity


@dataclass(frozen=True, slots=True)
class _OpenedPackObjects:
    pack_descriptor: int
    pack_identity: _FileIdentity
    index_descriptor: int
    index_identity: _FileIdentity


def _require_lease_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("ascii", errors="ignore")) != len(value)
        or len(value) > _MAXIMUM_LEASE_ID_BYTES
        or value[0] not in _LEASE_ID_CHARACTERS
        or any(character not in _LEASE_ID_CHARACTERS for character in value)
    ):
        raise ValueError(
            "lease_id must be 1-128 ASCII letters, digits, dots, underscores, "
            "or hyphens"
        )
    return value


def _require_object_ids(
    values: Iterable[str],
    *,
    object_format: str,
    location: str,
) -> tuple[str, ...]:
    if object_format == "sha1":
        object_id_length = 40
    elif object_format == "sha256":
        object_id_length = 64
    else:
        raise ValueError(f"unsupported Git object format: {object_format}")
    object_ids = tuple(dict.fromkeys(values))
    for index, object_id in enumerate(object_ids):
        if len(object_id) != object_id_length or any(
            character not in "0123456789abcdef" for character in object_id
        ):
            raise ValueError(
                f"{location}[{index}] must be a lowercase full Git object ID"
            )
    return object_ids


def _file_identity(metadata: os.stat_result) -> _FileIdentity:
    return _FileIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=metadata.st_mode,
        links=metadata.st_nlink,
        owner=metadata.st_uid,
        group=metadata.st_gid,
        size=metadata.st_size,
        modified_ns=metadata.st_mtime_ns,
        changed_ns=metadata.st_ctime_ns,
    )


def _require_directory_metadata(metadata: os.stat_result, location: str) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError(f"Git {location} is not a directory")


@contextmanager
def _pinned_pack_directory(object_directory: int) -> Iterator[int]:
    descriptor: int | None = None
    try:
        visible = os.stat("pack", dir_fd=object_directory, follow_symlinks=False)
        _require_directory_metadata(visible, "pack path")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open("pack", flags, dir_fd=object_directory)
        opened = os.fstat(descriptor)
    except OSError as error:
        if descriptor is not None:
            _close_file_descriptor(descriptor)
        raise RuntimeError(f"Cannot open the Git pack directory: {error}") from error
    expected_identity = (visible.st_dev, visible.st_ino)
    if (
        not stat.S_ISDIR(opened.st_mode)
        or (opened.st_dev, opened.st_ino) != expected_identity
    ):
        _close_file_descriptor(descriptor)
        raise RuntimeError("The Git pack directory identity changed")
    try:
        try:
            yield descriptor
        finally:
            visible_after = os.stat(
                "pack",
                dir_fd=object_directory,
                follow_symlinks=False,
            )
            opened_after = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(visible_after.st_mode)
                or not stat.S_ISDIR(opened_after.st_mode)
                or (visible_after.st_dev, visible_after.st_ino) != expected_identity
                or (opened_after.st_dev, opened_after.st_ino) != expected_identity
            ):
                raise RuntimeError("The Git pack directory identity changed")
    finally:
        os.close(descriptor)


@contextmanager
def _fresh_pack_directory_entries(
    pack_directory: int,
) -> Iterator[Iterator[os.DirEntry[str]]]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(".", flags, dir_fd=pack_directory)
    try:
        pinned = os.fstat(pack_directory)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(pinned.st_mode)
            or not stat.S_ISDIR(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (pinned.st_dev, pinned.st_ino)
        ):
            raise RuntimeError("The Git pack directory identity changed")
        with os.scandir(descriptor) as entries:
            yield entries
    finally:
        _close_file_descriptor(descriptor)


def _pack_artifact_name(pack_hash: str, suffix: str) -> str:
    return f"pack-{pack_hash}.{suffix}"


def _released_lease_name(lease: GitObjectPromotionLease) -> str:
    return (
        f".git-stage-batch-released-{lease.pack_hash}-"
        f"{_released_lease_digest(lease.lease_id)}"
    )


def _released_lease_digest(lease_id: str) -> str:
    digest = hashlib.sha256()
    digest.update(b"git-stage-batch pack lease release\0")
    digest.update(lease_id.encode("ascii"))
    return digest.hexdigest()


def _rename_noreplace(
    parent: int,
    source_name: str,
    destination_name: str,
) -> None:
    if sys.platform == "linux":
        symbol = "renameat2"
        flags = _LINUX_RENAME_NOREPLACE
    elif sys.platform == "darwin":
        symbol = "renameatx_np"
        flags = _DARWIN_RENAME_EXCL
    else:
        raise OSError(
            errno.ENOTSUP,
            "atomic no-replace rename is unsupported on this platform",
        )
    library = ctypes.CDLL(None, use_errno=True)
    try:
        function = getattr(library, symbol)
    except AttributeError as error:
        raise OSError(errno.ENOSYS, f"{symbol} is unavailable") from error
    function.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    function.restype = ctypes.c_int
    ctypes.set_errno(0)
    result = function(
        parent,
        os.fsencode(source_name),
        parent,
        os.fsencode(destination_name),
        flags,
    )
    if result != 0:
        error_number = ctypes.get_errno() or errno.EIO
        raise OSError(error_number, os.strerror(error_number), destination_name)


def _open_pack_artifact(
    pack_directory: int,
    name: str,
    *,
    require_single_link: bool,
    require_private: bool,
    require_nonwritable: bool,
) -> tuple[int, _FileIdentity]:
    try:
        visible = os.stat(name, dir_fd=pack_directory, follow_symlinks=False)
    except OSError as error:
        raise RuntimeError(
            f"Cannot inspect Git pack artifact {name}: {error}"
        ) from error
    if not stat.S_ISREG(visible.st_mode):
        raise RuntimeError(f"Git pack artifact is not a regular file: {name}")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(name, flags, dir_fd=pack_directory)
        opened = os.fstat(descriptor)
    except OSError as error:
        if descriptor is not None:
            _close_file_descriptor(descriptor)
        raise RuntimeError(f"Cannot open Git pack artifact {name}: {error}") from error
    visible_identity = _file_identity(visible)
    opened_identity = _file_identity(opened)
    if (
        not stat.S_ISREG(opened.st_mode)
        or visible_identity != opened_identity
        or opened.st_nlink < 1
        or (require_single_link and opened.st_nlink != 1)
        or (require_nonwritable and opened.st_mode & 0o222)
        or (require_private and stat.S_IMODE(opened.st_mode) != 0o600)
        or (
            require_private and hasattr(os, "geteuid") and opened.st_uid != os.geteuid()
        )
    ):
        os.close(descriptor)
        raise RuntimeError(f"Git pack artifact identity changed: {name}")
    return descriptor, opened_identity


def _require_pack_artifact_unchanged(
    pack_directory: int,
    name: str,
    descriptor: int,
    expected: _FileIdentity,
) -> None:
    try:
        visible = os.stat(name, dir_fd=pack_directory, follow_symlinks=False)
        opened = os.fstat(descriptor)
    except OSError as error:
        raise RuntimeError(
            f"Cannot reauthenticate Git pack artifact {name}: {error}"
        ) from error
    if (
        not stat.S_ISREG(visible.st_mode)
        or not stat.S_ISREG(opened.st_mode)
        or _file_identity(visible) != expected
        or _file_identity(opened) != expected
    ):
        raise RuntimeError(f"Git pack artifact changed: {name}")


def _read_exactly_at(
    descriptor: int,
    size: int,
    offset: int,
    *,
    location: str,
) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    current_offset = offset
    while remaining:
        chunk = os.pread(descriptor, remaining, current_offset)
        if not chunk:
            raise RuntimeError(f"Git {location} was truncated")
        chunks.append(chunk)
        current_offset += len(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _hash_file_prefix(
    descriptor: int,
    size: int,
    *,
    object_format: str,
    location: str,
) -> bytes:
    digest = hashlib.new(object_format, usedforsecurity=False)
    offset = 0
    remaining = size
    while remaining:
        chunk = os.pread(
            descriptor,
            min(remaining, _PACK_READ_CHUNK_BYTES),
            offset,
        )
        if not chunk:
            raise RuntimeError(f"Git {location} was truncated")
        digest.update(chunk)
        offset += len(chunk)
        remaining -= len(chunk)
    return digest.digest()


def _authenticate_pack_contents(
    descriptor: int,
    identity: _FileIdentity,
    lease: GitObjectPromotionLease,
) -> None:
    digest_size = 20 if lease.object_format == "sha1" else 32
    if identity.size < 12 + digest_size:
        raise RuntimeError("Git pack file is truncated")
    header = _read_exactly_at(
        descriptor,
        4,
        0,
        location="pack header",
    )
    if header != b"PACK":
        raise RuntimeError("Git pack file has an invalid header")
    payload_size = identity.size - digest_size
    expected = bytes.fromhex(lease.pack_hash)
    trailer = _read_exactly_at(
        descriptor,
        digest_size,
        payload_size,
        location="pack checksum",
    )
    calculated = _hash_file_prefix(
        descriptor,
        payload_size,
        object_format=lease.object_format,
        location="pack file",
    )
    if trailer != expected or calculated != expected:
        raise RuntimeError("Git pack file checksum does not match its lease")


def _authenticate_index_contents(
    descriptor: int,
    identity: _FileIdentity,
    lease: GitObjectPromotionLease,
) -> None:
    digest_size = 20 if lease.object_format == "sha1" else 32
    if identity.size < 2 * digest_size:
        raise RuntimeError("Git pack index is truncated")
    checksum_offset = identity.size - digest_size
    pack_checksum_offset = checksum_offset - digest_size
    expected = bytes.fromhex(lease.pack_hash)
    pack_checksum = _read_exactly_at(
        descriptor,
        digest_size,
        pack_checksum_offset,
        location="pack index pack checksum",
    )
    index_checksum = _read_exactly_at(
        descriptor,
        digest_size,
        checksum_offset,
        location="pack index checksum",
    )
    calculated = _hash_file_prefix(
        descriptor,
        checksum_offset,
        object_format=lease.object_format,
        location="pack index",
    )
    if pack_checksum != expected or index_checksum != calculated:
        raise RuntimeError("Git pack index checksum does not match its lease")


def _authenticate_keep_contents(
    descriptor: int,
    identity: _FileIdentity,
    lease: GitObjectPromotionLease,
) -> None:
    expected = f"{lease.lease_id}\n".encode("ascii")
    if identity.size != len(expected):
        raise RuntimeError("Git pack keep file does not match its lease")
    actual = _read_exactly_at(
        descriptor,
        identity.size,
        0,
        location="pack keep file",
    )
    if actual != expected:
        raise RuntimeError("Git pack keep file does not match its lease")


def _close_opened_pack_lease(opened: _OpenedPackLease) -> None:
    for descriptor in (
        opened.keep_descriptor,
        opened.index_descriptor,
        opened.pack_descriptor,
    ):
        _close_file_descriptor(descriptor)


def _close_opened_pack_objects(opened: _OpenedPackObjects) -> None:
    for descriptor in (
        opened.index_descriptor,
        opened.pack_descriptor,
    ):
        _close_file_descriptor(descriptor)


def _open_authenticated_pack_objects(
    pack_directory: int,
    lease: GitObjectPromotionLease,
) -> _OpenedPackObjects:
    descriptors: list[int] = []
    try:
        pack_descriptor, pack_identity = _open_pack_artifact(
            pack_directory,
            _pack_artifact_name(lease.pack_hash, "pack"),
            require_single_link=False,
            require_private=False,
            require_nonwritable=True,
        )
        descriptors.append(pack_descriptor)
        index_descriptor, index_identity = _open_pack_artifact(
            pack_directory,
            _pack_artifact_name(lease.pack_hash, "idx"),
            require_single_link=False,
            require_private=False,
            require_nonwritable=True,
        )
        descriptors.append(index_descriptor)
        opened = _OpenedPackObjects(
            pack_descriptor=pack_descriptor,
            pack_identity=pack_identity,
            index_descriptor=index_descriptor,
            index_identity=index_identity,
        )
        _authenticate_pack_contents(pack_descriptor, pack_identity, lease)
        _authenticate_index_contents(index_descriptor, index_identity, lease)
        return opened
    except BaseException:
        for descriptor in reversed(descriptors):
            _close_file_descriptor(descriptor)
        raise


def _open_authenticated_pack_lease(
    pack_directory: int,
    lease: GitObjectPromotionLease,
) -> _OpenedPackLease:
    objects = _open_authenticated_pack_objects(pack_directory, lease)
    keep_descriptor: int | None = None
    try:
        keep_descriptor, keep_identity = _open_pack_artifact(
            pack_directory,
            _pack_artifact_name(lease.pack_hash, "keep"),
            require_single_link=True,
            require_private=True,
            require_nonwritable=False,
        )
        opened = _OpenedPackLease(
            pack_descriptor=objects.pack_descriptor,
            pack_identity=objects.pack_identity,
            index_descriptor=objects.index_descriptor,
            index_identity=objects.index_identity,
            keep_descriptor=keep_descriptor,
            keep_identity=keep_identity,
        )
        _authenticate_keep_contents(keep_descriptor, keep_identity, lease)
        return opened
    except BaseException:
        if keep_descriptor is not None:
            _close_file_descriptor(keep_descriptor)
        for descriptor in (
            objects.index_descriptor,
            objects.pack_descriptor,
        ):
            _close_file_descriptor(descriptor)
        raise


def _require_opened_pack_lease_unchanged(
    pack_directory: int,
    lease: GitObjectPromotionLease,
    opened: _OpenedPackLease,
) -> None:
    _require_pack_artifact_unchanged(
        pack_directory,
        _pack_artifact_name(lease.pack_hash, "pack"),
        opened.pack_descriptor,
        opened.pack_identity,
    )
    _require_pack_artifact_unchanged(
        pack_directory,
        _pack_artifact_name(lease.pack_hash, "idx"),
        opened.index_descriptor,
        opened.index_identity,
    )
    _require_pack_artifact_unchanged(
        pack_directory,
        _pack_artifact_name(lease.pack_hash, "keep"),
        opened.keep_descriptor,
        opened.keep_identity,
    )


def _read_bounded_process_file(
    file: BinaryIO,
    *,
    location: str,
) -> bytes:
    file.seek(0, os.SEEK_END)
    size = file.tell()
    if size > _MAXIMUM_GIT_PIPE_DIAGNOSTIC_BYTES:
        raise RuntimeError(f"{location} exceeded its bounded output limit")
    file.seek(0)
    payload = file.read(_MAXIMUM_GIT_PIPE_DIAGNOSTIC_BYTES + 1)
    if len(payload) != size:
        raise RuntimeError(f"Could not read complete {location}")
    return payload


def _run_bounded_git_config(
    arguments: list[str],
    *,
    environment: dict[str, str],
) -> tuple[int, bytes]:
    process: command_streaming.StreamingProcess | None = None
    owned_file_descriptors: set[int] = set()
    try:
        with (
            tempfile.TemporaryFile() as stdout_file,
            tempfile.TemporaryFile() as stderr_file,
        ):
            stdout_descriptor = _duplicate_tracked_file_descriptor(
                stdout_file.fileno(),
                owned_file_descriptors,
            )
            stderr_descriptor = _duplicate_tracked_file_descriptor(
                stderr_file.fileno(),
                owned_file_descriptors,
            )
            process = start_command(
                ["git", "config", *arguments],
                stdout_fd=stdout_descriptor,
                stderr_fd=stderr_descriptor,
                env=environment,
                capture_stdout=False,
                capture_stderr=False,
            )
            owned_file_descriptors.difference_update(
                (stdout_descriptor, stderr_descriptor)
            )
            exit_code = process.wait()
            output = _read_bounded_process_file(
                stdout_file,
                location="git config stdout",
            )
            error = _read_bounded_process_file(
                stderr_file,
                location="git config stderr",
            )
            if exit_code not in {0, 1} or error:
                diagnostic = error.decode("utf-8", errors="replace").rstrip("\n")
                raise RuntimeError(
                    "Cannot inspect Git promisor configuration "
                    f"(git config={exit_code}): {diagnostic}"
                )
            return exit_code, output
    finally:
        try:
            if process is not None:
                try:
                    command_streaming.terminate_then_kill(process)
                finally:
                    process.close()
        finally:
            for file_descriptor in owned_file_descriptors:
                _close_file_descriptor(file_descriptor)


def _require_unset_config_value(
    arguments: list[str],
    *,
    environment: dict[str, str],
    location: str,
) -> None:
    exit_code, output = _run_bounded_git_config(
        arguments,
        environment=environment,
    )
    if exit_code == 0:
        if not output.endswith(b"\0"):
            raise RuntimeError(f"Git {location} config returned malformed output")
        raise RuntimeError(f"Git {location} repositories are unsupported")
    if output:
        raise RuntimeError(f"Git {location} config returned unexpected output")


def _require_no_promisor_remote_config(
    *,
    environment: dict[str, str],
) -> None:
    exit_code, output = _run_bounded_git_config(
        [
            "--includes",
            "--null",
            "--type=bool",
            "--get-regexp",
            r"^remote\..*\.promisor$",
        ],
        environment=environment,
    )
    if exit_code == 1:
        if output:
            raise RuntimeError("Git promisor remote config returned unexpected output")
        return
    if not output or not output.endswith(b"\0"):
        raise RuntimeError("Git promisor remote config returned malformed output")
    for record in output[:-1].split(b"\0"):
        if record.count(b"\n") != 1:
            raise RuntimeError("Git promisor remote config returned malformed output")
        key, value = record.split(b"\n", 1)
        if (
            not key.startswith(b"remote.")
            or not key.endswith(b".promisor")
            or value not in {b"true", b"false"}
        ):
            raise RuntimeError("Git promisor remote config returned malformed output")
        if value == b"true":
            raise RuntimeError("Git promisor repositories are unsupported")


def _require_no_promisor_pack(pack_directory: int) -> None:
    try:
        with _fresh_pack_directory_entries(pack_directory) as entries:
            for entry in entries:
                if entry.name.startswith("pack-") and entry.name.endswith(".promisor"):
                    raise RuntimeError("Git promisor repositories are unsupported")
    except OSError as error:
        raise RuntimeError(f"Cannot inspect Git promisor packs: {error}") from error


def _require_complete_git_object_store(
    pack_directory: int,
    *,
    environment: dict[str, str],
) -> None:
    _require_unset_config_value(
        [
            "--local",
            "--includes",
            "--null",
            "--get-all",
            "extensions.partialClone",
        ],
        environment=environment,
        location="partial clone",
    )
    _require_unset_config_value(
        [
            "--includes",
            "--null",
            "--get-regexp",
            r"^remote\..*\.partialclonefilter$",
        ],
        environment=environment,
        location="partial clone filter",
    )
    _require_no_promisor_remote_config(environment=environment)
    _require_no_promisor_pack(pack_directory)


def _stream_process_input(
    process: command_streaming.StreamingProcess,
    chunks: Iterable[bytes],
) -> int:
    exit_code: int | None = None
    for event in process.stream(stdin_chunks=chunks):
        if isinstance(event, command_events.ExitEvent):
            exit_code = event.exit_code
    if exit_code is None:
        raise RuntimeError("Git pack producer did not report an exit status")
    return exit_code


def _close_file_descriptor(file_descriptor: int) -> None:
    try:
        os.close(file_descriptor)
    except OSError:
        pass


def _track_file_descriptor(
    owned_file_descriptors: set[int],
    file_descriptor: int,
) -> int:
    try:
        owned_file_descriptors.add(file_descriptor)
    except BaseException:
        _close_file_descriptor(file_descriptor)
        raise
    return file_descriptor


def _duplicate_tracked_file_descriptor(
    source_descriptor: int,
    owned_file_descriptors: set[int],
) -> int:
    return _track_file_descriptor(
        owned_file_descriptors,
        os.dup(source_descriptor),
    )


def _open_tracked_pipe(
    owned_file_descriptors: set[int],
) -> tuple[int, int]:
    read_descriptor, write_descriptor = os.pipe()
    try:
        _track_file_descriptor(owned_file_descriptors, read_descriptor)
    except BaseException:
        _close_file_descriptor(write_descriptor)
        raise
    try:
        _track_file_descriptor(owned_file_descriptors, write_descriptor)
    except BaseException:
        owned_file_descriptors.remove(read_descriptor)
        _close_file_descriptor(read_descriptor)
        raise
    return read_descriptor, write_descriptor


def _inherited_object_directory_path(file_descriptor: int) -> str:
    if sys.platform == "linux":
        return f"/proc/self/fd/{file_descriptor}"
    raise OSError(
        errno.ENOTSUP,
        "descriptor-pinned Git object directories are unsupported",
    )


def _git_pack_pipeline(
    revisions: tuple[bytes, ...],
    *,
    lease_id: str,
    object_directory: int,
    session_lock_descriptor: int,
    producer_environment: dict[str, str],
    consumer_environment: dict[str, str],
) -> bytes:
    owned_file_descriptors: set[int] = set()
    pack_read_fd, pack_write_fd = _open_tracked_pipe(owned_file_descriptors)
    consumer: command_streaming.StreamingProcess | None = None
    producer: command_streaming.StreamingProcess | None = None
    try:
        with (
            tempfile.TemporaryFile() as producer_stderr,
            tempfile.TemporaryFile() as consumer_stdout,
            tempfile.TemporaryFile() as consumer_stderr,
        ):
            consumer_stdout_fd = _duplicate_tracked_file_descriptor(
                consumer_stdout.fileno(),
                owned_file_descriptors,
            )
            consumer_stderr_fd = _duplicate_tracked_file_descriptor(
                consumer_stderr.fileno(),
                owned_file_descriptors,
            )
            consumer_object_fd = _duplicate_tracked_file_descriptor(
                object_directory,
                owned_file_descriptors,
            )
            pinned_consumer_environment = consumer_environment.copy()
            if sys.platform == "darwin":
                pinned_consumer_environment[
                    DARWIN_OBJECT_DIRECTORY_DESCRIPTOR
                ] = str(consumer_object_fd)
            else:
                pinned_consumer_environment["GIT_OBJECT_DIRECTORY"] = (
                    _inherited_object_directory_path(consumer_object_fd)
                )
            consumer = start_command(
                [
                    "git",
                    "-c",
                    "core.fsync=pack,pack-metadata",
                    "-c",
                    "core.fsyncMethod=fsync",
                    "index-pack",
                    "--stdin",
                    f"--keep={lease_id}",
                    "--no-rev-index",
                    "--strict",
                    "--fsck-objects",
                ],
                stdin_fd=pack_read_fd,
                stdout_fd=consumer_stdout_fd,
                stderr_fd=consumer_stderr_fd,
                pass_fds=(session_lock_descriptor, consumer_object_fd),
                env=pinned_consumer_environment,
                capture_stdout=False,
                capture_stderr=False,
            )
            owned_file_descriptors.difference_update(
                (pack_read_fd, consumer_stdout_fd, consumer_stderr_fd)
            )
            _close_file_descriptor(consumer_object_fd)
            owned_file_descriptors.remove(consumer_object_fd)

            producer_stderr_fd = _duplicate_tracked_file_descriptor(
                producer_stderr.fileno(),
                owned_file_descriptors,
            )
            producer = start_command(
                [
                    "git",
                    "--no-replace-objects",
                    "pack-objects",
                    "--revs",
                    "--stdout",
                    "--no-thin",
                    "--compression=0",
                    "--threads=1",
                    "--no-reuse-delta",
                    "--no-reuse-object",
                    "--window=0",
                    "--depth=0",
                    "--no-use-bitmap-index",
                    "--missing=error",
                    "--no-filter",
                    "--no-sparse",
                    "--keep-true-parents",
                ],
                stdin=True,
                stdout_fd=pack_write_fd,
                stderr_fd=producer_stderr_fd,
                pass_fds=(session_lock_descriptor,),
                env=producer_environment,
                capture_stdout=False,
                capture_stderr=False,
            )
            owned_file_descriptors.difference_update(
                (pack_write_fd, producer_stderr_fd)
            )

            producer_exit = _stream_process_input(producer, revisions)
            consumer_exit = consumer.wait()
            producer_error = _read_bounded_process_file(
                producer_stderr,
                location="git pack-objects stderr",
            )
            consumer_error = _read_bounded_process_file(
                consumer_stderr,
                location="git index-pack stderr",
            )
            consumer_output = _read_bounded_process_file(
                consumer_stdout,
                location="git index-pack stdout",
            )
            if producer_exit != 0 or consumer_exit != 0:
                diagnostic = b"\n".join(
                    value.rstrip(b"\n")
                    for value in (producer_error, consumer_error)
                    if value
                ).decode("utf-8", errors="replace")
                raise RuntimeError(
                    "Git object promotion failed "
                    f"(pack-objects={producer_exit}, index-pack={consumer_exit}): "
                    f"{diagnostic}"
                )
            return consumer_output
    finally:
        try:
            if producer is not None:
                try:
                    command_streaming.terminate_then_kill(producer)
                finally:
                    producer.close()
        finally:
            try:
                if consumer is not None:
                    try:
                        command_streaming.terminate_then_kill(consumer)
                    finally:
                        consumer.close()
            finally:
                for file_descriptor in owned_file_descriptors:
                    _close_file_descriptor(file_descriptor)


def _lease_from_index_pack_output(
    output: bytes,
    *,
    lease_id: str,
    object_format: str,
) -> GitObjectPromotionLease:
    try:
        response = output.decode("ascii", errors="strict")
    except UnicodeDecodeError as error:
        raise RuntimeError("git index-pack returned an unexpected response") from error
    if response.count("\n") != 1 or not response.endswith("\n"):
        raise RuntimeError("git index-pack returned an unexpected response")
    fields = response[:-1].split("\t")
    if len(fields) != 2 or fields[0] not in {"keep", "pack"}:
        raise RuntimeError("git index-pack returned an unexpected response")
    pack_hash = fields[1]
    try:
        _require_object_ids(
            (pack_hash,),
            object_format=object_format,
            location="pack_hash",
        )
    except ValueError as error:
        raise RuntimeError("git index-pack returned an unexpected response") from error
    return GitObjectPromotionLease(
        lease_id=lease_id,
        pack_hash=pack_hash,
        object_format=object_format,
        keep_device=-1,
        keep_inode=-1,
        keep_changed_ns=-1,
        prior_released_device=None,
        prior_released_inode=None,
    )


def _require_promotion_lease(
    quarantine: GitObjectQuarantine,
    lease: GitObjectPromotionLease,
) -> GitObjectPromotionLease:
    if not isinstance(lease, GitObjectPromotionLease):
        raise ValueError("a Git object promotion lease is required")
    lease_id = _require_lease_id(lease.lease_id)
    if lease.object_format != quarantine.object_format:
        raise ValueError("the Git object promotion lease uses another object format")
    pack_hashes = _require_object_ids(
        (lease.pack_hash,),
        object_format=lease.object_format,
        location="pack_hash",
    )
    if (
        not isinstance(lease.keep_device, int)
        or isinstance(lease.keep_device, bool)
        or lease.keep_device < 0
        or not isinstance(lease.keep_inode, int)
        or isinstance(lease.keep_inode, bool)
        or lease.keep_inode <= 0
        or not isinstance(lease.keep_changed_ns, int)
        or isinstance(lease.keep_changed_ns, bool)
        or lease.keep_changed_ns < 0
    ):
        raise ValueError("the Git object promotion lease has an invalid keep identity")
    prior_released_identity = (
        lease.prior_released_device,
        lease.prior_released_inode,
    )
    if not (
        prior_released_identity == (None, None)
        or (
            isinstance(lease.prior_released_device, int)
            and not isinstance(lease.prior_released_device, bool)
            and lease.prior_released_device >= 0
            and isinstance(lease.prior_released_inode, int)
            and not isinstance(lease.prior_released_inode, bool)
            and lease.prior_released_inode > 0
        )
    ):
        raise ValueError(
            "the Git object promotion lease has an invalid prior release identity"
        )
    return GitObjectPromotionLease(
        lease_id=lease_id,
        pack_hash=pack_hashes[0],
        object_format=lease.object_format,
        keep_device=lease.keep_device,
        keep_inode=lease.keep_inode,
        keep_changed_ns=lease.keep_changed_ns,
        prior_released_device=lease.prior_released_device,
        prior_released_inode=lease.prior_released_inode,
    )


def _fsync_pack_lease(
    object_directory: int,
    pack_directory: int,
    lease: GitObjectPromotionLease,
    opened: _OpenedPackLease,
) -> None:
    _require_opened_pack_lease_unchanged(pack_directory, lease, opened)
    os.fsync(opened.pack_descriptor)
    os.fsync(opened.index_descriptor)
    os.fsync(opened.keep_descriptor)
    _require_opened_pack_lease_unchanged(pack_directory, lease, opened)
    os.fsync(pack_directory)
    os.fsync(object_directory)
    _require_opened_pack_lease_unchanged(pack_directory, lease, opened)


def _require_promoted_objects(
    expected_objects: Mapping[str, str],
    *,
    persistent_environment: dict[str, str],
) -> None:
    promoted = resolve_git_objects(
        tuple(expected_objects),
        env=persistent_environment,
    )
    for object_id, expected_type in expected_objects.items():
        actual = promoted.get(object_id)
        if (
            actual is None
            or actual.object_id != object_id
            or actual.object_type != expected_type
        ):
            raise RuntimeError(
                f"Promoted Git object {object_id} is not an accessible {expected_type}"
            )


def _native_keep_pack_hash(name: str, *, object_format: str) -> str | None:
    object_id_length = 40 if object_format == "sha1" else 64
    prefix = "pack-"
    suffix = ".keep"
    if (
        len(name) != len(prefix) + object_id_length + len(suffix)
        or not name.startswith(prefix)
        or not name.endswith(suffix)
    ):
        return None
    pack_hash = name[len(prefix) : -len(suffix)]
    if any(character not in "0123456789abcdef" for character in pack_hash):
        return None
    return pack_hash


def _exact_released_marker_pack_hash(
    name: str,
    *,
    lease_id: str,
    object_format: str,
) -> str | None:
    prefix = ".git-stage-batch-released-"
    suffix = f"-{_released_lease_digest(lease_id)}"
    if not name.startswith(prefix) or not name.endswith(suffix):
        return None
    object_id_length = 40 if object_format == "sha1" else 64
    expected_length = len(prefix) + object_id_length + len(suffix)
    if len(name) != expected_length:
        raise RuntimeError("A Git pack release marker has a malformed name")
    pack_hash = name[len(prefix) : -len(suffix)]
    if any(character not in "0123456789abcdef" for character in pack_hash):
        raise RuntimeError("A Git pack release marker has a malformed pack hash")
    return pack_hash


def _exact_lease_keep_identity(
    pack_directory: int,
    name: str,
    *,
    expected_contents: bytes,
) -> _FileIdentity | None:
    try:
        visible = os.stat(name, dir_fd=pack_directory, follow_symlinks=False)
    except OSError as error:
        raise RuntimeError(
            f"Cannot inspect Git pack lease candidate {name}: {error}"
        ) from error
    if not stat.S_ISREG(visible.st_mode):
        raise RuntimeError(f"Git pack lease candidate is not a regular file: {name}")

    descriptor: int | None = None
    identity: _FileIdentity | None = None
    try:
        descriptor, identity = _open_pack_artifact(
            pack_directory,
            name,
            require_single_link=False,
            require_private=False,
            require_nonwritable=False,
        )
        if identity.size != len(expected_contents):
            return None
        actual = _read_exactly_at(
            descriptor,
            identity.size,
            0,
            location="pack keep file",
        )
        if actual != expected_contents:
            return None
        return identity
    finally:
        if descriptor is not None and identity is not None:
            try:
                _require_pack_artifact_unchanged(
                    pack_directory,
                    name,
                    descriptor,
                    identity,
                )
            finally:
                _close_file_descriptor(descriptor)


def _find_exact_existing_pack_lease(
    pack_directory: int,
    *,
    lease_id: str,
    object_format: str,
) -> tuple[GitObjectPromotionLease, _FileIdentity] | None:
    expected_contents = f"{lease_id}\n".encode("ascii")
    existing: tuple[GitObjectPromotionLease, _FileIdentity] | None = None
    try:
        with _fresh_pack_directory_entries(pack_directory) as entries:
            for entry in entries:
                pack_hash = _native_keep_pack_hash(
                    entry.name,
                    object_format=object_format,
                )
                if pack_hash is None:
                    continue
                identity = _exact_lease_keep_identity(
                    pack_directory,
                    entry.name,
                    expected_contents=expected_contents,
                )
                if identity is None:
                    continue
                if existing is not None:
                    raise RuntimeError(
                        "Multiple Git pack leases match the requested lease_id"
                    )
                existing = (
                    GitObjectPromotionLease(
                        lease_id=lease_id,
                        pack_hash=pack_hash,
                        object_format=object_format,
                        keep_device=identity.device,
                        keep_inode=identity.inode,
                        keep_changed_ns=identity.changed_ns,
                        prior_released_device=None,
                        prior_released_inode=None,
                    ),
                    identity,
                )
    except OSError as error:
        raise RuntimeError(f"Cannot scan Git pack leases: {error}") from error
    return existing


def _find_exact_released_pack_lease(
    pack_directory: int,
    *,
    lease_id: str,
    object_format: str,
) -> tuple[GitObjectPromotionLease, _FileIdentity] | None:
    expected_contents = f"{lease_id}\n".encode("ascii")
    existing: tuple[GitObjectPromotionLease, _FileIdentity] | None = None
    try:
        with _fresh_pack_directory_entries(pack_directory) as entries:
            for entry in entries:
                pack_hash = _exact_released_marker_pack_hash(
                    entry.name,
                    lease_id=lease_id,
                    object_format=object_format,
                )
                if pack_hash is None:
                    continue
                identity = _exact_lease_keep_identity(
                    pack_directory,
                    entry.name,
                    expected_contents=expected_contents,
                )
                if identity is None:
                    raise RuntimeError(
                        "A Git pack release marker does not match its lease_id"
                    )
                if existing is not None:
                    raise RuntimeError(
                        "Multiple Git pack release markers match the requested lease_id"
                    )
                existing = (
                    GitObjectPromotionLease(
                        lease_id=lease_id,
                        pack_hash=pack_hash,
                        object_format=object_format,
                        keep_device=identity.device,
                        keep_inode=identity.inode,
                        keep_changed_ns=identity.changed_ns,
                        prior_released_device=None,
                        prior_released_inode=None,
                    ),
                    identity,
                )
    except OSError as error:
        raise RuntimeError(f"Cannot scan Git pack release markers: {error}") from error
    return existing


def _authenticate_promoted_pack_lease(
    object_directory: int,
    pack_directory: int,
    parsed_lease: GitObjectPromotionLease,
    *,
    expected_objects: Mapping[str, str] | None,
    persistent_environment: dict[str, str] | None,
    expected_keep_identity: _FileIdentity | None,
) -> GitObjectPromotionLease:
    opened = _open_authenticated_pack_lease(pack_directory, parsed_lease)
    try:
        if (
            expected_keep_identity is not None
            and opened.keep_identity != expected_keep_identity
        ):
            raise RuntimeError(
                "The existing Git pack keep file changed during lease retry"
            )
        canonical_released_name = _released_lease_name(parsed_lease)
        prior_released_identity: _FileIdentity | None = None
        if not _artifact_is_absent(pack_directory, canonical_released_name):
            prior_released_identity = _authenticate_released_lease_marker(
                object_directory,
                pack_directory,
                parsed_lease,
                released_name=canonical_released_name,
                expected_identity=None,
            )
        lease = GitObjectPromotionLease(
            lease_id=parsed_lease.lease_id,
            pack_hash=parsed_lease.pack_hash,
            object_format=parsed_lease.object_format,
            keep_device=opened.keep_identity.device,
            keep_inode=opened.keep_identity.inode,
            keep_changed_ns=opened.keep_identity.changed_ns,
            prior_released_device=(
                prior_released_identity.device
                if prior_released_identity is not None
                else None
            ),
            prior_released_inode=(
                prior_released_identity.inode
                if prior_released_identity is not None
                else None
            ),
        )
        _fsync_pack_lease(
            object_directory,
            pack_directory,
            lease,
            opened,
        )
        if expected_objects is not None:
            if persistent_environment is None:
                raise TypeError(
                    "expected promoted objects require a persistent environment"
                )
            _require_promoted_objects(
                expected_objects,
                persistent_environment=persistent_environment,
            )
        _require_opened_pack_lease_unchanged(
            pack_directory,
            lease,
            opened,
        )
        if prior_released_identity is not None:
            final_prior_identity = _authenticate_released_lease_marker(
                object_directory,
                pack_directory,
                lease,
                released_name=canonical_released_name,
                expected_identity=(
                    prior_released_identity.device,
                    prior_released_identity.inode,
                ),
            )
            if final_prior_identity != prior_released_identity:
                raise RuntimeError("The prior released Git pack marker changed")
        elif not _artifact_is_absent(pack_directory, canonical_released_name):
            raise RuntimeError("A prior released Git pack marker appeared")
        return lease
    finally:
        try:
            _require_opened_pack_lease_unchanged(
                pack_directory,
                parsed_lease,
                opened,
            )
        finally:
            _close_opened_pack_lease(opened)


def promote_git_object_closure(
    quarantine: GitObjectQuarantine,
    *,
    lease_id: str,
    include: Iterable[str],
    exclude: Iterable[str] = (),
    expected_objects: Mapping[str, str],
) -> GitObjectPromotionLease:
    """Promote one verified object closure without buffering its pack in Python."""
    if not isinstance(quarantine, GitObjectQuarantine):
        raise ValueError("a Git object quarantine capability is required")
    validated_lease_id = _require_lease_id(lease_id)
    include_ids = _require_object_ids(
        include,
        object_format=quarantine.object_format,
        location="include",
    )
    if not include_ids:
        raise ValueError("include must contain at least one Git object ID")
    exclude_ids = _require_object_ids(
        exclude,
        object_format=quarantine.object_format,
        location="exclude",
    )
    expected_ids = _require_object_ids(
        expected_objects,
        object_format=quarantine.object_format,
        location="expected_objects",
    )
    if not expected_ids:
        raise ValueError("expected_objects must not be empty")
    if not set(include_ids).issubset(expected_objects):
        raise ValueError("every included root must have an expected object type")
    allowed_types = {"blob", "tree", "commit", "tag"}
    if any(
        object_type not in allowed_types for object_type in expected_objects.values()
    ):
        raise ValueError("expected_objects contains an unsupported Git object type")

    persistent_environment = quarantine.persistent_environment()
    quarantine_environment = quarantine.environment()
    for environment in (persistent_environment, quarantine_environment):
        environment["GIT_ALLOW_PROTOCOL"] = ""
        environment["GIT_NO_REPLACE_OBJECTS"] = "1"
        environment["GIT_NO_LAZY_FETCH"] = "1"
    with (
        acquire_session_lock_descriptor() as session_lock_descriptor,
        quarantine.pinned_persistent_object_directory() as object_directory,
    ):
        with _pinned_pack_directory(object_directory) as pack_directory:
            _require_complete_git_object_store(
                pack_directory,
                environment=persistent_environment,
            )
            existing_lease = _find_exact_existing_pack_lease(
                pack_directory,
                lease_id=validated_lease_id,
                object_format=quarantine.object_format,
            )
            if existing_lease is not None:
                parsed_lease, keep_identity = existing_lease
                return _authenticate_promoted_pack_lease(
                    object_directory,
                    pack_directory,
                    parsed_lease,
                    expected_objects=expected_objects,
                    persistent_environment=persistent_environment,
                    expected_keep_identity=keep_identity,
                )
            requested_ids = tuple(
                dict.fromkeys((*include_ids, *exclude_ids, *expected_ids))
            )
            resolved = resolve_git_objects(
                requested_ids,
                env=quarantine_environment,
            )
            missing = tuple(
                object_id for object_id in requested_ids if object_id not in resolved
            )
            if missing:
                raise RuntimeError(
                    f"Git object promotion input is missing {missing[0]}"
                )
            for object_id in requested_ids:
                if resolved[object_id].object_id != object_id:
                    raise RuntimeError(
                        f"Git object promotion input resolved unexpectedly: {object_id}"
                    )
            for object_id, expected_type in expected_objects.items():
                if resolved[object_id].object_type != expected_type:
                    raise RuntimeError(
                        f"Git object {object_id} is "
                        f"{resolved[object_id].object_type}, not {expected_type}"
                    )

            revisions = tuple(
                chain(
                    (f"{object_id}\n".encode("ascii") for object_id in include_ids),
                    (f"^{object_id}\n".encode("ascii") for object_id in exclude_ids),
                )
            )
            output = _git_pack_pipeline(
                revisions,
                lease_id=validated_lease_id,
                object_directory=object_directory,
                session_lock_descriptor=session_lock_descriptor,
                producer_environment=quarantine_environment,
                consumer_environment=persistent_environment,
            )
            parsed_lease = _lease_from_index_pack_output(
                output,
                lease_id=validated_lease_id,
                object_format=quarantine.object_format,
            )
            installed_lease = _find_exact_existing_pack_lease(
                pack_directory,
                lease_id=validated_lease_id,
                object_format=quarantine.object_format,
            )
            if installed_lease is None or installed_lease[0].pack_hash != (
                parsed_lease.pack_hash
            ):
                raise RuntimeError(
                    "Git pack keep file does not match the requested lease"
                )
            parsed_lease, keep_identity = installed_lease
            return _authenticate_promoted_pack_lease(
                object_directory,
                pack_directory,
                parsed_lease,
                expected_objects=expected_objects,
                persistent_environment=persistent_environment,
                expected_keep_identity=keep_identity,
            )


def _artifact_is_absent(pack_directory: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=pack_directory, follow_symlinks=False)
    except OSError as error:
        if error.errno == errno.ENOENT:
            return True
        raise RuntimeError(
            f"Cannot inspect Git pack artifact {name}: {error}"
        ) from error
    return False


def _pack_node_identity(pack_directory: int, name: str) -> _FileIdentity:
    try:
        return _file_identity(
            os.stat(name, dir_fd=pack_directory, follow_symlinks=False)
        )
    except OSError as error:
        raise RuntimeError(
            f"Cannot authenticate Git pack artifact {name}: {error}"
        ) from error


def _require_pack_node_identity(
    pack_directory: int,
    name: str,
    expected: _FileIdentity,
) -> None:
    if _pack_node_identity(pack_directory, name) != expected:
        raise RuntimeError(f"Git pack artifact changed: {name}")


def _node_survived_rename(
    before: _FileIdentity,
    after: _FileIdentity,
) -> bool:
    return (
        before.device == after.device
        and before.inode == after.inode
        and before.mode == after.mode
        and before.links == after.links
        and before.owner == after.owner
        and before.group == after.group
        and before.size == after.size
        and before.modified_ns == after.modified_ns
    )


def _rollback_unowned_release_move(
    pack_directory: int,
    *,
    keep_name: str,
    released_name: str,
) -> None:
    """Restore a node moved by a raced release without replacing or deleting it."""
    try:
        moved_identity = _pack_node_identity(pack_directory, released_name)
        if not _artifact_is_absent(pack_directory, keep_name):
            raise RuntimeError("Git pack keep path reappeared before release rollback")
        _require_pack_node_identity(
            pack_directory,
            released_name,
            moved_identity,
        )
        _rename_noreplace(pack_directory, released_name, keep_name)
        if not _artifact_is_absent(pack_directory, released_name):
            raise RuntimeError("Released Git pack marker reappeared during rollback")
        restored_identity = _pack_node_identity(pack_directory, keep_name)
        if not _node_survived_rename(moved_identity, restored_identity):
            raise RuntimeError("The raced Git pack keep node changed during rollback")
        os.fsync(pack_directory)
        if not _artifact_is_absent(pack_directory, released_name):
            raise RuntimeError("Released Git pack marker reappeared after rollback")
        _require_pack_node_identity(pack_directory, keep_name, restored_identity)
    except (OSError, RuntimeError) as error:
        raise RuntimeError(
            f"Cannot restore a raced Git pack keep path: {error}"
        ) from error


def _require_pack_and_index_unchanged(
    pack_directory: int,
    lease: GitObjectPromotionLease,
    opened: _OpenedPackLease | _OpenedPackObjects,
) -> None:
    _require_pack_artifact_unchanged(
        pack_directory,
        _pack_artifact_name(lease.pack_hash, "pack"),
        opened.pack_descriptor,
        opened.pack_identity,
    )
    _require_pack_artifact_unchanged(
        pack_directory,
        _pack_artifact_name(lease.pack_hash, "idx"),
        opened.index_descriptor,
        opened.index_identity,
    )


def _require_recorded_keep_identity(
    lease: GitObjectPromotionLease,
    identity: _FileIdentity,
    *,
    require_original_change_time: bool,
) -> None:
    if (identity.device, identity.inode) != (lease.keep_device, lease.keep_inode) or (
        require_original_change_time and identity.changed_ns != lease.keep_changed_ns
    ):
        raise RuntimeError("The Git pack keep file does not match its lease identity")


def _recorded_prior_release_identity(
    lease: GitObjectPromotionLease,
) -> tuple[int, int] | None:
    if lease.prior_released_device is None or lease.prior_released_inode is None:
        return None
    return lease.prior_released_device, lease.prior_released_inode


def _require_open_keep_authenticated(
    lease: GitObjectPromotionLease,
    opened: _OpenedPackLease,
    *,
    expected_links: int = 1,
) -> None:
    try:
        metadata = os.fstat(opened.keep_descriptor)
    except OSError as error:
        raise RuntimeError(
            f"Cannot reauthenticate the open Git pack keep file: {error}"
        ) from error
    identity = _file_identity(metadata)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or (metadata.st_dev, metadata.st_ino)
        != (opened.keep_identity.device, opened.keep_identity.inode)
        or metadata.st_nlink != expected_links
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or (hasattr(os, "geteuid") and metadata.st_uid != os.geteuid())
    ):
        raise RuntimeError("The open Git pack keep file identity changed")
    _require_recorded_keep_identity(
        lease,
        identity,
        require_original_change_time=False,
    )
    _authenticate_keep_contents(opened.keep_descriptor, identity, lease)


def _require_keep_moved(
    pack_directory: int,
    lease: GitObjectPromotionLease,
    opened: _OpenedPackLease,
    *,
    released_name: str,
) -> _FileIdentity:
    keep_name = _pack_artifact_name(lease.pack_hash, "keep")
    if not _artifact_is_absent(pack_directory, keep_name):
        raise RuntimeError("Git pack keep file reappeared during lease release")
    try:
        visible = os.stat(
            released_name,
            dir_fd=pack_directory,
            follow_symlinks=False,
        )
        metadata = os.fstat(opened.keep_descriptor)
    except OSError as error:
        raise RuntimeError(
            f"Cannot authenticate released Git pack lease: {error}"
        ) from error
    visible_identity = _file_identity(visible)
    opened_identity = _file_identity(metadata)
    if (
        not stat.S_ISREG(visible.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or (visible.st_dev, visible.st_ino)
        != (opened.keep_identity.device, opened.keep_identity.inode)
        or (metadata.st_dev, metadata.st_ino)
        != (opened.keep_identity.device, opened.keep_identity.inode)
        or visible_identity != opened_identity
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or (hasattr(os, "geteuid") and metadata.st_uid != os.geteuid())
    ):
        raise RuntimeError("The released Git pack keep file identity changed")
    _authenticate_keep_contents(
        opened.keep_descriptor,
        opened_identity,
        lease,
    )
    return opened_identity


def _authenticate_released_lease_marker(
    object_directory: int,
    pack_directory: int,
    lease: GitObjectPromotionLease,
    *,
    released_name: str,
    expected_identity: tuple[int, int] | None,
) -> _FileIdentity:
    descriptor, identity = _open_pack_artifact(
        pack_directory,
        released_name,
        require_single_link=True,
        require_private=True,
        require_nonwritable=False,
    )
    try:
        if (
            expected_identity is not None
            and (
                identity.device,
                identity.inode,
            )
            != expected_identity
        ):
            raise RuntimeError(
                "The released Git pack keep file does not match its lease identity"
            )
        _authenticate_keep_contents(descriptor, identity, lease)
        _require_pack_artifact_unchanged(
            pack_directory,
            released_name,
            descriptor,
            identity,
        )
        os.fsync(descriptor)
        os.fsync(pack_directory)
        os.fsync(object_directory)
        _require_pack_artifact_unchanged(
            pack_directory,
            released_name,
            descriptor,
            identity,
        )
        return identity
    finally:
        _close_file_descriptor(descriptor)


def _authenticate_released_pack_lease(
    object_directory: int,
    pack_directory: int,
    parsed_lease: GitObjectPromotionLease,
    *,
    expected_marker_identity: _FileIdentity,
) -> GitObjectPromotionLease:
    marker_name = _released_lease_name(parsed_lease)
    keep_name = _pack_artifact_name(parsed_lease.pack_hash, "keep")
    if not _artifact_is_absent(pack_directory, keep_name):
        raise RuntimeError(
            "An active Git pack keep appeared beside its released marker"
        )
    opened = _open_authenticated_pack_objects(pack_directory, parsed_lease)
    try:
        marker_identity = _authenticate_released_lease_marker(
            object_directory,
            pack_directory,
            parsed_lease,
            released_name=marker_name,
            expected_identity=(
                expected_marker_identity.device,
                expected_marker_identity.inode,
            ),
        )
        if marker_identity != expected_marker_identity:
            raise RuntimeError("The released Git pack marker changed during adoption")
        _require_pack_and_index_unchanged(
            pack_directory,
            parsed_lease,
            opened,
        )
        os.fsync(opened.pack_descriptor)
        os.fsync(opened.index_descriptor)
        _require_pack_and_index_unchanged(
            pack_directory,
            parsed_lease,
            opened,
        )
        os.fsync(pack_directory)
        os.fsync(object_directory)
        _require_pack_and_index_unchanged(
            pack_directory,
            parsed_lease,
            opened,
        )
        final_marker_identity = _authenticate_released_lease_marker(
            object_directory,
            pack_directory,
            parsed_lease,
            released_name=marker_name,
            expected_identity=(marker_identity.device, marker_identity.inode),
        )
        if final_marker_identity != marker_identity or not _artifact_is_absent(
            pack_directory,
            keep_name,
        ):
            raise RuntimeError("The released Git pack lease changed during adoption")
        return GitObjectPromotionLease(
            lease_id=parsed_lease.lease_id,
            pack_hash=parsed_lease.pack_hash,
            object_format=parsed_lease.object_format,
            keep_device=marker_identity.device,
            keep_inode=marker_identity.inode,
            keep_changed_ns=marker_identity.changed_ns,
            prior_released_device=None,
            prior_released_inode=None,
        )
    finally:
        try:
            _require_pack_and_index_unchanged(
                pack_directory,
                parsed_lease,
                opened,
            )
        finally:
            _close_opened_pack_objects(opened)


def _fsync_release_directories(
    object_directory: int,
    pack_directory: int,
) -> None:
    os.fsync(pack_directory)
    os.fsync(object_directory)


def _node_survived_unlink(
    before: _FileIdentity,
    after: _FileIdentity,
) -> bool:
    return (
        before.device == after.device
        and before.inode == after.inode
        and before.mode == after.mode
        and before.links == 1
        and after.links == 0
        and before.owner == after.owner
        and before.group == after.group
        and before.size == after.size
        and before.modified_ns == after.modified_ns
    )


def _unlink_authenticated_released_lease_marker(
    object_directory: int,
    pack_directory: int,
    lease: GitObjectPromotionLease,
    *,
    released_name: str,
    expected_identity: tuple[int, int],
) -> None:
    descriptor, identity = _open_pack_artifact(
        pack_directory,
        released_name,
        require_single_link=True,
        require_private=True,
        require_nonwritable=False,
    )
    unlinked = False
    post_unlink_identity: _FileIdentity | None = None
    try:
        if (identity.device, identity.inode) != expected_identity:
            raise RuntimeError(
                "The released Git pack keep file does not match its lease identity"
            )
        _authenticate_keep_contents(descriptor, identity, lease)
        _require_pack_artifact_unchanged(
            pack_directory,
            released_name,
            descriptor,
            identity,
        )
        os.fsync(descriptor)
        _require_pack_artifact_unchanged(
            pack_directory,
            released_name,
            descriptor,
            identity,
        )
        try:
            os.unlink(released_name, dir_fd=pack_directory)
            unlinked = True
        except BaseException:
            try:
                metadata_after_error = os.fstat(descriptor)
                unlinked = metadata_after_error.st_nlink == 0 and _artifact_is_absent(
                    pack_directory, released_name
                )
            finally:
                if not unlinked:
                    _require_pack_artifact_unchanged(
                        pack_directory,
                        released_name,
                        descriptor,
                        identity,
                    )
            raise
        finally:
            if unlinked:
                metadata_after = os.fstat(descriptor)
                post_unlink_identity = _file_identity(metadata_after)
                if (
                    not stat.S_ISREG(metadata_after.st_mode)
                    or not _node_survived_unlink(identity, post_unlink_identity)
                    or not _artifact_is_absent(pack_directory, released_name)
                ):
                    raise RuntimeError(
                        "The released Git pack marker changed during cleanup"
                    )
                _fsync_release_directories(
                    object_directory,
                    pack_directory,
                )
                if _file_identity(
                    os.fstat(descriptor)
                ) != post_unlink_identity or not _artifact_is_absent(
                    pack_directory, released_name
                ):
                    raise RuntimeError(
                        "The released Git pack marker changed after cleanup"
                    )
    finally:
        try:
            if unlinked:
                final_identity = _file_identity(os.fstat(descriptor))
                if (
                    post_unlink_identity is None
                    or final_identity != post_unlink_identity
                    or final_identity.links != 0
                    or not _artifact_is_absent(pack_directory, released_name)
                ):
                    raise RuntimeError(
                        "The released Git pack marker cleanup was not stable"
                    )
            else:
                _require_pack_artifact_unchanged(
                    pack_directory,
                    released_name,
                    descriptor,
                    identity,
                )
        finally:
            _close_file_descriptor(descriptor)


def _select_released_marker_identity(
    pack_directory: int,
    name: str,
    candidates: tuple[tuple[int, int], ...],
) -> tuple[int, int]:
    identity = _pack_node_identity(pack_directory, name)
    visible = identity.device, identity.inode
    if visible not in candidates:
        raise RuntimeError(
            "The released Git pack keep file does not match a recorded identity"
        )
    return visible


def adopt_git_object_promotion_lease(
    quarantine: GitObjectQuarantine,
    *,
    lease_id: str,
) -> GitObjectPromotionLease | None:
    """Recover one exact durable pack lease without rebuilding its object set."""
    if not isinstance(quarantine, GitObjectQuarantine):
        raise ValueError("a Git object quarantine capability is required")
    validated_lease_id = _require_lease_id(lease_id)
    with (
        acquire_session_lock(),
        quarantine.pinned_persistent_object_directory() as object_directory,
    ):
        with _pinned_pack_directory(object_directory) as pack_directory:
            active = _find_exact_existing_pack_lease(
                pack_directory,
                lease_id=validated_lease_id,
                object_format=quarantine.object_format,
            )
            released = _find_exact_released_pack_lease(
                pack_directory,
                lease_id=validated_lease_id,
                object_format=quarantine.object_format,
            )
            if (
                active is not None
                and released is not None
                and (active[0].pack_hash != released[0].pack_hash)
            ):
                raise RuntimeError(
                    "Active and released Git pack leases disagree on their pack"
                )

            adopted: GitObjectPromotionLease | None
            if active is not None:
                active_lease, active_identity = active
                adopted = _authenticate_promoted_pack_lease(
                    object_directory,
                    pack_directory,
                    active_lease,
                    expected_objects=None,
                    persistent_environment=None,
                    expected_keep_identity=active_identity,
                )
                expected_prior_identity = (
                    None
                    if released is None
                    else (released[1].device, released[1].inode)
                )
                if _recorded_prior_release_identity(adopted) != (
                    expected_prior_identity
                ):
                    raise RuntimeError(
                        "The prior Git pack release marker changed during adoption"
                    )
            elif released is not None:
                released_lease, released_identity = released
                adopted = _authenticate_released_pack_lease(
                    object_directory,
                    pack_directory,
                    released_lease,
                    expected_marker_identity=released_identity,
                )
            else:
                adopted = None

            final_active = _find_exact_existing_pack_lease(
                pack_directory,
                lease_id=validated_lease_id,
                object_format=quarantine.object_format,
            )
            final_released = _find_exact_released_pack_lease(
                pack_directory,
                lease_id=validated_lease_id,
                object_format=quarantine.object_format,
            )
            if final_active != active or final_released != released:
                raise RuntimeError("The Git pack lease changed during adoption")
            return adopted


def release_git_object_promotion_lease(
    quarantine: GitObjectQuarantine,
    lease: GitObjectPromotionLease,
) -> bool:
    """Durably release an authenticated pack lease after ref publication.

    Return true when this call removed the active ``.keep`` name and all
    recovery markers, or false when a prior call already removed the keep.
    """
    if not isinstance(quarantine, GitObjectQuarantine):
        raise ValueError("a Git object quarantine capability is required")
    authenticated_lease = _require_promotion_lease(quarantine, lease)
    with (
        acquire_session_lock(),
        quarantine.pinned_persistent_object_directory() as object_directory,
    ):
        with _pinned_pack_directory(object_directory) as pack_directory:
            keep_name = _pack_artifact_name(
                authenticated_lease.pack_hash,
                "keep",
            )
            canonical_released_name = _released_lease_name(authenticated_lease)
            keep_absent = _artifact_is_absent(pack_directory, keep_name)
            canonical_released_absent = _artifact_is_absent(
                pack_directory,
                canonical_released_name,
            )
            keep_identity = (
                authenticated_lease.keep_device,
                authenticated_lease.keep_inode,
            )
            prior_released_identity = _recorded_prior_release_identity(
                authenticated_lease
            )
            if keep_absent:
                if not canonical_released_absent:
                    canonical_candidates: tuple[tuple[int, int], ...] = (keep_identity,)
                    if (
                        prior_released_identity is not None
                        and prior_released_identity != keep_identity
                    ):
                        canonical_candidates = (
                            *canonical_candidates,
                            prior_released_identity,
                        )
                    expected_canonical_identity = _select_released_marker_identity(
                        pack_directory,
                        canonical_released_name,
                        canonical_candidates,
                    )
                    _unlink_authenticated_released_lease_marker(
                        object_directory,
                        pack_directory,
                        authenticated_lease,
                        released_name=canonical_released_name,
                        expected_identity=expected_canonical_identity,
                    )
                _fsync_release_directories(
                    object_directory,
                    pack_directory,
                )
                if not _artifact_is_absent(
                    pack_directory, keep_name
                ) or not _artifact_is_absent(
                    pack_directory,
                    canonical_released_name,
                ):
                    raise RuntimeError(
                        "Git pack keep file appeared during lease recovery"
                    )
                return False

            opened = _open_authenticated_pack_lease(
                pack_directory,
                authenticated_lease,
            )
            _require_recorded_keep_identity(
                authenticated_lease,
                opened.keep_identity,
                require_original_change_time=True,
            )
            moved = False
            moved_identity: _FileIdentity | None = None
            rollback_attempted = False
            try:
                if not canonical_released_absent:
                    if prior_released_identity is None:
                        raise RuntimeError(
                            "The Git pack lease has an unexpected prior release marker"
                        )
                    _authenticate_released_lease_marker(
                        object_directory,
                        pack_directory,
                        authenticated_lease,
                        released_name=canonical_released_name,
                        expected_identity=prior_released_identity,
                    )
                    _require_opened_pack_lease_unchanged(
                        pack_directory,
                        authenticated_lease,
                        opened,
                    )
                    _unlink_authenticated_released_lease_marker(
                        object_directory,
                        pack_directory,
                        authenticated_lease,
                        released_name=canonical_released_name,
                        expected_identity=prior_released_identity,
                    )
                    _require_opened_pack_lease_unchanged(
                        pack_directory,
                        authenticated_lease,
                        opened,
                    )
                    if not _artifact_is_absent(
                        pack_directory,
                        canonical_released_name,
                    ):
                        raise RuntimeError(
                            "The prior released Git pack marker reappeared"
                        )
                _require_opened_pack_lease_unchanged(
                    pack_directory,
                    authenticated_lease,
                    opened,
                )
                try:
                    _rename_noreplace(
                        pack_directory,
                        keep_name,
                        canonical_released_name,
                    )
                except OSError as error:
                    raise RuntimeError(
                        f"Cannot release Git pack lease: {error}"
                    ) from error
                moved = True
                try:
                    moved_identity = _require_keep_moved(
                        pack_directory,
                        authenticated_lease,
                        opened,
                        released_name=canonical_released_name,
                    )
                except BaseException:
                    rollback_attempted = True
                    _rollback_unowned_release_move(
                        pack_directory,
                        keep_name=keep_name,
                        released_name=canonical_released_name,
                    )
                    moved = False
                    raise
                _fsync_release_directories(
                    object_directory,
                    pack_directory,
                )
                _require_pack_and_index_unchanged(
                    pack_directory,
                    authenticated_lease,
                    opened,
                )
                final_moved_identity = _require_keep_moved(
                    pack_directory,
                    authenticated_lease,
                    opened,
                    released_name=canonical_released_name,
                )
                if final_moved_identity != moved_identity:
                    raise RuntimeError(
                        "The released Git pack keep file changed during fsync"
                    )
                _unlink_authenticated_released_lease_marker(
                    object_directory,
                    pack_directory,
                    authenticated_lease,
                    released_name=canonical_released_name,
                    expected_identity=keep_identity,
                )
                _fsync_release_directories(
                    object_directory,
                    pack_directory,
                )
                if not _artifact_is_absent(
                    pack_directory, keep_name
                ) or not _artifact_is_absent(
                    pack_directory,
                    canonical_released_name,
                ):
                    raise RuntimeError(
                        "Git pack lease marker appeared after durable cleanup"
                    )
                return True
            finally:
                try:
                    if rollback_attempted:
                        _require_pack_and_index_unchanged(
                            pack_directory,
                            authenticated_lease,
                            opened,
                        )
                        _require_open_keep_authenticated(
                            authenticated_lease,
                            opened,
                        )
                    elif moved:
                        _require_pack_and_index_unchanged(
                            pack_directory,
                            authenticated_lease,
                            opened,
                        )
                        current_keep_metadata = os.fstat(opened.keep_descriptor)
                        if current_keep_metadata.st_nlink == 0:
                            _require_open_keep_authenticated(
                                authenticated_lease,
                                opened,
                                expected_links=0,
                            )
                        else:
                            final_moved_identity = _require_keep_moved(
                                pack_directory,
                                authenticated_lease,
                                opened,
                                released_name=canonical_released_name,
                            )
                            if (
                                moved_identity is not None
                                and final_moved_identity != moved_identity
                            ):
                                raise RuntimeError(
                                    "The released Git pack keep file changed"
                                )
                    else:
                        _require_opened_pack_lease_unchanged(
                            pack_directory,
                            authenticated_lease,
                            opened,
                        )
                finally:
                    _close_opened_pack_lease(opened)
