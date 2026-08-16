"""Identity-bound provenance for changes restored with ``apply --from``.

Named batch ownership normally hides an equivalent working-tree change from
live review.  An explicit apply is different: the user intentionally restored
that ownership to the working tree so it can be reviewed and staged.  This
module records that intent outside canonical batch metadata and accepts it only
while the batch revision, HEAD, and exact worktree bytes still match.  The index
must also match outside a session; a session may stage a path that was fresh at
its start, after which ``stop`` binds the record to the final index identity.
"""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass
from itertools import chain
import json
import os
from pathlib import Path
from typing import TypedDict, cast

from ..batch.state.metadata_schema import metadata_from_application_dict
from ..batch.line_matching.match import match_lines
from ..batch.line_matching.match_workspace import MatcherWorkspace
from ..batch.line_matching.occurrence_index import LinePayloadOccurrenceIndex
from ..core.buffer import LineBuffer
from ..core.line_selection import LineRanges
from ..core.text_lines import normalize_line_sequence_endings
from ..batch.state.metadata_types import (
    BatchFileMetadataDict,
    BatchMetadataDict,
)
from ..batch.ownership.attribution_metadata import (
    compact_ownership_metadata_for_attribution,
)
from ..batch.ownership.metadata_types import BatchOwnershipMetadata
from ..batch.state.query import list_batch_names, read_batch_metadata_for_batches
from ..batch.state.batch_names import validate_batch_name_constraints
from ..exceptions import BatchMetadataError, CommandError
from ..git_paths import display_path
from ..i18n import _
from ..utils.file_io import (
    read_file_paths_file,
    read_required_text_file_contents,
    write_file_paths_file,
    write_text_file_contents,
)
from ..utils.paths import (
    get_abort_applied_batch_overlay_fresh_index_file_path,
    get_abort_applied_batch_overlays_absent_file_path,
    get_abort_applied_batch_overlays_file_path,
    get_applied_batch_overlays_file_path,
    get_session_applied_batch_overlay_paths_file_path,
)
from ..utils.session_start_point import current_head_commit
from ..utils.git_object_io import list_git_tree_blobs
from ..utils.repository_buffers import load_git_blob_as_buffer
from ..batch.state.reference_names import format_batch_state_ref_name
from .session_marker import session_is_active
from .file_target_identity import (
    IndexIdentity,
    WorktreeIdentity,
    capture_worktree_identity,
    read_index_identities,
)


_SCHEMA_VERSION = 1
_OWNER_PREFIX = ":applied-batch-overlay:"


class _AppliedApplication(TypedDict, total=False):
    batch: str
    revision: str
    file_metadata: BatchFileMetadataDict
    source_object_id: str
    introduced_selected_presence: bool
    index_target_is_original: bool
    index_preimage_source_lines: list[str]


class _AppliedFileEntry(TypedDict):
    head: str | None
    index: dict[str, str | None]
    worktree: dict[str, object]
    applications: list[_AppliedApplication]


class _AppliedOverlayState(TypedDict):
    schema_version: int
    files: dict[str, _AppliedFileEntry]


@dataclass(frozen=True, slots=True)
class AppliedFileProvenance:
    """Compact selected ownership and its exact source blob."""

    file_metadata: BatchFileMetadataDict
    source_object_id: str | None
    introduced_selected_presence: bool = False
    index_preimage_source_ranges: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True, slots=True)
class AppliedBatchOverlayView:
    """Fresh supplemental ownership used by one attribution pass."""

    metadata_by_owner: dict[str, BatchMetadataDict]
    source_object_by_owner: dict[str, str]
    revealed_owner_names: frozenset[str]
    batch_names: frozenset[str]
    lifecycle_change_types: frozenset[str]
    applied_source_line_ranges_by_batch: dict[
        str,
        tuple[tuple[int, int], ...],
    ]
    source_line_ranges_by_batch: dict[str, tuple[tuple[int, int], ...]]
    index_preimage_source_line_ranges_by_batch: dict[
        str,
        tuple[tuple[int, int], ...],
    ]

    @classmethod
    def empty(cls) -> AppliedBatchOverlayView:
        """Return an empty immutable view."""
        return cls({}, {}, frozenset(), frozenset(), frozenset(), {}, {}, {})

    def contains_equivalent_file_provenance(
        self,
        file_path: str,
        file_metadata: BatchFileMetadataDict,
        source_object_id: str | None,
    ) -> bool:
        """Return whether exact fresh state proves this ownership is applied.

        Content matching cannot distinguish a selected duplicate from live
        unowned context.  This predicate is intentionally stronger: the view
        is already bound to exact HEAD, index, worktree, and batch revisions,
        and the application must also name the same source blob and compact
        selected ownership.
        """
        if source_object_id is None:
            return False
        compact_metadata = _compact_applied_file_metadata(file_metadata)
        return any(
            self.source_object_by_owner.get(owner_name) == source_object_id
            and owner_metadata.get("files", {}).get(file_path) == compact_metadata
            for owner_name, owner_metadata in self.metadata_by_owner.items()
        )


