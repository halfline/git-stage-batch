"""Private, bounded file transport for explicit history resolutions."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import stat
import sys
from collections.abc import Iterable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import NoReturn

from ..exceptions import CommandError
from ..git_paths import display_path, terminal_safe_text
from ..i18n import _


PRIVATE_RESOLUTION_DIRECTORY_MODE = 0o700
PRIVATE_RESOLUTION_FILE_MODE = 0o600
_TEMPORARY_FILE_PREFIX = ".git-stage-batch-resolution-"
_TEMPORARY_FILE_DOMAIN = b"git-stage-batch-resolution-write-v1\0"
_MAXIMUM_DIRECTORY_ENTRIES = 64 * 1024
_LINUX_RENAME_NOREPLACE = 1
_DARWIN_ACL_FIRST_ENTRY = 0
_DARWIN_ACL_TYPE_EXTENDED = 0x00000100
_DARWIN_MNT_UNKNOWNPERMISSIONS = 0x00200000
_DARWIN_RENAME_EXCL = 0x00000004
_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)


class _DarwinFileSystemId(ctypes.Structure):
    """Darwin ``fsid_t`` layout used by the stable ``statfs`` ABI."""

    _fields_ = [("values", ctypes.c_int32 * 2)]


class _DarwinFileSystemStatistics(ctypes.Structure):
    """Darwin ``struct statfs`` layout through its reserved tail."""

    _fields_ = [
        ("block_size", ctypes.c_uint32),
        ("io_size", ctypes.c_int32),
        ("blocks", ctypes.c_uint64),
        ("blocks_free", ctypes.c_uint64),
        ("blocks_available", ctypes.c_uint64),
        ("files", ctypes.c_uint64),
        ("files_free", ctypes.c_uint64),
        ("file_system_id", _DarwinFileSystemId),
        ("owner", ctypes.c_uint32),
        ("file_system_type", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("file_system_subtype", ctypes.c_uint32),
        ("file_system_type_name", ctypes.c_char * 16),
        ("mounted_on_name", ctypes.c_char * 1024),
        ("mounted_from_name", ctypes.c_char * 1024),
        ("extended_flags", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 7),
    ]


@dataclass(frozen=True, slots=True)
class ResolutionArtifactDigest:
    """Content identity for one bounded resolution artifact."""

    size: int
    sha256: str


class PrivateFilePublicationOutcome(str, Enum):
    """Known namespace outcome when create-only publication raises."""

    NOT_ATTEMPTED = "not-attempted"
    NOT_COMMITTED = "not-committed"
    COMMITTED = "committed"
    INDETERMINATE = "indeterminate"


class PrivateFilePublicationError(CommandError):
    """Report a create-only publication failure and its namespace outcome."""

    def __init__(
        self,
        cause: BaseException,
        *,
        path: Path,
        outcome: PrivateFilePublicationOutcome,
    ) -> None:
        if isinstance(cause, CommandError):
            message = cause.message
            exit_code = cause.exit_code
        else:
            detail = _("cannot publish artifact: {error}").format(error=cause)
            message = _("Invalid resolution artifact '{path}': {detail}").format(
                path=display_path(str(path)),
                detail=terminal_safe_text(detail),
            )
            exit_code = 1
        self.cause = cause
        self.outcome = outcome
        super().__init__(message, exit_code)


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    """Filesystem fields that must remain stable during one artifact read."""

    device: int
    inode: int
    mode: int
    links: int
    owner: int
    group: int
    size: int
    modified_ns: int
    changed_ns: int


def _invalid(path: Path, detail: str) -> NoReturn:
    raise CommandError(
        _("Invalid resolution artifact '{path}': {detail}").format(
            path=display_path(str(path)),
            detail=terminal_safe_text(detail),
        )
    )


def _exact_path(path: str | Path) -> Path:
    raw_path = os.fspath(path)
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        _invalid(candidate, _("path must be absolute"))
    if raw_path != os.path.normpath(raw_path) or ".." in candidate.parts:
        _invalid(
            candidate,
            _("path must not contain aliases or traversal"),
        )
    return candidate


def _require_directory_descriptor_support(path: Path) -> None:
    if (
        not getattr(os, "O_DIRECTORY", 0)
        or not getattr(os, "O_NOFOLLOW", 0)
        or os.open not in os.supports_dir_fd
        or os.stat not in os.supports_dir_fd
        or os.scandir not in os.supports_fd
    ):
        _invalid(path, _("platform does not support secure directory access"))


@contextmanager
def _walk_directory(path: Path) -> Iterator[int]:
    """Pin one absolute directory without following any path component."""
    _require_directory_descriptor_support(path)
    with ExitStack() as descriptors:
        try:
            descriptor = os.open(os.sep, _DIRECTORY_FLAGS)
            descriptors.callback(os.close, descriptor)
            for component in path.parts[1:]:
                descriptor = os.open(
                    component,
                    _DIRECTORY_FLAGS,
                    dir_fd=descriptor,
                )
                descriptors.callback(os.close, descriptor)
        except OSError as error:
            _invalid(
                path,
                _(
                    "path must not contain aliases, traversal, or symlinks "
                    "({error})"
                ).format(error=error),
            )
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            _invalid(path, _("path must be a directory"))
        yield descriptor


def _directory_object_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _darwin_descriptor_ignores_ownership(descriptor: int) -> bool:
    """Return whether a pinned descriptor's Darwin mount ignores ownership."""
    library = ctypes.CDLL(None, use_errno=True)
    try:
        function = library.fstatfs
    except AttributeError as error:
        raise OSError(errno.ENOSYS, "fstatfs is unavailable") from error
    function.argtypes = (
        ctypes.c_int,
        ctypes.POINTER(_DarwinFileSystemStatistics),
    )
    function.restype = ctypes.c_int
    file_system = _DarwinFileSystemStatistics()
    ctypes.set_errno(0)
    if function(descriptor, ctypes.byref(file_system)) != 0:
        error_number = ctypes.get_errno() or errno.EIO
        raise OSError(error_number, os.strerror(error_number))
    return bool(file_system.flags & _DARWIN_MNT_UNKNOWNPERMISSIONS)


