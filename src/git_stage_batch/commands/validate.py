"""Non-mutating batch metadata diagnostics."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from typing import Any

from ..batch.state.metadata_schema import (
    CURRENT_BATCH_METADATA_SCHEMA_VERSION,
    BatchMetadata,
    decode_batch_metadata,
)
from ..batch.state.query import list_batch_names
from ..batch.state.batch_names import (
    invalid_file_backed_batch_names,
    validate_batch_name,
    validate_batch_name_constraints,
)
from ..batch.state.reference_names import (
    format_batch_content_ref_name,
    format_batch_state_ref_name,
    format_legacy_batch_ref_name,
)
from ..exceptions import BatchMetadataError, CommandError
from ..i18n import _
from ..utils.file_io import read_text_file_contents
from ..utils.git_object_io import (
    GitObjectInfo,
    read_git_blobs_as_bytes,
    resolve_git_objects,
)
from ..utils.git_repository import require_git_repository
from ..utils.paths import get_batch_metadata_file_path


def inspect_batch_metadata() -> list[dict[str, Any]]:
    """Return diagnostics for every discoverable batch without writing state."""
    ref_batch_names = list_batch_names(validate_legacy_metadata=False)
    ref_batch_name_set = set(ref_batch_names)
    invalid_file_names = invalid_file_backed_batch_names(
        trusted_batch_names=ref_batch_name_set,
    )
    batch_names = sorted(
        ref_batch_name_set | set(invalid_file_names)
    )
    reports = []
    validated_names = []
    for batch_name in batch_names:
        report: dict[str, Any] = {
            "batch": batch_name,
            "status": "ok",
            "schema_version": None,
            "migration_required": False,
            "errors": [],
        }
        try:
            if batch_name in ref_batch_name_set:
                validate_batch_name_constraints(batch_name)
            else:
                validate_batch_name(batch_name)
        except CommandError as error:
            report["status"] = "error"
            report["source"] = "legacy-file"
            report["errors"].append(error.message)
            reports.append(report)
            continue
        validated_names.append(batch_name)
        reports.append(report)

    state_refspec_by_name = {
        batch_name: f"{format_batch_state_ref_name(batch_name)}:batch.json"
        for batch_name in validated_names
    }
    state_ref_by_name = {
        batch_name: format_batch_state_ref_name(batch_name)
        for batch_name in validated_names
    }
    related_ref_names = [
        ref_name
        for batch_name in validated_names
        for ref_name in (
            format_batch_content_ref_name(batch_name),
            format_legacy_batch_ref_name(batch_name),
        )
    ]
    ref_objects = resolve_git_objects(
        [
            *state_ref_by_name.values(),
            *state_refspec_by_name.values(),
            *related_ref_names,
        ]
    )
    state_payloads = read_git_blobs_as_bytes(
        object_info.object_id
        for refspec in state_refspec_by_name.values()
        if (object_info := ref_objects.get(refspec)) is not None
        and object_info.object_type == "blob"
    )

    decoded_models: list[tuple[dict[str, Any], BatchMetadata]] = []
    reports_by_name = {report["batch"]: report for report in reports}
    for batch_name in validated_names:
        report = reports_by_name[batch_name]
        state_ref = state_ref_by_name[batch_name]
        state_refspec = state_refspec_by_name[batch_name]
        state_ref_object = ref_objects.get(state_ref)
        state_object = ref_objects.get(state_refspec)
        authoritative_payload = (
            state_payloads.get(state_object.object_id)
            if state_object is not None and state_object.object_type == "blob"
            else None
        )
        state_read_error = None
        if state_ref_object is not None:
            payload: str | bytes = (
                authoritative_payload
                if authoritative_payload is not None
                else b""
            )
            source = "state-ref"
            if state_object is None:
                state_read_error = (
                    "authoritative batch state is missing path 'batch.json'"
                )
            elif state_object.object_type != "blob":
                state_read_error = (
                    "authoritative batch state path 'batch.json' is "
                    f"{state_object.object_type}, not a blob"
                )
            elif authoritative_payload is None:
                state_read_error = (
                    "authoritative batch state object could not be read"
                )
        else:
            metadata_path = get_batch_metadata_file_path(batch_name)
            payload = (
                read_text_file_contents(metadata_path) if metadata_path.exists() else ""
            )
            source = "legacy-file"
        report["source"] = source
        content_ref = format_batch_content_ref_name(batch_name)
        legacy_ref = format_legacy_batch_ref_name(batch_name)
        _classify_compatibility_residue(
            batch_name,
            report,
            authoritative_payload=authoritative_payload,
            authoritative_state_exists=state_ref_object is not None,
            related_ref_exists=(
                content_ref in ref_objects or legacy_ref in ref_objects
            ),
        )
        if state_read_error is not None:
            report["errors"].append(state_read_error)
            continue

        try:
            raw = json.loads(payload)
            report["schema_version"] = (
                raw.get("schema_version", 0) if isinstance(raw, dict) else None
            )
            report["migration_required"] = report["schema_version"] == 0
            model = decode_batch_metadata(payload, expected_batch=batch_name)
            report["revision"] = model.revision
            decoded_models.append((report, model))
        except UnicodeDecodeError as error:
            report["errors"].append(
                f"Batch '{batch_name}' metadata is not valid JSON: {error}"
            )
        except (BatchMetadataError, json.JSONDecodeError) as error:
            report["errors"].append(str(error))

    referenced_object_names = [
        object_name
        for _report, model in decoded_models
        for _field, object_name in _metadata_object_fields(model)
    ]
    referenced_objects = resolve_git_objects(referenced_object_names)
    for report, model in decoded_models:
        content_ref = format_batch_content_ref_name(model.batch)
        content_ref_info = ref_objects.get(content_ref)
        report["errors"].extend(
            _referential_errors(
                model,
                actual_commit=(
                    content_ref_info.object_id
                    if content_ref_info is not None
                    else None
                ),
                object_info_by_name=referenced_objects,
            )
        )

    for report in reports:
        if report["errors"]:
            report["status"] = "error"
    return reports


def _classify_compatibility_residue(
    batch_name: str,
    report: dict[str, Any],
    *,
    authoritative_payload: str | bytes | None,
    authoritative_state_exists: bool,
    related_ref_exists: bool,
) -> None:
    """Classify crash-residual file metadata without making it authoritative."""
    metadata_path = get_batch_metadata_file_path(batch_name)
    if not metadata_path.exists():
        report["residue"] = None
        return
    try:
        file_model = decode_batch_metadata(
            read_text_file_contents(metadata_path),
            expected_batch=batch_name,
        )
    except (BatchMetadataError, json.JSONDecodeError) as error:
        report["residue"] = {
            "class": "invalid_residue",
            "path": str(metadata_path),
            "safe_automatic_repair": False,
        }
        report["errors"].append(f"invalid compatibility metadata residue: {error}")
        return

    if authoritative_state_exists and authoritative_payload is None:
        report["residue"] = {
            "class": "unverifiable_residue",
            "path": str(metadata_path),
            "file_revision": file_model.revision,
            "safe_automatic_repair": False,
        }
        return

    if authoritative_payload is not None:
        try:
            state_model = decode_batch_metadata(
                authoritative_payload,
                expected_batch=batch_name,
            )
        except (BatchMetadataError, json.JSONDecodeError):
            report["residue"] = {
                "class": "unverifiable_residue",
                "path": str(metadata_path),
                "file_revision": file_model.revision,
                "safe_automatic_repair": False,
            }
            return
        if file_model.to_application_dict() == state_model.to_application_dict():
            residue_class = "redundant_residue"
            safe_repair = True
        elif file_model.revision == state_model.revision:
            residue_class = "stale_attempted_update"
            safe_repair = False
        else:
            residue_class = "concurrent_conflicting_residue"
            safe_repair = False
        report["residue"] = {
            "class": residue_class,
            "path": str(metadata_path),
            "authoritative_revision": state_model.revision,
            "file_revision": file_model.revision,
            "safe_automatic_repair": safe_repair,
        }
        if not safe_repair:
            report["errors"].append(
                f"batch compatibility metadata has {residue_class.replace('_', ' ')}"
            )
        return

    report["residue"] = {
        "class": "legacy_compatibility_state"
        if related_ref_exists
        else "orphaned_create",
        "path": str(metadata_path),
        "file_revision": file_model.revision,
        "safe_automatic_repair": False,
    }
    if not related_ref_exists:
        report["errors"].append("orphaned batch metadata has no related refs")


def _metadata_object_fields(model: BatchMetadata) -> list[tuple[str, str]]:
    object_fields = []
    if model.baseline is not None:
        object_fields.append(("baseline", model.baseline))
    for entry in model.files:
        source_commit = entry.values.get("batch_source_commit")
        if isinstance(source_commit, str):
            object_fields.append(
                (f"files[{entry.path!r}].batch_source_commit", source_commit)
            )
        for index, deletion in enumerate(entry.values.get("deletions", ())):
            blob = deletion.get("blob") if isinstance(deletion, Mapping) else None
            if isinstance(blob, str):
                object_fields.append(
                    (f"files[{entry.path!r}].deletions[{index}].blob", blob)
                )
    return object_fields


def _referential_errors(
    model: BatchMetadata,
    *,
    actual_commit: str | None,
    object_info_by_name: Mapping[str, GitObjectInfo],
) -> list[str]:
    errors = []
    expected_ref = format_batch_content_ref_name(model.batch)
    if model.content_ref is not None and model.content_ref != expected_ref:
        errors.append(
            f"content_ref is {model.content_ref!r}; expected {expected_ref!r}"
        )
    if model.content_commit is not None and model.content_commit != actual_commit:
        errors.append(
            "content_commit does not match the authoritative batch content ref"
        )
    for field, object_id in _metadata_object_fields(model):
        if object_id not in object_info_by_name:
            errors.append(f"{field} names missing object {object_id}")
    return errors


def command_validate_batches(*, porcelain: bool = False) -> None:
    """Validate all persisted batch metadata without migrating it."""
    require_git_repository()
    reports = inspect_batch_metadata()
    if porcelain:
        print(
            json.dumps(
                {
                    "metadata_schema": CURRENT_BATCH_METADATA_SCHEMA_VERSION,
                    "batches": reports,
                },
                indent=2,
            )
        )
    elif not reports:
        print(_("No batches found"), file=sys.stderr)
    else:
        for report in reports:
            if report["status"] == "ok":
                suffix = (
                    _(" (migration to schema v{version} available)").format(
                        version=CURRENT_BATCH_METADATA_SCHEMA_VERSION
                    )
                    if report["migration_required"]
                    else ""
                )
                print(f"✓ {report['batch']}: metadata valid{suffix}")
            else:
                print(f"✗ {report['batch']}:", file=sys.stderr)
                for error in report["errors"]:
                    print(f"  {error}", file=sys.stderr)
    if any(report["status"] == "error" for report in reports):
        raise CommandError("", exit_code=1)