@dataclass(frozen=True, slots=True)
class AppliedBatchOverlayAbortSnapshot:
    """Validated pre-session state to restore during abort."""

    captured: bool
    contents: str | None


@dataclass(frozen=True, slots=True)
class AppliedBatchOverlaySnapshot:
    """One state, HEAD, and bulk-index boundary reused across a live scan."""

    state: _AppliedOverlayState
    head_commit: str | None
    index_identities: dict[str, IndexIdentity]
    session_index_drift_paths: frozenset[str]


def applied_batch_overlays_repository_path() -> str:
    """Return the Git-directory-relative path tracked by undo checkpoints."""
    return "git-stage-batch/applied-batch-overlays.json"


def is_applied_batch_overlay_owner(owner_name: str) -> bool:
    """Return whether an attribution owner is an internal applied overlay."""
    return owner_name.startswith(_OWNER_PREFIX)


def build_applied_file_provenance(
    batch_name: str,
    file_path: str,
    file_metadata: BatchFileMetadataDict,
    selection_ids: set[int] | None,
    *,
    selected_file_metadata: BatchFileMetadataDict | None = None,
    before_lines: LineBuffer | None = None,
    after_lines: LineBuffer | None = None,
) -> AppliedFileProvenance:
    """Freeze the exact text ownership selected by one successful apply."""
    source_commit = file_metadata.get("batch_source_commit")
    source_object_id = None
    source_path = file_metadata.get("source_path")
    if source_path:
        state_entry = list_git_tree_blobs(
            format_batch_state_ref_name(batch_name),
            (source_path,),
        ).get(source_path)
        if state_entry is not None:
            source_object_id = state_entry.blob_sha
    if source_object_id is None and source_commit is not None:
        source_entry = list_git_tree_blobs(source_commit, (file_path,)).get(file_path)
        if source_entry is not None:
            source_object_id = source_entry.blob_sha

    has_prepared_metadata = selected_file_metadata is not None
    selected_file_metadata = _compact_applied_file_metadata(
        selected_file_metadata or file_metadata
    )
    if selection_ids is not None and not has_prepared_metadata:
        raise ValueError("partial applied provenance was not prepared by its file job")

    return AppliedFileProvenance(
        file_metadata=selected_file_metadata,
        source_object_id=source_object_id,
        introduced_selected_presence=_all_selected_presence_introduced(
            selected_file_metadata,
            source_object_id,
            before_lines,
            after_lines,
        ),
    )


def build_applied_file_provenances(
    batch_name: str,
    file_metadata_by_path: dict[str, BatchFileMetadataDict],
    selection_ids: set[int] | None,
    *,
    selected_file_metadata_by_path: dict[str, BatchFileMetadataDict],
    introduced_selected_presence_by_path: dict[str, bool] | None = None,
    index_preimage_source_ranges_by_path: dict[
        str,
        tuple[tuple[int, int], ...],
    ]
    | None = None,
) -> dict[str, AppliedFileProvenance]:
    """Freeze applied ownership while resolving each source tree only once."""
    source_paths = {
        file_path: source_path
        for file_path, file_metadata in file_metadata_by_path.items()
        if (source_path := file_metadata.get("source_path")) is not None
    }
    state_entries = list_git_tree_blobs(
        format_batch_state_ref_name(batch_name),
        source_paths.values(),
    )
    source_object_by_path = {
        file_path: state_entries[source_path].blob_sha
        for file_path, source_path in source_paths.items()
        if source_path in state_entries
    }

    fallback_paths_by_commit: dict[str, list[str]] = {}
    for file_path, file_metadata in file_metadata_by_path.items():
        if file_path in source_object_by_path:
            continue
        source_commit = file_metadata.get("batch_source_commit")
        if source_commit is not None:
            fallback_paths_by_commit.setdefault(source_commit, []).append(file_path)
    for source_commit, file_paths in fallback_paths_by_commit.items():
        entries = list_git_tree_blobs(source_commit, file_paths)
        source_object_by_path.update(
            (file_path, entry.blob_sha) for file_path, entry in entries.items()
        )

    provenances = {}
    for file_path, file_metadata in file_metadata_by_path.items():
        prepared_metadata = selected_file_metadata_by_path.get(file_path)
        if selection_ids is not None and prepared_metadata is None:
            raise ValueError(
                "partial applied provenance was not prepared by its file job"
            )
        selected_metadata = _compact_applied_file_metadata(
            prepared_metadata or file_metadata
        )
        provenances[file_path] = AppliedFileProvenance(
            file_metadata=selected_metadata,
            source_object_id=source_object_by_path.get(file_path),
            introduced_selected_presence=(
                False
                if introduced_selected_presence_by_path is None
                else introduced_selected_presence_by_path.get(
                    file_path,
                    False,
                )
            ),
            index_preimage_source_ranges=(
                ()
                if index_preimage_source_ranges_by_path is None
                else index_preimage_source_ranges_by_path.get(
                    file_path,
                    (),
                )
            ),
        )
    return provenances


