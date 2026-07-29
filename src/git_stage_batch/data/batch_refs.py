"""Batch reference snapshot and restore for abort support."""

from __future__ import annotations

import json
import shutil
from typing import NoReturn, TypedDict, cast

from ..batch.state.batch_names import validate_batch_name
from ..batch.state.metadata_schema import metadata_from_application_dict
from ..batch.state.metadata_types import BatchMetadataDict
from ..batch.state.reference_names import BATCH_CONTENT_REF_PREFIX, LEGACY_BATCH_REF_PREFIX
from ..batch.state.query import read_batch_metadata
from ..batch.state.compatibility_metadata import write_file_backed_batch_metadata
from ..batch.state.references import (
    delete_batch_state_refs,
    get_batch_content_ref_name,
    get_batch_state_ref_name,
    remove_file_backed_batch_metadata,
    sync_batch_state_refs,
)
from ..exceptions import BatchMetadataError, CommandError
from ..i18n import _
from ..utils.file_io import (
    read_required_text_file_contents,
    write_text_file_contents,
)
from ..utils.git_command import run_git_command
from ..utils.git_refs import update_git_refs
from ..utils.git_repository import object_id_hex_length
from ..utils.paths import get_batch_directory_path, get_batch_refs_snapshot_file_path


BATCH_REF_SNAPSHOT_SCHEMA_VERSION = 1


class BatchRefSnapshotEntry(TypedDict):
    """One batch's refs plus validated application metadata."""

    commit_sha: str
    state_commit_sha: str | None
    metadata: BatchMetadataDict


class BatchRefSnapshot(TypedDict):
    """Versioned abort snapshot of every batch reference."""

    schema_version: int
    batches: dict[str, BatchRefSnapshotEntry]


_SNAPSHOT_KEYS = frozenset({"schema_version", "batches"})
_ENTRY_KEYS = frozenset({"commit_sha", "state_commit_sha", "metadata"})
_REQUIRED_METADATA_KEYS = frozenset({"note", "created_at", "baseline", "files"})
_OPTIONAL_METADATA_KEYS = frozenset({"revision"})


def _list_batch_content_refs() -> dict[str, str]:
    refs: dict[str, str] = {}
    prefixes = (
        (BATCH_CONTENT_REF_PREFIX, len(BATCH_CONTENT_REF_PREFIX)),
        (LEGACY_BATCH_REF_PREFIX, len(LEGACY_BATCH_REF_PREFIX)),
    )
    for prefix, prefix_len in prefixes:
        result = run_git_command(
            ["for-each-ref", "--format=%(objectname) %(refname)", prefix],
            check=False,
            requires_index_lock=False,
        )
        if result.returncode != 0:
            continue
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            commit_sha, ref = line.split(None, 1)
            if ref.startswith(prefix):
                refs.setdefault(ref[prefix_len:], commit_sha)
    return refs


