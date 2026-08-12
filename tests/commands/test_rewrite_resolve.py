"""Command wiring for external rewrite-resolution workspaces."""

from __future__ import annotations

from unittest.mock import Mock, call

from git_stage_batch.commands import rewrite_resolve
from git_stage_batch.history.resolution_workspace import (
    HistoryResolutionWorkspaceResult,
)


def test_rewrite_resolve_checks_repository_and_renders_result(monkeypatch):
    calls: list[object] = []
    result = HistoryResolutionWorkspaceResult(
        status="NEEDS_RESOLUTION",
        plan_path="/tmp/plan.json",
        workspace_path="/tmp/workspace",
        completed_resolved_outputs=0,
        total_resolved_outputs=2,
        output_index=3,
        output_key="output-4",
        authorized_paths=("example.txt",),
        request_path="/tmp/workspace/request.json",
        result_path="/tmp/workspace/result.json",
        results_path="/tmp/workspace/results",
    )
    require_repository = Mock(side_effect=lambda: calls.append("repository"))
    require_history = Mock(side_effect=lambda: calls.append("history"))
    resolve = Mock(
        side_effect=lambda *_args, **_kwargs: (
            calls.append("resolve"),
            result,
        )[1]
    )
    render = Mock(side_effect=lambda *_args, **_kwargs: calls.append("render"))
    monkeypatch.setattr(
        rewrite_resolve,
        "require_git_repository",
        require_repository,
    )
    monkeypatch.setattr(
        rewrite_resolve,
        "require_repository_history",
        require_history,
    )
    monkeypatch.setattr(rewrite_resolve, "resolve_history_plan", resolve)
    monkeypatch.setattr(rewrite_resolve, "print_rewrite_resolution", render)

    rewrite_resolve.command_rewrite_resolve(
        "plan.json",
        workspace_path="/tmp/workspace",
        accept_result=True,
        porcelain=True,
    )

    assert calls == ["repository", "history", "resolve", "render"]
    assert require_repository.call_args_list == [call()]
    assert require_history.call_args_list == [call()]
    resolve.assert_called_once_with(
        "plan.json",
        "/tmp/workspace",
        accept_result=True,
    )
    render.assert_called_once_with(
        result,
        porcelain=True,
    )
