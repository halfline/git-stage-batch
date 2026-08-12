"""Command wiring for history-plan application."""

from __future__ import annotations

from unittest.mock import Mock, call

import pytest

from git_stage_batch.commands import rewrite_apply


def test_rewrite_apply_forwards_resolution_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    state = object()
    require_repository = Mock(side_effect=lambda: calls.append("repository"))
    require_history = Mock(side_effect=lambda: calls.append("history"))

    def start_operation(*_args: object, **_kwargs: object) -> object:
        calls.append("start")
        return state

    start = Mock(side_effect=start_operation)
    render = Mock(side_effect=lambda *_args, **_kwargs: calls.append("render"))
    monkeypatch.setattr(
        rewrite_apply,
        "require_git_repository",
        require_repository,
    )
    monkeypatch.setattr(
        rewrite_apply,
        "require_repository_history",
        require_history,
    )
    monkeypatch.setattr(rewrite_apply, "start_history_operation", start)
    monkeypatch.setattr(rewrite_apply, "print_rewrite_operation", render)

    rewrite_apply.command_rewrite_apply(
        "plan.json",
        resolutions_path="/tmp/rewrite-resolution",
        allowed_remote_refs=("refs/remotes/origin/topic",),
        porcelain=True,
    )

    assert calls == ["repository", "history", "start", "render"]
    assert require_repository.call_args_list == [call()]
    assert require_history.call_args_list == [call()]
    start.assert_called_once_with(
        "plan.json",
        resolutions_path="/tmp/rewrite-resolution",
        allowed_remote_refs=("refs/remotes/origin/topic",),
    )
    render.assert_called_once_with("apply", state, porcelain=True)
