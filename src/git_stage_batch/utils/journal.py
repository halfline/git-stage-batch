"""Low-overhead, privacy-conscious diagnostic journal."""

from __future__ import annotations

import atexit
import base64
import fcntl
import hashlib
import json
import os
import sys
import threading
import traceback
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import Any

from .git_repository import get_git_common_directory_path


JOURNAL_LEVEL_ENV = "GIT_STAGE_BATCH_JOURNAL"
JOURNAL_PATH_ENV = "GIT_STAGE_BATCH_JOURNAL_PATH"
# Kept as a path-override alias for existing debugging setups.
GLOBAL_JOURNAL_PATH_ENV = "GIT_STAGE_BATCH_GLOBAL_JOURNAL_PATH"
BUFFER_FLUSH_BYTES = 64 * 1024
MAX_CONTENT_FIELD_BYTES = 4 * 1024


class JournalLevel(IntEnum):
    """Amount of diagnostic information retained by the journal."""

    DISABLED = 0
    METADATA_ONLY = 1
    VERBOSE = 2
    CONTENT_DEBUG = 3


_LEVEL_NAMES = {
    JournalLevel.DISABLED: "disabled",
    JournalLevel.METADATA_ONLY: "metadata-only",
    JournalLevel.VERBOSE: "verbose",
    JournalLevel.CONTENT_DEBUG: "content-debug",
}
_LEVEL_VALUES = {name: level for level, name in _LEVEL_NAMES.items()}
_LEVEL_VALUES.update({
    "off": JournalLevel.DISABLED,
    "0": JournalLevel.DISABLED,
    "metadata": JournalLevel.METADATA_ONLY,
    "1": JournalLevel.METADATA_ONLY,
    "2": JournalLevel.VERBOSE,
    "content": JournalLevel.CONTENT_DEBUG,
    "3": JournalLevel.CONTENT_DEBUG,
})

_CONTENT_FIELDS = frozenset({
    "content",
    "raw_content",
    "stderr",
    "stdout",
    "detail",
    "index_before",
    "index_after",
})
_PATH_FIELDS = frozenset({
    "file_path",
    "filename",
    "path",
    "repo",
    "files",
    "renames",
    "deletions",
})
_REPOSITORY_IDS: dict[Path, str] = {}


def get_journal_level() -> JournalLevel:
    """Return the configured journal level without doing filesystem work."""
    configured = os.environ.get(JOURNAL_LEVEL_ENV)
    if configured is None:
        # Preserve the old debugging switch without making content capture implicit.
        return (
            JournalLevel.VERBOSE
            if os.environ.get("GIT_STAGE_BATCH_DEBUG")
            else JournalLevel.DISABLED
        )
    return _LEVEL_VALUES.get(configured.strip().lower(), JournalLevel.DISABLED)


def journal_level_name(level: JournalLevel | None = None) -> str:
    """Return the public name for a journal level."""
    return _LEVEL_NAMES[level if level is not None else get_journal_level()]


def journal_enabled(minimum: JournalLevel = JournalLevel.METADATA_ONLY) -> bool:
    """Cheap guard for call sites that would otherwise construct diagnostics."""
    return get_journal_level() >= minimum


def _state_home() -> Path:
    override = os.environ.get("XDG_STATE_HOME")
    if override:
        return Path(override).expanduser().absolute()
    return Path.home() / ".local" / "state"


def _repository_id() -> str:
    """Return a stable repository identifier without exposing its path."""
    cwd = Path.cwd()
    cached = _REPOSITORY_IDS.get(cwd)
    if cached is not None:
        return cached
    try:
        identity = os.fsencode(get_git_common_directory_path().resolve())
    except Exception:
        identity = os.fsencode(cwd.resolve())
    repository_id = hashlib.sha256(identity).hexdigest()[:24]
    _REPOSITORY_IDS[cwd] = repository_id
    return repository_id


def get_journal_path() -> Path:
    """Return the current repository's private diagnostic journal path."""
    override = (
        os.environ.get(JOURNAL_PATH_ENV)
        or os.environ.get(GLOBAL_JOURNAL_PATH_ENV)
    )
    if override:
        return Path(override)
    return (
        _state_home()
        / "git-stage-batch"
        / "journals"
        / f"{_repository_id()}.jsonl"
    )


def _byte_count(value: Any) -> int | None:
    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8", errors="surrogateescape"))
    return None


def _redacted_content(value: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"redacted": True}
    byte_count = _byte_count(value)
    if byte_count is not None:
        result["byte_count"] = byte_count
    elif isinstance(value, (list, tuple, dict)):
        result["item_count"] = len(value)
    return result


def _path_identifier(value: str | bytes) -> dict[str, str]:
    if isinstance(value, bytes):
        raw = value
    else:
        raw = os.path.normpath(value).encode("utf-8", errors="surrogateescape")
    return {"path_id": hashlib.sha256(raw).hexdigest()[:16]}


def _redact_paths(value: Any) -> Any:
    if isinstance(value, (str, bytes)):
        return _path_identifier(value)
    if isinstance(value, (list, tuple, set)):
        return [_redact_paths(item) for item in value]
    if isinstance(value, dict):
        return [
            {"from": _redact_paths(key), "to": _redact_paths(item)}
            for key, item in value.items()
        ]
    return _json_safe(value, include_content=False)


def _json_safe(value: Any, *, include_content: bool) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        if not include_content:
            return _redacted_content(value)
        return _bounded_content(value)
    if isinstance(value, dict):
        return {
            str(key): _json_safe(item, include_content=include_content)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item, include_content=include_content) for item in value]
    return {"type": type(value).__name__}


