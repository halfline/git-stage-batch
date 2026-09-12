"""Strict loading and live validation of rewrite-plan plans."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import NoReturn, cast

from ..exceptions import CommandError
from ..git_paths import terminal_safe_text
from ..i18n import _
from ..utils.file_io import (
    read_required_text_file_contents,
    read_required_text_file_contents_and_sha256,
)
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
    HISTORY_PLAN_MATERIALIZATIONS,
    HISTORY_PLAN_OPERATIONS,
    HistoryIdentity,
    HistoryPartitionedUnit,
    HistoryPlan,
    HistoryPlanDocument,
    HistoryPlanMaterialization,
    HistoryPlannedCommit,
)
from .plan_diagnostics import HistoryPlanLint
from .plan_semantics import validate_plan_semantics
from .plan_lint import (
    lint_frozen_history_plan,
)
from .json_files import history_canonical_json_sha256
from .records import history_snapshot_record
from .replay import validate_history_plan_materialization
from .safety import collect_history_safety_facts
from .scan import acquire_frozen_history_snapshot, acquire_history_plan_document
from .snapshot_cache import (
    HistorySnapshotCacheObservation,
    decode_history_snapshot_record,
)


_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "operation",
        "snapshot",
        "safety",
        "plan",
    }
)
_PLAN_KEYS = frozenset({"outputs", "partitioned_units"})
_OUTPUT_KEYS = frozenset(
    {
        "operation",
        "materialization",
        "source_commits",
        "source_unit_ids",
        "message",
        "encoding",
        "author",
        "rationale",
    }
)
_LEGACY_OUTPUT_KEYS = frozenset(
    {
        "operation",
        "source_commits",
        "unit_ids",
        "message",
        "encoding",
        "author",
        "rationale",
    }
)
_PARTITIONED_UNIT_KEYS = frozenset({"unit_id", "output_indexes"})
_IDENTITY_KEYS = frozenset(
    {
        "raw",
        "name",
        "email",
        "timestamp",
        "timezone",
    }
)


def _invalid(detail: str) -> NoReturn:
    raise CommandError(
        _("Invalid rewrite plan: {detail}").format(detail=terminal_safe_text(detail))
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


def _output_index_array(value: object, location: str) -> tuple[int, ...]:
    values = require_list(value, location)
    result: list[int] = []
    for index, item in enumerate(values):
        if type(item) is not int:
            _invalid(f"{location}[{index}] must be an integer")
        if item < 0:
            _invalid(f"{location}[{index}] must not be negative")
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
    legacy_v3: bool,
) -> HistoryPlannedCommit:
    location = f"plan.outputs[{index}]"
    record = require_object(value, location)
    require_exact_keys(
        record,
        _LEGACY_OUTPUT_KEYS if legacy_v3 else _OUTPUT_KEYS,
        location,
    )
    operation_value = require_string(record, "operation", location)
    if operation_value not in HISTORY_PLAN_OPERATIONS:
        _invalid(
            f"{location}.operation must be 'KEEP', 'REWORD', 'INTEGRATE', "
            "'SPLIT', or 'REORDER'"
        )
    if legacy_v3:
        materialization_value = "EXACT"
        source_unit_field = "unit_ids"
    else:
        materialization_value = require_string(record, "materialization", location)
        if materialization_value not in HISTORY_PLAN_MATERIALIZATIONS:
            _invalid(f"{location}.materialization must be 'EXACT' or 'RESOLVED'")
        source_unit_field = "source_unit_ids"
    source_commits = _string_array(
        record["source_commits"],
        f"{location}.source_commits",
        hex_length=oid_length,
    )
    source_unit_ids = _string_array(
        record[source_unit_field],
        f"{location}.{source_unit_field}",
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
    if operation_value in {"REWORD", "INTEGRATE", "SPLIT"}:
        try:
            message.encode(encoding or "utf-8", errors="surrogateescape")
        except (LookupError, UnicodeEncodeError) as error:
            _invalid(
                f"{location}.message cannot be encoded as "
                f"{encoding or 'UTF-8'} ({error})"
            )
    return HistoryPlannedCommit(
        operation=operation_value,
        materialization=cast(
            HistoryPlanMaterialization,
            materialization_value,
        ),
        source_commits=source_commits,
        source_unit_ids=source_unit_ids,
        message=message,
        encoding=encoding,
        author=_identity(record["author"], f"{location}.author"),
        rationale=rationale,
    )


def _partitioned_unit(value: object, index: int) -> HistoryPartitionedUnit:
    location = f"plan.partitioned_units[{index}]"
    record = require_object(value, location)
    require_exact_keys(record, _PARTITIONED_UNIT_KEYS, location)
    unit_id = require_string(record, "unit_id", location)
    _require_full_hex_id(unit_id, 64, f"{location}.unit_id")
    return HistoryPartitionedUnit(
        unit_id=unit_id,
        output_indexes=_output_index_array(
            record["output_indexes"],
            f"{location}.output_indexes",
        ),
    )


def _decode_plan(
    payload: str,
    *,
    allow_legacy_v3: bool = False,
) -> tuple[dict[str, object], str, str, HistoryPlan]:
    try:
        raw = loads(payload)
        document = require_object(raw, "document")
        require_exact_keys(document, _TOP_LEVEL_KEYS, "document")
        version = require_integer(document, "schema_version", "document")
        legacy_v3 = version == 3 and allow_legacy_v3
        if version != CURRENT_HISTORY_PLAN_SCHEMA_VERSION and not legacy_v3:
            _invalid(f"schema_version must be {CURRENT_HISTORY_PLAN_SCHEMA_VERSION}")
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
        movable_base = require_string(
            range_record, "movable_base", "snapshot.range"
        )
        _require_full_hex_id(base, oid_length, "snapshot.range.base")
        _require_full_hex_id(tip, oid_length, "snapshot.range.tip")
        _require_full_hex_id(
            movable_base, oid_length, "snapshot.range.movable_base"
        )

        plan_record = require_object(document["plan"], "plan")
        partitioned_units: tuple[HistoryPartitionedUnit, ...]
        if legacy_v3:
            require_exact_keys(plan_record, frozenset({"outputs"}), "plan")
            partitioned_units = ()
        else:
            require_exact_keys(plan_record, _PLAN_KEYS, "plan")
            partitioned_units = tuple(
                _partitioned_unit(value, index)
                for index, value in enumerate(
                    require_list(
                        plan_record["partitioned_units"],
                        "plan.partitioned_units",
                    )
                )
            )
        outputs = tuple(
            _planned_commit(
                value,
                index,
                oid_length=oid_length,
                legacy_v3=legacy_v3,
            )
            for index, value in enumerate(
                require_list(plan_record["outputs"], "plan.outputs")
            )
        )
    except StrictJsonError as error:
        _invalid(str(error))
    return (
        snapshot,
        base,
        movable_base,
        HistoryPlan(
            partitioned_units=partitioned_units,
            outputs=outputs,
        ),
    )


def decode_frozen_history_plan_payload(
    payload: str,
) -> tuple[dict[str, object], str, str, HistoryPlan]:
    """Strictly decode persisted plan declarations without Git reads."""
    return _decode_plan(payload, allow_legacy_v3=True)


def read_and_lint_frozen_history_plan(plan_path: str) -> HistoryPlanLint:
    """Advisory-check a persisted plan without repository reads or replay."""
    payload = _read_plan_payload(plan_path)
    frozen_snapshot, _base_commit, _movable_base, plan = _decode_plan(payload)
    snapshot = decode_history_snapshot_record(frozen_snapshot)
    return lint_frozen_history_plan(snapshot, plan)


def _require_static_plan_lint(
    frozen_snapshot: dict[str, object],
    plan: HistoryPlan,
) -> None:
    result = lint_frozen_history_plan(
        decode_history_snapshot_record(frozen_snapshot),
        plan,
    )
    _require_plan_lint_result(result)


def _require_plan_lint_result(result: HistoryPlanLint) -> None:
    """Raise one aggregate command error when advisory lint found failures."""
    if result.valid:
        return
    lines = [
        _("Invalid rewrite plan: static lint found {count} error(s):").format(
            count=len(result.diagnostics)
        )
    ]
    lines.extend(
        f"- [{diagnostic.code}] {diagnostic.location}: {diagnostic.message}"
        for diagnostic in result.diagnostics
    )
    raise CommandError("\n".join(lines))


def require_frozen_history_plan_workspace(
    plan_path: str,
    workspace_path: str | None,
) -> HistoryPlanLint:
    """Preflight plan validity and workspace presence without reading Git."""
    result = read_and_lint_frozen_history_plan(plan_path)
    _require_plan_lint_result(result)
    resolved_indexes = tuple(
        index
        for index, output in enumerate(result.plan.outputs)
        if output.materialization == "RESOLVED"
    )
    if workspace_path is None and resolved_indexes:
        raise CommandError(
            _(
                "Rewrite output {output} requires an explicit resolution workspace."
            ).format(output=resolved_indexes[0] + 1)
        )
    if workspace_path is not None and not resolved_indexes:
        raise CommandError(_("plan does not contain any RESOLVED outputs"))
    return result


def _read_plan_payload(plan_path: str) -> str:
    path = Path(plan_path)
    try:
        return read_required_text_file_contents(path)
    except (OSError, ValueError) as error:
        raise CommandError(
            _("Could not read rewrite plan {path}: {error}").format(
                path=terminal_safe_text(str(path)),
                error=terminal_safe_text(str(error)),
            )
        ) from error


def _read_plan_payload_and_sha256(plan_path: str) -> tuple[str, str]:
    path = Path(plan_path)
    try:
        return read_required_text_file_contents_and_sha256(path)
    except (OSError, ValueError) as error:
        raise CommandError(
            _("Could not read rewrite plan {path}: {error}").format(
                path=terminal_safe_text(str(path)),
                error=terminal_safe_text(str(error)),
            )
        ) from error


def _semantically_validated_document(
    frozen_snapshot: dict[str, object],
    live: HistoryPlanDocument,
    plan: HistoryPlan,
) -> HistoryPlanDocument:
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
    validate_plan_semantics(live.snapshot, plan, _invalid)
    return replace(live, plan=plan)


def _validated_document(
    frozen_snapshot: dict[str, object],
    live: HistoryPlanDocument,
    plan: HistoryPlan,
) -> HistoryPlanDocument:
    document = _semantically_validated_document(frozen_snapshot, live, plan)
    validate_history_plan_materialization(document)
    return document


def read_and_validate_history_plan(
    plan_path: str,
    *,
    allowed_remote_refs: tuple[str, ...] = (),
    cache_observer: Callable[[HistorySnapshotCacheObservation], None] | None = None,
) -> HistoryPlanDocument:
    """Reacquire immutable facts and validate the editable semantic plan."""
    payload = _read_plan_payload(plan_path)
    frozen_snapshot, base_commit, movable_base, plan = _decode_plan(payload)
    _require_static_plan_lint(frozen_snapshot, plan)
    live = acquire_history_plan_document(
        movable_base,
        onto_boundary=base_commit,
        allowed_remote_refs=allowed_remote_refs,
        cache_observer=cache_observer,
    )
    return _validated_document(frozen_snapshot, live, plan)


def read_and_validate_history_plan_semantics(
    plan_path: str,
    *,
    allowed_remote_refs: tuple[str, ...] = (),
    cache_observer: Callable[[HistorySnapshotCacheObservation], None] | None = None,
) -> tuple[HistoryPlanDocument, str]:
    """Validate plan semantics and return its same-read exact SHA-256."""
    payload, plan_sha256 = _read_plan_payload_and_sha256(plan_path)
    frozen_snapshot, base_commit, movable_base, plan = _decode_plan(payload)
    _require_static_plan_lint(frozen_snapshot, plan)
    live = acquire_history_plan_document(
        movable_base,
        onto_boundary=base_commit,
        allowed_remote_refs=allowed_remote_refs,
        cache_observer=cache_observer,
    )
    return (
        _semantically_validated_document(frozen_snapshot, live, plan),
        plan_sha256,
    )


def read_and_validate_frozen_history_plan_semantics_from_payload(
    payload: str,
    *,
    base_commit: str,
    tip_commit: str,
    branch_ref: str,
    allowed_remote_refs: tuple[str, ...],
) -> HistoryPlanDocument:
    """Validate one captured persisted plan against its frozen source objects."""
    frozen_snapshot, document_base, movable_base, plan = _decode_plan(
        payload,
        allow_legacy_v3=True,
    )
    _require_static_plan_lint(frozen_snapshot, plan)
    if document_base != base_commit:
        _invalid("snapshot.range.base does not match operation state")
    live_snapshot = acquire_frozen_history_snapshot(
        base_commit,
        tip_commit,
        branch_ref,
        movable_base=movable_base,
    )
    safety = collect_history_safety_facts(
        tip=tip_commit,
        final_tree=live_snapshot.final_tree,
        branch_ref=branch_ref,
        source_commits=tuple(commit.commit_id for commit in live_snapshot.commits),
        publication_source_commits=tuple(
            commit.commit_id
            for commit in live_snapshot.commits[
                live_snapshot.movable_commit_start :
            ]
        ),
        allowed_remote_refs=allowed_remote_refs,
    )
    live = HistoryPlanDocument(
        schema_version=CURRENT_HISTORY_PLAN_SCHEMA_VERSION,
        snapshot=live_snapshot,
        safety=safety,
        plan=plan,
    )
    return _semantically_validated_document(frozen_snapshot, live, plan)


def read_and_validate_frozen_history_plan_semantics(
    plan_path: str,
    *,
    base_commit: str,
    tip_commit: str,
    branch_ref: str,
    allowed_remote_refs: tuple[str, ...],
) -> tuple[HistoryPlanDocument, str]:
    """Validate frozen semantics and return the plan's same-read SHA-256."""
    payload, plan_sha256 = _read_plan_payload_and_sha256(plan_path)
    return (
        read_and_validate_frozen_history_plan_semantics_from_payload(
            payload,
            base_commit=base_commit,
            tip_commit=tip_commit,
            branch_ref=branch_ref,
            allowed_remote_refs=allowed_remote_refs,
        ),
        plan_sha256,
    )