def fresh_applied_batch_overlay_for_path(
    file_path: str,
    *,
    batch_metadata_by_name: dict[str, BatchMetadataDict] | None = None,
    snapshot: AppliedBatchOverlaySnapshot | None = None,
    worktree_identity: WorktreeIdentity | None = None,
) -> AppliedBatchOverlayView:
    """Return applied ownership whose complete repository identity is fresh."""
    snapshot = snapshot or load_applied_batch_overlay_snapshot()
    raw_entry = snapshot.state["files"].get(file_path)
    if raw_entry is None:
        return AppliedBatchOverlayView.empty()

    if batch_metadata_by_name is None:
        names = list_batch_names()
        batch_metadata_by_name = read_batch_metadata_for_batches(names)

    if not _entry_identity_matches(
        raw_entry,
        head=snapshot.head_commit,
        index_identity=snapshot.index_identities[file_path],
        allow_index_drift=file_path in snapshot.session_index_drift_paths,
        worktree=(
            capture_worktree_identity(file_path)
            if worktree_identity is None
            else worktree_identity
        ),
    ):
        return AppliedBatchOverlayView.empty()

    snapshot_index_identity = snapshot.index_identities[file_path]
    index_identity_is_exact = (
        not snapshot_index_identity.unmerged_entries
        and raw_entry["index"] == _index_identity_dict(snapshot_index_identity)
    )

    metadata_by_owner: dict[str, BatchMetadataDict] = {}
    source_object_by_owner: dict[str, str] = {}
    batch_names: set[str] = set()
    lifecycle_change_types: set[str] = set()
    applied_ranges_by_batch: dict[str, list[tuple[int, int]]] = {}
    source_line_ranges_by_batch: dict[str, list[tuple[int, int]]] = {}
    index_preimage_ranges_by_batch: dict[str, list[tuple[int, int]]] = {}
    for ordinal, application in enumerate(raw_entry["applications"]):
        batch_name = application["batch"]
        current_metadata = batch_metadata_by_name.get(batch_name)
        if (
            current_metadata is None
            or current_metadata.get("revision") != application["revision"]
        ):
            continue

        owner_name = f"{_OWNER_PREFIX}{ordinal}"
        file_metadata = deepcopy(application["file_metadata"])
        metadata_by_owner[owner_name] = {
            "files": {file_path: file_metadata},
        }
        source_object_id = application.get("source_object_id")
        if isinstance(source_object_id, str):
            source_object_by_owner[owner_name] = source_object_id
        change_type = file_metadata.get("change_type")
        if isinstance(change_type, str):
            lifecycle_change_types.add(change_type)
        selected_ranges = LineRanges.from_specs(
            source_line
            for claim in file_metadata.get("presence_claims", [])
            for source_line in claim.get("source_lines", [])
        ).ranges()
        if (
            index_identity_is_exact
            and application.get("index_target_is_original") is True
        ):
            applied_ranges_by_batch.setdefault(batch_name, []).extend(selected_ranges)
        if application.get("introduced_selected_presence") is True:
            batch_ranges = source_line_ranges_by_batch.setdefault(
                batch_name,
                [],
            )
            batch_ranges.extend(selected_ranges)
        preimage_specs = application.get(
            "index_preimage_source_lines",
            [],
        )
        if (
            preimage_specs
            and index_identity_is_exact
            and application.get("index_target_is_original") is True
        ):
            preimage_ranges = LineRanges.from_specs(preimage_specs).ranges()
            source_line_ranges_by_batch.setdefault(
                batch_name,
                [],
            ).extend(preimage_ranges)
            index_preimage_ranges_by_batch.setdefault(
                batch_name,
                [],
            ).extend(preimage_ranges)
        batch_names.add(batch_name)

    if not metadata_by_owner:
        return AppliedBatchOverlayView.empty()
    return AppliedBatchOverlayView(
        metadata_by_owner=metadata_by_owner,
        source_object_by_owner=source_object_by_owner,
        revealed_owner_names=frozenset(metadata_by_owner),
        batch_names=frozenset(batch_names),
        lifecycle_change_types=frozenset(lifecycle_change_types),
        applied_source_line_ranges_by_batch={
            batch_name: LineRanges.from_ranges(ranges).ranges()
            for batch_name, ranges in applied_ranges_by_batch.items()
        },
        source_line_ranges_by_batch={
            batch_name: LineRanges.from_ranges(ranges).ranges()
            for batch_name, ranges in source_line_ranges_by_batch.items()
        },
        index_preimage_source_line_ranges_by_batch={
            batch_name: LineRanges.from_ranges(ranges).ranges()
            for batch_name, ranges in index_preimage_ranges_by_batch.items()
        },
    )


