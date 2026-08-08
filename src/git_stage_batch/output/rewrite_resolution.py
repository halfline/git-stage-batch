"""Human and porcelain rendering for rewrite-resolution workspaces."""

from __future__ import annotations

import json

from ..git_paths import display_path, terminal_safe_shell_join, terminal_safe_text
from ..history.resolution_workspace import HistoryResolutionWorkspaceResult
from ..i18n import _, ngettext


def _resolution_record(
    result: HistoryResolutionWorkspaceResult,
) -> dict[str, object]:
    output_number = result.output_index + 1 if result.output_index is not None else None
    return {
        "schema_version": 1,
        "operation": "rewrite-resolve",
        "status": result.status,
        "plan_path": result.plan_path,
        "workspace_path": result.workspace_path,
        "completed_resolved_outputs": result.completed_resolved_outputs,
        "total_resolved_outputs": result.total_resolved_outputs,
        "output_index": result.output_index,
        "output_number": output_number,
        "output_key": result.output_key,
        "authorized_paths": list(result.authorized_paths),
        "request_path": result.request_path,
        "result_path": result.result_path,
        "results_path": result.results_path,
    }


def print_rewrite_resolution(
    result: HistoryResolutionWorkspaceResult,
    *,
    porcelain: bool,
) -> None:
    """Render the current external resolution-workspace checkpoint."""
    if porcelain:
        print(
            json.dumps(
                _resolution_record(result),
                indent=2,
                ensure_ascii=True,
            )
        )
        return

    workspace = display_path(result.workspace_path)
    if result.status == "COMPLETE":
        print(
            _("Rewrite resolution workspace is complete: {workspace}").format(
                workspace=workspace,
            )
        )
        print(
            ngettext(
                "{count} resolved output recorded.",
                "{count} resolved outputs recorded.",
                result.completed_resolved_outputs,
            ).format(count=result.completed_resolved_outputs)
        )
        return

    assert result.output_index is not None
    assert result.output_key is not None
    assert result.request_path is not None
    assert result.result_path is not None
    assert result.results_path is not None
    print(
        _("Rewrite output {number} needs resolution ({key}).").format(
            number=result.output_index + 1,
            key=terminal_safe_text(result.output_key),
        )
    )
    print(
        _("Inspect resolution request: {path}").format(
            path=display_path(result.request_path),
        )
    )
    print(
        _("Edit resolution metadata: {path}").format(
            path=display_path(result.result_path),
        )
    )
    print(
        _("Edit resolved artifacts in: {path}").format(
            path=display_path(result.results_path),
        )
    )
    print(
        ngettext(
            "Authorized path:",
            "Authorized paths:",
            len(result.authorized_paths),
        )
    )
    for path in result.authorized_paths:
        print(f"  {display_path(path)}")
    print(
        _("Then run: {command}").format(
            command=terminal_safe_shell_join(
                (
                    "git-stage-batch",
                    "rewrite",
                    "resolve",
                    result.plan_path,
                    "--workspace",
                    result.workspace_path,
                    "--accept",
                )
            )
        )
    )
