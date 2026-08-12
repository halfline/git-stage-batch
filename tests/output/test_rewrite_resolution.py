"""Rendering tests for rewrite-resolution workspace checkpoints."""

from __future__ import annotations

import json

from git_stage_batch.history.resolution_workspace import (
    HistoryResolutionWorkspaceResult,
)
from git_stage_batch.output.rewrite_resolution import print_rewrite_resolution


def test_resolution_porcelain_exposes_stable_checkpoint_fields(capsys):
    result = HistoryResolutionWorkspaceResult(
        status="NEEDS_RESOLUTION",
        plan_path="/tmp/plan.json",
        workspace_path="/tmp/history-workspace",
        completed_resolved_outputs=1,
        total_resolved_outputs=3,
        output_index=4,
        output_key="resolved-output-5",
        authorized_paths=("src/first.py", "docs/second.md"),
        request_path="/tmp/history-workspace/request.json",
        result_path="/tmp/history-workspace/result.json",
        results_path="/tmp/history-workspace/results",
    )

    print_rewrite_resolution(
        result,
        porcelain=True,
    )

    assert json.loads(capsys.readouterr().out) == {
        "schema_version": 1,
        "operation": "rewrite-resolve",
        "status": "NEEDS_RESOLUTION",
        "plan_path": "/tmp/plan.json",
        "workspace_path": "/tmp/history-workspace",
        "completed_resolved_outputs": 1,
        "total_resolved_outputs": 3,
        "output_index": 4,
        "output_number": 5,
        "output_key": "resolved-output-5",
        "authorized_paths": ["src/first.py", "docs/second.md"],
        "request_path": "/tmp/history-workspace/request.json",
        "result_path": "/tmp/history-workspace/result.json",
        "results_path": "/tmp/history-workspace/results",
    }


def test_resolution_human_output_identifies_one_based_output_and_workspace(
    capsys,
):
    result = HistoryResolutionWorkspaceResult(
        status="NEEDS_RESOLUTION",
        plan_path="/tmp/plan\nunsafe.json",
        workspace_path="/tmp/history-workspace\nunsafe",
        completed_resolved_outputs=0,
        total_resolved_outputs=1,
        output_index=6,
        output_key="output\nkey",
        authorized_paths=("src/first.py, ambiguous", "docs/second\nline.md"),
        request_path="/tmp/request\nunsafe.json",
        result_path="/tmp/result\nunsafe.json",
        results_path="/tmp/results\nunsafe",
    )

    print_rewrite_resolution(
        result,
        porcelain=False,
    )

    output = capsys.readouterr().out
    assert "Rewrite output 7 needs resolution" in output
    assert "output\\nkey" in output
    assert "/tmp/request\\nunsafe.json" in output
    assert "/tmp/result\\nunsafe.json" in output
    assert "/tmp/results\\nunsafe" in output
    assert "Authorized paths:\n  src/first.py, ambiguous\n" in output
    assert "docs/second\\nline.md" in output
    assert "rewrite resolve $'/tmp/plan\\nunsafe.json' --workspace" in output
    assert "--accept" in output
    assert output.count("\n") == 8


def test_resolution_complete_output_has_no_editing_instruction(capsys):
    result = HistoryResolutionWorkspaceResult(
        status="COMPLETE",
        plan_path="/tmp/plan.json",
        workspace_path="/tmp/history-workspace",
        completed_resolved_outputs=2,
        total_resolved_outputs=2,
        output_index=None,
        output_key=None,
        authorized_paths=(),
        request_path=None,
        result_path=None,
        results_path=None,
    )

    print_rewrite_resolution(
        result,
        porcelain=False,
    )

    output = capsys.readouterr().out
    assert "Rewrite resolution workspace is complete" in output
    assert "2 resolved outputs recorded." in output
    assert "needs resolution" not in output
    assert "Edit" not in output