def load_applied_batch_overlay_snapshot() -> AppliedBatchOverlaySnapshot:
    """Capture durable overlay state and its shared repository identity once."""
    state = _load_state()
    file_paths = state["files"]
    return AppliedBatchOverlaySnapshot(
        state=state,
        head_commit=current_head_commit(),
        index_identities=read_index_identities(file_paths),
        session_index_drift_paths=_session_index_drift_paths(state),
    )


def record_applied_batch_overlays(
    *,
    batch_name: str,
    batch_revision: str,
    files: dict[str, AppliedFileProvenance],
    before_worktree_identities: dict[str, WorktreeIdentity],
    expected_index_identities: dict[str, IndexIdentity] | None = None,
) -> None:
    """Record successfully applied text ownership against the final tree state."""
    if not files:
        return
    if not batch_revision:
        raise ValueError("applied-batch provenance requires a batch revision")

    state = _load_state()
    head = current_head_commit()
    index_identities = read_index_identities(files)
    session_index_drift_paths = _session_index_drift_paths(state)
    final_worktree_identities = {
        file_path: capture_worktree_identity(file_path) for file_path in files
    }
    previous_batch_names = {
        application["batch"]
        for file_path in files
        if (entry := state["files"].get(file_path)) is not None
        for application in entry["applications"]
    }
    current_metadata_by_name = read_batch_metadata_for_batches(
        sorted(previous_batch_names)
    )

    recorded_paths: list[str] = []
    for file_path, provenance in files.items():
        if index_identities[file_path].unmerged_entries:
            # Conflict stages can never authorize applied ownership. Forget an
            # older record too, so resolving the path to absence cannot revive
            # authority captured before the conflict.
            state["files"].pop(file_path, None)
            continue
        previous_entry = state["files"].get(file_path)
        applications: list[_AppliedApplication] = []
        before_identity = before_worktree_identities.get(file_path)
        if (
            previous_entry is not None
            and before_identity is not None
            and _entry_identity_matches(
                previous_entry,
                head=head,
                index_identity=index_identities[file_path],
                allow_index_drift=file_path in session_index_drift_paths,
                worktree=before_identity,
            )
        ):
            previous_index_is_exact = previous_entry["index"] == (
                _index_identity_dict(
                    index_identities[file_path],
                )
            )
            for previous_application in previous_entry["applications"]:
                if (
                    current_metadata_by_name.get(
                        previous_application["batch"],
                        {},
                    ).get("revision")
                    != previous_application["revision"]
                ):
                    continue
                carried_application = deepcopy(previous_application)
                if not previous_index_is_exact:
                    carried_application.pop(
                        "index_target_is_original",
                        None,
                    )
                    carried_application.pop(
                        "index_preimage_source_lines",
                        None,
                    )
                applications.append(carried_application)

        application: _AppliedApplication = {
            "batch": batch_name,
            "revision": batch_revision,
            "file_metadata": _compact_applied_file_metadata(provenance.file_metadata),
        }
        if provenance.source_object_id is not None:
            application["source_object_id"] = provenance.source_object_id
        if provenance.introduced_selected_presence:
            application["introduced_selected_presence"] = True
        index_preimage_lines = LineRanges.from_ranges(
            provenance.index_preimage_source_ranges
        ).to_range_strings()
        expected_index_identity = (
            None
            if expected_index_identities is None
            else expected_index_identities.get(file_path)
        )
        current_index_matches_apply_target = expected_index_identities is None or (
            expected_index_identity is not None
            and not index_identities[file_path].unmerged_entries
            and index_identities[file_path] == expected_index_identity
        )
        if current_index_matches_apply_target:
            application["index_target_is_original"] = True
        if index_preimage_lines and current_index_matches_apply_target:
            application["index_preimage_source_lines"] = index_preimage_lines
        for carried_application in applications:
            if _same_applied_ownership(
                carried_application,
                application,
            ):
                _merge_application_authority(
                    carried_application,
                    application,
                )
                break
        else:
            applications.append(application)

        state["files"][file_path] = {
            "head": head,
            "index": _index_identity_dict(index_identities[file_path]),
            "worktree": asdict(final_worktree_identities[file_path]),
            "applications": applications,
        }
        recorded_paths.append(file_path)

    _write_state(state)
    if session_is_active() and recorded_paths:
        session_paths_file = get_session_applied_batch_overlay_paths_file_path()
        write_file_paths_file(
            session_paths_file,
            [*read_file_paths_file(session_paths_file), *recorded_paths],
        )


