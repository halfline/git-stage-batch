"""Strict loading and live validation of reusable fixup-create plans."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import NoReturn, cast

from ..exceptions import CommandError
from ..git_paths import terminal_safe_text
from ..i18n import _
from ..utils.file_io import read_required_text_file_contents
from ..utils.git_command import run_git_command
from ..utils.git_index import git_write_tree
from ..utils.git_repository import get_git_object_format
from .commutation import tree_for_commit
from .models import (
    CURRENT_FIXUP_CREATE_PLAN_SCHEMA_VERSION,
    FixupAssignment,
    FixupAssignmentBasis,
    FixupCreatePlan,
    FixupUnitAnalysis,
)
from .planning import acquire_fixup_create_plan, build_fixup_target_groups
from .records import fixup_analysis_record


_TOP_LEVEL_KEYS = frozenset({
    "schema_version",
    "operation",
    "dry_run",
    "range",
    "source",
    "units",
    "assignments",
    "groups",
    "summary",
    "recovery_ref",
})
_RANGE_KEYS = frozenset({"base", "head", "commits_newest_first"})
_SOURCE_KEYS = frozenset({"object_format", "head_tree", "index_tree"})
_ASSIGNMENT_KEYS = frozenset({"unit_id", "target", "basis"})
_UNIT_ID_HEX_LENGTH = 64


@dataclass(frozen=True, slots=True)
class FixupCreatePlanDocument:
    """Validated immutable input fields from one reusable plan document."""

    base_commit: str
    head_commit: str
    commits_newest_first: tuple[str, ...]
    object_format: str
    head_tree: str
    index_tree: str
    units_fingerprint: str
    assignments: tuple[FixupAssignment, ...]


def _invalid(detail: str) -> NoReturn:
    raise CommandError(
        _("Invalid fixup plan: {detail}").format(
            detail=terminal_safe_text(detail)
        )
    )


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate field {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError(f"non-standard numeric value {value!r}")


def _require_object(value: object, location: str) -> dict[str, object]:
    if not isinstance(value, dict):
        _invalid(f"{location} must be an object")
    return cast(dict[str, object], value)


def _require_list(value: object, location: str) -> list[object]:
    if not isinstance(value, list):
        _invalid(f"{location} must be an array")
    return cast(list[object], value)


def _require_exact_keys(
    value: dict[str, object],
    expected: frozenset[str],
    location: str,
) -> None:
    missing = sorted(expected - value.keys())
    unknown = sorted(value.keys() - expected)
    if missing:
        _invalid(f"{location} is missing field(s): {', '.join(missing)}")
    if unknown:
        _invalid(f"{location} has unknown field(s): {', '.join(unknown)}")


def _require_string(
    value: dict[str, object],
    field: str,
    location: str,
) -> str:
    if field not in value:
        _invalid(f"{location} is missing field {field!r}")
    result = value[field]
    if not isinstance(result, str) or not result:
        _invalid(f"{location}.{field} must be a non-empty string")
    return result


def _require_full_hex_id(value: str, length: int, location: str) -> None:
    if len(value) != length or any(character not in "0123456789abcdef" for character in value):
        _invalid(f"{location} must be a full lowercase hexadecimal object ID")


def _object_id_length(object_format: str) -> int:
    if object_format == "sha1":
        return 40
    if object_format == "sha256":
        return 64
    _invalid("source.object_format must be 'sha1' or 'sha256'")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _decode_document(payload: str) -> FixupCreatePlanDocument:
    try:
        raw: object = json.loads(
            payload,
            object_pairs_hook=_json_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, RecursionError, ValueError) as error:
        _invalid(f"document is not strict JSON ({error})")

    document = _require_object(raw, "document")
    _require_exact_keys(document, _TOP_LEVEL_KEYS, "document")
    version = document["schema_version"]
    if type(version) is not int:
        _invalid("schema_version must be an integer")
    if version != CURRENT_FIXUP_CREATE_PLAN_SCHEMA_VERSION:
        _invalid(
            "schema_version must be "
            f"{CURRENT_FIXUP_CREATE_PLAN_SCHEMA_VERSION}"
        )
    if document["operation"] != "fixup-create":
        _invalid("operation must be 'fixup-create'")
    if document["dry_run"] is not True:
        _invalid("only dry-run output can be used as an input plan")
    if document["recovery_ref"] is not None:
        _invalid("a reusable plan cannot contain a recovery ref")

    range_record = _require_object(document["range"], "range")
    _require_exact_keys(range_record, _RANGE_KEYS, "range")
    source_record = _require_object(document["source"], "source")
    _require_exact_keys(source_record, _SOURCE_KEYS, "source")
    _require_list(document["groups"], "groups")
    _require_object(document["summary"], "summary")

    object_format = _require_string(
        source_record,
        "object_format",
        "source",
    )
    oid_length = _object_id_length(object_format)
    base_commit = _require_string(range_record, "base", "range")
    head_commit = _require_string(range_record, "head", "range")
    head_tree = _require_string(source_record, "head_tree", "source")
    index_tree = _require_string(source_record, "index_tree", "source")
    _require_full_hex_id(base_commit, oid_length, "range.base")
    _require_full_hex_id(head_commit, oid_length, "range.head")
    _require_full_hex_id(head_tree, oid_length, "source.head_tree")
    _require_full_hex_id(index_tree, oid_length, "source.index_tree")

    commit_values = _require_list(
        range_record["commits_newest_first"],
        "range.commits_newest_first",
    )
    commits: list[str] = []
    for index, value in enumerate(commit_values):
        if not isinstance(value, str):
            _invalid(f"range.commits_newest_first[{index}] must be a string")
        _require_full_hex_id(
            value,
            oid_length,
            f"range.commits_newest_first[{index}]",
        )
        commits.append(value)
    if not commits:
        _invalid("range.commits_newest_first must not be empty")
    if commits[0] != head_commit:
        _invalid("range.commits_newest_first must begin with range.head")
    if len(set(commits)) != len(commits):
        _invalid("range.commits_newest_first contains a duplicate commit")
    if base_commit in commits:
        _invalid("range.base must be excluded from range.commits_newest_first")

    unit_values = _require_list(document["units"], "units")
    unit_records: list[dict[str, object]] = []
    unit_ids: list[str] = []
    for index, value in enumerate(unit_values):
        record = _require_object(value, f"units[{index}]")
        unit_id = _require_string(record, "id", f"units[{index}]")
        _require_full_hex_id(unit_id, _UNIT_ID_HEX_LENGTH, f"units[{index}].id")
        unit_records.append(record)
        unit_ids.append(unit_id)
    if len(set(unit_ids)) != len(unit_ids):
        _invalid("units contains a duplicate stable unit ID")

    assignment_values = _require_list(document["assignments"], "assignments")
    assignments: list[FixupAssignment] = []
    assigned_ids: set[str] = set()
    unit_id_set = set(unit_ids)
    commit_set = set(commits)
    for index, value in enumerate(assignment_values):
        record = _require_object(value, f"assignments[{index}]")
        _require_exact_keys(record, _ASSIGNMENT_KEYS, f"assignments[{index}]")
        unit_id = _require_string(record, "unit_id", f"assignments[{index}]")
        target = _require_string(record, "target", f"assignments[{index}]")
        basis_value = _require_string(record, "basis", f"assignments[{index}]")
        if basis_value not in {"automatic", "explicit"}:
            _invalid(
                f"assignments[{index}].basis must be 'automatic' or 'explicit'"
            )
        if unit_id not in unit_id_set:
            _invalid(f"assignments[{index}] references an unknown unit ID")
        if unit_id in assigned_ids:
            _invalid(f"assignments[{index}] duplicates a unit assignment")
        _require_full_hex_id(target, oid_length, f"assignments[{index}].target")
        if target not in commit_set:
            _invalid(f"assignments[{index}].target is outside the frozen range")
        assigned_ids.add(unit_id)
        assignments.append(
            FixupAssignment(
                unit_id=unit_id,
                target=target,
                basis=cast(FixupAssignmentBasis, basis_value),
            )
        )

    try:
        units_fingerprint = _canonical_json(unit_records)
    except (RecursionError, ValueError) as error:
        _invalid(f"unit evidence is not canonical JSON ({error})")

    return FixupCreatePlanDocument(
        base_commit=base_commit,
        head_commit=head_commit,
        commits_newest_first=tuple(commits),
        object_format=object_format,
        head_tree=head_tree,
        index_tree=index_tree,
        units_fingerprint=units_fingerprint,
        assignments=tuple(assignments),
    )


def read_fixup_create_plan_file(plan_path: str) -> FixupCreatePlanDocument:
    """Read and structurally validate one reusable plan file."""
    path = Path(plan_path)
    try:
        payload = read_required_text_file_contents(path)
    except (OSError, ValueError) as error:
        raise CommandError(
            _("Could not read fixup plan {path}: {error}").format(
                path=terminal_safe_text(str(path)),
                error=terminal_safe_text(str(error)),
            )
        ) from error
    return _decode_document(payload)


def _require_current_source(document: FixupCreatePlanDocument) -> None:
    current_head = run_git_command(
        ["rev-parse", "--verify", "HEAD^{commit}"],
        requires_index_lock=False,
    ).stdout.strip()
    source_matches = (
        get_git_object_format() == document.object_format
        and current_head == document.head_commit
        and tree_for_commit(current_head) == document.head_tree
        and git_write_tree() == document.index_tree
    )
    if not source_matches:
        _invalid(
            "source does not match the current object format, HEAD, or staged "
            "index; generate a new dry-run plan"
        )


def _validate_explicit_assignment(
    analysis: FixupUnitAnalysis,
    target: str,
    commits_newest_first: tuple[str, ...],
) -> None:
    placement = analysis.placement
    if not analysis.unit.is_supported_text or placement.status == "unknown":
        _invalid(
            f"unit {analysis.unit.unit_id} has no conclusive mechanical placement"
        )

    if placement.status == "commutes-through":
        allowed_targets = commits_newest_first
    else:
        crossed_count = len(placement.commuted_across)
        if (
            placement.commuted_across != commits_newest_first[:crossed_count]
            or crossed_count >= len(commits_newest_first)
            or placement.barrier != commits_newest_first[crossed_count]
        ):
            _invalid(
                f"unit {analysis.unit.unit_id} has inconsistent placement evidence"
            )
        allowed_targets = commits_newest_first[:crossed_count + 1]

    if target not in allowed_targets:
        _invalid(
            f"unit {analysis.unit.unit_id} cannot cross the commits required "
            f"to reach target {target}"
        )


def validate_fixup_create_plan_document(
    document: FixupCreatePlanDocument,
    live_plan: FixupCreatePlan,
) -> FixupCreatePlan:
    """Bind reviewed assignments to a newly analyzed, byte-identical source."""
    expected_range = live_plan.commit_range
    if (
        live_plan.schema_version != CURRENT_FIXUP_CREATE_PLAN_SCHEMA_VERSION
        or live_plan.object_format != document.object_format
        or expected_range.object_format != document.object_format
        or expected_range.base_commit != document.base_commit
        or expected_range.head_commit != document.head_commit
        or expected_range.commits_newest_first != document.commits_newest_first
        or live_plan.head_tree != document.head_tree
        or live_plan.index_tree != document.index_tree
    ):
        _invalid(
            "the frozen range or source changed during validation; generate "
            "a new dry-run plan"
        )

    live_units_fingerprint = _canonical_json([
        fixup_analysis_record(analysis) for analysis in live_plan.units
    ])
    if live_units_fingerprint != document.units_fingerprint:
        _invalid(
            "the exact units or evidence no longer match the staged index; "
            "generate a new dry-run plan"
        )

    analyses_by_id = {
        analysis.unit.unit_id: analysis for analysis in live_plan.units
    }
    if len(analyses_by_id) != len(live_plan.units):
        _invalid("live analysis produced duplicate stable unit IDs")
    automatic_by_id = {
        assignment.unit_id: assignment for assignment in live_plan.assignments
    }
    assignments_by_id: dict[str, FixupAssignment] = {}
    for assignment in document.assignments:
        analysis = analyses_by_id[assignment.unit_id]
        if assignment.basis == "automatic":
            automatic = automatic_by_id.get(assignment.unit_id)
            if automatic is None or automatic.target != assignment.target:
                _invalid(
                    f"unit {assignment.unit_id} is not automatically eligible "
                    f"for target {assignment.target}; use basis 'explicit' for "
                    "a reviewed override"
                )
        else:
            _validate_explicit_assignment(
                analysis,
                assignment.target,
                live_plan.commit_range.commits_newest_first,
            )
        assignments_by_id[assignment.unit_id] = assignment

    canonical_assignments = tuple(
        assignments_by_id[analysis.unit.unit_id]
        for analysis in live_plan.units
        if analysis.unit.unit_id in assignments_by_id
    )
    groups = build_fixup_target_groups(
        live_plan.units,
        canonical_assignments,
        live_plan.commit_range.commits_newest_first,
    )
    return replace(
        live_plan,
        assignments=canonical_assignments,
        groups=groups,
    )


@contextmanager
def acquire_fixup_create_plan_from_file(
    plan_path: str,
) -> Iterator[FixupCreatePlan]:
    """Acquire a live plan after validating one reviewed plan document."""
    document = read_fixup_create_plan_file(plan_path)
    _require_current_source(document)
    with acquire_fixup_create_plan(document.base_commit) as live_plan:
        yield validate_fixup_create_plan_document(document, live_plan)