def _get_batch_state_ref_commit(batch_name: str) -> str | None:
    result = run_git_command(
        ["rev-parse", "--verify", get_batch_state_ref_name(batch_name)],
        check=False,
        requires_index_lock=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def snapshot_batch_refs() -> BatchRefSnapshot:
    """Save selected state of all batch refs to snapshot file for abort support.

    Stores a versioned JSON object containing a mapping from batch names to
    their refs and metadata.

    This includes complete metadata so dropped batches can be fully restored.
    """
    snapshot_data: BatchRefSnapshot = {
        "schema_version": BATCH_REF_SNAPSHOT_SCHEMA_VERSION,
        "batches": {},
    }
    for batch_name, commit_sha in _list_batch_content_refs().items():
        full_metadata = read_batch_metadata(batch_name)

        snapshot_data["batches"][batch_name] = {
            "commit_sha": commit_sha,
            "state_commit_sha": _get_batch_state_ref_commit(batch_name),
            "metadata": full_metadata,
        }

    write_text_file_contents(
        get_batch_refs_snapshot_file_path(),
        json.dumps(snapshot_data, indent=2, sort_keys=True) + "\n",
    )
    return snapshot_data


def _invalid_snapshot(detail: str, *, cause: BaseException | None = None) -> NoReturn:
    snapshot_path = get_batch_refs_snapshot_file_path()
    error = CommandError(
        _(
            "The batch-reference recovery snapshot is {detail}: {path}. "
            "The session remains active; repair the snapshot and run "
            "'git-stage-batch abort' again."
        ).format(detail=detail, path=snapshot_path)
    )
    if cause is None:
        raise error
    raise error from cause


def _validate_snapshot_object_id(
    value: object,
    *,
    batch_name: str,
    field: str,
) -> str:
    object_id_length = object_id_hex_length()
    if not isinstance(value, str) or len(value) != object_id_length:
        _invalid_snapshot(
            _("invalid: batch {batch!r} field {field!r} must be a full object ID").format(
                batch=batch_name,
                field=field,
            )
        )
    try:
        int(value, 16)
    except ValueError as error:
        _invalid_snapshot(
            _("invalid: batch {batch!r} field {field!r} is not hexadecimal").format(
                batch=batch_name,
                field=field,
            ),
            cause=error,
        )
    return value


def _decode_batch_ref_snapshot(payload: str) -> BatchRefSnapshot:
    """Decode and deeply validate one versioned batch-reference snapshot."""
    try:
        value: object = json.loads(payload)
    except json.JSONDecodeError as error:
        _invalid_snapshot(_("malformed JSON"), cause=error)

    if not isinstance(value, dict):
        _invalid_snapshot(_("invalid: the top level must be an object"))

    snapshot_keys = set(value)
    if snapshot_keys != _SNAPSHOT_KEYS:
        missing = sorted(_SNAPSHOT_KEYS - snapshot_keys)
        unknown = sorted(snapshot_keys - _SNAPSHOT_KEYS)
        details = []
        if missing:
            details.append(_("missing field(s) {fields}").format(fields=missing))
        if unknown:
            details.append(_("unknown field(s) {fields}").format(fields=unknown))
        _invalid_snapshot(_("invalid: {details}").format(details=", ".join(details)))

    schema_version = value["schema_version"]
    if type(schema_version) is not int:
        _invalid_snapshot(_("invalid: 'schema_version' must be an integer"))
    if schema_version != BATCH_REF_SNAPSHOT_SCHEMA_VERSION:
        _invalid_snapshot(
            _(
                "invalid: unsupported schema version {actual}; expected {expected}"
            ).format(
                actual=schema_version,
                expected=BATCH_REF_SNAPSHOT_SCHEMA_VERSION,
            )
        )

    raw_batches = value["batches"]
    if not isinstance(raw_batches, dict):
        _invalid_snapshot(_("invalid: 'batches' must be an object"))

    batches: dict[str, BatchRefSnapshotEntry] = {}
    for raw_batch_name, raw_entry in raw_batches.items():
        if not isinstance(raw_batch_name, str):
            _invalid_snapshot(_("invalid: every batch name must be a string"))
        try:
            validate_batch_name(raw_batch_name)
        except CommandError as error:
            _invalid_snapshot(
                _("invalid: batch name {batch!r} is not valid ({error})").format(
                    batch=raw_batch_name,
                    error=error.message,
                ),
                cause=error,
            )

        if not isinstance(raw_entry, dict):
            _invalid_snapshot(
                _("invalid: entry for batch {batch!r} must be an object").format(
                    batch=raw_batch_name
                )
            )
        entry_keys = set(raw_entry)
        if entry_keys != _ENTRY_KEYS:
            _invalid_snapshot(
                _(
                    "invalid: entry for batch {batch!r} must contain exactly {fields}"
                ).format(batch=raw_batch_name, fields=sorted(_ENTRY_KEYS))
            )

        commit_sha = _validate_snapshot_object_id(
            raw_entry["commit_sha"],
            batch_name=raw_batch_name,
            field="commit_sha",
        )
        raw_state_commit_sha = raw_entry["state_commit_sha"]
        state_commit_sha = (
            None
            if raw_state_commit_sha is None
            else _validate_snapshot_object_id(
                raw_state_commit_sha,
                batch_name=raw_batch_name,
                field="state_commit_sha",
            )
        )

        raw_metadata = raw_entry["metadata"]
        if not isinstance(raw_metadata, dict):
            _invalid_snapshot(
                _("invalid: metadata for batch {batch!r} must be an object").format(
                    batch=raw_batch_name
                )
            )
        metadata_keys = set(raw_metadata)
        missing_metadata_keys = _REQUIRED_METADATA_KEYS - metadata_keys
        unknown_metadata_keys = metadata_keys - (
            _REQUIRED_METADATA_KEYS | _OPTIONAL_METADATA_KEYS
        )
        if missing_metadata_keys or unknown_metadata_keys:
            field_errors = []
            if missing_metadata_keys:
                field_errors.append(
                    _("missing {fields}").format(
                        fields=sorted(missing_metadata_keys)
                    )
                )
            if unknown_metadata_keys:
                field_errors.append(
                    _("unknown {fields}").format(
                        fields=sorted(unknown_metadata_keys)
                    )
                )
            _invalid_snapshot(
                _(
                    "invalid: metadata for batch {batch!r} has an invalid field set "
                    "({details})"
                ).format(
                    batch=raw_batch_name,
                    details=", ".join(field_errors),
                )
            )
        if "revision" in raw_metadata and not isinstance(
            raw_metadata["revision"], str
        ):
            _invalid_snapshot(
                _(
                    "invalid: metadata for batch {batch!r} field 'revision' "
                    "must be a string"
                ).format(batch=raw_batch_name)
            )
        metadata = cast(BatchMetadataDict, raw_metadata)
        try:
            metadata_from_application_dict(raw_batch_name, metadata)
        except BatchMetadataError as error:
            _invalid_snapshot(
                _(
                    "invalid: metadata for batch {batch!r} is malformed ({error})"
                ).format(
                    batch=raw_batch_name,
                    error=error,
                ),
                cause=error,
            )

        batches[raw_batch_name] = {
            "commit_sha": commit_sha,
            "state_commit_sha": state_commit_sha,
            "metadata": metadata,
        }

    return {
        "schema_version": BATCH_REF_SNAPSHOT_SCHEMA_VERSION,
        "batches": batches,
    }


def load_batch_refs_snapshot() -> BatchRefSnapshot:
    """Load required abort recovery state without conflating absence and emptiness."""
    snapshot_path = get_batch_refs_snapshot_file_path()
    try:
        payload = read_required_text_file_contents(snapshot_path)
    except FileNotFoundError as error:
        _invalid_snapshot(_("missing"), cause=error)
    except OSError as error:
        _invalid_snapshot(_("unreadable"), cause=error)
    if not payload.strip():
        _invalid_snapshot(_("empty"))
    return _decode_batch_ref_snapshot(payload)


def batch_ref_snapshot_recovery_objects(
    snapshot: BatchRefSnapshot,
) -> list[str | None]:
    """Return every Git object directly referenced by a batch snapshot."""
    return [
        object_name
        for batch_state in snapshot["batches"].values()
        for object_name in (
            batch_state["commit_sha"],
            batch_state["state_commit_sha"],
        )
    ]


def restore_batch_refs(snapshot: BatchRefSnapshot) -> None:
    """Restore batch refs from snapshot, reverting all batch changes made during session.

    Compares snapshot with selected refs:
    - Batches in selected but not snapshot: drop (delete ref + metadata)
    - Batches in snapshot but not selected: restore (recreate ref + metadata)
    - Batches in both with different SHAs: revert (update ref to snapshot SHA)
    """
    snapshot_data = snapshot["batches"]

    # Get selected batch refs
    selected_batches = _list_batch_content_refs()

    # Drop batches created during session (in selected but not in snapshot)
    for batch_name in selected_batches:
        if batch_name not in snapshot_data:
            delete_batch_state_refs(batch_name)
            # Delete metadata directory
            metadata_dir = get_batch_directory_path(batch_name)
            if metadata_dir.exists():
                shutil.rmtree(metadata_dir, ignore_errors=True)

    # Restore/revert batches from snapshot
    for batch_name, batch_state in snapshot_data.items():
        commit_sha = batch_state["commit_sha"]
        state_commit_sha = batch_state["state_commit_sha"]
        full_metadata = batch_state["metadata"]

        if state_commit_sha:
            update_git_refs(
                updates=[
                    (get_batch_content_ref_name(batch_name), commit_sha),
                    (get_batch_state_ref_name(batch_name), state_commit_sha),
                ],
                deletes=[f"{LEGACY_BATCH_REF_PREFIX}{batch_name}"],
            )
            remove_file_backed_batch_metadata(batch_name)
        else:
            metadata_model = write_file_backed_batch_metadata(
                batch_name,
                full_metadata,
            )
            sync_batch_state_refs(
                batch_name,
                metadata_model,
                content_commit=commit_sha,
            )