def _sanitize_fields(fields: dict[str, Any], level: JournalLevel) -> dict[str, Any]:
    include_content = level >= JournalLevel.CONTENT_DEBUG
    return {
        key: _sanitize_value(key, value, include_content=include_content)
        for key, value in fields.items()
    }


def _sanitize_value(key: str, value: Any, *, include_content: bool) -> Any:
    is_preview = key.endswith("preview") or key.endswith("_preview")
    is_content = key in _CONTENT_FIELDS or is_preview
    is_path = (
        key in _PATH_FIELDS
        or key.endswith("_path")
        or key.endswith("_paths")
        or key.endswith("_files")
    )
    if is_content and not include_content:
        return _redacted_content(value)
    if is_content:
        return _bounded_content(value)
    if is_path and not include_content:
        return _redact_paths(value)
    if isinstance(value, dict):
        return {
            str(nested_key): _sanitize_value(
                str(nested_key),
                nested_value,
                include_content=include_content,
            )
            for nested_key, nested_value in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [
            _sanitize_value(key, item, include_content=include_content)
            for item in value
        ]
    return _json_safe(value, include_content=include_content)


def _bounded_content(value: Any) -> Any:
    """Encode explicitly enabled content without allowing one huge entry."""
    if isinstance(value, bytes):
        content = value[:MAX_CONTENT_FIELD_BYTES]
        result: dict[str, Any] = {
            "encoding": "base64",
            "data": base64.b64encode(content).decode("ascii"),
        }
        if len(value) > len(content):
            result.update({
                "truncated": True,
                "original_byte_count": len(value),
            })
        return result
    if isinstance(value, str):
        encoded = value.encode("utf-8", errors="surrogateescape")
        if len(encoded) <= MAX_CONTENT_FIELD_BYTES:
            return value
        prefix = encoded[:MAX_CONTENT_FIELD_BYTES]
        return {
            "text": prefix.decode("utf-8", errors="replace"),
            "truncated": True,
            "original_byte_count": len(encoded),
        }
    return _json_safe(value, include_content=True)


def _source_identifier(frame) -> str:
    module = frame.f_globals.get("__name__", "unknown")
    return f"{module}:{frame.f_code.co_name}"


def _bounded_stack() -> list[dict[str, Any]]:
    frames = traceback.extract_stack(limit=10)[:-2]
    return [
        {
            "source": Path(item.filename).name,
            "line": item.lineno,
            "function": item.name,
        }
        for item in frames
    ][-6:]


def _is_error_operation(operation: str) -> bool:
    lowered = operation.lower()
    return any(word in lowered for word in ("error", "fail", "exception"))


class _JournalWriter:
    """Buffer journal lines and serialize cross-process flushes."""

    def __init__(self, path: Path):
        self.path = path
        self._buffer = bytearray()
        self._lock = threading.RLock()

    def append(self, line: bytes) -> None:
        with self._lock:
            self._buffer.extend(line)
            if len(self._buffer) >= BUFFER_FLUSH_BYTES:
                self._flush_locked()

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def discard_buffer(self) -> None:
        with self._lock:
            self._buffer.clear()

    def _flush_locked(self) -> None:
        if not self._buffer:
            return
        payload = bytes(self._buffer)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.path.parent, 0o700)
            lock_path = self.path.with_name(f"{self.path.name}.lock")
            lock_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
            try:
                os.fchmod(lock_fd, 0o600)
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                journal_fd = os.open(
                    self.path,
                    os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                    0o600,
                )
                try:
                    os.fchmod(journal_fd, 0o600)
                    view = memoryview(payload)
                    while view:
                        view = view[os.write(journal_fd, view):]
                finally:
                    os.close(journal_fd)
            finally:
                os.close(lock_fd)
        except Exception:
            # Diagnostics must never change command behavior. Drop failed entries so
            # an unwritable destination cannot grow the process heap indefinitely.
            pass
        finally:
            self._buffer.clear()

_WRITERS: dict[Path, _JournalWriter] = {}
_WRITERS_LOCK = threading.Lock()


def _writer(path: Path) -> _JournalWriter:
    with _WRITERS_LOCK:
        writer = _WRITERS.get(path)
        if writer is None:
            writer = _JournalWriter(path)
            _WRITERS[path] = writer
        return writer


def log_journal(operation: str, **fields: Any) -> None:
    """Queue a structured diagnostic event when journaling is enabled."""
    level = get_journal_level()
    if level == JournalLevel.DISABLED:
        return
    try:
        caller = sys._getframe(1)
        entry: dict[str, Any] = {
            "timestamp": (
                datetime.now(tz=timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            ),
            "pid": os.getpid(),
            "repository_id": _repository_id(),
            "level": journal_level_name(level),
            "operation": operation,
            "source": _source_identifier(caller),
            "fields": _sanitize_fields(fields, level),
        }
        if level >= JournalLevel.VERBOSE or _is_error_operation(operation):
            entry["stack"] = _bounded_stack()
        encoded = (
            json.dumps(entry, separators=(",", ":"), sort_keys=True)
            .encode("utf-8")
            + b"\n"
        )
        _writer(get_journal_path()).append(encoded)
    except Exception:
        pass


def flush_journal() -> None:
    """Flush queued events at a command or interactive-action boundary."""
    with _WRITERS_LOCK:
        writers = list(_WRITERS.values())
    for writer in writers:
        writer.flush()


def _reset_journal_state_for_tests() -> None:
    """Discard process-global writers after environment changes in tests."""
    global _WRITERS, _REPOSITORY_IDS
    with _WRITERS_LOCK:
        _WRITERS = {}
        _REPOSITORY_IDS = {}


atexit.register(flush_journal)