def _darwin_descriptor_has_extended_acl(descriptor: int) -> bool:
    """Return whether a pinned descriptor has any Darwin extended ACL entry."""
    library = ctypes.CDLL(None, use_errno=True)
    try:
        get_acl = library.acl_get_fd_np
        get_entry = library.acl_get_entry
        free_acl = library.acl_free
    except AttributeError as error:
        raise OSError(errno.ENOSYS, "extended ACL inspection is unavailable") from error
    get_acl.argtypes = (ctypes.c_int, ctypes.c_int)
    get_acl.restype = ctypes.c_void_p
    get_entry.argtypes = (
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_void_p),
    )
    get_entry.restype = ctypes.c_int
    free_acl.argtypes = (ctypes.c_void_p,)
    free_acl.restype = ctypes.c_int

    ctypes.set_errno(0)
    acl = get_acl(descriptor, _DARWIN_ACL_TYPE_EXTENDED)
    if not acl:
        error_number = ctypes.get_errno() or errno.EIO
        if error_number == errno.ENOENT:
            return False
        raise OSError(error_number, os.strerror(error_number))
    try:
        entry = ctypes.c_void_p()
        ctypes.set_errno(0)
        result = get_entry(acl, _DARWIN_ACL_FIRST_ENTRY, ctypes.byref(entry))
        if result == 0:
            return True
        error_number = ctypes.get_errno()
        # Darwin reports the end of its zero-based ACL iterator as EINVAL.
        if result == -1 and error_number == errno.EINVAL:
            return False
        error_number = error_number or errno.EIO
        raise OSError(error_number, os.strerror(error_number))
    finally:
        free_acl(acl)


def _require_private_descriptor_metadata(
    path: Path,
    descriptor: int,
    *,
    permissions_detail: str,
) -> None:
    """Reject Darwin descriptor state that Unix mode bits cannot represent."""
    if sys.platform != "darwin":
        return
    try:
        ignores_ownership = _darwin_descriptor_ignores_ownership(descriptor)
        has_extended_acl = _darwin_descriptor_has_extended_acl(descriptor)
    except OSError:
        _invalid(path, _("platform does not support secure directory access"))
    if ignores_ownership:
        _invalid(path, _("path must be owned by the current user"))
    if has_extended_acl:
        _invalid(path, permissions_detail)


