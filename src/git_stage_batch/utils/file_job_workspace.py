"""Invocation-private artifact storage for file-scoped jobs."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager
from dataclasses import fields, is_dataclass
from enum import Enum
import json
from pathlib import (
    Path,
    PosixPath,
    PurePath,
    PurePosixPath,
    PureWindowsPath,
    WindowsPath,
)
import pickle
import stat
import tempfile
from typing import Any

from ..core.buffer import BufferInput, LineBuffer, write_buffer_to_path


_STANDARD_PATH_TYPES = frozenset({
    Path,
    PosixPath,
    PurePath,
    PurePosixPath,
    PureWindowsPath,
    WindowsPath,
})


class FileJobWorkspace(AbstractContextManager["FileJobWorkspace"]):
    """Own private input, output, and scratch paths for one invocation."""

    def __init__(
        self,
        *,
        parent_directory: str | Path | None = None,
    ) -> None:
        resolved_parent = (
            None
            if parent_directory is None
            else Path(parent_directory).resolve()
        )
        self._temporary_directory = tempfile.TemporaryDirectory(
            prefix="git-stage-batch-file-jobs-",
            dir=resolved_parent,
        )
        self._root = Path(self._temporary_directory.name).resolve()
        self._root.chmod(0o700)
        self._path_counters: dict[tuple[int, str], int] = {}
        self._closed = False

    @property
    def root(self) -> Path:
        """Return the workspace root."""
        return self._root

    def job_directory(self, ordinal: int) -> Path:
        """Return the deterministic private directory for one job ordinal."""
        self._require_open()
        normalized_ordinal = _normalize_ordinal(ordinal)
        path = self._require_workspace_path(
            self._root / "jobs" / f"{normalized_ordinal:08d}"
        )
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        return path

    def scratch_directory(self, ordinal: int) -> Path:
        """Return the mapped-storage scratch directory for one job."""
        path = self._require_workspace_path(
            self.job_directory(ordinal) / "scratch"
        )
        path.mkdir(mode=0o700, exist_ok=True)
        return path

    def artifact_path(self, ordinal: int, name: str) -> Path:
        """Allocate a unique parent-written artifact path for one job."""
        return self._allocate_path(ordinal, "artifacts", name)

    def output_path(self, ordinal: int, name: str) -> Path:
        """Allocate a unique worker-writable output path for one job."""
        return self._allocate_path(ordinal, "outputs", name)

    def write_buffer(
        self,
        ordinal: int,
        name: str,
        buffer: BufferInput,
    ) -> Path:
        """Stream buffer content to a unique private artifact."""
        path = self.artifact_path(ordinal, name)
        write_buffer_to_path(path, buffer)
        return path

    def read_buffer(
        self,
        path: str | Path,
        *,
        spool_dir: str | Path | None = None,
    ) -> LineBuffer:
        """Open a regular workspace artifact as a path-backed line buffer."""
        artifact_path = self._require_regular_workspace_file(path)
        return LineBuffer.from_path(
            artifact_path,
            spool_dir=spool_dir,
        )

    def write_json(
        self,
        ordinal: int,
        name: str,
        value: Any,
    ) -> Path:
        """Write small JSON metadata to a unique private artifact."""
        path = self.artifact_path(ordinal, name)
        with path.open("x", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=True, separators=(",", ":"))
            output.write("\n")
        return path

    def read_json(self, path: str | Path) -> Any:
        """Read small JSON metadata from a workspace artifact."""
        artifact_path = self._require_regular_workspace_file(path)
        with artifact_path.open(encoding="utf-8") as source:
            return json.load(source)

    def write_jsonl(
        self,
        ordinal: int,
        name: str,
        values: Iterable[Any],
    ) -> Path:
        """Stream JSON records to a unique private JSONL artifact."""
        path = self.artifact_path(ordinal, name)
        with path.open("x", encoding="utf-8") as output:
            for value in values:
                json.dump(
                    value,
                    output,
                    ensure_ascii=True,
                    separators=(",", ":"),
                )
                output.write("\n")
        return path

    def stream_jsonl(self, path: str | Path) -> Iterator[Any]:
        """Yield JSON records from a workspace artifact."""
        artifact_path = self._require_regular_workspace_file(path)
        with artifact_path.open(encoding="utf-8") as source:
            for line in source:
                yield json.loads(line)

    def write_pickle(
        self,
        ordinal: int,
        name: str,
        value: Any,
    ) -> Path:
        """Write trusted private metadata to a pickle artifact."""
        _assert_pickle_metadata_value(
            value,
            label="pickle metadata",
            active_ids=set(),
        )
        path = self.artifact_path(ordinal, name)
        with path.open("xb") as output:
            pickle.dump(value, output, protocol=pickle.HIGHEST_PROTOCOL)
        return path

    def read_pickle(self, path: str | Path) -> Any:
        """Read trusted private metadata from a workspace pickle artifact."""
        artifact_path = self._require_regular_workspace_file(path)
        with artifact_path.open("rb") as source:
            return pickle.load(source)

    def close(self) -> None:
        """Remove the workspace and every artifact it owns."""
        if self._closed:
            return
        self._temporary_directory.cleanup()
        self._closed = True

    def __enter__(self) -> FileJobWorkspace:
        self._require_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _allocate_path(
        self,
        ordinal: int,
        category: str,
        name: str,
    ) -> Path:
        normalized_name = _normalize_artifact_name(name)
        directory = self._require_workspace_path(
            self.job_directory(ordinal) / category
        )
        directory.mkdir(mode=0o700, exist_ok=True)
        key = (ordinal, category)
        sequence = self._path_counters.get(key, 0)
        self._path_counters[key] = sequence + 1
        path = directory / f"{sequence:08d}-{normalized_name}"
        return self._require_workspace_path(path)

    def _require_workspace_path(self, path: str | Path) -> Path:
        self._require_open()
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self._root / candidate
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(self._root):
            raise ValueError("artifact path must stay inside the file-job workspace")
        return resolved

    def _require_regular_workspace_file(self, path: str | Path) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self._root / candidate
        artifact_path = self._require_workspace_path(candidate)
        metadata = candidate.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("workspace artifact must be a regular file")
        return artifact_path

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError("file-job workspace is closed")


def _normalize_ordinal(ordinal: int) -> int:
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
        raise ValueError("job ordinal must be a non-negative integer")
    return ordinal


def _normalize_artifact_name(name: str) -> str:
    if not isinstance(name, str) or not name:
        raise ValueError("artifact name must be a non-empty file name")
    path = Path(name)
    if path.is_absolute() or len(path.parts) != 1 or path.name in {".", ".."}:
        raise ValueError("artifact name must not contain path traversal")
    return path.name


def _assert_pickle_metadata_value(
    value: object,
    *,
    label: str,
    active_ids: set[int],
) -> None:
    if value is None or type(value) in {bool, int, float, str}:
        return
    if isinstance(value, Enum):
        _assert_pickle_metadata_enum_shape(value, label=label)
        _assert_pickle_metadata_collection(
            value,
            label=label,
            active_ids=active_ids,
            values=(value.value,),
        )
        return
    if isinstance(value, (bool, int, float, str)):
        raise TypeError(f"{label} contains a state-bearing scalar subtype")
    if isinstance(value, PurePath):
        if type(value) not in _STANDARD_PATH_TYPES:
            raise TypeError(f"{label} contains a custom path subtype")
        return
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError(f"{label} contains file-content bytes")
    if callable(getattr(value, "close", None)):
        raise TypeError(f"{label} contains a resource-bearing value")
    if type(value) is dict:
        _assert_pickle_metadata_collection(
            value,
            label=label,
            active_ids=active_ids,
            values=(
                item
                for key_value in value.items()
                for item in key_value
            ),
        )
        return
    if type(value) in {list, tuple, set, frozenset}:
        _assert_pickle_metadata_collection(
            value,
            label=label,
            active_ids=active_ids,
            values=value,
        )
        return
    if isinstance(value, (dict, list, tuple, set, frozenset)):
        raise TypeError(f"{label} contains a state-bearing collection subtype")
    if is_dataclass(value) and not isinstance(value, type):
        field_names = {field.name for field in fields(value)}
        instance_state = getattr(value, "__dict__", {})
        extra_state_names = set(instance_state) - field_names
        if extra_state_names:
            raise TypeError(f"{label} contains undeclared dataclass state")
        _assert_pickle_metadata_dataclass_shape(
            value,
            label=label,
            field_names=field_names,
        )
        _assert_pickle_metadata_collection(
            value,
            label=label,
            active_ids=active_ids,
            values=(getattr(value, field.name) for field in fields(value)),
        )
        return
    raise TypeError(
        f"{label} contains unsupported {type(value).__module__}."
        f"{type(value).__qualname__}"
    )


def _assert_pickle_metadata_enum_shape(value: Enum, *, label: str) -> None:
    for value_class in type(value).__mro__:
        if value_class.__module__ in {"builtins", "enum"}:
            continue
        if _has_pickle_hook(value_class):
            raise TypeError(f"{label} contains custom pickle behavior")


def _assert_pickle_metadata_dataclass_shape(
    value: object,
    *,
    label: str,
    field_names: set[str],
) -> None:
    for value_class in type(value).__mro__:
        if value_class is object:
            continue
        slots = value_class.__dict__.get("__slots__", ())
        if isinstance(slots, str):
            slots = (slots,)
        undeclared_slots = set(slots) - field_names - {
            "__dict__",
            "__weakref__",
        }
        if undeclared_slots:
            raise TypeError(f"{label} contains undeclared dataclass slots")

        for hook_name in (
            "__reduce__",
            "__reduce_ex__",
            "__getnewargs__",
            "__getnewargs_ex__",
            "__getstate__",
            "__setstate__",
        ):
            hook = value_class.__dict__.get(hook_name)
            if hook is None:
                continue
            if (
                hook_name in {"__getstate__", "__setstate__"}
                and getattr(hook, "__module__", None) == "dataclasses"
            ):
                continue
            raise TypeError(f"{label} contains custom pickle behavior")


def _has_pickle_hook(value_class: type[object]) -> bool:
    return any(
        hook_name in value_class.__dict__
        for hook_name in (
            "__reduce__",
            "__reduce_ex__",
            "__getnewargs__",
            "__getnewargs_ex__",
            "__getstate__",
            "__setstate__",
        )
    )


def _assert_pickle_metadata_collection(
    collection: object,
    *,
    label: str,
    active_ids: set[int],
    values: Iterable[object],
) -> None:
    collection_id = id(collection)
    if collection_id in active_ids:
        raise TypeError(f"{label} contains a recursive value")
    active_ids.add(collection_id)
    try:
        for value in values:
            _assert_pickle_metadata_value(
                value,
                label=label,
                active_ids=active_ids,
            )
    finally:
        active_ids.remove(collection_id)
