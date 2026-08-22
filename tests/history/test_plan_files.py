"""Tests for strict reusable history-plan validation."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from git_stage_batch.exceptions import CommandError
from git_stage_batch.history import plan_files, replay
from git_stage_batch.history.plan_files import (
    read_and_lint_frozen_history_plan,
    read_and_validate_frozen_history_plan_semantics,
    read_and_validate_history_plan,
    read_and_validate_history_plan_semantics,
)
from git_stage_batch.history.plan_lint import PrefixMaximumIndex
from git_stage_batch.history.records import history_plan_document_record
from git_stage_batch.history.scan import acquire_history_plan_document

from .conftest import git


def _write_plan(path, plan: dict[str, object]) -> None:
    path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")


def _plan(repo, path) -> dict[str, object]:
    record = history_plan_document_record(
        acquire_history_plan_document(repo.base)
    )
    _write_plan(path, record)
    return record


def _scoped_plan(repo, path) -> dict[str, object]:
    """Write a plan whose first commit is pinned and second is movable."""
    record = history_plan_document_record(
        acquire_history_plan_document(repo.first, onto_boundary=repo.base)
    )
    _write_plan(path, record)
    return record


def test_prefix_maximum_index_matches_linear_search() -> None:
    for length in range(33):
        values = tuple(((position * 17) % 13) - 2 for position in range(length))
        index = PrefixMaximumIndex(values)
        for end in range(length + 1):
            for threshold in range(-2, 12):
                expected = next(
                    (
                        position
                        for position, value in enumerate(values[:end])
                        if value > threshold
                    ),
                    None,
                )
                assert index.first_above(end, threshold) == expected


def test_validate_accepts_unchanged_keep_plan(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    original = _plan(repo, path)

    validated = read_and_validate_history_plan(str(path))

    assert validated.snapshot.tip_commit == repo.tip
    assert [output.operation for output in validated.plan.outputs] == [
        "KEEP",
        "KEEP",
    ]
    assert history_plan_document_record(validated)["snapshot"] == original["snapshot"]


def test_lint_accepts_keep_plan_without_reacquiring_git(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    _plan(repo, path)

    result = read_and_lint_frozen_history_plan(str(path))

    assert result.valid
    assert result.diagnostics == ()
    assert result.skipped_checks == ()


def test_static_lint_aggregates_roots_before_snapshot_acquisition(
    linear_history_repo,
    monkeypatch,
):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    plan["plan"]["outputs"][0]["message"] = "Forged keep\n"
    plan["plan"]["outputs"][1]["source_unit_ids"] = []
    _write_plan(path, plan)
    acquire = pytest.fail
    monkeypatch.setattr(plan_files, "acquire_history_plan_document", acquire)

    result = read_and_lint_frozen_history_plan(str(path))
    with pytest.raises(CommandError, match="static lint found 2 error"):
        read_and_validate_history_plan(str(path))

    assert [diagnostic.code for diagnostic in result.diagnostics] == [
        "operation-message-shape",
        "operation-unit-shape",
    ]
    assert result.skipped_checks == (
        "conservation",
        "relative-order",
        "dependencies",
    )


def test_static_lint_reports_unsupported_resolution_units(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    plan["plan"]["outputs"][0]["materialization"] = "RESOLVED"
    plan["snapshot"]["commits"][0]["patch"]["units"][0]["kind"] = "rename"
    _write_plan(path, plan)

    result = read_and_lint_frozen_history_plan(str(path))

    diagnostic = next(
        item
        for item in result.diagnostics
        if item.code == "resolution-unit-kind-unsupported"
    )
    assert diagnostic.output_index == 0
    assert diagnostic.unit_kinds == ("rename",)
    assert diagnostic.paths


def test_validate_ignores_json_object_key_order(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    plan["snapshot"] = dict(reversed(tuple(plan["snapshot"].items())))
    _write_plan(path, plan)

    validated = read_and_validate_history_plan(str(path))

    assert validated.snapshot.tip_commit == repo.tip


def test_validate_accepts_message_only_reword(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    plan["plan"]["outputs"][0]["operation"] = "REWORD"
    plan["plan"]["outputs"][0]["message"] = "Explain alpha better\n"
    _write_plan(path, plan)

    validated = read_and_validate_history_plan(str(path))

    assert validated.plan.outputs[0].operation == "REWORD"
    assert validated.plan.outputs[0].message == "Explain alpha better\n"
    assert validated.snapshot.final_tree == git("rev-parse", "HEAD^{tree}")


def test_lint_rejects_restructuring_a_pinned_commit(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _scoped_plan(repo, path)
    plan["plan"]["outputs"][0]["operation"] = "REORDER"
    _write_plan(path, plan)

    result = read_and_lint_frozen_history_plan(str(path))

    assert "movable-scope-violation" in [
        diagnostic.code for diagnostic in result.diagnostics
    ]


def test_validate_rejects_restructuring_a_pinned_commit(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _scoped_plan(repo, path)
    plan["plan"]["outputs"][0]["operation"] = "REORDER"
    _write_plan(path, plan)

    with pytest.raises(CommandError, match="movable scope"):
        read_and_validate_history_plan(str(path))


def test_validate_accepts_rewording_a_pinned_commit(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _scoped_plan(repo, path)
    plan["plan"]["outputs"][0]["operation"] = "REWORD"
    plan["plan"]["outputs"][0]["message"] = "Explain alpha better\n"
    _write_plan(path, plan)

    validated = read_and_validate_history_plan(str(path))

    assert validated.snapshot.movable_base == repo.first
    assert validated.plan.outputs[0].operation == "REWORD"
    assert validated.plan.outputs[0].message == "Explain alpha better\n"


def test_validate_accepts_integrating_movable_units_into_a_pinned_commit(
    linear_history_repo,
):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _scoped_plan(repo, path)
    pinned, movable = plan["plan"]["outputs"]
    pinned["operation"] = "INTEGRATE"
    pinned["source_commits"] = [
        pinned["source_commits"][0],
        movable["source_commits"][0],
    ]
    pinned["source_unit_ids"] = [
        *pinned["source_unit_ids"],
        *movable["source_unit_ids"],
    ]
    plan["plan"]["outputs"] = [pinned]
    _write_plan(path, plan)

    validated = read_and_validate_history_plan(str(path))

    assert validated.plan.outputs[0].operation == "INTEGRATE"
    assert validated.snapshot.movable_base == repo.first


def test_validate_rejects_message_edit_marked_keep(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    plan["plan"]["outputs"][0]["message"] = "Forged keep\n"
    _write_plan(path, plan)

    with pytest.raises(CommandError, match="without a REWORD"):
        read_and_validate_history_plan(str(path))


def test_validate_requires_reword_for_encoding_change(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    plan["plan"]["outputs"][0]["encoding"] = "ISO-8859-1"
    _write_plan(path, plan)

    with pytest.raises(CommandError, match="encoding changed without a REWORD"):
        read_and_validate_history_plan(str(path))


def test_validate_rejects_unencodable_reword(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    plan["plan"]["outputs"][0]["operation"] = "REWORD"
    plan["plan"]["outputs"][0]["message"] = "snowman ☃\n"
    plan["plan"]["outputs"][0]["encoding"] = "ascii"
    _write_plan(path, plan)

    with pytest.raises(CommandError, match="cannot be encoded as ascii"):
        read_and_validate_history_plan(str(path))


def test_validate_rejects_omitted_patch_unit(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    plan["plan"]["outputs"][0]["source_unit_ids"] = []
    _write_plan(path, plan)

    with pytest.raises(CommandError, match="without any of its units"):
        read_and_validate_history_plan(str(path))


def test_lint_rejects_integrated_source_without_selected_units(
    linear_history_repo,
):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    first, second = plan["plan"]["outputs"]
    first["operation"] = "INTEGRATE"
    first["source_commits"] = [
        first["source_commits"][0],
        second["source_commits"][0],
    ]
    plan["plan"]["outputs"] = [first]
    _write_plan(path, plan)

    result = read_and_lint_frozen_history_plan(str(path))

    assert [diagnostic.code for diagnostic in result.diagnostics] == [
        "source-units-empty"
    ]
    assert result.skipped_checks == (
        "conservation",
        "relative-order",
        "dependencies",
    )


def test_validate_rejects_duplicate_patch_unit(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    units = plan["plan"]["outputs"][0]["source_unit_ids"]
    plan["plan"]["outputs"][0]["source_unit_ids"] = [units[0], units[0]]
    _write_plan(path, plan)

    with pytest.raises(CommandError, match="must not contain duplicates"):
        read_and_validate_history_plan(str(path))


def test_validate_rejects_reordered_source_commits(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    plan["plan"]["outputs"].reverse()
    _write_plan(path, plan)

    with pytest.raises(CommandError, match="must use REORDER or SPLIT"):
        read_and_validate_history_plan(str(path))


def test_validate_rejects_forged_snapshot_fact(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    plan["snapshot"]["commits"][0]["patch"]["units"][0]["path"] = "forged.txt"
    _write_plan(path, plan)

    with pytest.raises(CommandError, match="immutable range.*changed"):
        read_and_validate_history_plan(str(path))


def test_validate_rejects_stale_tip(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    _plan(repo, path)
    repo.source.write_text("new tip\n", encoding="utf-8")
    git("commit", "-am", "Move tip")

    with pytest.raises(CommandError, match="immutable range.*changed"):
        read_and_validate_history_plan(str(path))


def test_validate_recalculates_informational_safety(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    plan["safety"] = {"forged": True}
    _write_plan(path, plan)

    validated = read_and_validate_history_plan(str(path))

    assert validated.safety.mutation_ready is True


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.replace(
            '"operation": "rewrite-plan"',
            '"operation": "rewrite-plan", "operation": "rewrite-plan"',
            1,
        ),
        lambda payload: payload.replace('"schema_version": 5', '"schema_version": NaN', 1),
        lambda payload: payload.replace(
            '"operation": "rewrite-plan"',
            '"operation": "rewrite-plan", "unknown": true',
            1,
        ),
    ],
)
def test_validate_rejects_non_strict_json(linear_history_repo, mutation):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    payload = mutation(json.dumps(plan))
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(CommandError, match="Invalid rewrite plan"):
        read_and_validate_history_plan(str(path))


def test_validate_rejects_unknown_future_operation(
    linear_history_repo,
):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    plan["plan"]["outputs"][0]["operation"] = "FUTURE"
    _write_plan(path, plan)

    with pytest.raises(
        CommandError,
        match="KEEP.*REWORD.*INTEGRATE.*SPLIT.*REORDER",
    ):
        read_and_validate_history_plan(str(path))


def test_validate_requires_a_fresh_scan_for_schema_three_plan(
    linear_history_repo,
):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    plan["schema_version"] = 3
    _write_plan(path, plan)

    with pytest.raises(CommandError, match="schema_version must be 5"):
        read_and_validate_history_plan(str(path))


def _partition_first_source(plan: dict[str, object]) -> str:
    original = plan["plan"]["outputs"][0]
    unit_id = original["source_unit_ids"][0]
    first = copy.deepcopy(original)
    second = copy.deepcopy(original)
    first["operation"] = "SPLIT"
    first["materialization"] = "RESOLVED"
    first["message"] = "First semantic part\n"
    second["operation"] = "SPLIT"
    second["materialization"] = "RESOLVED"
    second["message"] = "Second semantic part\n"
    plan["plan"]["outputs"] = [
        first,
        second,
        *plan["plan"]["outputs"][1:],
    ]
    plan["plan"]["partitioned_units"] = [
        {"unit_id": unit_id, "output_indexes": [0, 1]}
    ]
    return unit_id


def _partition_later_source_across_integration(
    plan: dict[str, object],
) -> str:
    target, later = plan["plan"]["outputs"]
    later_unit = later["source_unit_ids"][0]
    target["operation"] = "INTEGRATE"
    target["materialization"] = "RESOLVED"
    target["source_commits"].extend(later["source_commits"])
    target["source_unit_ids"].append(later_unit)
    later["operation"] = "SPLIT"
    later["materialization"] = "RESOLVED"
    plan["plan"]["partitioned_units"] = [
        {"unit_id": later_unit, "output_indexes": [0, 1]}
    ]
    return later_unit


def test_validate_accepts_partitioned_provenance_before_materialization(
    linear_history_repo,
    monkeypatch,
):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    _partition_first_source(plan)
    _write_plan(path, plan)
    monkeypatch.setattr(
        replay,
        "acquire_history_replay_units",
        lambda _snapshot: pytest.fail("resolved plan acquired replay units"),
    )
    monkeypatch.setattr(
        replay,
        "_apply_whole_source_output",
        lambda *_args, **_kwargs: pytest.fail(
            "resolved plan replayed a whole source"
        ),
    )

    with pytest.raises(CommandError, match="explicit resolution workspace"):
        read_and_validate_history_plan(str(path))


def test_validate_stops_full_source_resolution_before_whole_replay(
    linear_history_repo,
    monkeypatch,
):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    plan["plan"]["outputs"][0]["materialization"] = "RESOLVED"
    _write_plan(path, plan)
    monkeypatch.setattr(
        replay,
        "_apply_whole_source_output",
        lambda *_args, **_kwargs: pytest.fail(
            "resolved plan replayed a whole source"
        ),
    )

    with pytest.raises(CommandError, match="explicit resolution workspace"):
        read_and_validate_history_plan(str(path))


def test_semantic_validation_accepts_resolved_output_without_replay(
    linear_history_repo,
):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    plan["plan"]["outputs"][0]["materialization"] = "RESOLVED"
    _write_plan(path, plan)

    document, plan_sha256 = read_and_validate_history_plan_semantics(str(path))

    assert document.plan.outputs[0].materialization == "RESOLVED"
    assert plan_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_frozen_semantic_validation_hashes_the_captured_resolved_plan(
    linear_history_repo,
):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    plan["plan"]["outputs"][0]["materialization"] = "RESOLVED"
    _write_plan(path, plan)

    document, plan_sha256 = read_and_validate_frozen_history_plan_semantics(
        str(path),
        base_commit=repo.base,
        tip_commit=repo.tip,
        branch_ref="refs/heads/topic",
        allowed_remote_refs=(),
    )

    assert document.plan.outputs[0].materialization == "RESOLVED"
    assert plan_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_validate_accepts_partitioned_secondary_with_later_residual(
    linear_history_repo,
):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    later_unit = _partition_later_source_across_integration(plan)
    _write_plan(path, plan)

    with pytest.raises(CommandError, match="explicit resolution workspace"):
        read_and_validate_history_plan(str(path))

    assert plan["plan"]["outputs"][0]["source_unit_ids"][-1] == later_unit
    assert plan["plan"]["outputs"][1]["source_unit_ids"] == [later_unit]


def test_validate_rejects_residual_before_its_secondary_destination(
    linear_history_repo,
):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    later_unit = _partition_later_source_across_integration(plan)
    target, residual = plan["plan"]["outputs"]
    plan["plan"]["outputs"] = [residual, target]
    plan["plan"]["partitioned_units"] = [
        {"unit_id": later_unit, "output_indexes": [0, 1]}
    ]
    _write_plan(path, plan)

    with pytest.raises(CommandError, match="secondary outputs must precede"):
        read_and_validate_history_plan(str(path))


def test_validate_rejects_repeated_unit_without_partition_declaration(
    linear_history_repo,
):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    _partition_first_source(plan)
    plan["plan"]["partitioned_units"] = []
    _write_plan(path, plan)

    with pytest.raises(CommandError, match="nonpartitioned source unit"):
        read_and_validate_history_plan(str(path))


def test_validate_requires_every_partition_occurrence_to_be_resolved(
    linear_history_repo,
):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    _partition_first_source(plan)
    plan["plan"]["outputs"][1]["materialization"] = "EXACT"
    _write_plan(path, plan)

    with pytest.raises(CommandError, match="only in RESOLVED outputs"):
        read_and_validate_history_plan(str(path))


def test_validate_requires_partition_indexes_to_match_occurrences(
    linear_history_repo,
):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    _partition_first_source(plan)
    plan["plan"]["partitioned_units"][0]["output_indexes"] = [0, 2]
    _write_plan(path, plan)

    with pytest.raises(CommandError, match="exactly match the unit's outputs"):
        read_and_validate_history_plan(str(path))


def test_validate_rejects_duplicate_partition_declarations(
    linear_history_repo,
):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    _partition_first_source(plan)
    plan["plan"]["partitioned_units"].append(
        copy.deepcopy(plan["plan"]["partitioned_units"][0])
    )
    _write_plan(path, plan)

    with pytest.raises(CommandError, match="duplicates a partitioned unit"):
        read_and_validate_history_plan(str(path))


@pytest.mark.parametrize(
    ("field", "replacement", "match"),
    [
        ("materialization", "DERIVED", "EXACT.*RESOLVED"),
        ("materialization", None, "missing field.*materialization"),
        ("partitioned_units", None, "missing field.*partitioned_units"),
    ],
)
def test_validate_rejects_invalid_resolution_schema_fields(
    linear_history_repo,
    field,
    replacement,
    match,
):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    record = (
        plan["plan"] if field == "partitioned_units" else plan["plan"]["outputs"][0]
    )
    if replacement is None:
        record.pop(field)
    else:
        record[field] = replacement
    _write_plan(path, plan)

    with pytest.raises(CommandError, match=match):
        read_and_validate_history_plan(str(path))


def test_validate_rejects_the_legacy_unit_ids_field(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    output = plan["plan"]["outputs"][0]
    output["unit_ids"] = output.pop("source_unit_ids")
    _write_plan(path, plan)

    with pytest.raises(CommandError, match="source_unit_ids"):
        read_and_validate_history_plan(str(path))


def test_validate_rejects_unitless_resolved_output(linear_history_repo):
    repo = linear_history_repo
    git("commit", "--allow-empty", "-m", "Empty marker")
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    plan["plan"]["outputs"][-1]["materialization"] = "RESOLVED"
    _write_plan(path, plan)

    with pytest.raises(CommandError, match="declare at least one source unit"):
        read_and_validate_history_plan(str(path))


def test_validate_accepts_one_empty_integrated_secondary(linear_history_repo):
    repo = linear_history_repo
    git("commit", "--allow-empty", "-m", "Empty marker")
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    first, target, empty = plan["plan"]["outputs"]
    target["operation"] = "INTEGRATE"
    target["source_commits"].extend(empty["source_commits"])
    plan["plan"]["outputs"] = [first, target]
    _write_plan(path, plan)

    validated = read_and_validate_history_plan(str(path))

    assert validated.plan.outputs[-1].operation == "INTEGRATE"
    assert validated.plan.outputs[-1].source_unit_ids


@pytest.mark.parametrize(
    "output_indexes",
    [[0], [1, 0], [0, 0], [-1, 0], [0, True], [0, 99]],
)
def test_validate_rejects_invalid_partition_output_indexes(
    linear_history_repo,
    output_indexes,
):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    _partition_first_source(plan)
    plan["plan"]["partitioned_units"][0]["output_indexes"] = output_indexes
    _write_plan(path, plan)

    with pytest.raises(CommandError, match="output_indexes"):
        read_and_validate_history_plan(str(path))