def _require_private_directory_metadata(
    path: Path,
    metadata: os.stat_result,
    *,
    descriptor: int,
) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        _invalid(path, _("path must be a directory"))
    _require_current_owner(path, metadata)
    if stat.S_IMODE(metadata.st_mode) != PRIVATE_RESOLUTION_DIRECTORY_MODE:
        _invalid(path, _("directory permissions must be 0700"))
    _require_private_descriptor_metadata(
        path,
        descriptor,
        permissions_detail=_("directory permissions must be 0700"),
    )


@contextmanager
def _pinned_directory(
    path: Path,
    *,
    require_private: bool,
) -> Iterator[int]:
    with _walk_directory(path) as descriptor:
        initial_metadata = os.fstat(descriptor)
        if require_private:
            _require_private_directory_metadata(
                path,
                initial_metadata,
                descriptor=descriptor,
            )
        try:
            yield descriptor
        finally:
            with _walk_directory(path) as current_descriptor:
                current_metadata = os.fstat(current_descriptor)
                if require_private:
                    _require_private_directory_metadata(
                        path,
                        current_metadata,
                        descriptor=current_descriptor,
                    )
                if _directory_object_identity(current_metadata) != (
                    _directory_object_identity(initial_metadata)
                ):
                    _invalid(
                        path,
                        _("directory path changed during artifact access"),
                    )


def _identity(metadata: os.stat_result) -> _FileIdentity:
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


def _require_regular_metadata(path: Path, metadata: os.stat_result) -> None:
    if stat.S_ISLNK(metadata.st_mode):
        _invalid(path, _("path must not contain aliases, traversal, or symlinks"))
    if not stat.S_ISREG(metadata.st_mode):
        _invalid(path, _("path must be a regular file"))


def _require_current_owner(path: Path, metadata: os.stat_result) -> None:
    if hasattr(os, "geteuid") and metadata.st_uid != os.geteuid():
        _invalid(path, _("path must be owned by the current user"))


def _require_private_file_metadata(
    path: Path,
    metadata: os.stat_result,
    *,
    descriptor: int,
) -> None:
    _require_regular_metadata(path, metadata)
    _require_current_owner(path, metadata)
    if metadata.st_nlink != 1:
        _invalid(path, _("file must not have hard links"))
    if stat.S_IMODE(metadata.st_mode) != PRIVATE_RESOLUTION_FILE_MODE:
        _invalid(path, _("file permissions must be 0600"))
    _require_private_descriptor_metadata(
        path,
        descriptor,
        permissions_detail=_("file permissions must be 0600"),
    )


def create_private_resolution_directory(path: str | Path) -> Path:
    """Create exactly one private resolution directory without following links."""
    directory = _exact_path(path)
    leaf_name = directory.name
    if not leaf_name:
        _invalid(directory, _("cannot create the filesystem root"))
    created_identity: tuple[int, int] | None = None
    completed = False
    with _pinned_directory(directory.parent, require_private=False) as parent:
        try:
            os.mkdir(
                leaf_name,
                mode=PRIVATE_RESOLUTION_DIRECTORY_MODE,
                dir_fd=parent,
            )
            path_metadata = os.stat(
                leaf_name,
                dir_fd=parent,
                follow_symlinks=False,
            )
            descriptor = os.open(leaf_name, _DIRECTORY_FLAGS, dir_fd=parent)
            try:
                opened_metadata = os.fstat(descriptor)
                created_identity = _directory_object_identity(opened_metadata)
                _require_private_directory_metadata(
                    directory,
                    opened_metadata,
                    descriptor=descriptor,
                )
                _require_private_directory_metadata(
                    directory,
                    path_metadata,
                    descriptor=descriptor,
                )
                if _identity(opened_metadata) != _identity(path_metadata):
                    _invalid(directory, _("directory changed while it was opened"))
                os.fsync(descriptor)
                os.fsync(parent)
            finally:
                os.close(descriptor)
            completed = True
        except OSError as error:
            _invalid(
                directory,
                _("cannot create private directory: {error}").format(error=error),
            )
        finally:
            if not completed and created_identity is not None:
                try:
                    current_metadata = os.stat(
                        leaf_name,
                        dir_fd=parent,
                        follow_symlinks=False,
                    )
                    if (
                        stat.S_ISDIR(current_metadata.st_mode)
                        and _directory_object_identity(current_metadata)
                        == created_identity
                        and (
                            not hasattr(os, "geteuid")
                            or current_metadata.st_uid == os.geteuid()
                        )
                    ):
                        os.rmdir(leaf_name, dir_fd=parent)
                except OSError:
                    pass
    return require_private_resolution_directory(directory)