def snapshot_applied_batch_overlays_for_abort() -> None:
    """Snapshot durable overlay state before a new session can mutate it."""
    source_path = get_applied_batch_overlays_file_path()
    snapshot_path = get_abort_applied_batch_overlays_file_path()
    absent_path = get_abort_applied_batch_overlays_absent_file_path()
    fresh_index_path = get_abort_applied_batch_overlay_fresh_index_file_path()
    if source_path.exists():
        contents = read_required_text_file_contents(source_path)
        state = _decode_and_validate(contents)
        file_paths = state["files"]
        index_identities = read_index_identities(file_paths)
        fresh_index_paths = sorted(
            file_path
            for file_path, entry in file_paths.items()
            if not index_identities[file_path].unmerged_entries
            and entry["index"] == _index_identity_dict(index_identities[file_path])
        )
        write_text_file_contents(snapshot_path, contents)
        _write_fresh_index_paths(fresh_index_path, fresh_index_paths)
        if absent_path.exists():
            absent_path.unlink()
        return

    write_text_file_contents(absent_path, "")
    _write_fresh_index_paths(fresh_index_path, [])
    if snapshot_path.exists():
        snapshot_path.unlink()


def rebind_applied_batch_overlays_after_session() -> None:
    """Bind still-valid session overlays to the session's final index."""
    if not session_is_active():
        return
    state = _load_state()
    trusted_paths = _session_index_drift_paths(state)
    if not trusted_paths:
        return

    head = current_head_commit()
    index_identities = read_index_identities(trusted_paths)
    batch_names = {
        application["batch"]
        for file_path in trusted_paths
        if (entry := state["files"].get(file_path)) is not None
        for application in entry["applications"]
    }
    metadata_by_name = read_batch_metadata_for_batches(sorted(batch_names))
    changed = False
    for file_path in trusted_paths:
        entry = state["files"].get(file_path)
        if entry is None or entry["head"] != head:
            continue
        if index_identities[file_path].unmerged_entries:
            del state["files"][file_path]
            changed = True
            continue
        if entry["worktree"] != asdict(capture_worktree_identity(file_path)):
            continue
        applications = [
            application
            for application in entry["applications"]
            if metadata_by_name.get(application["batch"], {}).get("revision")
            == application["revision"]
        ]
        if not applications:
            continue
        rebound_index = _index_identity_dict(index_identities[file_path])
        if entry["index"] != rebound_index:
            for application in applications:
                application.pop("index_target_is_original", None)
                application.pop("index_preimage_source_lines", None)
        if entry["index"] == rebound_index and entry["applications"] == applications:
            continue
        entry["index"] = rebound_index
        entry["applications"] = applications
        changed = True

    if changed:
        _write_state(state)


def load_applied_batch_overlay_abort_snapshot() -> AppliedBatchOverlayAbortSnapshot:
    """Load and validate the optional pre-session overlay snapshot."""
    snapshot_path = get_abort_applied_batch_overlays_file_path()
    absent_path = get_abort_applied_batch_overlays_absent_file_path()
    if snapshot_path.exists():
        contents = read_required_text_file_contents(snapshot_path)
        _decode_and_validate(contents)
        return AppliedBatchOverlayAbortSnapshot(True, contents)
    if absent_path.exists():
        return AppliedBatchOverlayAbortSnapshot(True, None)
    # Sessions created by older versions have neither marker.  Leaving durable
    # overlay state untouched preserves backward-compatible abort behavior.
    return AppliedBatchOverlayAbortSnapshot(False, None)


