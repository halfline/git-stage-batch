"""Phase ordering and metadata-work bounds for shared plan validation."""

from dataclasses import replace

import pytest

from git_stage_batch.history.models import (
    HistoryCommitSnapshot,
    HistoryIdentity,
    HistoryPatchUnit,
    HistoryPlan,
    HistoryPlannedCommit,
    HistorySnapshot,
    HistoryUnitDependency,
)
from git_stage_batch.history.plan_dependencies import (
    PlanDependencyEvidenceError,
    PrefixMaximumIndex,
    iter_plan_dependency_crossings,
)
from git_stage_batch.history.plan_diagnostics import PlanDiagnosticCollector
from git_stage_batch.history.plan_lint import lint_frozen_history_plan
from git_stage_batch.history.plan_output_lint import lint_plan_output
from git_stage_batch.history.plan_semantics import validate_plan_semantics
from git_stage_batch.history.plan_source_index import PlanSourceIndex


def _history(unit_count):
    identity = HistoryIdentity(
        "Test <test@example.com> 0 +0000", "Test", "test@example.com", 0, "+0000"
    )
    commits = []
    for index in range(unit_count):
        unit = HistoryPatchUnit(
            f"u{index}",
            "patch",
            f"c{index}",
            f"file{index}",
            "text",
            1,
            1,
            1,
            1,
            None,
        )
        commits.append(
            HistoryCommitSnapshot(
                f"c{index}",
                f"c{index - 1}",
                "tree",
                "parent-tree",
                identity,
                identity,
                None,
                f"Commit {index}",
                "message-sha",
                (),
                (),
                (unit,),
            )
        )
    snapshot = HistorySnapshot(
        "sha1",
        "base",
        commits[-1].commit_id,
        "base",
        "base-tree",
        "final-tree",
        None,
        tuple(commits),
        tuple(
            HistoryUnitDependency(
                f"u{index}",
                index,
                index,
                f"u{index - 1}" if index else None,
                "UNKNOWN" if index else None,
                "unproven" if index else None,
            )
            for index in range(unit_count)
        ),
    )
    plan = HistoryPlan(
        (),
        tuple(
            HistoryPlannedCommit(
                "KEEP",
                "EXACT",
                (commit.commit_id,),
                (commit.units[0].unit_id,),
                commit.message,
                None,
                identity,
                "",
            )
            for commit in commits
        ),
    )
    return snapshot, plan


def _reject(message):
    raise ValueError(message)


def test_output_errors_preserve_diagnostic_order_and_skip_global_checks():
    snapshot, plan = _history(2)
    bad = replace(
        plan.outputs[0],
        source_commits=("missing", "missing"),
        source_unit_ids=("missing-unit", "missing-unit"),
    )
    plan = replace(plan, outputs=(bad, plan.outputs[1]))
    result = lint_frozen_history_plan(snapshot, plan)
    assert [finding.code for finding in result.diagnostics] == [
        "source-unknown",
        "source-duplicate",
        "operation-source-cardinality",
        "unit-unknown",
        "unit-duplicate",
    ]
    assert result.skipped_checks == ("conservation", "relative-order", "dependencies")
    with pytest.raises(
        ValueError, match=r"plan.outputs\[0\].source_commits contains an unknown commit"
    ):
        validate_plan_semantics(snapshot, plan, _reject)


def test_corrupt_late_evidence_prevents_earlier_crossing_reports():
    snapshot, plan = _history(3)
    plan = replace(
        plan,
        outputs=(
            replace(plan.outputs[1], operation="REORDER"),
            plan.outputs[0],
            plan.outputs[2],
        ),
    )
    snapshot = replace(
        snapshot,
        dependencies=(
            *snapshot.dependencies[:2],
            replace(snapshot.dependencies[2], barrier_unit_id="wrong"),
        ),
    )
    findings = iter_plan_dependency_crossings(snapshot, plan, ("u0", "u1", "u2"), {})
    with pytest.raises(PlanDependencyEvidenceError) as caught:
        next(findings)
    assert caught.value.code == "dependency-barrier-invalid"
    result = lint_frozen_history_plan(snapshot, plan)
    assert [finding.code for finding in result.diagnostics] == [
        "dependency-barrier-invalid"
    ]
    assert result.skipped_checks == ("dependencies",)
    with pytest.raises(ValueError, match="inconsistent barrier evidence"):
        validate_plan_semantics(snapshot, plan, _reject)


def test_crossing_search_retains_logarithmic_prefix_queries(monkeypatch):
    original_init = PrefixMaximumIndex.__init__
    lookups = 0

    class CountedStorage:
        def __init__(self, values):
            self.values = values

        def __getitem__(self, index):
            nonlocal lookups
            lookups += 1
            return self.values[index]

    def counted_init(index, values):
        original_init(index, values)
        index._maxima = CountedStorage(index._maxima)

    monkeypatch.setattr(PrefixMaximumIndex, "__init__", counted_init)
    counts = []
    for unit_count in (128, 1024):
        snapshot, plan = _history(unit_count)
        plan = replace(plan, outputs=tuple(reversed(plan.outputs)))
        expected_units = tuple(f"u{index}" for index in range(unit_count))
        lookups = 0
        # Consume findings incrementally; there is no collection of crossed pairs.
        assert (
            sum(
                1
                for _ in iter_plan_dependency_crossings(
                    snapshot, plan, expected_units, {}
                )
            )
            == unit_count - 1
        )
        assert lookups < 12 * unit_count * unit_count.bit_length()
        counts.append(lookups)
    assert counts[0] < counts[1] < counts[0] * 12


def test_integrated_source_coverage_uses_one_pass_over_selected_units():
    reads = 0

    class CountedUnit:
        def __init__(self, unit):
            self.unit = unit

        def __getattr__(self, name):
            nonlocal reads
            if name == "source_commit":
                reads += 1
            return getattr(self.unit, name)

    for source_count in (64, 512):
        snapshot, plan = _history(source_count)
        snapshot = replace(
            snapshot,
            commits=tuple(
                replace(commit, units=tuple(CountedUnit(unit) for unit in commit.units))
                for commit in snapshot.commits
            ),
        )
        output = replace(
            plan.outputs[0],
            operation="INTEGRATE",
            source_commits=tuple(commit.commit_id for commit in snapshot.commits),
            source_unit_ids=tuple(
                unit.unit_id for commit in snapshot.commits for unit in commit.units
            ),
        )
        plan = replace(plan, outputs=(output,))
        source_index = PlanSourceIndex.build(snapshot)
        report = PlanDiagnosticCollector(snapshot, plan)
        reads = 0
        assert lint_plan_output(0, output, source_index, report)
        assert report.finish().valid
        assert 0 < reads <= 6 * source_count
