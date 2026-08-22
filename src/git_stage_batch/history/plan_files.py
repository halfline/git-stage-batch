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
    HistoryPlanOperation,
)
from .plan_lint import (
    HistoryPlanLint,
    PrefixMaximumIndex,
    grouped_block_chain_can_defer_to_replay,
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


def _validate_plan_semantics(
    live: HistoryPlanDocument,
    plan: HistoryPlan,
) -> None:
    source_commits = live.snapshot.commits
    if not plan.outputs:
        _invalid("plan.outputs must contain at least one output commit")
    movable_commit_start = live.snapshot.movable_commit_start
    pinned_commit_ids = {
        commit.commit_id
        for commit in source_commits[:movable_commit_start]
    }
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
    expected_unit_set = set(expected_units)
    unit_occurrences: dict[str, list[int]] = {
        unit_id: [] for unit_id in expected_units
    }
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
        if output.materialization == "RESOLVED" and not output.source_unit_ids:
            _invalid(f"{location}.RESOLVED must declare at least one source unit")

        sources = tuple(source_by_id[commit] for commit in output.source_commits)
        unknown_units = [
            unit_id
            for unit_id in output.source_unit_ids
            if unit_id not in unit_by_id
        ]
        if unknown_units:
            _invalid(f"{location}.source_unit_ids contains an unknown unit")
        if len(set(output.source_unit_ids)) != len(output.source_unit_ids):
            _invalid(f"{location}.source_unit_ids must not contain duplicates")
        selected_keys: list[tuple[int, int]] = []
        selected_by_source: dict[str, list[str]] = {
            source.commit_id: [] for source in sources
        }
        source_order = {
            source.commit_id: source_index
            for source_index, source in enumerate(sources)
        }
        for unit_id in output.source_unit_ids:
            unit = unit_by_id[unit_id]
            if unit.source_commit not in source_order:
                _invalid(
                    f"{location}.source_unit_ids contains a unit from an "
                    "unlisted source"
                )
            selected_by_source[unit.source_commit].append(unit_id)
            unit_occurrences[unit_id].append(index)
            selected_keys.append(
                (
                    source_order[unit.source_commit],
                    unit_positions_by_source[unit.source_commit][unit_id],
                )
            )
        if selected_keys != sorted(selected_keys):
            _invalid(
                f"{location}.source_unit_ids must retain source and unit order"
            )
        for source in sources:
            if source.units and not selected_by_source[source.commit_id]:
                _invalid(
                    f"{location} lists source {source.commit_id} without any "
                    "of its units"
                )
        target_source = sources[0]
        if (
            target_source.commit_id in pinned_commit_ids
            and output.operation in {"SPLIT", "REORDER"}
        ):
            _invalid(
                f"{location}.{output.operation} may not restructure pinned "
                f"source commit {target_source.commit_id} outside the movable "
                "scope"
            )
        pinned_secondary = next(
            (
                source
                for source in sources[1:]
                if source.commit_id in pinned_commit_ids
            ),
            None,
        )
        if pinned_secondary is not None:
            _invalid(
                f"{location} may not consume pinned source commit "
                f"{pinned_secondary.commit_id} as a donor; pinned commits "
                "outside the movable scope may only receive units through "
                "INTEGRATE"
            )
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
            if output.source_unit_ids != target_units:
                _invalid(
                    f"{location}.{output.operation} must consume every target "
                    "unit in order"
                )
        elif output.operation == "SPLIT":
            if not output.source_unit_ids:
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

    partitioned_by_id: dict[str, HistoryPartitionedUnit] = {}
    partition_positions: list[int] = []
    expected_positions = {
        unit_id: position for position, unit_id in enumerate(expected_units)
    }
    for index, partition in enumerate(plan.partitioned_units):
        location = f"plan.partitioned_units[{index}]"
        if partition.unit_id in partitioned_by_id:
            _invalid(f"{location}.unit_id duplicates a partitioned unit")
        if partition.unit_id not in expected_unit_set:
            _invalid(f"{location}.unit_id names an unknown unit")
        if len(partition.output_indexes) < 2:
            _invalid(f"{location}.output_indexes must contain at least two outputs")
        if partition.output_indexes != tuple(sorted(set(partition.output_indexes))):
            _invalid(
                f"{location}.output_indexes must be unique and strictly increasing"
            )
        if partition.output_indexes[-1] >= len(plan.outputs):
            _invalid(f"{location}.output_indexes contains an unknown output")
        if tuple(unit_occurrences[partition.unit_id]) != partition.output_indexes:
            _invalid(
                f"{location}.output_indexes must exactly match the unit's outputs"
            )
        if any(
            plan.outputs[output_index].materialization != "RESOLVED"
            for output_index in partition.output_indexes
        ):
            _invalid(f"{location} may appear only in RESOLVED outputs")
        partitioned_by_id[partition.unit_id] = partition
        partition_positions.append(expected_positions[partition.unit_id])
    if partition_positions != sorted(partition_positions):
        _invalid("plan.partitioned_units must retain source unit order")

    partitioned_unit_ids = set(partitioned_by_id)
    for index, output in enumerate(plan.outputs):
        if output.operation == "SPLIT":
            continue
        partition_checked_target_units = {
            unit.unit_id for unit in source_by_id[output.source_commits[0]].units
        }
        if partition_checked_target_units & partitioned_unit_ids:
            _invalid(
                f"plan.outputs[{index}].{output.operation} target units must "
                "not be partitioned"
            )

    for unit_id in expected_units:
        occurrences = unit_occurrences[unit_id]
        if unit_id in partitioned_by_id:
            continue
        if len(occurrences) != 1:
            _invalid(
                "plan.outputs must assign every nonpartitioned source unit "
                "exactly once"
            )

    for source in source_commits:
        source_id = source.commit_id
        targets = target_occurrences.get(source_id, [])
        secondary = secondary_occurrences.get(source_id, [])
        if targets and secondary:
            if any(operation != "SPLIT" for _output, operation in targets):
                _invalid(
                    f"source commit {source_id} may be both a secondary and a "
                    "target only through residual SPLIT outputs"
                )
            target_indexes = [output for output, _operation in targets]
            if max(secondary) >= min(target_indexes):
                _invalid(
                    f"source commit {source_id} secondary outputs must precede "
                    "its residual SPLIT outputs"
                )
            if len(set((*secondary, *target_indexes))) < 2:
                _invalid(
                    f"source commit {source_id} must have at least two "
                    "destinations when split across target and secondary outputs"
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
        if len(targets) == 1 and targets[0][1] == "SPLIT" and not secondary:
            _invalid(
                f"source commit {source_id} must produce at least two SPLIT outputs"
            )
        if not source.units and source_mentions.get(source_id, 0) != 1:
            _invalid(
                f"empty source commit {source_id} must be consumed exactly once"
            )

    moved_earlier_outputs: set[int] = set()
    suffix_minimum = output_target_positions[-1]
    for earlier_index in range(len(output_target_positions) - 2, -1, -1):
        earlier_position = output_target_positions[earlier_index]
        if earlier_position > suffix_minimum:
            moved_earlier_outputs.add(earlier_index)
            if plan.outputs[earlier_index].operation not in {"REORDER", "SPLIT"}:
                _invalid(
                    f"plan.outputs[{earlier_index}] must use REORDER or SPLIT "
                    "when moving before an earlier source"
                )
        suffix_minimum = min(suffix_minimum, earlier_position)
    for output_index, output in enumerate(plan.outputs):
        if output.operation == "REORDER" and output_index not in moved_earlier_outputs:
            _invalid(
                f"plan.outputs[{output_index}].REORDER does not move its source "
                "earlier"
            )

    ordered_nonpartitioned_units = tuple(
        unit_id
        for output in plan.outputs
        for unit_id in output.source_unit_ids
        if unit_id not in partitioned_unit_ids
    )
    desired_positions = {
        unit_id: position
        for position, unit_id in enumerate(ordered_nonpartitioned_units)
    }
    output_positions = {
        unit_id: output_index
        for output_index, output in enumerate(plan.outputs)
        for unit_id in output.source_unit_ids
        if unit_id not in partitioned_unit_ids
    }
    nonpartitioned_position_index = PrefixMaximumIndex(
        tuple(
            desired_positions.get(unit_id, -1)
            if unit_id not in partitioned_unit_ids
            else -1
            for unit_id in expected_units
        )
    )
    partitioned_output_index = PrefixMaximumIndex(
        tuple(
            max(partitioned_by_id[unit_id].output_indexes)
            if unit_id in partitioned_unit_ids
            else -1
            for unit_id in expected_units
        )
    )
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
        if dependency.unit_id in partitioned_unit_ids:
            first_crossings[dependency.unit_id] = None
            continue
        moving_position = desired_positions[dependency.unit_id]
        moving_output = output_positions[dependency.unit_id]
        nonpartitioned_crossing = nonpartitioned_position_index.first_above(
            dependency.earliest_position,
            moving_position,
        )
        partitioned_crossing = partitioned_output_index.first_above(
            dependency.earliest_position,
            moving_output,
        )
        crossing_positions = tuple(
            position
            for position in (nonpartitioned_crossing, partitioned_crossing)
            if position is not None
        )
        first_crossings[dependency.unit_id] = (
            expected_units[min(crossing_positions)] if crossing_positions else None
        )

    for dependency in live.snapshot.dependencies:
        crossed_unit = first_crossings[dependency.unit_id]
        if crossed_unit is None:
            continue
        moving_output = output_positions[dependency.unit_id]
        if plan.outputs[moving_output].materialization == "RESOLVED":
            continue
        if crossed_unit not in partitioned_unit_ids and (
            grouped_block_chain_can_defer_to_replay(
                dependency,
                dependencies_by_unit=dependencies_by_unit,
                first_crossings=first_crossings,
                desired_positions=desired_positions,
                output_positions=output_positions,
            )
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
    _validate_plan_semantics(live, plan)
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