def restore_applied_batch_overlay_abort_snapshot(
    snapshot: AppliedBatchOverlayAbortSnapshot,
) -> None:
    """Restore the pre-session durable overlay after repository recovery."""
    if not snapshot.captured:
        return
    target_path = get_applied_batch_overlays_file_path()
    if snapshot.contents is None:
        try:
            target_path.unlink()
        except FileNotFoundError:
            pass
        return
    write_text_file_contents(target_path, snapshot.contents)


def _load_state() -> _AppliedOverlayState:
    path = get_applied_batch_overlays_file_path()
    if not path.exists():
        return {"schema_version": _SCHEMA_VERSION, "files": {}}
    contents = read_required_text_file_contents(path)
    return _decode_and_validate(contents)


def _decode_and_validate(contents: str) -> _AppliedOverlayState:
    path = get_applied_batch_overlays_file_path()
    try:
        value = json.loads(contents)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise _state_error(path) from error
    if (
        type(value) is not dict
        or value.get("schema_version") != _SCHEMA_VERSION
        or set(value) != {"schema_version", "files"}
        or type(value.get("files")) is not dict
    ):
        raise _state_error(path)

    files = value["files"]
    assert isinstance(files, dict)
    for file_path, entry in files.items():
        if not _valid_repository_path(file_path) or type(entry) is not dict:
            raise _state_error(path)
        if set(entry) != {"head", "index", "worktree", "applications"}:
            raise _state_error(path)
        if entry["head"] is not None and not _valid_object_id(entry["head"]):
            raise _state_error(path)
        if not _valid_index_identity(entry["index"]):
            raise _state_error(path)
        if not _valid_worktree_identity(entry["worktree"]):
            raise _state_error(path)
        applications = entry["applications"]
        if type(applications) is not list or not applications:
            raise _state_error(path)
        for application in applications:
            _validate_application(file_path, application, path)
    return cast(_AppliedOverlayState, value)


def _validate_application(
    file_path: str,
    application: object,
    state_path: Path,
) -> None:
    if type(application) is not dict:
        raise _state_error(state_path)
    if not {"batch", "revision", "file_metadata"} <= set(application):
        raise _state_error(state_path)
    if set(application) - {
        "batch",
        "revision",
        "file_metadata",
        "source_object_id",
        "introduced_selected_presence",
        "index_target_is_original",
        "index_preimage_source_lines",
    }:
        raise _state_error(state_path)
    batch_name = application["batch"]
    revision = application["revision"]
    file_metadata = application["file_metadata"]
    source_object_id = application.get("source_object_id")
    introduced_selected_presence = application.get("introduced_selected_presence")
    index_target_is_original = application.get("index_target_is_original")
    index_preimage_source_lines = application.get("index_preimage_source_lines")
    if (
        type(batch_name) is not str
        or not batch_name
        or type(revision) is not str
        or not revision
        or type(file_metadata) is not dict
        or (source_object_id is not None and not _valid_object_id(source_object_id))
        or (
            introduced_selected_presence is not None
            and type(introduced_selected_presence) is not bool
        )
        or (
            index_target_is_original is not None
            and type(index_target_is_original) is not bool
        )
        or (
            index_preimage_source_lines is not None
            and (
                type(index_preimage_source_lines) is not list
                or any(
                    type(specification) is not str
                    for specification in index_preimage_source_lines
                )
            )
        )
    ):
        raise _state_error(state_path)
    if index_preimage_source_lines is not None:
        if index_target_is_original is False:
            raise _state_error(state_path)
        try:
            canonical_preimage_lines = LineRanges.from_specs(
                index_preimage_source_lines
            ).to_range_strings()
        except ValueError as error:
            raise _state_error(state_path) from error
        if canonical_preimage_lines != index_preimage_source_lines:
            raise _state_error(state_path)
    try:
        validate_batch_name_constraints(batch_name)
    except CommandError as error:
        raise _state_error(state_path) from error
    try:
        metadata_from_application_dict(
            "applied-overlay",
            {
                "revision": "applied-overlay-state",
                "baseline": None,
                "files": {
                    file_path: cast(BatchFileMetadataDict, file_metadata),
                },
            },
        )
    except BatchMetadataError as error:
        raise _state_error(state_path) from error
    if file_metadata != _compact_applied_file_metadata(
        cast(BatchFileMetadataDict, file_metadata)
    ):
        raise _state_error(state_path)
    if index_preimage_source_lines is not None:
        selected_presence_lines = LineRanges.from_specs(
            source_line
            for claim in file_metadata.get("presence_claims", [])
            for source_line in claim.get("source_lines", [])
        )
        index_preimage_lines = LineRanges.from_specs(index_preimage_source_lines)
        if index_preimage_lines.difference(selected_presence_lines):
            raise _state_error(state_path)
        if index_target_is_original is None:
            # Early versions of index-preimage provenance wrote the selected
            # ranges before they wrote the identity marker that makes those
            # ranges authoritative.  Keep accepting that historical shape,
            # but discard its unbound proof while decoding.  In particular,
            # this prevents a later equivalent application from attaching a
            # fresh identity marker to ranges whose original index was never
            # recorded.
            application.pop("index_preimage_source_lines")


