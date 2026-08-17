"""Versioned batch metadata parsing and canonical serialization."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from itertools import chain
from types import MappingProxyType
from typing import Any, NoReturn, TypeAlias, cast

from ...exceptions import BatchMetadataError
from ...i18n import _, ngettext
from ...utils.git_repository import object_id_hex_length
from ..ownership.claims import parse_ownership_line_ranges
from .metadata_types import (
    BatchFileMetadataDict,
    BatchMetadataDict,
    BatchStorageMetadataDict,
)


CURRENT_BATCH_METADATA_SCHEMA_VERSION = 2

JsonScalar: TypeAlias = None | bool | int | str
JsonValue: TypeAlias = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]

_LINE_RANGE_RE = re.compile(r"^(?P<start>[1-9][0-9]*)(?:-(?P<end>[1-9][0-9]*))?$")
_FILE_METADATA_KEYS = frozenset(
    {
        "batch_source_commit",
        "change_type",
        "claimed_lines",
        "deletions",
        "file_type",
        "legacy_unmarked_source_alternatives",
        "mode",
        "new_mode",
        "new_oid",
        "old_mode",
        "old_oid",
        "presence_claims",
        "replacement_masks",
        "replacement_units",
        "source_path",
    }
)
_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "revision",
        "batch",
        "note",
        "created_at",
        "baseline",
        "content_ref",
        "content_commit",
        "files",
    }
)
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

    def to_dict(self) -> BatchFileMetadataDict:
        return cast(BatchFileMetadataDict, _thaw_mapping(self.values))

    @property
    def batch_source_commit(self) -> str | None:
        """Return the validated source commit, when this entry has one."""
        return cast(str | None, self.values.get("batch_source_commit"))

    @property
    def mode(self) -> str:
        """Return the validated Git mode used for stored source content."""
        mode = self.values.get("mode")
        return mode if isinstance(mode, str) else "100644"

    def with_source_path(self) -> BatchFileMetadata:
        """Return this entry with its canonical state-tree source path."""
        return replace(
            self,
            values=MappingProxyType(
                {
                    **self.values,
                    "source_path": f"sources/{self.path}",
                }
            ),
        )


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

    def to_application_dict(self) -> BatchMetadataDict:
        """Return the compatibility mapping used by current domain code."""
        return {
            "revision": self.revision,
            "note": self.note,
            "created_at": self.created_at,
            "baseline": self.baseline,
            "files": {entry.path: entry.to_dict() for entry in self.files},
        }

    def to_storage_dict(self) -> BatchStorageMetadataDict:
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

    def for_publication(
        self,
        *,
        content_ref: str,
        content_commit: str,
        source_paths: Iterable[str],
    ) -> BatchMetadata:
        """Derive a new canonical state-ref model from validated metadata."""
        if not content_ref:
            _invalid(self.batch, _("'content_ref' must be a non-empty string"))
        _validate_object_id(content_commit, self.batch, "content_commit")

        source_path_set = frozenset(source_paths)
        file_paths = {entry.path for entry in self.files}
        unknown_source_paths = source_path_set - file_paths
        if unknown_source_paths:
            _invalid(
                self.batch,
                ngettext(
                    "source snapshot references an unknown file: {files}",
                    "source snapshots reference unknown files: {files}",
                    len(unknown_source_paths),
                ).format(files=_field_list(unknown_source_paths)),
            )

        return replace(
            self,
            revision=new_batch_metadata_revision(),
            files=tuple(
                entry.with_source_path() if entry.path in source_path_set else entry
                for entry in self.files
            ),
            content_ref=content_ref,
            content_commit=content_commit,
        )


def new_batch_metadata_revision() -> str:
    """Return an opaque revision identifier for stale-writer detection."""
    return str(uuid.uuid4())


def decode_batch_metadata(
    payload: str | bytes | Mapping[str, Any],
    *,
    expected_batch: str,
) -> BatchMetadata:
    """Decode supported metadata and return a validated immutable model."""
    data = _load_json_object(payload, expected_batch)
    version = data.get("schema_version", 0)
    if type(version) is not int:
        _invalid(expected_batch, _("'schema_version' must be an integer"))
    if version > CURRENT_BATCH_METADATA_SCHEMA_VERSION:
        raise BatchMetadataError(
            _(
                "Batch '{name}' uses metadata schema version {version}, but this "
                "version of git-stage-batch supports through version {supported}. "
                "Upgrade git-stage-batch or use a compatible version; the metadata "
                "was not modified."
            ).format(
                name=expected_batch,
                version=version,
                supported=CURRENT_BATCH_METADATA_SCHEMA_VERSION,
            )
        )
    if version < 0:
        _invalid(expected_batch, _("'schema_version' cannot be negative"))
    migrated_from_v0 = version == 0
    if version == 0:
        data = _migrate_v0_to_v1(data, expected_batch)
        version = 1
    if version == 1:
        data = _migrate_v1_to_v2(data)
    elif version != CURRENT_BATCH_METADATA_SCHEMA_VERSION:
        _invalid(
            expected_batch,
            _("unsupported metadata schema version {version}").format(version=version),
        )
    return _decode_current(data, expected_batch, allow_legacy=migrated_from_v0)


def encode_batch_metadata(metadata: BatchMetadata) -> str:
    """Serialize current metadata deterministically."""
    return (
        json.dumps(
            metadata.to_storage_dict(),
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )


def metadata_from_application_dict(
    batch_name: str,
    data: BatchMetadataDict,
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
    return _decode_current(storage, batch_name)


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
                _("Batch '{name}' metadata is not valid JSON: {error}").format(
                    name=batch_name,
                    error=error,
                )
            ) from error
    if not isinstance(data, dict):
        _invalid(batch_name, _("top-level metadata must be an object"))
    return data


def _migrate_v0_to_v1(data: dict[str, Any], batch_name: str) -> dict[str, Any]:
    """Pure, deterministic migration from the historical unversioned shape."""
    canonical_legacy = json.dumps(data, sort_keys=True, separators=(",", ":"))
    revision = data.get("revision")
    if revision is None:
        revision = "v0-" + hashlib.sha256(canonical_legacy.encode("utf-8")).hexdigest()
    return {
        "schema_version": 1,
        "revision": revision,
        "batch": data.get("batch", batch_name),
        "note": data.get("note", ""),
        "created_at": data.get("created_at", ""),
        "baseline": data.get("baseline_commit", data.get("baseline")),
        "content_ref": data.get("content_ref"),
        "content_commit": data.get("content_commit"),
        "files": data.get("files", {}),
    }


def _migrate_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """Mark text ownership whose source-alternative intent was not recorded."""
    files = data.get("files")
    if not isinstance(files, dict):
        return {**data, "schema_version": 2}

    migrated_files: dict[str, Any] = {}
    for path, values in files.items():
        if (
            not isinstance(values, dict)
            or values.get("file_type") is not None
            or not any(
                field in values
                for field in (
                    "claimed_lines",
                    "presence_claims",
                    "deletions",
                    "replacement_units",
                )
            )
        ):
            migrated_files[path] = values
            continue
        migrated_files[path] = {
            **values,
            "legacy_unmarked_source_alternatives": True,
        }
    return {
        **data,
        "schema_version": 2,
        "files": migrated_files,
    }


def _decode_current(
    data: dict[str, Any],
    expected_batch: str,
    *,
    allow_legacy: bool = False,
) -> BatchMetadata:
    unknown_keys = set(data) - _TOP_LEVEL_KEYS
    missing_keys = _TOP_LEVEL_KEYS - set(data)
    if unknown_keys:
        _invalid(
            expected_batch,
            ngettext(
                "unknown top-level field: {fields}",
                "unknown top-level fields: {fields}",
                len(unknown_keys),
            ).format(fields=_field_list(unknown_keys)),
        )
    if missing_keys:
        _invalid(
            expected_batch,
            ngettext(
                "missing required field: {fields}",
                "missing required fields: {fields}",
                len(missing_keys),
            ).format(fields=_field_list(missing_keys)),
        )
    if data["schema_version"] != CURRENT_BATCH_METADATA_SCHEMA_VERSION:
        _invalid(
            expected_batch,
            _("metadata was not migrated to the current schema"),
        )

    revision = _required_string(data, "revision", expected_batch)
    batch = _required_string(data, "batch", expected_batch)
    if batch != expected_batch:
        _invalid(
            expected_batch,
            _("metadata identifies batch '{name}'").format(name=batch),
        )
    note = _required_string(data, "note", expected_batch, allow_empty=True)
    created_at = _required_string(data, "created_at", expected_batch, allow_empty=True)
    if created_at:
        try:
            datetime.fromisoformat(
                created_at[:-1] + "+00:00" if created_at.endswith("Z") else created_at
            )
        except ValueError as error:
            raise BatchMetadataError(
                _(
                    "Batch '{name}' metadata field 'created_at' is not an ISO-8601 "
                    "timestamp"
                ).format(name=expected_batch)
            ) from error

    baseline = _optional_object_id(data, "baseline", expected_batch)
    content_ref = _optional_string(data, "content_ref", expected_batch)
    content_commit = _optional_object_id(data, "content_commit", expected_batch)
    files_data = data["files"]
    if not isinstance(files_data, dict):
        _invalid(expected_batch, _("'files' must be an object"))

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
        _invalid(
            batch_name,
            _("file metadata path must be a non-empty string without NUL"),
        )
    path_parts = path.split("/")
    if path.startswith("/") or any(part in ("", ".", "..") for part in path_parts):
        _invalid(
            batch_name,
            _("file metadata path is not repository-relative: {path!r}").format(
                path=path
            ),
        )
    if not isinstance(values, dict):
        _invalid(
            batch_name,
            _("file entry for {path!r} must be an object").format(path=path),
        )
    unknown_keys = set(values) - _FILE_METADATA_KEYS
    if unknown_keys:
        _invalid(
            batch_name,
            ngettext(
                "file entry for {path!r} has an unknown field: {fields}",
                "file entry for {path!r} has unknown fields: {fields}",
                len(unknown_keys),
            ).format(path=path, fields=_field_list(unknown_keys)),
        )

    file_type = values.get("file_type")
    if file_type is not None and file_type not in {
        item.value for item in BatchFileType
    }:
        _invalid(
            batch_name,
            _("file entry for {path!r} has invalid file_type").format(path=path),
        )
    change_type = values.get("change_type")
    if change_type is not None and change_type not in {
        item.value for item in BatchChangeType
    }:
        _invalid(
            batch_name,
            _("file entry for {path!r} has invalid change_type").format(path=path),
        )
    for key in ("mode", "old_mode", "new_mode"):
        value = values.get(key)
        if value is not None and value not in _GIT_FILE_MODES:
            _invalid(
                batch_name,
                _("file entry for {path!r} has invalid {field}").format(
                    path=path,
                    field=key,
                ),
            )
    if (
        "legacy_unmarked_source_alternatives" in values
        and type(values["legacy_unmarked_source_alternatives"]) is not bool
    ):
        _invalid(
            batch_name,
            _(
                "file entry for {path!r} has an invalid legacy source-alternative flag"
            ).format(path=path),
        )
    if "legacy_unmarked_source_alternatives" in values and file_type is not None:
        _invalid(
            batch_name,
            _(
                "non-text file entry for {path!r} has a legacy "
                "source-alternative marker"
            ).format(path=path),
        )
    for key in ("batch_source_commit",):
        if key in values:
            _validate_object_id(values[key], batch_name, f"files[{path!r}].{key}")
    for key in ("old_oid", "new_oid"):
        value = values.get(key)
        if value is not None:
            _validate_hex_object_id(
                value, batch_name, f"files[{path!r}].{key}", (40, 64)
            )
    if "source_path" in values and values["source_path"] != f"sources/{path}":
        _invalid(
            batch_name,
            _("file entry for {path!r} has inconsistent source_path").format(path=path),
        )
    if file_type in (None, BatchFileType.BINARY.value, BatchFileType.MODE.value):
        if "batch_source_commit" not in values:
            if not allow_legacy or file_type not in {
                BatchFileType.BINARY.value,
                BatchFileType.GITLINK.value,
            }:
                _invalid(
                    batch_name,
                    _(
                        "file entry for {path!r} is missing 'batch_source_commit'"
                    ).format(path=path),
                )
    if file_type == BatchFileType.GITLINK.value and values.get("mode") != "160000":
        _invalid(
            batch_name,
            _("gitlink entry for {path!r} must use mode 160000").format(path=path),
        )
    if file_type == BatchFileType.MODE.value:
        if not {"old_mode", "new_mode", "mode"} <= set(values):
            _invalid(
                batch_name,
                _("mode entry for {path!r} is missing mode fields").format(path=path),
            )
        if (
            values["mode"] != values["new_mode"]
            or values["old_mode"] == values["new_mode"]
        ):
            _invalid(
                batch_name,
                _("mode entry for {path!r} has inconsistent transition").format(
                    path=path
                ),
            )

    _validate_claims(values, path, batch_name)
    return BatchFileMetadata(path=path, values=_freeze_mapping(values, batch_name))


def _validate_claims(values: dict[str, Any], path: str, batch_name: str) -> None:
    for key in ("presence_claims", "deletions", "replacement_units", "claimed_lines"):
        if key in values and not isinstance(values[key], list):
            _invalid(
                batch_name,
                _("files[{path!r}].{field} must be an array").format(
                    path=path,
                    field=key,
                ),
            )
    if "claimed_lines" in values:
        _validate_line_ranges(
            values["claimed_lines"],
            batch_name,
            f"files[{path!r}].claimed_lines",
        )
    seen_presence: set[tuple[tuple[int, int], ...]] = set()
    for claim in values.get("presence_claims", []):
        if not isinstance(claim, dict) or not isinstance(
            claim.get("source_lines"), list
        ):
            _invalid(
                batch_name,
                _("files[{path!r}].presence_claims has an invalid claim").format(
                    path=path
                ),
            )
        _reject_unknown_keys(
            claim,
            {"source_lines", "baseline_references"},
            batch_name,
            f"files[{path!r}].presence_claims",
        )
        range_specs = claim["source_lines"]
        _validate_line_ranges(
            range_specs,
            batch_name,
            f"files[{path!r}].presence_claims",
        )
        canonical_ranges = parse_ownership_line_ranges(range_specs).ranges()
        if canonical_ranges in seen_presence:
            _invalid(
                batch_name,
                _("files[{path!r}] has duplicate presence claims").format(path=path),
            )
        seen_presence.add(canonical_ranges)
        references = claim.get("baseline_references", {})
        if not isinstance(references, dict):
            _invalid(
                batch_name,
                _("files[{path!r}] has invalid baseline_references").format(path=path),
            )
        for line, reference in references.items():
            if not isinstance(line, str) or not line.isdigit() or int(line) < 1:
                _invalid(
                    batch_name,
                    _("files[{path!r}] has invalid baseline reference line").format(
                        path=path
                    ),
                )
            _validate_baseline_reference(reference, batch_name, path)
    source_alternative_indices: set[int] = set()
    for deletion_index, deletion in enumerate(values.get("deletions", [])):
        if not isinstance(deletion, dict):
            _invalid(
                batch_name,
                _("files[{path!r}].deletions has a non-object entry").format(path=path),
            )
        _reject_unknown_keys(
            deletion,
            {
                "after_source_line",
                "blob",
                "baseline_reference",
                "source_alternative",
            },
            batch_name,
            f"files[{path!r}].deletions",
        )
        anchor = deletion.get("after_source_line")
        if anchor is not None and (type(anchor) is not int or anchor < 1):
            _invalid(
                batch_name,
                _("files[{path!r}] has an invalid deletion anchor").format(path=path),
            )
        _validate_object_id(
            deletion.get("blob"), batch_name, f"files[{path!r}].deletions.blob"
        )
        if (
            "source_alternative" in deletion
            and type(deletion["source_alternative"]) is not bool
        ):
            _invalid(
                batch_name,
                _("files[{path!r}] has an invalid source-alternative flag").format(
                    path=path
                ),
            )
        if "baseline_reference" in deletion:
            _validate_baseline_reference(
                deletion["baseline_reference"], batch_name, path
            )
        if deletion.get("source_alternative") is True:
            baseline_reference = deletion.get("baseline_reference")
            if not isinstance(baseline_reference, dict) or not {
                "after_line",
                "before_line",
            }.intersection(baseline_reference):
                _invalid(
                    batch_name,
                    _(
                        "files[{path!r}] has a source alternative without "
                        "a live boundary"
                    ).format(path=path),
                )
            source_alternative_indices.add(deletion_index)
    deletion_count = len(values.get("deletions", []))
    owned_presence = parse_ownership_line_ranges(
        line_range
        for source_lines in chain(
            (values.get("claimed_lines", []),),
            (claim["source_lines"] for claim in values.get("presence_claims", [])),
        )
        for line_range in source_lines
    )
    coupled_source_alternatives: set[int] = set()
    for replacement in values.get("replacement_units", []):
        if not isinstance(replacement, dict):
            _invalid(
                batch_name,
                _("files[{path!r}].replacement_units has a non-object entry").format(
                    path=path
                ),
            )
        _reject_unknown_keys(
            replacement,
            {"presence_lines", "claimed_lines", "deletion_indices", "original_unit"},
            batch_name,
            f"files[{path!r}].replacement_units",
        )
        presence_lines = replacement.get(
            "presence_lines", replacement.get("claimed_lines")
        )
        deletion_indices = replacement.get("deletion_indices")
        if not isinstance(presence_lines, list) or not isinstance(
            deletion_indices, list
        ):
            _invalid(
                batch_name,
                _("files[{path!r}] has an invalid replacement unit").format(path=path),
            )
        _validate_line_ranges(
            presence_lines,
            batch_name,
            f"files[{path!r}].replacement_units.presence_lines",
        )
        if any(
            type(index) is not int or not 0 <= index < deletion_count
            for index in deletion_indices
        ) or len(set(deletion_indices)) != len(deletion_indices):
            _invalid(
                batch_name,
                _("files[{path!r}] has invalid replacement deletion indices").format(
                    path=path
                ),
            )
        replacement_presence = parse_ownership_line_ranges(presence_lines)
        if replacement_presence and all(
            owned_presence.contains_range(start, end)
            for start, end in replacement_presence.ranges()
        ):
            coupled_source_alternatives.update(
                source_alternative_indices.intersection(deletion_indices)
            )
        if "original_unit" in replacement:
            _validate_replacement_origin(replacement["original_unit"], batch_name, path)
    if source_alternative_indices - coupled_source_alternatives:
        _invalid(
            batch_name,
            _(
                "files[{path!r}] has a source alternative without an owned "
                "replacement side"
            ).format(path=path),
        )
    _validate_json_value(values, batch_name, f"files[{path!r}]")


def _validate_baseline_reference(reference: Any, batch_name: str, path: str) -> None:
    if not isinstance(reference, dict):
        _invalid(
            batch_name,
            _("files[{path!r}] has a non-object baseline reference").format(path=path),
        )
    _reject_unknown_keys(
        reference,
        {"after_line", "after_blob", "before_line", "before_blob"},
        batch_name,
        f"files[{path!r}].baseline_reference",
    )
    for key in ("after_line", "before_line"):
        value = reference.get(key)
        if value is not None and (type(value) is not int or value < 1):
            _invalid(
                batch_name,
                _("files[{path!r}] has invalid {field}").format(
                    path=path,
                    field=key,
                ),
            )
    for key in ("after_blob", "before_blob"):
        if key in reference:
            _validate_object_id(reference[key], batch_name, f"files[{path!r}].{key}")


def _validate_replacement_origin(origin: Any, batch_name: str, path: str) -> None:
    if not isinstance(origin, dict):
        _invalid(
            batch_name,
            _("files[{path!r}] has a non-object replacement origin").format(path=path),
        )
    required = {"old_start", "old_end", "new_start", "new_end"}
    _reject_unknown_keys(
        origin,
        required | {"baseline_reference"},
        batch_name,
        f"files[{path!r}].replacement_units.original_unit",
    )
    if not required <= set(origin):
        _invalid(
            batch_name,
            _("files[{path!r}] has an incomplete replacement origin").format(path=path),
        )
    for key in required:
        if type(origin[key]) is not int or origin[key] < 1:
            _invalid(
                batch_name,
                _("files[{path!r}] has invalid replacement {field}").format(
                    path=path,
                    field=key,
                ),
            )
    if (
        origin["old_end"] < origin["old_start"]
        or origin["new_end"] < origin["new_start"]
    ):
        _invalid(
            batch_name,
            _("files[{path!r}] has descending replacement coordinates").format(
                path=path
            ),
        )
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
        _invalid(
            batch_name,
            ngettext(
                "{field} has an unknown field: {fields}",
                "{field} has unknown fields: {fields}",
                len(unknown),
            ).format(field=field, fields=_field_list(unknown)),
        )


def _validate_line_ranges(
    values: Sequence[Any],
    batch_name: str,
    field: str,
) -> None:
    for value in values:
        if type(value) is int:
            if value < 1:
                _invalid(
                    batch_name,
                    _("{field} contains a non-positive line").format(field=field),
                )
            continue
        if not isinstance(value, str):
            _invalid(
                batch_name,
                _("{field} contains a non-string range").format(field=field),
            )
        for segment in value.split(","):
            match = _LINE_RANGE_RE.fullmatch(segment)
            if match is None:
                _invalid(
                    batch_name,
                    _("{field} contains invalid range {value!r}").format(
                        field=field,
                        value=value,
                    ),
                )
            end = match.group("end")
            if end is not None and int(end) < int(match.group("start")):
                _invalid(
                    batch_name,
                    _("{field} contains descending range {value!r}").format(
                        field=field,
                        value=value,
                    ),
                )


def _freeze_mapping(
    values: Mapping[str, Any], batch_name: str
) -> Mapping[str, JsonValue]:
    return MappingProxyType(
        {
            key: _freeze_json_value(value, batch_name, key)
            for key, value in values.items()
        }
    )


def _freeze_json_value(value: Any, batch_name: str, field: str) -> JsonValue:
    if value is None or type(value) in (bool, int, str):
        return cast(JsonScalar, value)
    if isinstance(value, list):
        return tuple(_freeze_json_value(item, batch_name, field) for item in value)
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            _invalid(
                batch_name,
                _("{field} contains a non-string object key").format(field=field),
            )
        return _freeze_mapping(value, batch_name)
    _invalid(
        batch_name,
        _("{field} contains unsupported value type {type}").format(
            field=field,
            type=type(value).__name__,
        ),
    )


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
        detail = (
            _("'{field}' must be a possibly empty string")
            if allow_empty
            else _("'{field}' must be a non-empty string")
        )
        _invalid(batch_name, detail.format(field=key))
    return value


def _optional_string(data: Mapping[str, Any], key: str, batch_name: str) -> str | None:
    value = data[key]
    if value is not None and not isinstance(value, str):
        _invalid(
            batch_name,
            _("'{field}' must be a string or null").format(field=key),
        )
    return value


def _optional_object_id(
    data: Mapping[str, Any], key: str, batch_name: str
) -> str | None:
    value = data[key]
    if value is not None:
        _validate_object_id(value, batch_name, key)
    return cast(str | None, value)


def _validate_object_id(value: Any, batch_name: str, field: str) -> None:
    _validate_hex_object_id(value, batch_name, field, (object_id_hex_length(),))


def _validate_hex_object_id(
    value: Any,
    batch_name: str,
    field: str,
    lengths: tuple[int, ...],
) -> None:
    if not isinstance(value, str) or len(value) not in lengths:
        _invalid(
            batch_name,
            _("'{field}' must be a hexadecimal object ID").format(field=field),
        )
    if any(character not in "0123456789abcdefABCDEF" for character in value):
        raise BatchMetadataError(
            _("Batch '{name}' metadata field '{field}' is not hexadecimal").format(
                name=batch_name, field=field
            )
        )


def _field_list(fields: set[str] | frozenset[str]) -> str:
    return ", ".join(repr(field) for field in sorted(fields))


def _invalid(batch_name: str, detail: str) -> NoReturn:
    raise BatchMetadataError(
        _("Batch '{name}' metadata is invalid: {detail}.").format(
            name=batch_name,
            detail=detail,
        )
    )
