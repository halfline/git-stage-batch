"""Non-mutating batch metadata diagnostics."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from typing import Any

from ..batch.metadata_schema import (
    CURRENT_BATCH_METADATA_SCHEMA_VERSION,
    BatchMetadata,
    decode_batch_metadata,
)
from ..batch.query import list_batch_names
from ..batch.validation import invalid_file_backed_batch_names, validate_batch_name
from ..batch.state_refs import (
    get_authoritative_batch_commit_sha,
    get_batch_content_ref_name,
    get_batch_state_ref_name,
)
from ..exceptions import BatchMetadataError, CommandError
from ..i18n import _
from ..utils.file_io import read_text_file_contents
from ..utils.git_command import run_git_command
from ..utils.git_repository import require_git_repository
from ..utils.paths import get_batch_metadata_file_path


def inspect_batch_metadata() -> list[dict[str, Any]]:
    """Return diagnostics for every discoverable batch without writing state."""
    reports = []
    invalid_file_names = invalid_file_backed_batch_names()
    batch_names = sorted(set(list_batch_names(validate_legacy_metadata=False)) | set(invalid_file_names))
    for batch_name in batch_names:
        report: dict[str, Any] = {
            "batch": batch_name,
            "status": "ok",
            "schema_version": None,
            "migration_required": False,
            "errors": [],
        }
        try:
            validate_batch_name(batch_name)
        except CommandError as error:
            report["status"] = "error"
            report["source"] = "legacy-file"
            report["errors"].append(error.message)
            reports.append(report)
            continue
        state_result = run_git_command(
            ["show", f"{get_batch_state_ref_name(batch_name)}:batch.json"],
            check=False,
            requires_index_lock=False,
        )
        if state_result.returncode == 0:
            payload = state_result.stdout
            source = "state-ref"
        else:
            metadata_path = get_batch_metadata_file_path(batch_name)
            payload = read_text_file_contents(metadata_path) if metadata_path.exists() else ""
            source = "legacy-file"
        report["source"] = source

        try:
            raw = json.loads(payload)
            report["schema_version"] = raw.get("schema_version", 0) if isinstance(raw, dict) else None
            report["migration_required"] = report["schema_version"] == 0
            model = decode_batch_metadata(payload, expected_batch=batch_name)
            report["revision"] = model.revision
            report["errors"].extend(_referential_errors(model))
        except (BatchMetadataError, json.JSONDecodeError) as error:
            report["errors"].append(str(error))

        if report["errors"]:
            report["status"] = "error"
        reports.append(report)
    return reports


def _referential_errors(model: BatchMetadata) -> list[str]:
    errors = []
    expected_ref = get_batch_content_ref_name(model.batch)
    actual_commit = get_authoritative_batch_commit_sha(model.batch)
    if model.content_ref is not None and model.content_ref != expected_ref:
        errors.append(
            f"content_ref is {model.content_ref!r}; expected {expected_ref!r}"
        )
    if model.content_commit is not None and model.content_commit != actual_commit:
        errors.append(
            "content_commit does not match the authoritative batch content ref"
        )
    object_fields = []
    if model.baseline is not None:
        object_fields.append(("baseline", model.baseline))
    for entry in model.files:
        source_commit = entry.values.get("batch_source_commit")
        if isinstance(source_commit, str):
            object_fields.append((f"files[{entry.path!r}].batch_source_commit", source_commit))
        for index, deletion in enumerate(entry.values.get("deletions", ())):
            blob = deletion.get("blob") if isinstance(deletion, Mapping) else None
            if isinstance(blob, str):
                object_fields.append((f"files[{entry.path!r}].deletions[{index}].blob", blob))
    for field, object_id in object_fields:
        result = run_git_command(
            ["cat-file", "-e", object_id],
            check=False,
            requires_index_lock=False,
        )
        if result.returncode != 0:
            errors.append(f"{field} names missing object {object_id}")
    return errors


def command_validate_batches(*, porcelain: bool = False) -> None:
    """Validate all persisted batch metadata without migrating it."""
    require_git_repository()
    reports = inspect_batch_metadata()
    if porcelain:
        print(json.dumps(
            {
                "metadata_schema": CURRENT_BATCH_METADATA_SCHEMA_VERSION,
                "batches": reports,
            },
            indent=2,
        ))
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
