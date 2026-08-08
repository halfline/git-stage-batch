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
    HistoryUnitDependency,
)
from .json_files import history_canonical_json_sha256
from .records import history_snapshot_record
from .replay import validate_history_plan_materialization
from .safety import collect_history_safety_facts
from .scan import acquire_frozen_history_snapshot, acquire_history_plan_document


_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "operation",
        "snapshot",
        "safety",
        "plan",
    }
)
_PLAN_KEYS = frozenset({"outputs"})
_OUTPUT_KEYS = frozenset(
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
    operations = {"KEEP", "REWORD", "INTEGRATE", "SPLIT", "REORDER"}
    if operation_value not in operations:
        _invalid(
            f"{location}.operation must be 'KEEP', 'REWORD', 'INTEGRATE', "
            "'SPLIT', or 'REORDER'"
        )
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
    if operation_value in {"REWORD", "INTEGRATE", "SPLIT"}:
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


def _grouped_block_chain_can_defer_to_replay(
    dependency: HistoryUnitDependency,
    *,
    dependencies_by_unit: dict[str, HistoryUnitDependency],
    first_crossings: dict[str, str | None],
    desired_positions: dict[str, int],
    output_positions: dict[str, int],
) -> bool:
    """Return whether ordered blockers move inside one planned output.

    A unit scan stops at its first real blocker, so it has no independent
    evidence for the prefix crossed by that blocker. When the plan keeps a
    chain of real blockers ordered inside one output, exact materialization can
    prove the compound movement as a whole. UNKNOWN barriers never qualify.
    """
    visited: set[str] = set()
    current = dependency
    while first_crossings[current.unit_id] is not None:
        if current.unit_id in visited:
            return False
        visited.add(current.unit_id)
        barrier_unit = current.barrier_unit_id
        if (
            current.barrier != "BLOCKED"
            or barrier_unit is None
            or output_positions[barrier_unit] != output_positions[current.unit_id]
            or desired_positions[barrier_unit]
            >= desired_positions[current.unit_id]
        ):
            return False
        current = dependencies_by_unit[barrier_unit]
    return True


def _validate_plan_semantics(
    live: HistoryPlanDocument,
    plan: HistoryPlan,
) -> None:
    source_commits = live.snapshot.commits
    if not plan.outputs:
        _invalid("plan.outputs must contain at least one output commit")
    source_by_id = {commit.commit_id: commit for commit in source_commits}
    source_positions = {
        commit.commit_id: index for index, commit in enumerate(source_commits)
    }
    unit_by_id = {
        unit.unit_id: unit
        for source in source_commits
        for unit in source.units
    }
    unit_positions_by_source = {
        source.commit_id: {
            unit.unit_id: index for index, unit in enumerate(source.units)
        }
        for source in source_commits
    }
    expected_units = tuple(
        unit.unit_id for source in source_commits for unit in source.units
    )
    consumed_units: set[str] = set()
    target_occurrences: dict[str, list[tuple[int, HistoryPlanOperation]]] = {}
    secondary_occurrences: dict[str, list[int]] = {}
    source_mentions: dict[str, int] = {}
    output_target_positions: list[int] = []

    for index, output in enumerate(plan.outputs):
        location = f"plan.outputs[{index}]"
        if not output.source_commits:
            _invalid(f"{location}.source_commits must not be empty")
        if any(commit not in source_by_id for commit in output.source_commits):
            _invalid(f"{location}.source_commits contains an unknown commit")
        if len(set(output.source_commits)) != len(output.source_commits):
            _invalid(f"{location}.source_commits must not contain duplicates")
        positions = tuple(source_positions[commit] for commit in output.source_commits)
        if positions != tuple(sorted(positions)):
            _invalid(f"{location}.source_commits must retain source order")
        if output.operation in {"KEEP", "REWORD", "SPLIT", "REORDER"} and len(
            positions
        ) != 1:
            _invalid(f"{location}.{output.operation} must consume one source commit")
        if output.operation == "INTEGRATE" and len(positions) < 2:
            _invalid(f"{location}.INTEGRATE must consume at least two commits")

        sources = tuple(source_by_id[commit] for commit in output.source_commits)
        unknown_units = [
            unit_id for unit_id in output.unit_ids if unit_id not in unit_by_id
        ]
        if unknown_units:
            _invalid(f"{location}.unit_ids contains an unknown unit")
        if len(set(output.unit_ids)) != len(output.unit_ids):
            _invalid(f"{location}.unit_ids must not contain duplicates")
        if any(unit_id in consumed_units for unit_id in output.unit_ids):
            _invalid(f"{location}.unit_ids duplicates a consumed unit")
        selected_keys: list[tuple[int, int]] = []
        selected_by_source: dict[str, list[str]] = {
            source.commit_id: [] for source in sources
        }
        source_order = {
            source.commit_id: source_index
            for source_index, source in enumerate(sources)
        }
        for unit_id in output.unit_ids:
            unit = unit_by_id[unit_id]
            if unit.source_commit not in source_order:
                _invalid(
                    f"{location}.unit_ids contains a unit from an unlisted source"
                )
            selected_by_source[unit.source_commit].append(unit_id)
            selected_keys.append(
                (
                    source_order[unit.source_commit],
                    unit_positions_by_source[unit.source_commit][unit_id],
                )
            )
        if selected_keys != sorted(selected_keys):
            _invalid(
                f"{location}.unit_ids must retain source and unit order"
            )
        for source in sources:
            if source.units and not selected_by_source[source.commit_id]:
                _invalid(
                    f"{location} lists source {source.commit_id} without any "
                    "of its units"
                )

        consumed_units.update(output.unit_ids)
        target_source = sources[0]
        unsupported_source = next(
            (source for source in sources if source.unsupported_headers),
            None,
        )
        if unsupported_source is not None:
            _invalid(
                f"source commit {unsupported_source.commit_id} has unsupported "
                "header(s): "
                f"{', '.join(unsupported_source.unsupported_headers)}"
            )
        if output.author != target_source.author:
            _invalid(f"{location}.author must preserve the target author")
        target_units = tuple(unit.unit_id for unit in target_source.units)
        selected_target_units = tuple(selected_by_source[target_source.commit_id])
        if output.operation in {"KEEP", "REWORD", "REORDER"}:
            if output.unit_ids != target_units:
                _invalid(
                    f"{location}.{output.operation} must consume every target "
                    "unit in order"
                )
        elif output.operation == "SPLIT":
            if not output.unit_ids:
                _invalid(f"{location}.SPLIT must contain at least one unit")
        elif selected_target_units != target_units:
            _invalid(
                f"{location}.INTEGRATE must consume every target unit in order"
            )

        if output.operation in {"KEEP", "REORDER"}:
            if output.message != target_source.message:
                _invalid(
                    f"{location}.message changed without a REWORD, SPLIT, or "
                    "INTEGRATE operation"
                )
            if output.encoding != target_source.encoding:
                _invalid(
                    f"{location}.encoding changed without a REWORD, SPLIT, or "
                    "INTEGRATE operation"
                )

        target_occurrences.setdefault(target_source.commit_id, []).append(
            (index, output.operation)
        )
        output_target_positions.append(positions[0])
        for source in sources:
            source_mentions[source.commit_id] = (
                source_mentions.get(source.commit_id, 0) + 1
            )
        for secondary_source in sources[1:]:
            secondary_occurrences.setdefault(
                secondary_source.commit_id,
                [],
            ).append(index)

    if consumed_units != set(expected_units):
        _invalid("plan.outputs must consume every patch unit exactly once")

    for source in source_commits:
        source_id = source.commit_id
        targets = target_occurrences.get(source_id, [])
        secondary = secondary_occurrences.get(source_id, [])
        if targets and secondary:
            _invalid(
                f"source commit {source_id} cannot be both an output target "
                "and an integrated secondary source"
            )
        if not targets and not secondary:
            _invalid(f"source commit {source_id} is not consumed by the plan")
        if len(targets) > 1 and any(
            operation != "SPLIT" for _output, operation in targets
        ):
            _invalid(
                f"source commit {source_id} may target several outputs only "
                "through SPLIT"
            )
        if len(targets) == 1 and targets[0][1] == "SPLIT":
            _invalid(
                f"source commit {source_id} must produce at least two SPLIT outputs"
            )
        if not source.units and source_mentions.get(source_id, 0) != 1:
            _invalid(
                f"empty source commit {source_id} must be consumed exactly once"
            )

    moved_earlier_outputs: set[int] = set()
    for earlier_index, earlier_position in enumerate(output_target_positions):
        for later_position in output_target_positions[earlier_index + 1 :]:
            if earlier_position <= later_position:
                continue
            moved_earlier_outputs.add(earlier_index)
            if plan.outputs[earlier_index].operation not in {"REORDER", "SPLIT"}:
                _invalid(
                    f"plan.outputs[{earlier_index}] must use REORDER or SPLIT "
                    "when moving before an earlier source"
                )
    for output_index, output in enumerate(plan.outputs):
        if output.operation == "REORDER" and output_index not in moved_earlier_outputs:
            _invalid(
                f"plan.outputs[{output_index}].REORDER does not move its source "
                "earlier"
            )

    desired_positions = {
        unit_id: position
        for position, unit_id in enumerate(
            unit_id for output in plan.outputs for unit_id in output.unit_ids
        )
    }
    output_positions = {
        unit_id: output_index
        for output_index, output in enumerate(plan.outputs)
        for unit_id in output.unit_ids
    }
    if len(live.snapshot.dependencies) != len(expected_units):
        _invalid("snapshot dependency graph does not cover every patch unit")
    dependencies_by_unit = {
        dependency.unit_id: dependency
        for dependency in live.snapshot.dependencies
    }
    first_crossings: dict[str, str | None] = {}
    for dependency in live.snapshot.dependencies:
        original_position = dependency.original_position
        if (
            original_position >= len(expected_units)
            or expected_units[original_position] != dependency.unit_id
            or dependency.earliest_position < 0
            or dependency.earliest_position > original_position
        ):
            _invalid("snapshot dependency graph has inconsistent unit positions")
        expected_barrier_unit = (
            expected_units[dependency.earliest_position - 1]
            if dependency.earliest_position > 0
            else None
        )
        barrier_inconsistent = (
            dependency.barrier_unit_id != expected_barrier_unit
            or (dependency.barrier is None) != (dependency.detail is None)
            or (
                expected_barrier_unit is not None
                and dependency.barrier is None
            )
            or (
                expected_barrier_unit is None
                and dependency.barrier == "BLOCKED"
            )
        )
        if barrier_inconsistent:
            _invalid("snapshot dependency graph has inconsistent barrier evidence")
        moving_position = desired_positions[dependency.unit_id]
        first_crossings[dependency.unit_id] = next(
            (
                crossed_unit
                for crossed_unit in expected_units[: dependency.earliest_position]
                if desired_positions[crossed_unit] > moving_position
            ),
            None,
        )

    for dependency in live.snapshot.dependencies:
        crossed_unit = first_crossings[dependency.unit_id]
        if crossed_unit is None or _grouped_block_chain_can_defer_to_replay(
            dependency,
            dependencies_by_unit=dependencies_by_unit,
            first_crossings=first_crossings,
            desired_positions=desired_positions,
            output_positions=output_positions,
        ):
            continue
        barrier = (
            dependency.barrier
            if crossed_unit == dependency.barrier_unit_id
            else "UNKNOWN"
        )
        _invalid(
            f"planned unit order crosses a {barrier} dependency between "
            f"{crossed_unit} and {dependency.unit_id}"
        )


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


def _validated_document(
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
    _validate_plan_semantics(live, plan)
    document = replace(live, plan=plan)
    validate_history_plan_materialization(document)
    return document


def read_and_validate_history_plan(
    plan_path: str,
    *,
    allowed_remote_refs: tuple[str, ...] = (),
) -> HistoryPlanDocument:
    """Regenerate immutable facts and validate the editable semantic plan."""
    payload = _read_plan_payload(plan_path)
    frozen_snapshot, base_commit, plan = _decode_plan(payload)
    live = acquire_history_plan_document(
        base_commit,
        allowed_remote_refs=allowed_remote_refs,
    )
    return _validated_document(frozen_snapshot, live, plan)


def read_and_validate_frozen_history_plan(
    plan_path: str,
    *,
    base_commit: str,
    tip_commit: str,
    branch_ref: str,
    allowed_remote_refs: tuple[str, ...],
) -> HistoryPlanDocument:
    """Validate a persisted plan from its frozen source objects after a rewrite."""
    payload = _read_plan_payload(plan_path)
    frozen_snapshot, document_base, plan = _decode_plan(payload)
    if document_base != base_commit:
        _invalid("snapshot.range.base does not match operation state")
    live_snapshot = acquire_frozen_history_snapshot(
        base_commit,
        tip_commit,
        branch_ref,
    )
    safety = collect_history_safety_facts(
        tip=tip_commit,
        final_tree=live_snapshot.final_tree,
        branch_ref=branch_ref,
        source_commits=tuple(commit.commit_id for commit in live_snapshot.commits),
        allowed_remote_refs=allowed_remote_refs,
    )
    live = HistoryPlanDocument(
        schema_version=CURRENT_HISTORY_PLAN_SCHEMA_VERSION,
        snapshot=live_snapshot,
        safety=safety,
        plan=plan,
    )
    return _validated_document(frozen_snapshot, live, plan)
