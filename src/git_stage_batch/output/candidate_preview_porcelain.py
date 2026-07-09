"""Porcelain JSON output for operation candidate previews."""

from __future__ import annotations

import json

from ..batch.operation_candidate_types import OperationCandidatePreview
from . import candidate_preview_snippets
from . import candidate_preview_summary
from .candidate_preview_commands import (
    candidate_selector_text,
    execute_candidate_command,
    show_candidate_command,
)


def print_operation_candidate_overview_porcelain(
    previews: tuple[OperationCandidatePreview, ...],
    candidate_summaries: list[
        list[candidate_preview_summary.CandidateTargetSummary]
    ],
) -> None:
    """Print the machine-readable overview for candidate choices."""
    first = previews[0]
    print(json.dumps({
        "status": "candidates",
        "changed": False,
        "batch": first.batch_name,
        "selector": {
            "operation": first.operation,
            "count": len(previews),
        },
        "scope": {
            "file": first.file_path,
        },
        "candidates": [
            {
                "ordinal": preview.ordinal,
                "selector": candidate_selector_text(
                    preview.batch_name,
                    preview.operation,
                    preview.ordinal,
                ),
                "commands": {
                    "preview": show_candidate_command(
                        preview,
                        preview.ordinal,
                    ),
                    "execute": (
                        execute_candidate_command(preview)
                    ),
                },
                "targets": [
                    {
                        "target": target.target,
                        "summary": summary.title,
                        "context": list(
                            candidate_preview_snippets.plain_candidate_snippet_lines(
                                summary.lines,
                            )
                        ),
                    }
                    for target, summary in zip(preview.targets, summaries)
                ],
            }
            for preview, summaries in zip(previews, candidate_summaries)
        ],
    }, sort_keys=True))


def print_operation_candidate_porcelain(
    preview: OperationCandidatePreview,
) -> None:
    """Print the machine-readable detail for one candidate choice."""
    print(json.dumps({
        "status": "candidate",
        "changed": False,
        "batch": preview.batch_name,
        "selector": {
            "operation": preview.operation,
            "ordinal": preview.ordinal,
            "count": preview.count,
            "id": preview.candidate_id,
        },
        "scope": {
            "file": preview.file_path,
        },
        "targets": [
            {
                "target": target.target,
                "file": target.file_path,
                "summary": target.summary,
                "resolution_ordinal": target.resolution_ordinal,
                "resolution_count": target.resolution_count,
            }
            for target in preview.targets
        ],
    }, sort_keys=True))