def require_private_resolution_directory(path: str | Path) -> Path:
    """Require an exact, current-user-owned 0700 resolution directory."""
    directory = _exact_path(path)
    with _pinned_directory(directory, require_private=True):
        pass
    return directory


def list_resolution_directory(
    path: str | Path,
    *,
    maximum_entries: int = _MAXIMUM_DIRECTORY_ENTRIES,
) -> tuple[str, ...]:
    """Return exact entry names from one pinned private directory."""
    if type(maximum_entries) is not int or maximum_entries <= 0:
        raise ValueError("maximum_entries must be a positive integer")
    directory = _exact_path(path)
    with _pinned_directory(directory, require_private=True) as descriptor:
        try:
            names: list[str] = []
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    if len(names) == maximum_entries:
                        _invalid(
                            directory,
                            _("directory exceeds the supported entry-count limit"),
                        )
                    names.append(entry.name)
        except OSError as error:
            _invalid(
                directory,
                _("cannot enumerate directory: {error}").format(error=error),
            )
    return tuple(sorted(names))


def _rename_with_platform_flags(
    parent: int,
    source_name: str,
    destination_name: str,
    *,
    linux_flags: int,
    darwin_flags: int,
) -> None:
    if sys.platform == "linux":
        symbol = "renameat2"
        flags = linux_flags
    elif sys.platform == "darwin":
        symbol = "renameatx_np"
        flags = darwin_flags
    else:
        raise OSError(
            errno.ENOTSUP,
            "atomic rename flags are not supported on this platform",
        )

    library = ctypes.CDLL(None, use_errno=True)
    try:
        function = getattr(library, symbol)
    except AttributeError as error:
        raise OSError(
            errno.ENOSYS,
            f"{symbol} is unavailable on this platform",
        ) from error
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
        raise OSError(
            error_number,
            os.strerror(error_number),
            destination_name,
        )


def _rename_noreplace(
    parent: int,
    source_name: str,
    destination_name: str,
) -> None:
    """Rename one sibling without replacing an existing destination."""
    _rename_with_platform_flags(
        parent,
        source_name,
        destination_name,
        linux_flags=_LINUX_RENAME_NOREPLACE,
        darwin_flags=_DARWIN_RENAME_EXCL,
    )


