"""Versioned batch metadata parsing and canonical serialization."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, TypeAlias

from ..exceptions import BatchMetadataError
from ..utils.git_repository import object_id_hex_length


CURRENT_BATCH_METADATA_SCHEMA_VERSION = 1

JsonScalar: TypeAlias = None | bool | int | str
JsonValue: TypeAlias = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]

_LINE_RANGE_RE = re.compile(r"^(?P<start>[1-9][0-9]*)(?:-(?P<end>[1-9][0-9]*))?$")
_FILE_METADATA_KEYS = frozenset({
    "batch_source_commit",
    "change_type",
    "claimed_lines",
    "deletions",
    "file_type",
    "mode",
    "new_mode",
    "new_oid",
    "old_mode",
    "old_oid",
    "presence_claims",
    "replacement_masks",
    "replacement_units",
    "source_path",
})
_TOP_LEVEL_KEYS = frozenset({
    "schema_version",
    "revision",
    "batch",
    "note",
    "created_at",
    "baseline",
    "content_ref",
    "content_commit",
    "files",
})
_GIT_FILE_MODES = frozenset({"100644", "100755", "120000", "160000"})


class BatchFileType(Enum):
    """Persisted atomic file type; text entries omit the field."""

    BINARY = "binary"
    GITLINK = "gitlink"
    MODE = "mode"


class BatchChangeType(Enum):
    """Persisted whole-file lifecycle state."""

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"


@dataclass(frozen=True)
class BatchFileMetadata:
    """Immutable validated metadata for one repository path."""

    path: str
    values: Mapping[str, JsonValue]

    def to_dict(self) -> dict[str, Any]:
        return _thaw_mapping(self.values)


@dataclass(frozen=True)
class BatchMetadata:
    """Immutable current-schema batch metadata."""

    revision: str
    batch: str
    note: str
    created_at: str
    baseline: str | None
    files: tuple[BatchFileMetadata, ...]
    content_ref: str | None = None
    content_commit: str | None = None

    def to_application_dict(self) -> dict[str, Any]:
        """Return the compatibility mapping used by current domain code."""
        return {
            "revision": self.revision,
            "note": self.note,
            "created_at": self.created_at,
            "baseline": self.baseline,
            "files": {entry.path: entry.to_dict() for entry in self.files},
        }

    def to_storage_dict(self) -> dict[str, Any]:
        """Return the canonical current-version storage representation."""
        return {
            "schema_version": CURRENT_BATCH_METADATA_SCHEMA_VERSION,
            "revision": self.revision,
            "batch": self.batch,
            "note": self.note,
            "created_at": self.created_at,
            "baseline": self.baseline,
            "content_ref": self.content_ref,
            "content_commit": self.content_commit,
            "files": {entry.path: entry.to_dict() for entry in self.files},
        }


def new_batch_metadata_revision() -> str:
    """Return an opaque revision identifier for stale-writer detection."""
    return str(uuid.uuid4())


def decode_batch_metadata(
    payload: str | bytes | Mapping[str, Any],
    *,
    expected_batch: str,
) -> BatchMetadata:
    """Decode v0 or v1 metadata and return a validated immutable model."""
    data = _load_json_object(payload, expected_batch)
    version = data.get("schema_version", 0)
    if type(version) is not int:
        _invalid(expected_batch, "'schema_version' must be an integer")
    if version > CURRENT_BATCH_METADATA_SCHEMA_VERSION:
        raise BatchMetadataError(
            f"Batch '{expected_batch}' uses metadata schema version {version}, but "
            f"this version of git-stage-batch supports through version "
            f"{CURRENT_BATCH_METADATA_SCHEMA_VERSION}. Upgrade git-stage-batch "
            "or use a compatible version; the metadata was not modified."
        )
    if version < 0:
        _invalid(expected_batch, "'schema_version' cannot be negative")
    migrated_from_v0 = version == 0
    if migrated_from_v0:
        data = _migrate_v0_to_v1(data, expected_batch)
    elif version != CURRENT_BATCH_METADATA_SCHEMA_VERSION:
        _invalid(expected_batch, f"unsupported metadata schema version {version}")
    return _decode_v1(data, expected_batch, allow_legacy=migrated_from_v0)


def encode_batch_metadata(metadata: BatchMetadata) -> str:
    """Serialize current metadata deterministically."""
    return json.dumps(
        metadata.to_storage_dict(),
        indent=2,
        ensure_ascii=False,
    ) + "\n"


def metadata_from_application_dict(
    batch_name: str,
    data: Mapping[str, Any],
    *,
    content_ref: str | None = None,
    content_commit: str | None = None,
    new_revision: bool = False,
) -> BatchMetadata:
    """Validate a mutable application mapping before persistence."""
    revision = data.get("revision")
    if new_revision or revision is None:
        revision = new_batch_metadata_revision()
    storage = {
        "schema_version": CURRENT_BATCH_METADATA_SCHEMA_VERSION,
        "revision": revision,
        "batch": batch_name,
        "note": data.get("note", ""),
        "created_at": data.get("created_at", ""),
        "baseline": data.get("baseline"),
        "content_ref": content_ref,
        "content_commit": content_commit,
        "files": data.get("files", {}),
    }
    return _decode_v1(storage, batch_name)


def _load_json_object(
    payload: str | bytes | Mapping[str, Any],
    batch_name: str,
) -> dict[str, Any]:
    if isinstance(payload, Mapping):
        data = dict(payload)
    else:
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise BatchMetadataError(
                f"Batch '{batch_name}' metadata is not valid JSON: {error}"
            ) from error
    if not isinstance(data, dict):
        _invalid(batch_name, "top-level metadata must be an object")
    return data


def _migrate_v0_to_v1(data: dict[str, Any], batch_name: str) -> dict[str, Any]:
    """Pure, deterministic migration from the historical unversioned shape."""
    canonical_legacy = json.dumps(data, sort_keys=True, separators=(",", ":"))
    revision = data.get("revision")
    if revision is None:
        revision = "v0-" + hashlib.sha256(canonical_legacy.encode("utf-8")).hexdigest()
    return {
        "schema_version": CURRENT_BATCH_METADATA_SCHEMA_VERSION,
        "revision": revision,
        "batch": data.get("batch", batch_name),
        "note": data.get("note", ""),
        "created_at": data.get("created_at", ""),
        "baseline": data.get("baseline_commit", data.get("baseline")),
        "content_ref": data.get("content_ref"),
        "content_commit": data.get("content_commit"),
        "files": data.get("files", {}),
    }


def _decode_v1(
    data: dict[str, Any],
    expected_batch: str,
    *,
    allow_legacy: bool = False,
) -> BatchMetadata:
    unknown_keys = set(data) - _TOP_LEVEL_KEYS
    missing_keys = _TOP_LEVEL_KEYS - set(data)
    if unknown_keys:
        _invalid(expected_batch, f"unknown top-level field(s): {_field_list(unknown_keys)}")
    if missing_keys:
        _invalid(expected_batch, f"missing required field(s): {_field_list(missing_keys)}")
    if data["schema_version"] != CURRENT_BATCH_METADATA_SCHEMA_VERSION:
        _invalid(expected_batch, "metadata was not migrated to the current schema")

    revision = _required_string(data, "revision", expected_batch)
    batch = _required_string(data, "batch", expected_batch)
    if batch != expected_batch:
        _invalid(expected_batch, f"metadata identifies batch '{batch}'")
    note = _required_string(data, "note", expected_batch, allow_empty=True)
    created_at = _required_string(data, "created_at", expected_batch, allow_empty=True)
    if created_at:
        try:
            datetime.fromisoformat(
                created_at[:-1] + "+00:00" if created_at.endswith("Z") else created_at
            )
        except ValueError as error:
            raise BatchMetadataError(
                f"Batch '{expected_batch}' metadata field 'created_at' is not an "
                "ISO-8601 timestamp"
            ) from error

    baseline = _optional_object_id(data, "baseline", expected_batch)
    content_ref = _optional_string(data, "content_ref", expected_batch)
    content_commit = _optional_object_id(data, "content_commit", expected_batch)
    files_data = data["files"]
    if not isinstance(files_data, dict):
        _invalid(expected_batch, "'files' must be an object")

    files = tuple(
        _decode_file_metadata(path, values, expected_batch, allow_legacy=allow_legacy)
        for path, values in files_data.items()
    )
    return BatchMetadata(
        revision=revision,
        batch=batch,
        note=note,
        created_at=created_at,
        baseline=baseline,
        files=files,
        content_ref=content_ref,
        content_commit=content_commit,
    )


def _decode_file_metadata(
    path: Any,
    values: Any,
    batch_name: str,
    *,
    allow_legacy: bool,
) -> BatchFileMetadata:
    if not isinstance(path, str) or not path or "\x00" in path:
        _invalid(batch_name, "file metadata path must be a non-empty string without NUL")
    path_parts = path.split("/")
    if path.startswith("/") or any(part in ("", ".", "..") for part in path_parts):
        _invalid(batch_name, f"file metadata path is not repository-relative: {path!r}")
    if not isinstance(values, dict):
        _invalid(batch_name, f"file entry for {path!r} must be an object")
    unknown_keys = set(values) - _FILE_METADATA_KEYS
    if unknown_keys:
        _invalid(
            batch_name,
            f"file entry for {path!r} has unknown field(s): {_field_list(unknown_keys)}",
        )

    file_type = values.get("file_type")
    if file_type is not None and file_type not in {item.value for item in BatchFileType}:
        _invalid(batch_name, f"file entry for {path!r} has invalid file_type")
    change_type = values.get("change_type")
    if change_type is not None and change_type not in {item.value for item in BatchChangeType}:
        _invalid(batch_name, f"file entry for {path!r} has invalid change_type")
    for key in ("mode", "old_mode", "new_mode"):
        value = values.get(key)
        if value is not None and value not in _GIT_FILE_MODES:
            _invalid(batch_name, f"file entry for {path!r} has invalid {key}")
    for key in ("batch_source_commit",):
        if key in values:
            _validate_object_id(values[key], batch_name, f"files[{path!r}].{key}")
    for key in ("old_oid", "new_oid"):
        value = values.get(key)
        if value is not None:
            _validate_hex_object_id(value, batch_name, f"files[{path!r}].{key}", (40, 64))
    if "source_path" in values and values["source_path"] != f"sources/{path}":
        _invalid(batch_name, f"file entry for {path!r} has inconsistent source_path")
    if file_type in (None, BatchFileType.BINARY.value, BatchFileType.MODE.value):
        if "batch_source_commit" not in values:
            if not allow_legacy or file_type not in {
                BatchFileType.BINARY.value,
                BatchFileType.GITLINK.value,
            }:
                _invalid(batch_name, f"file entry for {path!r} is missing 'batch_source_commit'")
    if file_type == BatchFileType.GITLINK.value and values.get("mode") != "160000":
        _invalid(batch_name, f"gitlink entry for {path!r} must use mode 160000")
    if file_type == BatchFileType.MODE.value:
        if not {"old_mode", "new_mode", "mode"} <= set(values):
            _invalid(batch_name, f"mode entry for {path!r} is missing mode fields")
        if values["mode"] != values["new_mode"] or values["old_mode"] == values["new_mode"]:
            _invalid(batch_name, f"mode entry for {path!r} has inconsistent transition")

    _validate_claims(values, path, batch_name)
    return BatchFileMetadata(path=path, values=_freeze_mapping(values, batch_name))


def _validate_claims(values: dict[str, Any], path: str, batch_name: str) -> None:
    for key in ("presence_claims", "deletions", "replacement_units", "claimed_lines"):
        if key in values and not isinstance(values[key], list):
            _invalid(batch_name, f"files[{path!r}].{key} must be an array")
    seen_presence: set[tuple[str, ...]] = set()
    for claim in values.get("presence_claims", []):
        if not isinstance(claim, dict) or not isinstance(claim.get("source_lines"), list):
            _invalid(batch_name, f"files[{path!r}].presence_claims has an invalid claim")
        _reject_unknown_keys(
            claim,
            {"source_lines", "baseline_references"},
            batch_name,
            f"files[{path!r}].presence_claims",
        )
        ranges = tuple(claim["source_lines"])
        _validate_line_ranges(ranges, batch_name, f"files[{path!r}].presence_claims")
        if ranges in seen_presence:
            _invalid(batch_name, f"files[{path!r}] has duplicate presence claims")
        seen_presence.add(ranges)
        references = claim.get("baseline_references", {})
        if not isinstance(references, dict):
            _invalid(batch_name, f"files[{path!r}] has invalid baseline_references")
        for line, reference in references.items():
            if not isinstance(line, str) or not line.isdigit() or int(line) < 1:
                _invalid(batch_name, f"files[{path!r}] has invalid baseline reference line")
            _validate_baseline_reference(reference, batch_name, path)
    for deletion in values.get("deletions", []):
        if not isinstance(deletion, dict):
            _invalid(batch_name, f"files[{path!r}].deletions has a non-object entry")
        _reject_unknown_keys(
            deletion,
            {"after_source_line", "blob", "baseline_reference"},
            batch_name,
            f"files[{path!r}].deletions",
        )
        anchor = deletion.get("after_source_line")
        if anchor is not None and (type(anchor) is not int or anchor < 1):
            _invalid(batch_name, f"files[{path!r}] has an invalid deletion anchor")
        _validate_object_id(deletion.get("blob"), batch_name, f"files[{path!r}].deletions.blob")
        if "baseline_reference" in deletion:
            _validate_baseline_reference(deletion["baseline_reference"], batch_name, path)
    deletion_count = len(values.get("deletions", []))
    for replacement in values.get("replacement_units", []):
        if not isinstance(replacement, dict):
            _invalid(batch_name, f"files[{path!r}].replacement_units has a non-object entry")
        _reject_unknown_keys(
            replacement,
            {"presence_lines", "claimed_lines", "deletion_indices", "original_unit"},
            batch_name,
            f"files[{path!r}].replacement_units",
        )
        presence_lines = replacement.get("presence_lines", replacement.get("claimed_lines"))
        deletion_indices = replacement.get("deletion_indices")
        if not isinstance(presence_lines, list) or not isinstance(deletion_indices, list):
            _invalid(batch_name, f"files[{path!r}] has an invalid replacement unit")
        _validate_line_ranges(
            tuple(presence_lines),
            batch_name,
            f"files[{path!r}].replacement_units.presence_lines",
        )
        if (
            any(type(index) is not int or not 0 <= index < deletion_count for index in deletion_indices)
            or len(set(deletion_indices)) != len(deletion_indices)
        ):
            _invalid(batch_name, f"files[{path!r}] has invalid replacement deletion indices")
        if "original_unit" in replacement:
            _validate_replacement_origin(replacement["original_unit"], batch_name, path)
    if "claimed_lines" in values:
        _validate_line_ranges(
            tuple(values["claimed_lines"]),
            batch_name,
            f"files[{path!r}].claimed_lines",
        )
    _validate_json_value(values, batch_name, f"files[{path!r}]")


def _validate_baseline_reference(reference: Any, batch_name: str, path: str) -> None:
    if not isinstance(reference, dict):
        _invalid(batch_name, f"files[{path!r}] has a non-object baseline reference")
    _reject_unknown_keys(
        reference,
        {"after_line", "after_blob", "before_line", "before_blob"},
        batch_name,
        f"files[{path!r}].baseline_reference",
    )
    for key in ("after_line", "before_line"):
        value = reference.get(key)
        if value is not None and (type(value) is not int or value < 1):
            _invalid(batch_name, f"files[{path!r}] has invalid {key}")
    for key in ("after_blob", "before_blob"):
        if key in reference:
            _validate_object_id(reference[key], batch_name, f"files[{path!r}].{key}")


def _validate_replacement_origin(origin: Any, batch_name: str, path: str) -> None:
    if not isinstance(origin, dict):
        _invalid(batch_name, f"files[{path!r}] has a non-object replacement origin")
    required = {"old_start", "old_end", "new_start", "new_end"}
    _reject_unknown_keys(
        origin,
        required | {"baseline_reference"},
        batch_name,
        f"files[{path!r}].replacement_units.original_unit",
    )
    if not required <= set(origin):
        _invalid(batch_name, f"files[{path!r}] has an incomplete replacement origin")
    for key in required:
        if type(origin[key]) is not int or origin[key] < 1:
            _invalid(batch_name, f"files[{path!r}] has invalid replacement {key}")
    if origin["old_end"] < origin["old_start"] or origin["new_end"] < origin["new_start"]:
        _invalid(batch_name, f"files[{path!r}] has descending replacement coordinates")
    if "baseline_reference" in origin:
        _validate_baseline_reference(origin["baseline_reference"], batch_name, path)


def _reject_unknown_keys(
    data: dict[str, Any],
    allowed: set[str],
    batch_name: str,
    field: str,
) -> None:
    unknown = set(data) - allowed
    if unknown:
        _invalid(batch_name, f"{field} has unknown field(s): {_field_list(unknown)}")


def _validate_line_ranges(values: tuple[Any, ...], batch_name: str, field: str) -> None:
    for value in values:
        if type(value) is int:
            if value < 1:
                _invalid(batch_name, f"{field} contains a non-positive line")
            continue
        if not isinstance(value, str):
            _invalid(batch_name, f"{field} contains a non-string range")
        for segment in value.split(","):
            match = _LINE_RANGE_RE.fullmatch(segment)
            if match is None:
                _invalid(batch_name, f"{field} contains invalid range {value!r}")
            end = match.group("end")
            if end is not None and int(end) < int(match.group("start")):
                _invalid(batch_name, f"{field} contains descending range {value!r}")


def _freeze_mapping(values: Mapping[str, Any], batch_name: str) -> Mapping[str, JsonValue]:
    return MappingProxyType({
        key: _freeze_json_value(value, batch_name, key)
        for key, value in values.items()
    })


def _freeze_json_value(value: Any, batch_name: str, field: str) -> JsonValue:
    if value is None or type(value) in (bool, int, str):
        return value
    if isinstance(value, list):
        return tuple(_freeze_json_value(item, batch_name, field) for item in value)
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            _invalid(batch_name, f"{field} contains a non-string object key")
        return _freeze_mapping(value, batch_name)
    _invalid(batch_name, f"{field} contains unsupported value type {type(value).__name__}")


def _validate_json_value(value: Any, batch_name: str, field: str) -> None:
    _freeze_json_value(value, batch_name, field)


def _thaw_mapping(values: Mapping[str, JsonValue]) -> dict[str, Any]:
    return {key: _thaw_json_value(value) for key, value in values.items()}


def _thaw_json_value(value: JsonValue) -> Any:
    if isinstance(value, Mapping):
        return _thaw_mapping(value)
    if isinstance(value, tuple):
        return [_thaw_json_value(item) for item in value]
    return value


def _required_string(
    data: Mapping[str, Any],
    key: str,
    batch_name: str,
    *,
    allow_empty: bool = False,
) -> str:
    value = data[key]
    if not isinstance(value, str) or (not allow_empty and not value):
        _invalid(batch_name, f"'{key}' must be a {'possibly empty ' if allow_empty else ''}string")
    return value


def _optional_string(data: Mapping[str, Any], key: str, batch_name: str) -> str | None:
    value = data[key]
    if value is not None and not isinstance(value, str):
        _invalid(batch_name, f"'{key}' must be a string or null")
    return value


def _required_object_id(data: Mapping[str, Any], key: str, batch_name: str) -> str:
    value = data[key]
    _validate_object_id(value, batch_name, key)
    return value


def _optional_object_id(data: Mapping[str, Any], key: str, batch_name: str) -> str | None:
    value = data[key]
    if value is not None:
        _validate_object_id(value, batch_name, key)
    return value


def _validate_object_id(value: Any, batch_name: str, field: str) -> None:
    _validate_hex_object_id(value, batch_name, field, (object_id_hex_length(),))


def _validate_hex_object_id(
    value: Any,
    batch_name: str,
    field: str,
    lengths: tuple[int, ...],
) -> None:
    if not isinstance(value, str) or len(value) not in lengths:
        _invalid(batch_name, f"'{field}' must be a hexadecimal object ID")
    try:
        int(value, 16)
    except ValueError as error:
        raise BatchMetadataError(
            f"Batch '{batch_name}' metadata field '{field}' is not hexadecimal"
        ) from error


def _field_list(fields: set[str] | frozenset[str]) -> str:
    return ", ".join(repr(field) for field in sorted(fields))


def _invalid(batch_name: str, detail: str) -> None:
    raise BatchMetadataError(f"Batch '{batch_name}' metadata is invalid: {detail}.")