def _compact_applied_file_metadata(
    file_metadata: BatchFileMetadataDict,
) -> BatchFileMetadataDict:
    """Project a text batch entry onto the fields used by attribution."""
    compact = cast(
        BatchFileMetadataDict,
        compact_ownership_metadata_for_attribution(
            cast(BatchOwnershipMetadata, file_metadata)
        ),
    )
    source_commit = file_metadata.get("batch_source_commit")
    if source_commit is not None:
        compact["batch_source_commit"] = source_commit
    change_type = file_metadata.get("change_type")
    if change_type is not None:
        compact["change_type"] = change_type
    mode = file_metadata.get("mode")
    if mode is not None:
        compact["mode"] = mode
    return compact


def _same_applied_ownership(
    left: _AppliedApplication,
    right: _AppliedApplication,
) -> bool:
    """Return whether two applications describe the same selected ownership."""
    return all(
        left.get(field) == right.get(field)
        for field in (
            "batch",
            "revision",
            "file_metadata",
            "source_object_id",
        )
    )


def _merge_application_authority(
    target: _AppliedApplication,
    source: _AppliedApplication,
) -> None:
    """Retain the union of still-valid proof for equivalent applications."""
    if source.get("introduced_selected_presence") is True:
        target["introduced_selected_presence"] = True
    if source.get("index_target_is_original") is True:
        target["index_target_is_original"] = True

    source_preimage = source.get("index_preimage_source_lines", [])
    if source_preimage:
        target["index_preimage_source_lines"] = LineRanges.from_specs(
            chain(
                target.get("index_preimage_source_lines", []),
                source_preimage,
            )
        ).to_range_strings()


def _all_selected_presence_introduced(
    file_metadata: BatchFileMetadataDict,
    source_object_id: str | None,
    before_lines: LineBuffer | None,
    after_lines: LineBuffer | None,
) -> bool:
    """Return whether every selected source line was introduced by this apply."""
    if source_object_id is None or before_lines is None or after_lines is None:
        return False
    with load_git_blob_as_buffer(source_object_id) as source_lines:
        return selected_presence_was_introduced(
            file_metadata,
            source_lines,
            before_lines,
            after_lines,
        )


def selected_presence_was_introduced(
    file_metadata: BatchFileMetadataDict,
    source_lines: Sequence[bytes],
    before_lines: Sequence[bytes],
    after_lines: Sequence[bytes],
) -> bool:
    """Return whether every selected source line was added to this target."""
    selected_lines = LineRanges.from_specs(
        source_line
        for claim in file_metadata.get("presence_claims", [])
        for source_line in claim.get("source_lines", [])
    )
    if not selected_lines:
        return False

    normalized_source = normalize_line_sequence_endings(source_lines)
    normalized_before = normalize_line_sequence_endings(before_lines)
    normalized_after = normalize_line_sequence_endings(after_lines)
    with (
        match_lines(normalized_before, normalized_after) as applied_mapping,
        MatcherWorkspace() as workspace,
    ):
        introduced_occurrences = LinePayloadOccurrenceIndex(
            workspace,
            normalized_after,
            normalize_payloads=False,
            target_indexes=(
                target_index
                for target_index in range(len(normalized_after))
                if applied_mapping.get_source_line_from_target_line(target_index + 1)
                is None
            ),
        )
        previous_source = 0
        previous_target = 0
        for start, end in selected_lines.ranges():
            for source_line in range(start, end + 1):
                if source_line > len(normalized_source):
                    return False
                source_content = normalized_source[source_line - 1]
                if introduced_occurrences.occurrence_count(source_content) != 1:
                    return False
                after_target = (
                    next(introduced_occurrences.matching_line_indexes(source_content))
                    + 1
                )
                if previous_target != 0:
                    if source_line == previous_source + 1:
                        if after_target != previous_target + 1:
                            return False
                    elif after_target <= previous_target:
                        return False
                previous_source = source_line
                previous_target = after_target
        return True


