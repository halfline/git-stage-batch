"""Rendering tests for authenticated history-plan validation."""

from __future__ import annotations

from dataclasses import replace
import json

from git_stage_batch.history.replay import HistoryReplayResult
from git_stage_batch.history.resolution_workspace import (
    HistoryAuthenticatedResolution,
)
from git_stage_batch.history.scan import acquire_history_plan_document
from git_stage_batch.history.snapshot_cache import HistorySnapshotCacheObservation
from git_stage_batch.output.rewrite_validate import print_rewrite_validation


def _resolved_document(base_commit: str):
    document = acquire_history_plan_document(base_commit)
    first_output, *remaining_outputs = document.plan.outputs
    plan = replace(
        document.plan,
        outputs=(
            replace(first_output, materialization="RESOLVED"),
            *remaining_outputs,
        ),
    )
    return replace(document, plan=plan)


def test_exact_validation_porcelain_has_explicit_empty_resolution(
    linear_history_repo,
    capsys,
):
    document = acquire_history_plan_document(linear_history_repo.base)

    print_rewrite_validation(document, porcelain=True)

    record = json.loads(capsys.readouterr().out)
    assert record["summary"]["resolved_outputs"] == 0
    assert record["resolution"] is None
    assert record["snapshot_cache"] is None


def test_validation_porcelain_reports_snapshot_cache_disposition(
    linear_history_repo,
    capsys,
):
    document = acquire_history_plan_document(linear_history_repo.base)
    observation = HistorySnapshotCacheObservation(
        status="hit",
        key="a" * 64,
        path="/var/tmp/cache/history-snapshot-example.json",
        reason="authenticated",
        retained=True,
    )

    print_rewrite_validation(
        document,
        cache_observation=observation,
        porcelain=True,
    )

    record = json.loads(capsys.readouterr().out)
    assert record["snapshot_cache"] == {
        "status": "hit",
        "key": "a" * 64,
        "path": "/var/tmp/cache/history-snapshot-example.json",
        "reason": "authenticated",
        "retained": True,
    }


def test_authenticated_validation_porcelain_exposes_only_safe_provenance(
    linear_history_repo,
    capsys,
):
    document = _resolved_document(linear_history_repo.base)
    resolution = HistoryAuthenticatedResolution(
        raw_plan_sha256="a" * 64,
        complete_sha256="b" * 64,
        workspace_path="/tmp/history resolutions",
        replay=HistoryReplayResult(
            output_trees=("external-output-tree",),
            final_tree="external-final-tree",
        ),
    )

    print_rewrite_validation(
        document,
        resolution=resolution,
        porcelain=True,
    )

    output = capsys.readouterr().out
    record = json.loads(output)
    assert record["summary"]["resolved_outputs"] == 1
    assert record["resolution"] == {
        "workspace": "/tmp/history resolutions",
        "complete_sha256": "b" * 64,
        "resolved_outputs": 1,
    }
    assert "a" * 64 not in output
    assert "external-output-tree" not in output
    assert "external-final-tree" not in output


def test_authenticated_validation_human_output_escapes_workspace(
    linear_history_repo,
    capsys,
):
    document = _resolved_document(linear_history_repo.base)
    resolution = HistoryAuthenticatedResolution(
        raw_plan_sha256="a" * 64,
        complete_sha256="b" * 64,
        workspace_path="/tmp/history\nresolutions",
        replay=HistoryReplayResult(
            output_trees=("external-output-tree",),
            final_tree="external-final-tree",
        ),
    )

    print_rewrite_validation(
        document,
        resolution=resolution,
        porcelain=False,
    )

    output = capsys.readouterr().out
    assert "1 resolved output is authenticated." in output
    assert 'Resolution workspace: "/tmp/history\\nresolutions"' in output
    assert f"Resolution completion SHA-256: {'b' * 64}" in output
    assert "external-output-tree" not in output
    assert "external-final-tree" not in output