def publish_private_resolution_directory(
    staging_path: str | Path,
    destination_path: str | Path,
) -> Path:
    """Atomically publish one complete private sibling directory."""
    staging = _exact_path(staging_path)
    destination = _exact_path(destination_path)
    if staging == destination or staging.parent != destination.parent:
        _invalid(destination, _("staging and destination must be distinct siblings"))
    with _pinned_directory(staging.parent, require_private=False) as parent:
        try:
            staging_metadata = os.stat(
                staging.name,
                dir_fd=parent,
                follow_symlinks=False,
            )
        except OSError as error:
            _invalid(
                staging,
                _("cannot inspect staging directory: {error}").format(error=error),
            )
        try:
            descriptor = os.open(
                staging.name,
                _DIRECTORY_FLAGS,
                dir_fd=parent,
            )
        except OSError as error:
            _invalid(
                staging,
                _("cannot open staging directory: {error}").format(error=error),
            )
        try:
            opened_metadata = os.fstat(descriptor)
            _require_private_directory_metadata(
                staging,
                staging_metadata,
                descriptor=descriptor,
            )
            _require_private_directory_metadata(
                staging,
                opened_metadata,
                descriptor=descriptor,
            )
            initial_identity = _identity(opened_metadata)
            if _identity(staging_metadata) != initial_identity:
                _invalid(staging, _("staging directory changed while it was opened"))
            os.fsync(descriptor)

            try:
                os.stat(
                    destination.name,
                    dir_fd=parent,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                pass
            except OSError as error:
                _invalid(
                    destination,
                    _("cannot inspect destination: {error}").format(error=error),
                )
            else:
                _invalid(destination, _("destination already exists"))

            try:
                current_staging_metadata = os.stat(
                    staging.name,
                    dir_fd=parent,
                    follow_symlinks=False,
                )
            except OSError as error:
                _invalid(
                    staging,
                    _("cannot re-authenticate staging directory: {error}").format(
                        error=error
                    ),
                )
            current_opened_metadata = os.fstat(descriptor)
            _require_private_directory_metadata(
                staging,
                current_staging_metadata,
                descriptor=descriptor,
            )
            _require_private_directory_metadata(
                staging,
                current_opened_metadata,
                descriptor=descriptor,
            )
            if (
                _identity(current_staging_metadata) != initial_identity
                or _identity(current_opened_metadata) != initial_identity
            ):
                _invalid(staging, _("staging directory changed before publication"))

            try:
                _rename_noreplace(
                    parent,
                    staging.name,
                    destination.name,
                )
            except FileExistsError:
                _invalid(destination, _("destination already exists"))
            except OSError as error:
                _invalid(
                    destination,
                    _("cannot publish directory: {error}").format(error=error),
                )

            published_metadata = os.stat(
                destination.name,
                dir_fd=parent,
                follow_symlinks=False,
            )
            opened_metadata = os.fstat(descriptor)
            _require_private_directory_metadata(
                destination,
                published_metadata,
                descriptor=descriptor,
            )
            _require_private_directory_metadata(
                destination,
                opened_metadata,
                descriptor=descriptor,
            )
            if _identity(published_metadata) != _identity(
                opened_metadata
            ) or _directory_object_identity(
                opened_metadata
            ) != _directory_object_identity(staging_metadata):
                _invalid(destination, _("published directory identity changed"))
            os.fsync(parent)
            final_metadata = os.stat(
                destination.name,
                dir_fd=parent,
                follow_symlinks=False,
            )
            final_opened_metadata = os.fstat(descriptor)
            _require_private_directory_metadata(
                destination,
                final_metadata,
                descriptor=descriptor,
            )
            _require_private_directory_metadata(
                destination,
                final_opened_metadata,
                descriptor=descriptor,
            )
            if _identity(final_metadata) != _identity(
                final_opened_metadata
            ) or _directory_object_identity(
                final_opened_metadata
            ) != _directory_object_identity(staging_metadata):
                _invalid(destination, _("published directory identity changed"))
        finally:
            os.close(descriptor)
    return require_private_resolution_directory(destination)


def _temporary_resolution_artifact_name(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(_TEMPORARY_FILE_DOMAIN)
    digest.update(os.fsencode(path.name))
    return f"{_TEMPORARY_FILE_PREFIX}{digest.hexdigest()}.tmp"


def _recover_interrupted_resolution_artifact_write(
    artifact_path: Path,
    parent: int,
) -> None:
    """Authenticate, unlink, and durably forget one deterministic temporary."""
    temporary_path = artifact_path.with_name(
        _temporary_resolution_artifact_name(artifact_path)
    )
    try:
        initial_metadata = os.stat(
            temporary_path.name,
            dir_fd=parent,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return
    except OSError as error:
        _invalid(
            temporary_path,
            _("cannot inspect interrupted write: {error}").format(error=error),
        )
    _require_regular_metadata(temporary_path, initial_metadata)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(
            temporary_path.name,
            flags,
            dir_fd=parent,
        )
    except OSError as error:
        _invalid(
            temporary_path,
            _("cannot open interrupted write: {error}").format(error=error),
        )
    try:
        opened_metadata = os.fstat(descriptor)
        _require_private_file_metadata(
            temporary_path,
            initial_metadata,
            descriptor=descriptor,
        )
        _require_private_file_metadata(
            temporary_path,
            opened_metadata,
            descriptor=descriptor,
        )
        current_metadata = os.stat(
            temporary_path.name,
            dir_fd=parent,
            follow_symlinks=False,
        )
        _require_private_file_metadata(
            temporary_path,
            current_metadata,
            descriptor=descriptor,
        )
        if _identity(initial_metadata) != _identity(opened_metadata) or _identity(
            current_metadata
        ) != _identity(opened_metadata):
            _invalid(
                temporary_path,
                _("interrupted write changed before recovery"),
            )
        os.unlink(temporary_path.name, dir_fd=parent)
        os.fsync(parent)
    except OSError as error:
        _invalid(
            temporary_path,
            _("cannot remove interrupted write: {error}").format(error=error),
        )
    finally:
        os.close(descriptor)


def recover_interrupted_resolution_artifact_write(path: str | Path) -> None:
    """Remove only the deterministic temporary file for one exact destination."""
    artifact_path = _exact_path(path)
    with _pinned_directory(artifact_path.parent, require_private=True) as parent:
        _recover_interrupted_resolution_artifact_write(artifact_path, parent)


def _require_open_file_path(
    path: Path,
    parent: int,
    descriptor: int,
    object_identity: tuple[int, int],
    detail: str,
) -> os.stat_result:
    try:
        path_metadata = os.stat(
            path.name,
            dir_fd=parent,
            follow_symlinks=False,
        )
        opened_metadata = os.fstat(descriptor)
    except OSError as error:
        _invalid(path, _("{detail} ({error})").format(detail=detail, error=error))
    _require_private_file_metadata(
        path,
        path_metadata,
        descriptor=descriptor,
    )
    _require_private_file_metadata(
        path,
        opened_metadata,
        descriptor=descriptor,
    )
    if (
        _identity(path_metadata) != _identity(opened_metadata)
        or _directory_object_identity(opened_metadata) != object_identity
    ):
        _invalid(path, detail)
    return opened_metadata


def _remove_temporary_file_if_unchanged(
    parent: int,
    temporary_name: str,
    object_identity: tuple[int, int],
) -> None:
    try:
        metadata = os.stat(
            temporary_name,
            dir_fd=parent,
            follow_symlinks=False,
        )
    except OSError:
        return
    if _directory_object_identity(metadata) != object_identity:
        return
    try:
        os.unlink(temporary_name, dir_fd=parent)
    except OSError:
        pass


def _require_private_file_destination_absent(
    path: Path,
    parent: int,
    *,
    detail: str,
) -> None:
    try:
        os.stat(
            path.name,
            dir_fd=parent,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return
    except OSError as error:
        _invalid(
            path,
            _("cannot inspect destination: {error}").format(error=error),
        )
    _invalid(path, detail)


def _optional_path_object_identity(
    path: Path,
    parent: int,
) -> tuple[int, int] | None:
    try:
        metadata = os.stat(
            path.name,
            dir_fd=parent,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return None
    return _directory_object_identity(metadata)


def _reconcile_private_file_publication(
    temporary_path: Path,
    destination_path: Path,
    parent: int,
    descriptor: int,
    object_identity: tuple[int, int],
) -> PrivateFilePublicationOutcome:
    """Classify a rename exception against the still-open published inode."""
    try:
        opened_identity = _directory_object_identity(os.fstat(descriptor))
        temporary_identity = _optional_path_object_identity(temporary_path, parent)
        destination_identity = _optional_path_object_identity(
            destination_path,
            parent,
        )
    except OSError:
        return PrivateFilePublicationOutcome.INDETERMINATE
    if opened_identity != object_identity:
        return PrivateFilePublicationOutcome.INDETERMINATE
    temporary_matches = temporary_identity == object_identity
    destination_matches = destination_identity == object_identity
    if destination_matches and not temporary_matches:
        return PrivateFilePublicationOutcome.COMMITTED
    if temporary_matches and not destination_matches:
        return PrivateFilePublicationOutcome.NOT_COMMITTED
    return PrivateFilePublicationOutcome.INDETERMINATE


def publish_new_private_file(
    path: str | Path,
    chunks: Iterable[bytes] | bytes,
    *,
    maximum_bytes: int | None = None,
) -> ResolutionArtifactDigest:
    """Create and durably publish one private file without replacement.

    The supplied chunks are streamed through a same-directory 0600 temporary
    file.  On failure, :class:`PrivateFilePublicationError` reports the known
    namespace outcome.  The generic primitive remains in this module because
    it shares the pinned-directory, Darwin privacy, and authenticated recovery
    machinery used by resolution artifacts.
    """
    if maximum_bytes is not None and (
        type(maximum_bytes) is not int or maximum_bytes <= 0
    ):
        raise ValueError("maximum_bytes must be a positive integer or None")
    artifact_path = _exact_path(path)
    outcome = PrivateFilePublicationOutcome.NOT_ATTEMPTED
    try:
        with _pinned_directory(
            artifact_path.parent,
            require_private=True,
        ) as parent:
            _require_private_file_destination_absent(
                artifact_path,
                parent,
                detail=_("destination already exists"),
            )
            _recover_interrupted_resolution_artifact_write(artifact_path, parent)
            _require_private_file_destination_absent(
                artifact_path,
                parent,
                detail=_("destination appeared while it was being written"),
            )
            temporary_name = _temporary_resolution_artifact_name(artifact_path)
            temporary_path = artifact_path.with_name(temporary_name)
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                file_descriptor = os.open(
                    temporary_name,
                    flags,
                    PRIVATE_RESOLUTION_FILE_MODE,
                    dir_fd=parent,
                )
            except OSError as error:
                _invalid(
                    artifact_path,
                    _("cannot create temporary artifact: {error}").format(error=error),
                )

            digest = hashlib.sha256()
            size = 0
            temporary_identity: tuple[int, int] | None = None
            try:
                os.fchmod(file_descriptor, PRIVATE_RESOLUTION_FILE_MODE)
                created_metadata = os.fstat(file_descriptor)
                temporary_identity = _directory_object_identity(created_metadata)
                _require_private_file_metadata(
                    temporary_path,
                    created_metadata,
                    descriptor=file_descriptor,
                )
                _require_open_file_path(
                    temporary_path,
                    parent,
                    file_descriptor,
                    temporary_identity,
                    _("temporary artifact changed while it was opened"),
                )
                source_chunks = (chunks,) if isinstance(chunks, bytes) else chunks
                with os.fdopen(file_descriptor, "wb", closefd=False) as destination:
                    for chunk in source_chunks:
                        chunk_size = len(chunk)
                        if (
                            maximum_bytes is not None
                            and size + chunk_size > maximum_bytes
                        ):
                            _invalid(
                                artifact_path,
                                _("artifact exceeds the supported size limit"),
                            )
                        destination.write(chunk)
                        digest.update(chunk)
                        size += chunk_size
                    destination.flush()
                    os.fchmod(destination.fileno(), PRIVATE_RESOLUTION_FILE_MODE)
                    os.fsync(destination.fileno())
                written_metadata = _require_open_file_path(
                    temporary_path,
                    parent,
                    file_descriptor,
                    temporary_identity,
                    _("temporary artifact path changed while it was written"),
                )
                if written_metadata.st_size != size:
                    _invalid(artifact_path, _("temporary artifact size changed"))
                _require_private_file_destination_absent(
                    artifact_path,
                    parent,
                    detail=_("destination appeared while it was being written"),
                )
                rename_error: BaseException | None = None
                outcome = PrivateFilePublicationOutcome.NOT_COMMITTED
                try:
                    _rename_noreplace(
                        parent,
                        temporary_name,
                        artifact_path.name,
                    )
                    outcome = PrivateFilePublicationOutcome.COMMITTED
                except BaseException as error:
                    outcome = PrivateFilePublicationOutcome.INDETERMINATE
                    outcome = _reconcile_private_file_publication(
                        temporary_path,
                        artifact_path,
                        parent,
                        file_descriptor,
                        temporary_identity,
                    )
                    if outcome is not PrivateFilePublicationOutcome.COMMITTED:
                        if isinstance(error, FileExistsError):
                            _invalid(
                                artifact_path,
                                _("destination appeared while it was being written"),
                            )
                        if isinstance(error, OSError):
                            _invalid(
                                artifact_path,
                                _("cannot publish artifact: {error}").format(
                                    error=error
                                ),
                            )
                        raise
                    rename_error = error
                _require_open_file_path(
                    artifact_path,
                    parent,
                    file_descriptor,
                    temporary_identity,
                    _("published artifact identity changed"),
                )
                os.fsync(parent)
                _require_open_file_path(
                    artifact_path,
                    parent,
                    file_descriptor,
                    temporary_identity,
                    _("published artifact identity changed"),
                )
                result = ResolutionArtifactDigest(
                    size=size,
                    sha256=digest.hexdigest(),
                )
                if rename_error is not None:
                    raise rename_error
                return result
            finally:
                if temporary_identity is not None and outcome in {
                    PrivateFilePublicationOutcome.NOT_ATTEMPTED,
                    PrivateFilePublicationOutcome.NOT_COMMITTED,
                }:
                    _remove_temporary_file_if_unchanged(
                        parent,
                        temporary_name,
                        temporary_identity,
                    )
                os.close(file_descriptor)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as error:
        if isinstance(error, PrivateFilePublicationError):
            raise
        raise PrivateFilePublicationError(
            error,
            path=artifact_path,
            outcome=outcome,
        ) from error
