"""Small persistent snapshots for rich shell prompts."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
from typing import cast
import uuid

from .session_marker import active_session_marker_path
from .status_types import PromptStatusSummary
from ..utils.paths import (
    get_status_summary_cache_file_path,
    get_status_summary_prompt_marker_file_path,
)
from ..utils.strict_json import (
    StrictJsonError,
    loads,
    require_exact_keys,
    require_integer,
    require_list,
    require_object,
)


STATUS_SUMMARY_CACHE_SCHEMA_VERSION = 1
_MAXIMUM_CACHE_BYTES = 8 * 1024 * 1024
_CACHE_KEYS = frozenset(
    {
        "schema_version",
        "exact",
        "lock_generation",
        "session_marker",
        "summary",
    }
)
_MARKER_KEYS = frozenset({"device", "inode", "size", "mtime_ns", "ctime_ns"})
_SUMMARY_KEYS = frozenset(
    {"session", "selected_change", "file_review", "progress"}
)
_SESSION_KEYS = frozenset({"active", "iteration", "status", "in_progress"})
_PROGRESS_KEYS = frozenset({"included", "skipped", "discarded", "remaining"})
_CHANGE_KEYS = frozenset(
    {
        "hash",
        "kind",
        "file",
        "line",
        "ids",
        "type",
        "change_type",
        "old_path",
        "new_path",
        "old_mode",
        "new_mode",
        "old_oid",
        "new_oid",
    }
)
_FILE_REVIEW_KEYS = frozenset(
    {
        "source",
        "batch_name",
        "file",
        "page_spec",
        "shown_pages",
        "page_count",
        "entire_file_shown",
        "fresh",
    }
)


@dataclass(frozen=True, slots=True)
class SessionMarkerIdentity:
    """Filesystem identity of the marker created once per session."""

    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int

    @classmethod
    def from_stat(cls, metadata: os.stat_result) -> SessionMarkerIdentity:
        """Copy the fields that distinguish one marker from its replacement."""
        return cls(
            device=metadata.st_dev,
            inode=metadata.st_ino,
            size=metadata.st_size,
            mtime_ns=metadata.st_mtime_ns,
            ctime_ns=metadata.st_ctime_ns,
        )

    def to_record(self) -> dict[str, int]:
        """Return the JSON form stored with a cache entry."""
        return {
            "device": self.device,
            "inode": self.inode,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "ctime_ns": self.ctime_ns,
        }


@dataclass(frozen=True, slots=True)
class CachedPromptStatus:
    """One validated prompt snapshot and the state that produced it."""

    summary: PromptStatusSummary
    lock_generation: int
    session_marker: SessionMarkerIdentity
    exact: bool


def read_session_marker_identity(
    git_dir: Path | None = None,
) -> SessionMarkerIdentity | None:
    """Return the current session marker identity without following links."""
    try:
        metadata = active_session_marker_path(git_dir).lstat()
    except (FileNotFoundError, NotADirectoryError, OSError):
        return None
    if not stat.S_ISREG(metadata.st_mode):
        return None
    return SessionMarkerIdentity.from_stat(metadata)


def prompt_status_cache_requested(git_dir: Path | None = None) -> bool:
    """Return whether this session has used fields that need a summary."""
    try:
        metadata = get_status_summary_prompt_marker_file_path(git_dir).lstat()
    except (FileNotFoundError, NotADirectoryError, OSError):
        return False
    return stat.S_ISREG(metadata.st_mode)


def mark_prompt_status_cache_requested(git_dir: Path | None = None) -> bool:
    """Record that later commands should refresh the prompt summary."""
    path = get_status_summary_prompt_marker_file_path(git_dir)
    if prompt_status_cache_requested(git_dir):
        return True
    return _write_in_existing_directory(path, (b"",))


def read_cached_prompt_status(
    git_dir: Path | None = None,
    *,
    session_marker: SessionMarkerIdentity | None = None,
) -> CachedPromptStatus | None:
    """Read a valid cache entry for the current session, if one exists."""
    selected_marker = session_marker or read_session_marker_identity(git_dir)
    if selected_marker is None:
        return None
    path = get_status_summary_cache_file_path(git_dir)
    try:
        payload = _read_small_regular_file(path)
        record = require_object(loads(payload), "status summary cache")
        require_exact_keys(record, _CACHE_KEYS, "status summary cache")
        if (
            require_integer(record, "schema_version", "status summary cache")
            != STATUS_SUMMARY_CACHE_SCHEMA_VERSION
        ):
            return None
        exact = record["exact"]
        if type(exact) is not bool:
            return None
        lock_generation = require_integer(
            record,
            "lock_generation",
            "status summary cache",
        )
        if lock_generation < 0:
            return None
        marker = _decode_marker(record["session_marker"])
        if marker != selected_marker:
            return None
        summary = _decode_summary(record["summary"])
        return CachedPromptStatus(
            summary=summary,
            lock_generation=lock_generation,
            session_marker=marker,
            exact=exact,
        )
    except (
        FileNotFoundError,
        NotADirectoryError,
        OSError,
        StrictJsonError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        return None


def write_cached_prompt_status(
    summary: PromptStatusSummary,
    *,
    lock_generation: int,
    exact: bool,
    session_marker: SessionMarkerIdentity | None = None,
    git_dir: Path | None = None,
) -> bool:
    """Atomically publish one prompt snapshot in an existing session."""
    if lock_generation < 0:
        raise ValueError("lock generation cannot be negative")
    selected_marker = session_marker or read_session_marker_identity(git_dir)
    if selected_marker is None:
        return False
    record = {
        "schema_version": STATUS_SUMMARY_CACHE_SCHEMA_VERSION,
        "exact": exact,
        "lock_generation": lock_generation,
        "session_marker": selected_marker.to_record(),
        "summary": summary,
    }
    encoder = json.JSONEncoder(
        ensure_ascii=True,
        separators=(",", ":"),
    )
    chunks = (
        chunk.encode("ascii")
        for chunk in encoder.iterencode(record)
    )
    return _write_in_existing_directory(
        get_status_summary_cache_file_path(git_dir),
        chunks,
        maximum_bytes=_MAXIMUM_CACHE_BYTES,
    )


def _read_small_regular_file(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError("status summary cache is not a regular file")
        if metadata.st_size > _MAXIMUM_CACHE_BYTES:
            raise OSError("status summary cache is too large")
        with os.fdopen(descriptor, "rb", closefd=False) as file_handle:
            payload = file_handle.read(_MAXIMUM_CACHE_BYTES + 1)
        if len(payload) > _MAXIMUM_CACHE_BYTES:
            raise OSError("status summary cache grew while being read")
        return payload.decode("utf-8")
    finally:
        os.close(descriptor)


def _write_in_existing_directory(
    path: Path,
    chunks: Iterable[bytes],
    *,
    maximum_bytes: int | None = None,
) -> bool:
    """Replace one file without recreating a session removed concurrently."""
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_CLOEXEC", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_descriptor = os.open(path.parent, directory_flags)
    except (FileNotFoundError, NotADirectoryError, OSError):
        return False

    temporary_name = f".{path.name}.{uuid.uuid4().hex}.tmp"
    temporary_descriptor: int | None = None
    try:
        temporary_descriptor = os.open(
            temporary_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_descriptor,
        )
        with os.fdopen(temporary_descriptor, "wb", closefd=False) as file_handle:
            bytes_written = 0
            for chunk in chunks:
                bytes_written += len(chunk)
                if maximum_bytes is not None and bytes_written > maximum_bytes:
                    return False
                file_handle.write(chunk)
            file_handle.flush()
            os.fchmod(temporary_descriptor, 0o600)
            os.fsync(temporary_descriptor)
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
        os.fsync(directory_descriptor)
        return True
    except (FileNotFoundError, NotADirectoryError, OSError):
        return False
    finally:
        if temporary_descriptor is not None:
            os.close(temporary_descriptor)
        try:
            os.unlink(temporary_name, dir_fd=directory_descriptor)
        except FileNotFoundError:
            pass
        os.close(directory_descriptor)


def _decode_marker(value: object) -> SessionMarkerIdentity:
    record = require_object(value, "status summary cache.session_marker")
    require_exact_keys(record, _MARKER_KEYS, "status summary cache.session_marker")
    fields = {
        field: require_integer(record, field, "status summary cache.session_marker")
        for field in _MARKER_KEYS
    }
    if any(value < 0 for value in fields.values()):
        raise StrictJsonError("status summary cache marker fields cannot be negative")
    return SessionMarkerIdentity(**fields)


def _decode_summary(value: object) -> PromptStatusSummary:
    summary = require_object(value, "status summary cache.summary")
    require_exact_keys(summary, _SUMMARY_KEYS, "status summary cache.summary")
    _validate_session(summary["session"])
    _validate_progress(summary["progress"])
    _validate_change(summary["selected_change"])
    _validate_file_review(summary["file_review"])
    return cast(PromptStatusSummary, summary)


def _validate_session(value: object) -> None:
    record = require_object(value, "status summary cache.summary.session")
    require_exact_keys(record, _SESSION_KEYS, "status summary cache.summary.session")
    iteration = require_integer(
        record,
        "iteration",
        "status summary cache.summary.session",
    )
    if iteration < 1:
        raise StrictJsonError("status summary cache iteration must be positive")
    if record["active"] is not True:
        raise StrictJsonError("status summary cache session must be active")
    status_value = record["status"]
    if status_value not in {"in_progress", "complete"}:
        raise StrictJsonError("status summary cache session status is invalid")
    in_progress = record["in_progress"]
    if type(in_progress) is not bool or in_progress != (status_value == "in_progress"):
        raise StrictJsonError("status summary cache session flags disagree")


def _validate_progress(value: object) -> None:
    record = require_object(value, "status summary cache.summary.progress")
    require_exact_keys(record, _PROGRESS_KEYS, "status summary cache.summary.progress")
    for field in _PROGRESS_KEYS:
        if require_integer(
            record,
            field,
            "status summary cache.summary.progress",
        ) < 0:
            raise StrictJsonError("status summary cache progress cannot be negative")


def _validate_change(value: object) -> None:
    if value is None:
        return
    record = require_object(value, "status summary cache.summary.selected_change")
    unknown = record.keys() - _CHANGE_KEYS
    if unknown:
        raise StrictJsonError("status summary cache selected change has unknown fields")
    for field in ("kind", "file"):
        if not isinstance(record.get(field), str):
            raise StrictJsonError(
                f"status summary cache selected change {field} must be a string"
            )
    line = record.get("line")
    if line is not None and type(line) is not int:
        raise StrictJsonError("status summary cache selected line must be an integer")
    ids = require_list(
        record.get("ids"),
        "status summary cache.summary.selected_change.ids",
    )
    if any(type(line_id) is not int or line_id < 0 for line_id in ids):
        raise StrictJsonError("status summary cache selected IDs must be integers")
    for field in _CHANGE_KEYS - {"line", "ids"}:
        field_value = record.get(field)
        if field_value is not None and not isinstance(field_value, str):
            raise StrictJsonError(
                f"status summary cache selected change {field} must be a string"
            )


def _validate_file_review(value: object) -> None:
    if value is None:
        return
    record = require_object(value, "status summary cache.summary.file_review")
    require_exact_keys(
        record,
        _FILE_REVIEW_KEYS,
        "status summary cache.summary.file_review",
    )
    for field in ("source", "file", "page_spec"):
        if not isinstance(record[field], str):
            raise StrictJsonError(
                f"status summary cache file review {field} must be a string"
            )
    if record["batch_name"] is not None and not isinstance(
        record["batch_name"], str
    ):
        raise StrictJsonError("status summary cache batch name must be a string")
    for field in ("entire_file_shown", "fresh"):
        if type(record[field]) is not bool:
            raise StrictJsonError(
                f"status summary cache file review {field} must be a boolean"
            )
    page_count = require_integer(
        record,
        "page_count",
        "status summary cache.summary.file_review",
    )
    if page_count < 0:
        raise StrictJsonError("status summary cache page count cannot be negative")
    shown_pages = require_list(
        record["shown_pages"],
        "status summary cache.summary.file_review.shown_pages",
    )
    if any(type(page) is not int or page < 1 for page in shown_pages):
        raise StrictJsonError("status summary cache shown pages must be positive")
