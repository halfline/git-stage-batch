"""Persistent reuse of immutable history-snapshot analysis."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Literal, NoReturn, cast

from ..exceptions import CommandError
from ..fixup.models import FixupUnitKind
from ..fixup.staged_units import (
    acquire_fixup_units_from_diff,
    stream_tree_fixup_diff,
)
from ..utils.file_io import (
    AtomicWriteModePolicy,
    read_required_text_file_contents,
    write_file_byte_chunks_atomically,
)
from ..utils.git_command import (
    run_git_command,
    stream_git_command,
    stream_git_command_bytes,
)
from ..utils.git_object_io import resolve_git_objects
from ..utils.git_repository import get_git_common_directory_path
from ..utils.scratch import default_scratch_parent
from ..utils.strict_json import (
    StrictJsonError,
    loads,
    require_exact_keys,
    require_integer,
    require_list,
    require_object,
    require_string,
)
from .json_files import history_canonical_json_sha256, history_json_byte_chunks
from .models import (
    CURRENT_HISTORY_PLAN_SCHEMA_VERSION,
    HistoryCommitSnapshot,
    HistoryIdentity,
    HistoryPatchUnit,
    HistorySignature,
    HistorySnapshot,
    HistoryUnitDependency,
)
from .ranges import ResolvedHistoryRange
from .records import history_snapshot_record
from .unit_ids import history_unit_id


HISTORY_SNAPSHOT_CACHE_ENVIRONMENT = "GIT_STAGE_BATCH_HISTORY_CACHE_ROOT"
HISTORY_SNAPSHOT_CACHE_SCHEMA_VERSION = 1
HISTORY_SNAPSHOT_ALGORITHM_VERSION = 1
HISTORY_DEPENDENCY_ALGORITHM_VERSION = 1
HISTORY_SNAPSHOT_CACHE_CODE_VERSION = 1
HISTORY_SNAPSHOT_CACHE_MAXIMUM_ENTRIES = 64
_CACHE_OPERATION = "history-snapshot-cache"
_MAXIMUM_CACHE_BYTES = 512 * 1024 * 1024
_MAXIMUM_GIT_CONFIG_BYTES = 16 * 1024 * 1024
_CACHE_KEYS = frozenset(
    {"schema_version", "operation", "key", "snapshot_sha256", "snapshot"}
)
_KEY_KEYS = frozenset(
    {
        "repository_id",
        "object_format",
        "base_commit",
        "tip_commit",
        "base_tree",
        "final_tree",
        "branch_ref",
        "commits_oldest_first",
        "plan_schema_version",
        "snapshot_algorithm_version",
        "dependency_algorithm_version",
        "code_version",
        "git_behavior_fingerprint",
        "git_config_fingerprint",
        "source_edge_diff_sha256",
    }
)
_KEY_INTEGER_FIELDS = (
    "plan_schema_version",
    "snapshot_algorithm_version",
    "dependency_algorithm_version",
    "code_version",
)
_SNAPSHOT_KEYS = frozenset(
    {
        "object_format",
        "range",
        "trees",
        "branch_ref",
        "rewritten_signatures_preserved",
        "commits",
        "dependency_graph",
    }
)
_RANGE_KEYS = frozenset({"base", "tip", "commits_oldest_first"})
_TREE_KEYS = frozenset({"base", "final"})
_COMMIT_KEYS = frozenset(
    {
        "id",
        "parent",
        "tree",
        "parent_tree",
        "author",
        "committer",
        "encoding",
        "message",
        "message_sha256",
        "signatures",
        "unsupported_headers",
        "patch",
    }
)
_IDENTITY_KEYS = frozenset({"raw", "name", "email", "timestamp", "timezone"})
_SIGNATURE_KEYS = frozenset({"header", "sha256"})
_PATCH_KEYS = frozenset({"old_tree", "new_tree", "units"})
_UNIT_KEYS = frozenset(
    {
        "id",
        "patch_id",
        "source_commit",
        "path",
        "kind",
        "old_start",
        "old_len",
        "new_start",
        "new_len",
        "unsupported_reason",
    }
)
_DEPENDENCY_GRAPH_KEYS = frozenset({"algorithm_version", "units"})
_DEPENDENCY_KEYS = frozenset(
    {
        "unit_id",
        "original_position",
        "earliest_position",
        "barrier_unit_id",
        "barrier",
        "detail",
    }
)


HistorySnapshotCacheStatus = Literal["hit", "miss", "rejected", "bypassed"]


@dataclass(frozen=True, slots=True)
class HistorySnapshotCacheObservation:
    """One cache disposition suitable for user-visible success reports."""

    status: HistorySnapshotCacheStatus
    key: str | None
    path: str | None
    reason: str
    retained: bool
_UNIT_KINDS = frozenset(
    {
        "text-addition",
        "text-deletion",
        "text-replacement",
        "text-file-addition",
        "text-file-deletion",
        "binary",
        "rename",
        "mode",
        "file-type",
        "gitlink",
    }
)
_GIT_BEHAVIOR_FINGERPRINT: str | None = None


class _InvalidCache(ValueError):
    pass


def _invalid(detail: str) -> NoReturn:
    raise _InvalidCache(detail)


def _cache_root() -> Path:
    override = os.environ.get(HISTORY_SNAPSHOT_CACHE_ENVIRONMENT)
    if override:
        return Path(override).expanduser().absolute()
    scratch_parent = default_scratch_parent()
    if scratch_parent is None:
        scratch_parent = Path(tempfile.gettempdir())
    return scratch_parent / f"git-stage-batch-{os.getuid()}" / "history-snapshots"


def _repository_id() -> str:
    common_directory = get_git_common_directory_path().resolve()
    return hashlib.sha256(os.fsencode(common_directory)).hexdigest()


def _git_behavior_fingerprint() -> str:
    global _GIT_BEHAVIOR_FINGERPRINT
    if _GIT_BEHAVIOR_FINGERPRINT is None:
        build_options = run_git_command(
            ["version", "--build-options"],
            requires_index_lock=False,
        ).stdout.encode("utf-8", errors="surrogateescape")
        _GIT_BEHAVIOR_FINGERPRINT = hashlib.sha256(build_options).hexdigest()
    return _GIT_BEHAVIOR_FINGERPRINT


def _git_config_fingerprint() -> str:
    digest = hashlib.sha256()
    size = 0
    for chunk in stream_git_command_bytes(
        ["config", "--null", "--list", "--show-origin", "--show-scope"],
        requires_index_lock=False,
    ):
        size += len(chunk)
        if size > _MAXIMUM_GIT_CONFIG_BYTES:
            raise _InvalidCache("effective Git configuration is too large to cache")
        digest.update(chunk)
    return digest.hexdigest()


def _locator_record(
    commit_range: ResolvedHistoryRange,
    branch_ref: str | None,
    *,
    base_tree: str,
    final_tree: str,
) -> dict[str, object]:
    return {
        "repository_id": _repository_id(),
        "object_format": commit_range.object_format,
        "base_commit": commit_range.base_commit,
        "tip_commit": commit_range.tip_commit,
        "base_tree": base_tree,
        "final_tree": final_tree,
        "branch_ref": branch_ref,
        "commits_oldest_first": list(commit_range.commits_oldest_first),
        "plan_schema_version": CURRENT_HISTORY_PLAN_SCHEMA_VERSION,
        "snapshot_algorithm_version": HISTORY_SNAPSHOT_ALGORITHM_VERSION,
        "dependency_algorithm_version": HISTORY_DEPENDENCY_ALGORITHM_VERSION,
        "code_version": HISTORY_SNAPSHOT_CACHE_CODE_VERSION,
        "git_behavior_fingerprint": _git_behavior_fingerprint(),
        "git_config_fingerprint": _git_config_fingerprint(),
    }


def _key_record(
    locator: dict[str, object],
    *,
    source_edge_diff_sha256: str,
) -> dict[str, object]:
    return {
        **locator,
        "source_edge_diff_sha256": source_edge_diff_sha256,
    }


def _cache_path(locator: dict[str, object]) -> Path:
    key_sha256 = history_canonical_json_sha256(locator)
    return _cache_root() / f"history-snapshot-{key_sha256}.json"


def _nullable_string(value: object, location: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        _invalid(f"{location} must be a string or null")
    return value


def _integer_or_none(value: object, location: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        _invalid(f"{location} must be an integer or null")
    return value


def _string_list(value: object, location: str) -> tuple[str, ...]:
    result: list[str] = []
    for index, item in enumerate(require_list(value, location)):
        if not isinstance(item, str):
            _invalid(f"{location}[{index}] must be a string")
        result.append(item)
    return tuple(result)


def _identity(value: object, location: str) -> HistoryIdentity:
    record = require_object(value, location)
    require_exact_keys(record, _IDENTITY_KEYS, location)
    return HistoryIdentity(
        raw=require_string(record, "raw", location, allow_empty=True),
        name=require_string(record, "name", location, allow_empty=True),
        email=require_string(record, "email", location, allow_empty=True),
        timestamp=require_integer(record, "timestamp", location),
        timezone=require_string(record, "timezone", location),
    )


def _signature(value: object, location: str) -> HistorySignature:
    record = require_object(value, location)
    require_exact_keys(record, _SIGNATURE_KEYS, location)
    return HistorySignature(
        header=require_string(record, "header", location),
        sha256=require_string(record, "sha256", location),
    )


def _unit(value: object, location: str) -> HistoryPatchUnit:
    record = require_object(value, location)
    require_exact_keys(record, _UNIT_KEYS, location)
    kind = require_string(record, "kind", location)
    if kind not in _UNIT_KINDS:
        _invalid(f"{location}.kind is unsupported")
    return HistoryPatchUnit(
        unit_id=require_string(record, "id", location),
        patch_id=require_string(record, "patch_id", location),
        source_commit=require_string(record, "source_commit", location),
        path=require_string(record, "path", location, allow_empty=True),
        kind=cast(FixupUnitKind, kind),
        old_start=_integer_or_none(record["old_start"], f"{location}.old_start"),
        old_len=_integer_or_none(record["old_len"], f"{location}.old_len"),
        new_start=_integer_or_none(record["new_start"], f"{location}.new_start"),
        new_len=_integer_or_none(record["new_len"], f"{location}.new_len"),
        unsupported_reason=_nullable_string(
            record["unsupported_reason"],
            f"{location}.unsupported_reason",
        ),
    )


def _commit(value: object, location: str) -> HistoryCommitSnapshot:
    record = require_object(value, location)
    require_exact_keys(record, _COMMIT_KEYS, location)
    patch = require_object(record["patch"], f"{location}.patch")
    require_exact_keys(patch, _PATCH_KEYS, f"{location}.patch")
    tree = require_string(record, "tree", location)
    parent_tree = require_string(record, "parent_tree", location)
    if patch["old_tree"] != parent_tree or patch["new_tree"] != tree:
        _invalid(f"{location}.patch tree pair does not match commit metadata")
    units = tuple(
        _unit(item, f"{location}.patch.units[{index}]")
        for index, item in enumerate(
            require_list(patch["units"], f"{location}.patch.units")
        )
    )
    return HistoryCommitSnapshot(
        commit_id=require_string(record, "id", location),
        parent=require_string(record, "parent", location),
        tree=tree,
        parent_tree=parent_tree,
        author=_identity(record["author"], f"{location}.author"),
        committer=_identity(record["committer"], f"{location}.committer"),
        encoding=_nullable_string(record["encoding"], f"{location}.encoding"),
        message=require_string(record, "message", location, allow_empty=True),
        message_sha256=require_string(record, "message_sha256", location),
        signatures=tuple(
            _signature(item, f"{location}.signatures[{index}]")
            for index, item in enumerate(
                require_list(record["signatures"], f"{location}.signatures")
            )
        ),
        unsupported_headers=_string_list(
            record["unsupported_headers"],
            f"{location}.unsupported_headers",
        ),
        units=units,
    )


def _dependency(value: object, location: str) -> HistoryUnitDependency:
    record = require_object(value, location)
    require_exact_keys(record, _DEPENDENCY_KEYS, location)
    barrier = record["barrier"]
    if barrier not in {None, "BLOCKED", "UNKNOWN"}:
        _invalid(f"{location}.barrier is unsupported")
    return HistoryUnitDependency(
        unit_id=require_string(record, "unit_id", location),
        original_position=require_integer(record, "original_position", location),
        earliest_position=require_integer(record, "earliest_position", location),
        barrier_unit_id=_nullable_string(
            record["barrier_unit_id"],
            f"{location}.barrier_unit_id",
        ),
        barrier=barrier,
        detail=_nullable_string(record["detail"], f"{location}.detail"),
    )


def _decode_snapshot(value: object) -> HistorySnapshot:
    record = require_object(value, "snapshot")
    require_exact_keys(record, _SNAPSHOT_KEYS, "snapshot")
    object_format = require_string(record, "object_format", "snapshot")
    if object_format not in {"sha1", "sha256"}:
        _invalid("snapshot.object_format is unsupported")
    range_record = require_object(record["range"], "snapshot.range")
    require_exact_keys(range_record, _RANGE_KEYS, "snapshot.range")
    tree_record = require_object(record["trees"], "snapshot.trees")
    require_exact_keys(tree_record, _TREE_KEYS, "snapshot.trees")
    if record["rewritten_signatures_preserved"] is not False:
        _invalid("snapshot.rewritten_signatures_preserved must be false")
    dependency_graph = require_object(
        record["dependency_graph"],
        "snapshot.dependency_graph",
    )
    require_exact_keys(
        dependency_graph,
        _DEPENDENCY_GRAPH_KEYS,
        "snapshot.dependency_graph",
    )
    if (
        require_integer(
            dependency_graph,
            "algorithm_version",
            "snapshot.dependency_graph",
        )
        != HISTORY_DEPENDENCY_ALGORITHM_VERSION
    ):
        _invalid("snapshot dependency algorithm version changed")
    commits = tuple(
        _commit(item, f"snapshot.commits[{index}]")
        for index, item in enumerate(
            require_list(record["commits"], "snapshot.commits")
        )
    )
    dependencies = tuple(
        _dependency(item, f"snapshot.dependency_graph.units[{index}]")
        for index, item in enumerate(
            require_list(
                dependency_graph["units"],
                "snapshot.dependency_graph.units",
            )
        )
    )
    snapshot = HistorySnapshot(
        object_format=object_format,
        base_commit=require_string(range_record, "base", "snapshot.range"),
        tip_commit=require_string(range_record, "tip", "snapshot.range"),
        base_tree=require_string(tree_record, "base", "snapshot.trees"),
        final_tree=require_string(tree_record, "final", "snapshot.trees"),
        branch_ref=_nullable_string(record["branch_ref"], "snapshot.branch_ref"),
        commits=commits,
        dependencies=dependencies,
    )
    expected_parent = snapshot.base_commit
    expected_parent_tree = snapshot.base_tree
    unit_ids: list[str] = []
    for commit in commits:
        if (
            commit.parent != expected_parent
            or commit.parent_tree != expected_parent_tree
        ):
            _invalid("snapshot commit chain is inconsistent")
        if any(unit.source_commit != commit.commit_id for unit in commit.units):
            _invalid("snapshot unit source does not match its commit")
        unit_ids.extend(unit.unit_id for unit in commit.units)
        expected_parent = commit.commit_id
        expected_parent_tree = commit.tree
    if not commits or commits[-1].commit_id != snapshot.tip_commit:
        _invalid("snapshot tip does not match its commit chain")
    if commits[-1].tree != snapshot.final_tree:
        _invalid("snapshot final tree does not match its commit chain")
    if len(unit_ids) != len(set(unit_ids)):
        _invalid("snapshot contains duplicate unit IDs")
    if tuple(dependency.unit_id for dependency in dependencies) != tuple(unit_ids):
        _invalid("snapshot dependency inventory does not match its units")
    return snapshot


def decode_history_snapshot_record(value: object) -> HistorySnapshot:
    """Strictly decode one frozen snapshot record without reading Git."""
    try:
        return _decode_snapshot(value)
    except StrictJsonError as error:
        _invalid(str(error))


def _record(key: dict[str, object], snapshot: HistorySnapshot) -> dict[str, object]:
    snapshot_record = history_snapshot_record(snapshot)
    return {
        "schema_version": HISTORY_SNAPSHOT_CACHE_SCHEMA_VERSION,
        "operation": _CACHE_OPERATION,
        "key": key,
        "snapshot_sha256": history_canonical_json_sha256(snapshot_record),
        "snapshot": snapshot_record,
    }


def _load(
    path: Path,
    expected_locator: dict[str, object],
) -> tuple[HistorySnapshot, dict[str, object]] | None:
    try:
        metadata = path.stat()
        if metadata.st_size > _MAXIMUM_CACHE_BYTES:
            return None
        value = require_object(
            loads(read_required_text_file_contents(path)),
            "history snapshot cache",
        )
        require_exact_keys(value, _CACHE_KEYS, "history snapshot cache")
        if (
            require_integer(value, "schema_version", "history snapshot cache")
            != HISTORY_SNAPSHOT_CACHE_SCHEMA_VERSION
            or value["operation"] != _CACHE_OPERATION
        ):
            return None
        key = require_object(value["key"], "history snapshot cache.key")
        require_exact_keys(key, _KEY_KEYS, "history snapshot cache.key")
        for field in _KEY_INTEGER_FIELDS:
            require_integer(key, field, "history snapshot cache.key")
        source_edge_diff_sha256 = require_string(
            key,
            "source_edge_diff_sha256",
            "history snapshot cache.key",
        )
        if len(source_edge_diff_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in source_edge_diff_sha256
        ):
            return None
        locator = {
            field: field_value
            for field, field_value in key.items()
            if field != "source_edge_diff_sha256"
        }
        if locator != expected_locator:
            return None
        snapshot_record = value["snapshot"]
        if value["snapshot_sha256"] != history_canonical_json_sha256(snapshot_record):
            return None
        snapshot = _decode_snapshot(snapshot_record)
        if history_snapshot_record(snapshot) != snapshot_record:
            return None
        return snapshot, key
    except (
        FileNotFoundError,
        OSError,
        RecursionError,
        StrictJsonError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        return None


def _store(path: Path, record: dict[str, object]) -> bool:
    try:
        try:
            path.parent.mkdir(parents=True, mode=0o700)
        except FileExistsError:
            pass
        else:
            os.chmod(path.parent, 0o700)
        write_file_byte_chunks_atomically(
            path,
            history_json_byte_chunks(record),
            mode_policy=AtomicWriteModePolicy.PRIVATE,
        )
        _evict_old_entries(path.parent, preserve=path)
        return True
    except (CommandError, OSError):
        # Cache availability never changes command correctness.
        return False


def _evict_old_entries(root: Path, *, preserve: Path) -> None:
    entries: list[tuple[int, str, Path]] = []
    for candidate in root.glob("history-snapshot-*.json"):
        name = candidate.name
        digest = name.removeprefix("history-snapshot-").removesuffix(".json")
        if len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            continue
        try:
            metadata = candidate.stat()
        except OSError:
            continue
        if candidate != preserve:
            entries.append((metadata.st_mtime_ns, name, candidate))
    excess = len(entries) + 1 - HISTORY_SNAPSHOT_CACHE_MAXIMUM_ENTRIES
    for _mtime, _name, candidate in sorted(entries)[: max(0, excess)]:
        try:
            candidate.unlink()
        except OSError:
            continue


def _source_edge_analysis(
    snapshot: HistorySnapshot,
) -> tuple[str, tuple[tuple[HistoryPatchUnit, ...], ...]]:
    digest = hashlib.sha256(b"git-stage-batch-rewrite-source-diffs-v1\0")
    edge_units: list[tuple[HistoryPatchUnit, ...]] = []
    for commit in snapshot.commits:
        digest.update(commit.parent_tree.encode("ascii"))
        digest.update(b"\0")
        digest.update(commit.tree.encode("ascii"))
        digest.update(b"\0")

        def observed_diff() -> Iterator[bytes]:
            for chunk in stream_tree_fixup_diff(commit.parent_tree, commit.tree):
                digest.update(chunk)
                yield chunk

        with acquire_fixup_units_from_diff(
            observed_diff(),
            allow_file_type_changes=True,
        ) as fixup_units:
            edge_units.append(
                tuple(
                    HistoryPatchUnit(
                        unit_id=history_unit_id(commit.commit_id, unit.unit_id),
                        patch_id=unit.unit_id,
                        source_commit=commit.commit_id,
                        path=unit.path,
                        kind=unit.kind,
                        old_start=unit.old_start,
                        old_len=unit.old_len,
                        new_start=unit.new_start,
                        new_len=unit.new_len,
                        unsupported_reason=unit.unsupported_reason,
                    )
                    for unit in fixup_units
                )
            )
        digest.update(b"\0history-source-edge-end\0")
    return digest.hexdigest(), tuple(edge_units)


def _source_object_closure_matches(snapshot: HistorySnapshot) -> bool:
    expected_commits = {
        commit_id: "commit"
        for commit_id in {
            snapshot.base_commit,
            *(commit.commit_id for commit in snapshot.commits),
        }
    }
    try:
        commits = resolve_git_objects(expected_commits)
    except (OSError, RuntimeError, subprocess.CalledProcessError):
        return False
    if not all(
        object_id in commits
        and commits[object_id].object_id == object_id
        and commits[object_id].object_type == object_type
        for object_id, object_type in expected_commits.items()
    ):
        return False

    object_id_width = 40 if snapshot.object_format == "sha1" else 64
    tree_roots = tuple(
        dict.fromkeys(
            (
                snapshot.base_tree,
                *(commit.tree for commit in snapshot.commits),
            )
        )
    )
    roots_seen: set[str] = set()
    requested_count = 0
    reported_count = 0
    try:
        scratch_parent = default_scratch_parent()
        with (
            tempfile.TemporaryFile(dir=scratch_parent) as root_input,
            tempfile.TemporaryFile(dir=scratch_parent) as object_ids,
            tempfile.TemporaryFile(dir=scratch_parent) as expected_object_ids,
        ):
            for tree in tree_roots:
                root_input.write(tree.encode("ascii") + b"\n")
            root_input.seek(0)
            for line in stream_git_command(
                [
                    "rev-list",
                    "--objects",
                    "--no-object-names",
                    "--missing=error",
                    "--stdin",
                ],
                stdin_chunks=root_input,
                requires_index_lock=False,
            ):
                object_id = line.removesuffix(b"\n")
                if len(object_id) != object_id_width or any(
                    byte not in b"0123456789abcdef" for byte in object_id
                ):
                    return False
                object_id_text = object_id.decode("ascii")
                if object_id_text in tree_roots:
                    roots_seen.add(object_id_text)
                object_ids.write(object_id + b"\n")
                expected_object_ids.write(object_id + b"\n")
                requested_count += 1
            if roots_seen != set(tree_roots):
                return False
            object_ids.seek(0)
            expected_object_ids.seek(0)
            for line in stream_git_command(
                [
                    "cat-file",
                    "--batch-check=%(objectname) %(objecttype)",
                ],
                stdin_chunks=object_ids,
                requires_index_lock=False,
            ):
                fields = line.removesuffix(b"\n").split(b" ")
                expected_object_id = expected_object_ids.readline().removesuffix(b"\n")
                if (
                    len(fields) != 2
                    or fields[0] != expected_object_id
                    or fields[1] not in {b"blob", b"tree"}
                ):
                    return False
                reported_count += 1
    except (OSError, RuntimeError, subprocess.CalledProcessError):
        return False
    return reported_count == requested_count


def acquire_cached_history_snapshot(
    commit_range: ResolvedHistoryRange,
    branch_ref: str | None,
    *,
    base_tree: str,
    final_tree: str,
    build: Callable[[], HistorySnapshot],
    observe: Callable[[HistorySnapshotCacheObservation], None] | None = None,
) -> HistorySnapshot:
    """Reuse exact immutable range analysis, or build and publish it once."""
    try:
        locator = _locator_record(
            commit_range,
            branch_ref,
            base_tree=base_tree,
            final_tree=final_tree,
        )
        path = _cache_path(locator)
    except (
        OSError,
        RecursionError,
        subprocess.CalledProcessError,
        TypeError,
        ValueError,
    ):
        if observe is not None:
            observe(
                HistorySnapshotCacheObservation(
                    status="bypassed",
                    key=None,
                    path=None,
                    reason="cache-key-unavailable",
                    retained=False,
                )
            )
        return build()
    try:
        cache_entry_existed = path.exists()
        cache_path_unavailable = False
    except OSError:
        cache_entry_existed = False
        cache_path_unavailable = True
    loaded = _load(path, locator)
    if loaded is not None:
        cached, stored_key = loaded
        expected_commits = commit_range.commits_oldest_first
        try:
            source_edge_diff_sha256, current_edge_units = _source_edge_analysis(cached)
            expected_key = _key_record(
                locator,
                source_edge_diff_sha256=source_edge_diff_sha256,
            )
            if (
                cached.object_format == commit_range.object_format
                and cached.base_commit == commit_range.base_commit
                and cached.tip_commit == commit_range.tip_commit
                and cached.base_tree == base_tree
                and cached.final_tree == final_tree
                and cached.branch_ref == branch_ref
                and tuple(commit.commit_id for commit in cached.commits)
                == expected_commits
                and current_edge_units
                == tuple(commit.units for commit in cached.commits)
                and stored_key == expected_key
                and _source_object_closure_matches(cached)
            ):
                if observe is not None:
                    observe(
                        HistorySnapshotCacheObservation(
                            status="hit",
                            key=history_canonical_json_sha256(stored_key),
                            path=str(path),
                            reason="authenticated",
                            retained=True,
                        )
                    )
                return cached
        except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError):
            pass
    snapshot = build()
    if (
        snapshot.object_format != commit_range.object_format
        or snapshot.base_commit != commit_range.base_commit
        or snapshot.tip_commit != commit_range.tip_commit
        or snapshot.base_tree != base_tree
        or snapshot.final_tree != final_tree
        or snapshot.branch_ref != branch_ref
        or tuple(commit.commit_id for commit in snapshot.commits)
        != commit_range.commits_oldest_first
    ):
        raise ValueError("history snapshot builder returned different range facts")
    retained = False
    reported_cache_key: str | None = None
    try:
        source_edge_diff_sha256, current_edge_units = _source_edge_analysis(snapshot)
        post_build_locator = _locator_record(
            commit_range,
            branch_ref,
            base_tree=base_tree,
            final_tree=final_tree,
        )
        if (
            post_build_locator == locator
            and current_edge_units == tuple(commit.units for commit in snapshot.commits)
            and _source_object_closure_matches(snapshot)
        ):
            key = _key_record(
                locator,
                source_edge_diff_sha256=source_edge_diff_sha256,
            )
            reported_cache_key = history_canonical_json_sha256(key)
            retained = _store(path, _record(key, snapshot))
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError):
        pass
    if observe is not None:
        observe(
            HistorySnapshotCacheObservation(
                status="rejected" if cache_entry_existed else "miss",
                key=reported_cache_key,
                path=str(path),
                reason=(
                    "entry-failed-authentication"
                    if cache_entry_existed
                    else (
                        "cache-path-unavailable"
                        if cache_path_unavailable
                        else "entry-absent"
                    )
                ),
                retained=retained,
            )
        )
    return snapshot
