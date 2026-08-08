"""Strict loading and live validation of rewrite-plan plans."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import NoReturn, cast

from ..exceptions import CommandError
from ..git_paths import terminal_safe_text
from ..i18n import _
from ..utils.file_io import read_required_text_file_contents
from ..utils.strict_json import (
    StrictJsonError,
    loads,
    require_exact_keys,
    require_integer,
    require_list,
    require_object,
    require_string,
)
from .models import (
    CURRENT_HISTORY_PLAN_SCHEMA_VERSION,
    HistoryIdentity,
    HistoryPlan,
    HistoryPlanDocument,
    HistoryPlannedCommit,
    HistoryPlanOperation,
)
from .json_files import history_canonical_json_sha256
from .records import history_snapshot_record
from .scan import acquire_history_plan_document


_TOP_LEVEL_KEYS = frozenset({
    "schema_version",
    "operation",
    "snapshot",
    "safety",
    "plan",
})
_PLAN_KEYS = frozenset({"outputs"})
_OUTPUT_KEYS = frozenset({
    "operation",
    "source_commits",
    "unit_ids",
    "message",
    "encoding",
    "author",
    "rationale",
})
_IDENTITY_KEYS = frozenset({
    "raw",
    "name",
    "email",
    "timestamp",
    "timezone",
})


def _invalid(detail: str) -> NoReturn:
    raise CommandError(
        _("Invalid rewrite plan: {detail}").format(
            detail=terminal_safe_text(detail)
        )
    )


def _object_id_length(object_format: str) -> int:
    if object_format == "sha1":
        return 40
    if object_format == "sha256":
        return 64
    _invalid("snapshot.object_format must be 'sha1' or 'sha256'")


def _require_full_hex_id(value: str, length: int, location: str) -> None:
    if len(value) != length or any(
        character not in "0123456789abcdef" for character in value
    ):
        _invalid(f"{location} must be a full lowercase hexadecimal object ID")


def _string_array(
    value: object,
    location: str,
    *,
    hex_length: int,
) -> tuple[str, ...]:
    values = require_list(value, location)
    result: list[str] = []
    for index, item in enumerate(values):
        if not isinstance(item, str):
            _invalid(f"{location}[{index}] must be a string")
        _require_full_hex_id(item, hex_length, f"{location}[{index}]")
        result.append(item)
    return tuple(result)


def _identity(value: object, location: str) -> HistoryIdentity:
    record = require_object(value, location)
    require_exact_keys(record, _IDENTITY_KEYS, location)
    raw = require_string(record, "raw", location, allow_empty=True)
    name = require_string(record, "name", location, allow_empty=True)
    email = require_string(record, "email", location, allow_empty=True)
    timezone = require_string(record, "timezone", location)
    timestamp = require_integer(record, "timestamp", location)
    return HistoryIdentity(
        raw=raw,
        name=name,
        email=email,
        timestamp=timestamp,
        timezone=timezone,
    )


def _nullable_encoding(value: object, location: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        _invalid(f"{location} must be a non-empty string or null")
    if "\0" in value or "\n" in value or "\r" in value:
        _invalid(f"{location} must not contain NUL or a newline")
    return value


def _planned_commit(
    value: object,
    index: int,
    *,
    oid_length: int,
) -> HistoryPlannedCommit:
    location = f"plan.outputs[{index}]"
    record = require_object(value, location)
    require_exact_keys(record, _OUTPUT_KEYS, location)
    operation_value = require_string(record, "operation", location)
    if operation_value not in {"KEEP", "REWORD"}:
        _invalid(f"{location}.operation must be 'KEEP' or 'REWORD'")
    source_commits = _string_array(
        record["source_commits"],
        f"{location}.source_commits",
        hex_length=oid_length,
    )
    unit_ids = _string_array(
        record["unit_ids"],
        f"{location}.unit_ids",
        hex_length=64,
    )
    message = require_string(record, "message", location, allow_empty=True)
    if "\0" in message:
        _invalid(f"{location}.message must not contain NUL")
    rationale = require_string(
        record,
        "rationale",
        location,
        allow_empty=True,
    )
    encoding = _nullable_encoding(
        record["encoding"],
        f"{location}.encoding",
    )
    if operation_value == "REWORD":
        try:
            message.encode(encoding or "utf-8", errors="surrogateescape")
        except (LookupError, UnicodeEncodeError) as error:
            _invalid(
                f"{location}.message cannot be encoded as "
                f"{encoding or 'UTF-8'} ({error})"
            )
    return HistoryPlannedCommit(
        operation=cast(HistoryPlanOperation, operation_value),
        source_commits=source_commits,
        unit_ids=unit_ids,
        message=message,
        encoding=encoding,
        author=_identity(record["author"], f"{location}.author"),
        rationale=rationale,
    )


def _decode_plan(payload: str) -> tuple[dict[str, object], str, HistoryPlan]:
    try:
        raw = loads(payload)
        document = require_object(raw, "document")
        require_exact_keys(document, _TOP_LEVEL_KEYS, "document")
        version = require_integer(document, "schema_version", "document")
        if version != CURRENT_HISTORY_PLAN_SCHEMA_VERSION:
            _invalid(
                "schema_version must be "
                f"{CURRENT_HISTORY_PLAN_SCHEMA_VERSION}"
            )
        if document["operation"] != "rewrite-plan":
            _invalid("operation must be 'rewrite-plan'")

        snapshot = require_object(document["snapshot"], "snapshot")
        require_object(document["safety"], "safety")
        object_format = require_string(
            snapshot,
            "object_format",
            "snapshot",
        )
        oid_length = _object_id_length(object_format)
        range_record = require_object(snapshot.get("range"), "snapshot.range")
        base = require_string(range_record, "base", "snapshot.range")
        tip = require_string(range_record, "tip", "snapshot.range")
        _require_full_hex_id(base, oid_length, "snapshot.range.base")
        _require_full_hex_id(tip, oid_length, "snapshot.range.tip")

        plan_record = require_object(document["plan"], "plan")
        require_exact_keys(plan_record, _PLAN_KEYS, "plan")
        outputs = tuple(
            _planned_commit(value, index, oid_length=oid_length)
            for index, value in enumerate(
                require_list(plan_record["outputs"], "plan.outputs")
            )
        )
    except StrictJsonError as error:
        _invalid(str(error))
    return snapshot, base, HistoryPlan(outputs=outputs)


def _validate_plan_semantics(
    live: HistoryPlanDocument,
    plan: HistoryPlan,
) -> None:
    source_commits = live.snapshot.commits
    if len(plan.outputs) != len(source_commits):
        _invalid("KEEP/REWORD plans must produce one output per source commit")

    consumed_units: set[str] = set()
    for index, (output, source) in enumerate(
        zip(plan.outputs, source_commits, strict=True)
    ):
        location = f"plan.outputs[{index}]"
        if output.source_commits != (source.commit_id,):
            _invalid(
                f"{location}.source_commits must consume the next source commit"
            )
        expected_units = tuple(unit.unit_id for unit in source.units)
        if output.unit_ids != expected_units:
            _invalid(
                f"{location}.unit_ids must exactly conserve the source units"
            )
        if any(unit_id in consumed_units for unit_id in output.unit_ids):
            _invalid(f"{location}.unit_ids duplicates a consumed unit")
        consumed_units.update(output.unit_ids)
        if output.author != source.author:
            _invalid(f"{location}.author must preserve the source author")
        if output.operation == "KEEP" and output.message != source.message:
            _invalid(f"{location}.message changed without a REWORD operation")
        if output.operation == "KEEP" and output.encoding != source.encoding:
            _invalid(f"{location}.encoding changed without a REWORD operation")


def read_and_validate_history_plan(plan_path: str) -> HistoryPlanDocument:
    """Regenerate immutable facts and validate the editable semantic plan."""
    path = Path(plan_path)
    try:
        payload = read_required_text_file_contents(path)
    except (OSError, ValueError) as error:
        raise CommandError(
            _("Could not read rewrite plan {path}: {error}").format(
                path=terminal_safe_text(str(path)),
                error=terminal_safe_text(str(error)),
            )
        ) from error

    frozen_snapshot, base_commit, plan = _decode_plan(payload)
    live = acquire_history_plan_document(base_commit)
    try:
        frozen_digest = history_canonical_json_sha256(frozen_snapshot)
        live_digest = history_canonical_json_sha256(
            history_snapshot_record(live.snapshot)
        )
    except (RecursionError, TypeError, ValueError) as error:
        _invalid(f"snapshot cannot be canonicalized ({error})")
    if frozen_digest != live_digest:
        _invalid(
            "the immutable range, commit metadata, or patch units changed; "
            "generate a new scan"
        )
    _validate_plan_semantics(live, plan)
    return replace(live, plan=plan)
