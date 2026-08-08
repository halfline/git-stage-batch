"""Resumable, explicit materialization for resolved rewrite outputs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from typing import Literal, NoReturn, cast
import uuid

from ..exceptions import CommandError
from ..git_paths import display_path, terminal_safe_text
from ..i18n import _
from ..utils.git_index import (
    GitIndexEntryUpdate,
    git_read_tree,
    git_update_index_entries,
    git_write_tree,
    temp_git_index,
)
from ..utils.git_object_io import (
    GitObjectQuarantine,
    GitTreeEntry,
    list_git_tree_entries,
    stream_git_blobs,
    temporary_git_object_environment,
)
from ..utils.strict_json import (
    StrictJsonError,
    loads as strict_json_loads,
    require_exact_keys,
    require_integer,
    require_list,
    require_object,
    require_string,
)
from .json_files import (
    history_canonical_json_sha256,
    history_json_byte_chunks,
    history_json_sha256,
)
from .models import HistoryPlanDocument, HistoryPlannedCommit, HistoryPatchUnit
from .plan_files import read_and_validate_history_plan_semantics
from .records import (
    history_plan_document_record,
    history_planned_commit_record,
    history_snapshot_record,
)
from .replay import HistoryReplayResult, materialize_history_output_trees
from .resolution_files import (
    MAXIMUM_RESOLUTION_METADATA_BYTES,
    ResolutionArtifactDigest,
    copy_resolution_artifact_atomically,
    create_private_resolution_directory,
    digest_resolution_artifact,
    import_resolution_artifact_blob,
    list_resolution_directory,
    lock_resolution_directory,
    publish_private_resolution_directory,
    read_resolution_metadata,
    recover_interrupted_resolution_artifact_write,
    require_private_resolution_directory,
    resolution_artifact_name,
    write_resolution_artifact_atomically,
)


CURRENT_HISTORY_RESOLUTION_WORKSPACE_SCHEMA_VERSION = 1
HistoryResolutionWorkspaceStatus = Literal["NEEDS_RESOLUTION", "COMPLETE"]
_WORKSPACE_OPERATION = "rewrite-resolution"
_REQUEST_OPERATION = "rewrite-resolution-request"
_RESULT_OPERATION = "rewrite-resolution-result"
_RECEIPT_OPERATION = "rewrite-resolution-receipt"
_COMPLETE_OPERATION = "rewrite-resolution-complete"
_SUPPORTED_MODES = frozenset({"100644", "100755"})
_UNSUPPORTED_UNIT_KINDS = frozenset({"rename", "file-type", "gitlink"})
_CONTENT_UNIT_KINDS = frozenset(
    {
        "text-addition",
        "text-deletion",
        "text-replacement",
        "text-file-addition",
        "text-file-deletion",
        "binary",
    }
)
_RESULT_KEYS = (
    "schema_version",
    "operation",
    "resolution_id",
    "output_index",
    "output_key",
    "parent_tree",
    "paths",
)
_RESULT_PATH_KEYS = ("path", "artifact", "state", "mode")


@dataclass(frozen=True, slots=True)
class HistoryResolutionWorkspaceResult:
    """User-facing progress from one explicit resolution workspace step."""

    status: HistoryResolutionWorkspaceStatus
    plan_path: str
    workspace_path: str
    completed_resolved_outputs: int
    total_resolved_outputs: int
    output_index: int | None
    output_key: str | None
    authorized_paths: tuple[str, ...]
    request_path: str | None
    result_path: str | None
    results_path: str | None


@dataclass(frozen=True, slots=True)
class HistoryAuthenticatedResolution:
    """Authenticated provenance and replay facts for one COMPLETE workspace."""

    raw_plan_sha256: str
    complete_sha256: str
    workspace_path: str
    replay: HistoryReplayResult


@dataclass(frozen=True, slots=True)
class _WorkspaceBinding:
    record: dict[str, object]
    exact_sha256: str
    resolution_id: str
    resolved_output_indexes: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _PathResult:
    path: str
    artifact: str
    state: Literal["PRESENT", "ABSENT"]
    mode: str | None


@dataclass(frozen=True, slots=True)
class _PathPolicy:
    state: Literal["PRESENT", "ABSENT"]
    mode: str | None
    blob: str | None
    may_add: bool
    may_delete: bool
    may_change_mode: bool
    may_change_content: bool
    authorized_modes: frozenset[str]


@dataclass(frozen=True, slots=True)
class _ReceiptReplay:
    output_index: int
    output_key: str
    parent_tree: str
    output_tree: str
    receipt_sha256: str


@dataclass(frozen=True, slots=True)
class _PendingResolution:
    output_index: int
    output_key: str
    authorized_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ProvisionalReceipt:
    output_path: Path
    record: dict[str, object]
    replay: _ReceiptReplay


class _ResolutionNeeded(Exception):
    def __init__(self, pending: _PendingResolution) -> None:
        super().__init__(pending.output_key)
        self.pending = pending


class _AcceptanceReady(Exception):
    pass


def _invalid(detail: str) -> NoReturn:
    raise CommandError(
        _("Invalid rewrite resolution workspace: {detail}").format(
            detail=terminal_safe_text(detail)
        )
    )


def _absolute_workspace_path(workspace_path: str) -> Path:
    if not isinstance(workspace_path, str) or not workspace_path:
        _invalid(_("workspace path must be a non-empty string"))
    return Path(os.path.abspath(workspace_path))


def _semantic_plan_record(document: HistoryPlanDocument) -> dict[str, object]:
    document_record = history_plan_document_record(document)
    return {
        "schema_version": document.schema_version,
        "plan": document_record["plan"],
    }


def _workspace_binding(
    document: HistoryPlanDocument,
    plan_sha256: str,
) -> _WorkspaceBinding:
    snapshot_sha256 = history_canonical_json_sha256(
        history_snapshot_record(document.snapshot)
    )
    semantic_plan_sha256 = history_canonical_json_sha256(
        _semantic_plan_record(document)
    )
    resolution_id = history_canonical_json_sha256(
        {
            "operation": _WORKSPACE_OPERATION,
            "snapshot_sha256": snapshot_sha256,
            "semantic_plan_sha256": semantic_plan_sha256,
        }
    )
    resolved_indexes = tuple(
        index
        for index, output in enumerate(document.plan.outputs)
        if output.materialization == "RESOLVED"
    )
    record: dict[str, object] = {
        "schema_version": CURRENT_HISTORY_RESOLUTION_WORKSPACE_SCHEMA_VERSION,
        "operation": _WORKSPACE_OPERATION,
        "resolution_id": resolution_id,
        "plan_sha256": plan_sha256,
        "snapshot_sha256": snapshot_sha256,
        "semantic_plan_sha256": semantic_plan_sha256,
        "object_format": document.snapshot.object_format,
        "base_tree": document.snapshot.base_tree,
        "final_tree": document.snapshot.final_tree,
        "output_count": len(document.plan.outputs),
        "resolved_output_indexes": list(resolved_indexes),
    }
    return _WorkspaceBinding(
        record=record,
        exact_sha256=history_json_sha256(record),
        resolution_id=resolution_id,
        resolved_output_indexes=resolved_indexes,
    )


def _write_json(path: Path, record: object) -> ResolutionArtifactDigest:
    return write_resolution_artifact_atomically(
        path,
        history_json_byte_chunks(record),
        maximum_bytes=MAXIMUM_RESOLUTION_METADATA_BYTES,
    )


def _load_json(path: Path, location: str) -> tuple[dict[str, object], str]:
    try:
        payload, digest = read_resolution_metadata(path)
        value = strict_json_loads(payload)
        record = require_object(value, location)
    except (StrictJsonError, UnicodeError) as error:
        _invalid(
            _("{location} is not valid strict JSON ({error})").format(
                location=location,
                error=error,
            )
        )
    return record, digest.sha256


def _read_expected_json(
    path: Path,
    expected: dict[str, object],
    location: str,
) -> str:
    record, digest = _load_json(path, location)
    if record != expected or digest != history_json_sha256(expected):
        _invalid(
            _("{location} does not match its immutable binding").format(
                location=location
            )
        )
    return digest


def _require_directory_entries(
    path: Path,
    expected: set[str],
    location: str,
) -> None:
    actual = set(list_resolution_directory(path))
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append(
                "missing " + ", ".join(display_path(name) for name in missing)
            )
        if extra:
            details.append(
                "unexpected " + ", ".join(display_path(name) for name in extra)
            )
        _invalid(
            _("{location} has the wrong entries ({detail})").format(
                location=location,
                detail="; ".join(details),
            )
        )


def _workspace_exists(path: Path) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as error:
        _invalid(_("cannot inspect workspace path ({error})").format(error=error))
    return True


def _initialize_workspace(path: Path, binding: _WorkspaceBinding) -> None:
    staging = path.parent / (
        f".git-stage-batch-resolution-workspace-{uuid.uuid4().hex}"
    )
    create_private_resolution_directory(staging)
    with lock_resolution_directory(staging, moved_to=path):
        create_private_resolution_directory(staging / "outputs")
        _write_json(staging / "workspace.json", binding.record)
        _require_directory_entries(
            staging,
            {".workspace.lock", "outputs", "workspace.json"},
            "staged workspace root",
        )
        publish_private_resolution_directory(staging, path)


def _recover_mutable_workspace_root(
    path: Path,
    binding: _WorkspaceBinding,
) -> None:
    require_private_resolution_directory(path)
    require_private_resolution_directory(path / "outputs")
    _read_expected_json(path / "workspace.json", binding.record, "workspace.json")
    if "complete.json" not in set(list_resolution_directory(path)):
        recover_interrupted_resolution_artifact_write(path / "complete.json")


def _validate_workspace_root(
    path: Path,
    binding: _WorkspaceBinding,
    *,
    allow_complete: bool,
) -> None:
    require_private_resolution_directory(path)
    require_private_resolution_directory(path / "outputs")
    _read_expected_json(path / "workspace.json", binding.record, "workspace.json")
    expected = {".workspace.lock", "outputs", "workspace.json"}
    if allow_complete:
        expected.add("complete.json")
    actual = set(list_resolution_directory(path))
    if actual not in (
        expected,
        expected - {"complete.json"},
    ):
        _require_directory_entries(path, expected, "workspace root")


def _output_key(
    binding: _WorkspaceBinding,
    output_index: int,
    output: HistoryPlannedCommit,
    parent_tree: str,
) -> str:
    return history_canonical_json_sha256(
        {
            "operation": _REQUEST_OPERATION,
            "resolution_id": binding.resolution_id,
            "output_index": output_index,
            "output": history_planned_commit_record(output),
            "parent_tree": parent_tree,
        }
    )


def _unit_inventory(document: HistoryPlanDocument) -> dict[str, HistoryPatchUnit]:
    return {
        unit.unit_id: unit
        for commit in document.snapshot.commits
        for unit in commit.units
    }


def _authorized_paths(
    document: HistoryPlanDocument,
    output: HistoryPlannedCommit,
) -> tuple[tuple[str, tuple[HistoryPatchUnit, ...]], ...]:
    units = _unit_inventory(document)
    paths: dict[str, list[HistoryPatchUnit]] = {}
    for unit_id in output.source_unit_ids:
        unit = units[unit_id]
        if unit.kind in _UNSUPPORTED_UNIT_KINDS:
            _invalid(
                _("source unit {unit} has unsupported kind {kind}").format(
                    unit=unit_id,
                    kind=unit.kind,
                )
            )
        if unit.unsupported_reason in {
            "rename-with-content",
            "file-type-with-content",
        }:
            _invalid(
                _("source unit {unit} has unsupported coupling {reason}").format(
                    unit=unit_id,
                    reason=unit.unsupported_reason,
                )
            )
        paths.setdefault(unit.path, []).append(unit)
    return tuple((path, tuple(path_units)) for path, path_units in paths.items())


def _tree_entry(
    tree: str,
    path: str,
    *,
    env: dict[str, str],
    context: str,
) -> GitTreeEntry | None:
    entry = list_git_tree_entries(tree, [path], env=env).get(path)
    if entry is None:
        return None
    if entry.object_type != "blob" or entry.mode not in _SUPPORTED_MODES:
        _invalid(
            _("{context} path {path!r} has unsupported kind or mode {mode}").format(
                context=context,
                path=path,
                mode=entry.mode,
            )
        )
    return entry


def _reference_name(
    output_index: int,
    role: str,
    source_commit: str | None,
    path: str,
) -> str:
    return resolution_artifact_name(
        output_index,
        "\0".join((role, source_commit or "", path)),
    )


def _stream_blob_digest(
    entry: GitTreeEntry,
    *,
    env: dict[str, str],
    destination: Path | None,
) -> ResolutionArtifactDigest:
    streams = stream_git_blobs([entry.object_id], env=env)
    try:
        blob = next(streams)
    except StopIteration:
        _invalid(_("source blob {blob} is not accessible").format(blob=entry.object_id))
    if blob.object_id != entry.object_id:
        _invalid(_("Git returned the wrong source blob"))
    if destination is not None:
        recover_interrupted_resolution_artifact_write(destination)
        digest = write_resolution_artifact_atomically(
            destination,
            blob.content_chunks,
        )
    else:
        sha256 = hashlib.sha256()
        size = 0
        for chunk in blob.content_chunks:
            sha256.update(chunk)
            size += len(chunk)
        digest = ResolutionArtifactDigest(size=size, sha256=sha256.hexdigest())
    try:
        next(streams)
    except StopIteration:
        pass
    else:
        _invalid(_("Git returned duplicate source blobs"))
    if digest.size != blob.size:
        _invalid(_("source blob size changed while it was exported"))
    return digest


def _reference_record(
    *,
    output_index: int,
    role: str,
    source_commit: str | None,
    tree: str,
    path: str,
    env: dict[str, str],
    references_path: Path,
    export: bool,
) -> dict[str, object]:
    entry = _tree_entry(
        tree,
        path,
        env=env,
        context=role if source_commit is None else f"{role} {source_commit}",
    )
    artifact = _reference_name(output_index, role, source_commit, path)
    if entry is None:
        return {
            "role": role,
            "source_commit": source_commit,
            "state": "ABSENT",
            "mode": None,
            "blob": None,
            "artifact": None,
            "size": None,
            "sha256": None,
        }
    digest = _stream_blob_digest(
        entry,
        env=env,
        destination=references_path / artifact if export else None,
    )
    if not export:
        digest_resolution_artifact(
            references_path / artifact,
            expected=digest,
        )
    return {
        "role": role,
        "source_commit": source_commit,
        "state": "PRESENT",
        "mode": entry.mode,
        "blob": entry.object_id,
        "artifact": artifact,
        "size": digest.size,
        "sha256": digest.sha256,
    }


def _request_record(
    document: HistoryPlanDocument,
    binding: _WorkspaceBinding,
    output_index: int,
    output: HistoryPlannedCommit,
    parent_tree: str,
    output_key: str,
    output_path: Path,
    *,
    env: dict[str, str],
    export: bool,
) -> tuple[dict[str, object], tuple[str, ...]]:
    references_path = output_path / "references"
    sources = {commit.commit_id: commit for commit in document.snapshot.commits}
    path_records: list[dict[str, object]] = []
    expected_reference_files: set[str] = set()
    authorized = _authorized_paths(document, output)
    for path, path_units in authorized:
        source_commits = tuple(dict.fromkeys(unit.source_commit for unit in path_units))
        references = [
            _reference_record(
                output_index=output_index,
                role="CURRENT_PARENT",
                source_commit=None,
                tree=parent_tree,
                path=path,
                env=env,
                references_path=references_path,
                export=export,
            )
        ]
        for source_commit in source_commits:
            source = sources[source_commit]
            references.extend(
                (
                    _reference_record(
                        output_index=output_index,
                        role="SOURCE_BEFORE",
                        source_commit=source_commit,
                        tree=source.parent_tree,
                        path=path,
                        env=env,
                        references_path=references_path,
                        export=export,
                    ),
                    _reference_record(
                        output_index=output_index,
                        role="SOURCE_AFTER",
                        source_commit=source_commit,
                        tree=source.tree,
                        path=path,
                        env=env,
                        references_path=references_path,
                        export=export,
                    ),
                )
            )
        for reference in references:
            artifact = reference["artifact"]
            if isinstance(artifact, str):
                if artifact in expected_reference_files:
                    _invalid(_("resolution reference artifact names collided"))
                expected_reference_files.add(artifact)
        path_records.append(
            {
                "path": path,
                "result_artifact": resolution_artifact_name(output_index, path),
                "source_units": [
                    {
                        "unit_id": unit.unit_id,
                        "source_commit": unit.source_commit,
                        "kind": unit.kind,
                    }
                    for unit in path_units
                ],
                "references": references,
            }
        )
    _require_directory_entries(
        references_path,
        expected_reference_files,
        f"output {output_index + 1} references",
    )
    return (
        {
            "schema_version": CURRENT_HISTORY_RESOLUTION_WORKSPACE_SCHEMA_VERSION,
            "operation": _REQUEST_OPERATION,
            "resolution_id": binding.resolution_id,
            "output_index": output_index,
            "output_key": output_key,
            "parent_tree": parent_tree,
            "output": history_planned_commit_record(output),
            "authorized_paths": path_records,
        },
        tuple(path for path, _units in authorized),
    )


def _seeded_result_record(request: dict[str, object]) -> dict[str, object]:
    paths: list[dict[str, object]] = []
    for raw_path in cast(list[object], request["authorized_paths"]):
        path_record = cast(dict[str, object], raw_path)
        current = cast(list[dict[str, object]], path_record["references"])[0]
        paths.append(
            {
                "path": path_record["path"],
                "artifact": path_record["result_artifact"],
                "state": current["state"],
                "mode": current["mode"],
            }
        )
    return {
        "schema_version": CURRENT_HISTORY_RESOLUTION_WORKSPACE_SCHEMA_VERSION,
        "operation": _RESULT_OPERATION,
        "resolution_id": request["resolution_id"],
        "output_index": request["output_index"],
        "output_key": request["output_key"],
        "parent_tree": request["parent_tree"],
        "paths": paths,
    }


def _export_resolution(
    document: HistoryPlanDocument,
    binding: _WorkspaceBinding,
    output_index: int,
    output: HistoryPlannedCommit,
    parent_tree: str,
    output_key: str,
    output_path: Path,
    *,
    env: dict[str, str],
) -> tuple[str, ...]:
    staging_path = output_path.parent / f".staging-{output_key}"
    output_entries = set(list_resolution_directory(output_path.parent))
    if staging_path.name not in output_entries:
        create_private_resolution_directory(staging_path)
    else:
        require_private_resolution_directory(staging_path)
    staging_entries = set(list_resolution_directory(staging_path))
    if not staging_entries.issubset(
        {"references", "request.json", "result.json", "results"}
    ):
        _invalid(
            _("staged output {output} contains unexpected entries").format(
                output=output_index + 1
            )
        )
    for directory_name in ("references", "results"):
        directory_path = staging_path / directory_name
        if directory_name not in staging_entries:
            create_private_resolution_directory(directory_path)
        else:
            require_private_resolution_directory(directory_path)
    request, authorized_paths = _request_record(
        document,
        binding,
        output_index,
        output,
        parent_tree,
        output_key,
        staging_path,
        env=env,
        export=True,
    )
    result = _seeded_result_record(request)
    expected_result_files: set[str] = set()
    request_paths = cast(list[dict[str, object]], request["authorized_paths"])
    result_paths = cast(list[dict[str, object]], result["paths"])
    for request_path, result_path in zip(request_paths, result_paths, strict=True):
        if result_path["state"] != "PRESENT":
            continue
        current = cast(list[dict[str, object]], request_path["references"])[0]
        reference_artifact = cast(str, current["artifact"])
        result_artifact = cast(str, result_path["artifact"])
        recover_interrupted_resolution_artifact_write(
            staging_path / "results" / result_artifact
        )
        copy_resolution_artifact_atomically(
            staging_path / "references" / reference_artifact,
            staging_path / "results" / result_artifact,
            expected=ResolutionArtifactDigest(
                size=cast(int, current["size"]),
                sha256=cast(str, current["sha256"]),
            ),
        )
        expected_result_files.add(result_artifact)
    _require_directory_entries(
        staging_path / "results",
        expected_result_files,
        f"output {output_index + 1} results",
    )
    recover_interrupted_resolution_artifact_write(staging_path / "result.json")
    _write_json(staging_path / "result.json", result)
    recover_interrupted_resolution_artifact_write(staging_path / "request.json")
    _write_json(staging_path / "request.json", request)
    _require_directory_entries(
        staging_path,
        {"references", "request.json", "result.json", "results"},
        f"staged output {output_index + 1}",
    )
    publish_private_resolution_directory(staging_path, output_path)
    return authorized_paths


def _read_request(
    document: HistoryPlanDocument,
    binding: _WorkspaceBinding,
    output_index: int,
    output: HistoryPlannedCommit,
    parent_tree: str,
    output_key: str,
    output_path: Path,
    *,
    env: dict[str, str],
) -> tuple[dict[str, object], tuple[str, ...]]:
    require_private_resolution_directory(output_path)
    require_private_resolution_directory(output_path / "references")
    require_private_resolution_directory(output_path / "results")
    expected, authorized_paths = _request_record(
        document,
        binding,
        output_index,
        output,
        parent_tree,
        output_key,
        output_path,
        env=env,
        export=False,
    )
    _read_expected_json(
        output_path / "request.json",
        expected,
        f"output {output_index + 1} request.json",
    )
    return expected, authorized_paths


def _parse_result(
    path: Path,
    request: dict[str, object],
    output_index: int,
) -> tuple[tuple[_PathResult, ...], str]:
    location = f"output {output_index + 1} result.json"
    try:
        record, digest = _load_json(path, location)
        require_exact_keys(record, frozenset(_RESULT_KEYS), location)
        if (
            require_integer(record, "schema_version", location)
            != CURRENT_HISTORY_RESOLUTION_WORKSPACE_SCHEMA_VERSION
            or require_string(record, "operation", location) != _RESULT_OPERATION
            or require_string(record, "resolution_id", location)
            != request["resolution_id"]
            or require_integer(record, "output_index", location) != output_index
            or require_string(record, "output_key", location) != request["output_key"]
            or require_string(record, "parent_tree", location) != request["parent_tree"]
        ):
            raise StrictJsonError(f"{location} binding fields do not match request")
        raw_paths = require_list(record["paths"], f"{location}.paths")
        request_paths = cast(list[dict[str, object]], request["authorized_paths"])
        if len(raw_paths) != len(request_paths):
            raise StrictJsonError(f"{location}.paths has the wrong length")
        paths: list[_PathResult] = []
        for index, (raw_path, expected) in enumerate(
            zip(raw_paths, request_paths, strict=True)
        ):
            path_location = f"{location}.paths[{index}]"
            path_record = require_object(raw_path, path_location)
            require_exact_keys(path_record, frozenset(_RESULT_PATH_KEYS), path_location)
            repository_path = require_string(path_record, "path", path_location)
            artifact = require_string(path_record, "artifact", path_location)
            state = require_string(path_record, "state", path_location)
            if repository_path != expected["path"]:
                raise StrictJsonError(f"{path_location}.path is not authorized")
            if artifact != expected["result_artifact"]:
                raise StrictJsonError(f"{path_location}.artifact is not authorized")
            mode = path_record["mode"]
            if state == "ABSENT":
                if mode is not None:
                    raise StrictJsonError(f"{path_location}.mode must be null")
                parsed_state: Literal["PRESENT", "ABSENT"] = "ABSENT"
            elif state == "PRESENT":
                if not isinstance(mode, str) or mode not in _SUPPORTED_MODES:
                    raise StrictJsonError(
                        f"{path_location}.mode must be 100644 or 100755"
                    )
                parsed_state = "PRESENT"
            else:
                raise StrictJsonError(
                    f"{path_location}.state must be PRESENT or ABSENT"
                )
            paths.append(
                _PathResult(
                    path=repository_path,
                    artifact=artifact,
                    state=parsed_state,
                    mode=mode,
                )
            )
    except StrictJsonError as error:
        _invalid(str(error))
    return tuple(paths), digest


def _path_receipt_record(
    result: _PathResult,
    digest: ResolutionArtifactDigest | None,
) -> dict[str, object]:
    return {
        "path": result.path,
        "artifact": result.artifact,
        "state": result.state,
        "mode": result.mode,
        "size": None if digest is None else digest.size,
        "sha256": None if digest is None else digest.sha256,
    }


def _path_policies(request: dict[str, object]) -> tuple[_PathPolicy, ...]:
    policies: list[_PathPolicy] = []
    for raw_path in cast(list[dict[str, object]], request["authorized_paths"]):
        references = cast(list[dict[str, object]], raw_path["references"])
        current = references[0]
        may_add = False
        may_delete = False
        may_change_mode = False
        may_change_content = False
        source_states: dict[str, tuple[dict[str, object], dict[str, object]]] = {}
        for offset in range(1, len(references), 2):
            before = references[offset]
            after = references[offset + 1]
            source_commit = cast(str, before["source_commit"])
            source_states[source_commit] = (before, after)
        authorized_modes: set[str] = set()
        for source_unit in cast(list[dict[str, object]], raw_path["source_units"]):
            kind = cast(str, source_unit["kind"])
            before, after = source_states[cast(str, source_unit["source_commit"])]
            before_present = before["state"] == "PRESENT"
            after_present = after["state"] == "PRESENT"
            content_unit = kind in _CONTENT_UNIT_KINDS
            may_add = may_add or (content_unit and not before_present and after_present)
            may_delete = may_delete or (
                content_unit and before_present and not after_present
            )
            may_change_mode = may_change_mode or kind == "mode"
            may_change_content = may_change_content or content_unit
            if (
                after_present
                and isinstance(after["mode"], str)
                and (kind == "mode" or not before_present)
            ):
                authorized_modes.add(after["mode"])
        policies.append(
            _PathPolicy(
                state=cast(Literal["PRESENT", "ABSENT"], current["state"]),
                mode=cast(str | None, current["mode"]),
                blob=cast(str | None, current["blob"]),
                may_add=may_add,
                may_delete=may_delete,
                may_change_mode=may_change_mode,
                may_change_content=may_change_content,
                authorized_modes=frozenset(authorized_modes),
            )
        )
    return tuple(policies)


def _require_authorized_transition(
    path: _PathResult,
    policy: _PathPolicy,
    blob_object_id: str | None,
) -> None:
    if policy.state == "ABSENT" and path.state == "PRESENT":
        if not policy.may_add:
            _invalid(
                _("result path {path!r} is not authorized to be created").format(
                    path=path.path
                )
            )
        if path.mode not in policy.authorized_modes:
            _invalid(
                _("result path {path!r} uses an unauthorized creation mode").format(
                    path=path.path
                )
            )
        return
    if policy.state == "PRESENT" and path.state == "ABSENT":
        if not policy.may_delete:
            _invalid(
                _("result path {path!r} is not authorized to be deleted").format(
                    path=path.path
                )
            )
        return
    if policy.state == "ABSENT":
        _invalid(
            _("authorized result path {path!r} did not change").format(path=path.path)
        )
    if path.state != "PRESENT" or blob_object_id is None:
        raise AssertionError("present path transition lacks a blob")
    mode_changed = path.mode != policy.mode
    content_changed = blob_object_id != policy.blob
    if mode_changed and not policy.may_change_mode:
        _invalid(
            _("result path {path!r} is not authorized to change mode").format(
                path=path.path
            )
        )
    if mode_changed and path.mode not in policy.authorized_modes:
        _invalid(
            _("result path {path!r} uses an unauthorized changed mode").format(
                path=path.path
            )
        )
    if content_changed and not policy.may_change_content:
        _invalid(
            _("result path {path!r} is not authorized to change content").format(
                path=path.path
            )
        )
    if not mode_changed and not content_changed:
        _invalid(
            _("authorized result path {path!r} did not change").format(path=path.path)
        )


def _apply_result(
    parent_tree: str,
    paths: tuple[_PathResult, ...],
    policies: tuple[_PathPolicy, ...],
    results_path: Path,
    *,
    quarantine: GitObjectQuarantine,
    expected_digests: tuple[ResolutionArtifactDigest | None, ...] | None = None,
) -> tuple[str, tuple[dict[str, object], ...]]:
    if len(paths) != len(policies):
        raise AssertionError("result path policy count differs")
    expected_files = {path.artifact for path in paths if path.state == "PRESENT"}
    _require_directory_entries(results_path, expected_files, "result artifacts")
    updates: list[GitIndexEntryUpdate] = []
    receipt_paths: list[dict[str, object]] = []
    for index, (path, policy) in enumerate(zip(paths, policies, strict=True)):
        expected = None if expected_digests is None else expected_digests[index]
        if path.state == "ABSENT":
            if expected is not None:
                _invalid(_("an absent result path has an artifact digest"))
            updates.append(GitIndexEntryUpdate(file_path=path.path, force_remove=True))
            _require_authorized_transition(path, policy, None)
            receipt_paths.append(_path_receipt_record(path, None))
            continue
        imported = import_resolution_artifact_blob(
            results_path / path.artifact,
            env=quarantine,
            expected=expected,
        )
        _require_authorized_transition(path, policy, imported.blob_object_id)
        updates.append(
            GitIndexEntryUpdate(
                file_path=path.path,
                mode=cast(str, path.mode),
                blob_sha=imported.blob_object_id,
            )
        )
        receipt_paths.append(_path_receipt_record(path, imported.digest))
    with temp_git_index(base_env=quarantine.environment()) as index_env:
        git_read_tree(parent_tree, env=index_env)
        git_update_index_entries(updates, env=index_env)
        output_tree = git_write_tree(env=index_env)
    _require_directory_entries(results_path, expected_files, "result artifacts")
    if output_tree == parent_tree:
        _invalid(_("resolved output would be an empty commit"))
    return output_tree, tuple(receipt_paths)


def _receipt_record(
    binding: _WorkspaceBinding,
    output_index: int,
    output_key: str,
    parent_tree: str,
    output_tree: str,
    result_sha256: str,
    paths: tuple[dict[str, object], ...],
) -> dict[str, object]:
    return {
        "schema_version": CURRENT_HISTORY_RESOLUTION_WORKSPACE_SCHEMA_VERSION,
        "operation": _RECEIPT_OPERATION,
        "resolution_id": binding.resolution_id,
        "output_index": output_index,
        "output_key": output_key,
        "parent_tree": parent_tree,
        "output_tree": output_tree,
        "result_sha256": result_sha256,
        "paths": list(paths),
    }


def _receipt_expected_digests(
    receipt: dict[str, object],
    paths: tuple[_PathResult, ...],
    output_index: int,
) -> tuple[ResolutionArtifactDigest | None, ...]:
    location = f"output {output_index + 1} receipt.json"
    raw_paths = receipt.get("paths")
    if not isinstance(raw_paths, list) or len(raw_paths) != len(paths):
        _invalid(_("{location} has the wrong path inventory").format(location=location))
    digests: list[ResolutionArtifactDigest | None] = []
    for index, (raw_path, result) in enumerate(zip(raw_paths, paths, strict=True)):
        if not isinstance(raw_path, dict):
            _invalid(
                _("{location}.paths[{index}] must be an object").format(
                    location=location,
                    index=index,
                )
            )
        expected_keys = {"path", "artifact", "state", "mode", "size", "sha256"}
        if set(raw_path) != expected_keys:
            _invalid(
                _("{location}.paths[{index}] has the wrong fields").format(
                    location=location,
                    index=index,
                )
            )
        if any(
            raw_path[field] != value
            for field, value in (
                ("path", result.path),
                ("artifact", result.artifact),
                ("state", result.state),
                ("mode", result.mode),
            )
        ):
            _invalid(
                _("{location}.paths[{index}] does not match result.json").format(
                    location=location,
                    index=index,
                )
            )
        size = raw_path["size"]
        sha256 = raw_path["sha256"]
        if result.state == "ABSENT":
            if size is not None or sha256 is not None:
                _invalid(_("an absent receipt path has an artifact digest"))
            digests.append(None)
        else:
            if type(size) is not int or not isinstance(sha256, str):
                _invalid(_("a present receipt path has an invalid artifact digest"))
            digests.append(ResolutionArtifactDigest(size=size, sha256=sha256))
    return tuple(digests)


class _WorkspaceMaterializer:
    def __init__(
        self,
        document: HistoryPlanDocument,
        binding: _WorkspaceBinding,
        workspace_path: Path,
        quarantine: GitObjectQuarantine,
        *,
        accept_one: bool,
        export_missing: bool,
        read_only: bool,
    ) -> None:
        if read_only and (accept_one or export_missing):
            raise AssertionError("read-only materialization cannot mutate workspace")
        self.document = document
        self.binding = binding
        self.workspace_path = workspace_path
        self.outputs_path = workspace_path / "outputs"
        self.quarantine = quarantine
        self.accept_one = accept_one
        self.export_missing = export_missing
        self.read_only = read_only
        self.accepted = False
        self.output_keys: list[str] = []
        self.receipts: list[_ReceiptReplay] = []
        self.provisional_receipt: _ProvisionalReceipt | None = None

    @property
    def completed_count(self) -> int:
        return len(self.receipts)

    def __call__(
        self,
        document: HistoryPlanDocument,
        output_index: int,
        output: HistoryPlannedCommit,
        parent_tree: str,
        *,
        env: dict[str, str] | None,
    ) -> str:
        if document is not self.document or env is None:
            _invalid(_("resolution replay used the wrong document or environment"))
        if self.provisional_receipt is not None:
            raise _AcceptanceReady
        output_key = _output_key(
            self.binding,
            output_index,
            output,
            parent_tree,
        )
        output_path = self.outputs_path / output_key
        output_names = set(list_resolution_directory(self.outputs_path))
        if output_key not in output_names:
            if not self.export_missing:
                _invalid(
                    _("output {output} resolution is missing").format(
                        output=output_index + 1
                    )
                )
            staging_name = f".staging-{output_key}"
            allowed_entries = set(self.output_keys) | {staging_name}
            unknown_entries = output_names - allowed_entries
            if unknown_entries:
                _invalid(
                    _("workspace outputs contain unexpected entries: {entries}").format(
                        entries=", ".join(
                            display_path(name) for name in sorted(unknown_entries)
                        )
                    )
                )
            authorized_paths = _export_resolution(
                document,
                self.binding,
                output_index,
                output,
                parent_tree,
                output_key,
                output_path,
                env=env,
            )
            self.output_keys.append(output_key)
            raise _ResolutionNeeded(
                _PendingResolution(output_index, output_key, authorized_paths)
            )

        request, authorized_paths = _read_request(
            document,
            self.binding,
            output_index,
            output,
            parent_tree,
            output_key,
            output_path,
            env=env,
        )
        if (
            not self.read_only
            and "receipt.json" not in set(list_resolution_directory(output_path))
        ):
            recover_interrupted_resolution_artifact_write(output_path / "receipt.json")
        output_entries = set(list_resolution_directory(output_path))
        base_entries = {"references", "request.json", "result.json", "results"}
        if output_entries not in (base_entries, base_entries | {"receipt.json"}):
            _require_directory_entries(
                output_path,
                base_entries,
                f"output {output_index + 1}",
            )
        paths, result_sha256 = _parse_result(
            output_path / "result.json",
            request,
            output_index,
        )
        policies = _path_policies(request)
        receipt_exists = "receipt.json" in output_entries
        if not receipt_exists:
            if not self.accept_one or self.accepted:
                self.output_keys.append(output_key)
                raise _ResolutionNeeded(
                    _PendingResolution(output_index, output_key, authorized_paths)
                )
            allowed_entries = set(self.output_keys) | {output_key}
            unknown_entries = output_names - allowed_entries
            if unknown_entries:
                _invalid(
                    _("workspace outputs contain unexpected entries: {entries}").format(
                        entries=", ".join(
                            display_path(name) for name in sorted(unknown_entries)
                        )
                    )
                )
            output_tree, receipt_paths = _apply_result(
                parent_tree,
                paths,
                policies,
                output_path / "results",
                quarantine=self.quarantine,
            )
            receipt = _receipt_record(
                self.binding,
                output_index,
                output_key,
                parent_tree,
                output_tree,
                result_sha256,
                receipt_paths,
            )
            receipt_sha256 = history_json_sha256(receipt)
            receipt_digest = ResolutionArtifactDigest(
                size=0,
                sha256=receipt_sha256,
            )
            self.provisional_receipt = _ProvisionalReceipt(
                output_path=output_path,
                record=receipt,
                replay=_ReceiptReplay(
                    output_index=output_index,
                    output_key=output_key,
                    parent_tree=parent_tree,
                    output_tree=output_tree,
                    receipt_sha256=receipt_sha256,
                ),
            )
            self.accepted = True
        else:
            receipt, receipt_sha256 = _load_json(
                output_path / "receipt.json",
                f"output {output_index + 1} receipt.json",
            )
            if receipt.get("result_sha256") != result_sha256:
                _invalid(
                    _("output {output} result.json changed after acceptance").format(
                        output=output_index + 1
                    )
                )
            expected_digests = _receipt_expected_digests(
                receipt,
                paths,
                output_index,
            )
            output_tree, receipt_paths = _apply_result(
                parent_tree,
                paths,
                policies,
                output_path / "results",
                quarantine=self.quarantine,
                expected_digests=expected_digests,
            )
            expected_receipt = _receipt_record(
                self.binding,
                output_index,
                output_key,
                parent_tree,
                output_tree,
                result_sha256,
                receipt_paths,
            )
            if receipt != expected_receipt or receipt_sha256 != history_json_sha256(
                expected_receipt
            ):
                _invalid(
                    _("output {output} receipt.json is not authentic").format(
                        output=output_index + 1
                    )
                )
            receipt_digest = ResolutionArtifactDigest(
                size=0,
                sha256=receipt_sha256,
            )
        expected_output_entries = set(base_entries)
        if receipt_exists:
            expected_output_entries.add("receipt.json")
        _require_directory_entries(
            output_path,
            expected_output_entries,
            f"output {output_index + 1}",
        )
        self.output_keys.append(output_key)
        self.receipts.append(
            _ReceiptReplay(
                output_index=output_index,
                output_key=output_key,
                parent_tree=parent_tree,
                output_tree=output_tree,
                receipt_sha256=receipt_digest.sha256,
            )
        )
        return output_tree

    def publish_provisional_receipt(self) -> None:
        if self.read_only:
            raise AssertionError("read-only materialization cannot publish a receipt")
        provisional = self.provisional_receipt
        if provisional is None:
            raise AssertionError("no provisional resolution receipt to publish")
        output_index = provisional.replay.output_index
        _require_directory_entries(
            provisional.output_path,
            {"references", "request.json", "result.json", "results"},
            f"output {output_index + 1}",
        )
        receipt_digest = _write_json(
            provisional.output_path / "receipt.json",
            provisional.record,
        )
        if receipt_digest.sha256 != provisional.replay.receipt_sha256:
            raise AssertionError("published resolution receipt digest changed")
        _require_directory_entries(
            provisional.output_path,
            {
                "receipt.json",
                "references",
                "request.json",
                "result.json",
                "results",
            },
            f"output {output_index + 1}",
        )


def _complete_record(
    binding: _WorkspaceBinding,
    materializer: _WorkspaceMaterializer,
    replay: HistoryReplayResult,
) -> dict[str, object]:
    return {
        "schema_version": CURRENT_HISTORY_RESOLUTION_WORKSPACE_SCHEMA_VERSION,
        "operation": _COMPLETE_OPERATION,
        "resolution_id": binding.resolution_id,
        "workspace_sha256": binding.exact_sha256,
        "plan_sha256": binding.record["plan_sha256"],
        "snapshot_sha256": binding.record["snapshot_sha256"],
        "semantic_plan_sha256": binding.record["semantic_plan_sha256"],
        "resolved_outputs": [
            {
                "output_index": receipt.output_index,
                "output_key": receipt.output_key,
                "parent_tree": receipt.parent_tree,
                "output_tree": receipt.output_tree,
                "receipt_sha256": receipt.receipt_sha256,
            }
            for receipt in materializer.receipts
        ],
        "output_trees": list(replay.output_trees),
        "final_tree": replay.final_tree,
    }


def _materialize_workspace(
    document: HistoryPlanDocument,
    binding: _WorkspaceBinding,
    workspace_path: Path,
    quarantine: GitObjectQuarantine,
    *,
    accept_one: bool,
    export_missing: bool,
    require_complete: bool,
    read_only: bool,
) -> tuple[
    HistoryReplayResult | None,
    _WorkspaceMaterializer,
    _PendingResolution | None,
    bool,
]:
    materializer = _WorkspaceMaterializer(
        document,
        binding,
        workspace_path,
        quarantine,
        accept_one=accept_one,
        export_missing=export_missing,
        read_only=read_only,
    )
    try:
        replay = materialize_history_output_trees(
            document,
            env=quarantine.environment(),
            resolved_output_materializer=materializer,
        )
    except _ResolutionNeeded as needed:
        if require_complete:
            _invalid(
                _("output {output} has not been accepted").format(
                    output=needed.pending.output_index + 1
                )
            )
        _require_directory_entries(
            workspace_path / "outputs",
            set(materializer.output_keys),
            "workspace outputs",
        )
        return None, materializer, needed.pending, False
    except _AcceptanceReady:
        if materializer.provisional_receipt is None:
            raise AssertionError("acceptance boundary has no provisional receipt")
        _require_directory_entries(
            workspace_path / "outputs",
            set(materializer.output_keys),
            "workspace outputs",
        )
        return None, materializer, None, True
    _require_directory_entries(
        workspace_path / "outputs",
        set(materializer.output_keys),
        "workspace outputs",
    )
    return replay, materializer, None, False


def _finish_workspace(
    workspace_path: Path,
    binding: _WorkspaceBinding,
    materializer: _WorkspaceMaterializer,
    replay: HistoryReplayResult,
) -> None:
    complete = _complete_record(binding, materializer, replay)
    root_entries = set(list_resolution_directory(workspace_path))
    if "complete.json" in root_entries:
        _read_expected_json(
            workspace_path / "complete.json",
            complete,
            "complete.json",
        )
    else:
        _write_json(workspace_path / "complete.json", complete)
    _require_directory_entries(
        workspace_path,
        {".workspace.lock", "complete.json", "outputs", "workspace.json"},
        "workspace root",
    )


def materialize_completed_history_resolution(
    document: HistoryPlanDocument,
    raw_plan_sha256: str,
    workspace_path: str,
    *,
    quarantine: GitObjectQuarantine,
) -> HistoryAuthenticatedResolution:
    """Rebuild and authenticate one COMPLETE workspace in a fresh quarantine."""
    if not isinstance(quarantine, GitObjectQuarantine):
        raise ValueError("a Git object quarantine environment is required")
    binding = _workspace_binding(document, raw_plan_sha256)
    if not binding.resolved_output_indexes:
        _invalid(_("plan does not contain any RESOLVED outputs"))
    absolute_path = _absolute_workspace_path(workspace_path)
    require_private_resolution_directory(absolute_path)
    with lock_resolution_directory(absolute_path, create=False):
        _validate_workspace_root(absolute_path, binding, allow_complete=True)
        _require_directory_entries(
            absolute_path,
            {".workspace.lock", "complete.json", "outputs", "workspace.json"},
            "workspace root",
        )
        replay, materializer, pending, acceptance_ready = _materialize_workspace(
            document,
            binding,
            absolute_path,
            quarantine,
            accept_one=False,
            export_missing=False,
            require_complete=True,
            read_only=True,
        )
        if replay is None or pending is not None or acceptance_ready:
            raise AssertionError("complete materialization returned pending output")
        complete_sha256 = _read_expected_json(
            absolute_path / "complete.json",
            _complete_record(binding, materializer, replay),
            "complete.json",
        )
        return HistoryAuthenticatedResolution(
            raw_plan_sha256=raw_plan_sha256,
            complete_sha256=complete_sha256,
            workspace_path=str(absolute_path),
            replay=replay,
        )


def resolve_history_plan(
    plan_path: str,
    workspace_path: str,
    *,
    accept_result: bool = False,
) -> HistoryResolutionWorkspaceResult:
    """Create, advance, or authenticate one explicit resolution workspace."""
    absolute_plan_path = os.path.abspath(plan_path)
    document, plan_sha256 = read_and_validate_history_plan_semantics(absolute_plan_path)
    binding = _workspace_binding(document, plan_sha256)
    if not binding.resolved_output_indexes:
        _invalid(_("plan does not contain any RESOLVED outputs"))
    absolute_path = _absolute_workspace_path(workspace_path)
    exists = _workspace_exists(absolute_path)
    if accept_result and not exists:
        _invalid(_("--accept requires an existing workspace"))
    if not exists:
        _initialize_workspace(absolute_path, binding)
    require_private_resolution_directory(absolute_path)
    with lock_resolution_directory(absolute_path, create=False):
        _recover_mutable_workspace_root(absolute_path, binding)
        _validate_workspace_root(absolute_path, binding, allow_complete=True)
        workspace_complete = "complete.json" in set(
            list_resolution_directory(absolute_path)
        )
        with temporary_git_object_environment() as quarantine:
            replay, materializer, pending, acceptance_ready = _materialize_workspace(
                document,
                binding,
                absolute_path,
                quarantine,
                accept_one=accept_result and not workspace_complete,
                export_missing=not workspace_complete,
                require_complete=workspace_complete,
                read_only=False,
            )
            if acceptance_ready or materializer.provisional_receipt is not None:
                if workspace_complete:
                    raise AssertionError("complete workspace produced a new receipt")
                materializer.publish_provisional_receipt()
                replay, materializer, pending, acceptance_ready = (
                    _materialize_workspace(
                        document,
                        binding,
                        absolute_path,
                        quarantine,
                        accept_one=False,
                        export_missing=True,
                        require_complete=False,
                        read_only=False,
                    )
                )
                if acceptance_ready or materializer.provisional_receipt is not None:
                    raise AssertionError(
                        "persisted receipt replay requested acceptance"
                    )
            if pending is not None:
                _validate_workspace_root(
                    absolute_path,
                    binding,
                    allow_complete=False,
                )
                pending_output_path = absolute_path / "outputs" / pending.output_key
                return HistoryResolutionWorkspaceResult(
                    status="NEEDS_RESOLUTION",
                    plan_path=absolute_plan_path,
                    workspace_path=str(absolute_path),
                    completed_resolved_outputs=materializer.completed_count,
                    total_resolved_outputs=len(binding.resolved_output_indexes),
                    output_index=pending.output_index,
                    output_key=pending.output_key,
                    authorized_paths=pending.authorized_paths,
                    request_path=str(pending_output_path / "request.json"),
                    result_path=str(pending_output_path / "result.json"),
                    results_path=str(pending_output_path / "results"),
                )
            if replay is None:
                raise AssertionError("resolution completed without replay result")
            _finish_workspace(absolute_path, binding, materializer, replay)
            return HistoryResolutionWorkspaceResult(
                status="COMPLETE",
                plan_path=absolute_plan_path,
                workspace_path=str(absolute_path),
                completed_resolved_outputs=materializer.completed_count,
                total_resolved_outputs=len(binding.resolved_output_indexes),
                output_index=None,
                output_key=None,
                authorized_paths=(),
                request_path=None,
                result_path=None,
                results_path=None,
            )