def _entry_identity_matches(
    entry: _AppliedFileEntry,
    *,
    head: str | None,
    index_identity: IndexIdentity,
    allow_index_drift: bool = False,
    worktree: WorktreeIdentity,
) -> bool:
    return (
        entry["head"] == head
        and not index_identity.unmerged_entries
        and (
            allow_index_drift or entry["index"] == _index_identity_dict(index_identity)
        )
        and entry["worktree"] == asdict(worktree)
    )


def _session_index_drift_paths(
    state: _AppliedOverlayState,
) -> frozenset[str]:
    """Return overlays whose index identity is trusted for this session."""
    if not session_is_active():
        return frozenset()
    written_in_session = frozenset(
        read_file_paths_file(get_session_applied_batch_overlay_paths_file_path())
    )
    fresh_index_path = get_abort_applied_batch_overlay_fresh_index_file_path()
    if not fresh_index_path.exists():
        # An older session has no trustworthy start boundary, but overlays
        # written by this version inside it have an exact apply-time identity.
        return frozenset(state["files"]).intersection(written_in_session)
    fresh_at_start = _load_fresh_index_paths(fresh_index_path)
    return frozenset(state["files"]).intersection(fresh_at_start | written_in_session)


def _write_fresh_index_paths(path: Path, file_paths: list[str]) -> None:
    write_text_file_contents(
        path,
        json.dumps(
            {"schema_version": _SCHEMA_VERSION, "paths": file_paths},
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


def _load_fresh_index_paths(path: Path) -> frozenset[str]:
    try:
        value = json.loads(read_required_text_file_contents(path))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise _state_error(path) from error
    if (
        type(value) is not dict
        or set(value) != {"schema_version", "paths"}
        or value.get("schema_version") != _SCHEMA_VERSION
        or type(value.get("paths")) is not list
        or any(not _valid_repository_path(item) for item in value["paths"])
        or len(set(value["paths"])) != len(value["paths"])
    ):
        raise _state_error(path)
    return frozenset(value["paths"])


def _write_state(state: _AppliedOverlayState) -> None:
    write_text_file_contents(
        get_applied_batch_overlays_file_path(),
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _index_identity_dict(
    identity: IndexIdentity,
) -> dict[str, str | None]:
    if identity.unmerged_entries:
        raise ValueError("unmerged index identity cannot authorize applied overlay")
    if not identity.exists or identity.intent_to_add:
        # ``start`` adds untracked paths with intent-to-add.  Treat that index
        # sentinel as equivalent to the absent entry seen by ``apply``.
        return {"mode": None, "object_id": None}
    return {"mode": identity.mode, "object_id": identity.object_id}


def _valid_index_identity(value: object) -> bool:
    if type(value) is not dict or set(value) != {"mode", "object_id"}:
        return False
    mode = value["mode"]
    object_id = value["object_id"]
    return (
        (mode is None or type(mode) is str)
        and (object_id is None or _valid_object_id(object_id))
        and ((mode is None) == (object_id is None))
    )


def _valid_worktree_identity(value: object) -> bool:
    if type(value) is not dict or set(value) != {
        "exists",
        "kind",
        "mode",
        "size",
        "digest",
    }:
        return False
    return (
        type(value["exists"]) is bool
        and type(value["kind"]) is str
        and (value["mode"] is None or type(value["mode"]) is int)
        and (value["size"] is None or type(value["size"]) is int)
        and (value["digest"] is None or _valid_digest(value["digest"]))
    )


def _valid_repository_path(value: object) -> bool:
    if type(value) is not str or not value or "\x00" in value or value.startswith("/"):
        return False
    return all(component not in {"", ".", ".."} for component in value.split("/"))


def _valid_object_id(value: object) -> bool:
    return (
        type(value) is str
        and len(value) in {40, 64}
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def _valid_digest(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _state_error(path: Path) -> CommandError:
    return CommandError(
        _(
            "Applied-batch state is corrupt: {path}. Remove this file to "
            "forget restored-change review provenance."
        ).format(path=display_path(os.fspath(path)))
    )
