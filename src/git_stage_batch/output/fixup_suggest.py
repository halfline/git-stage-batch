"""Render exact-evidence fixup suggestions."""

from __future__ import annotations

import json
import subprocess
import sys

from ..fixup.models import (
    FixupRange,
    FixupUnitAnalysis,
    LineageHistoryEvidence,
    SuggestFixupCandidate,
)
from ..git_paths import display_path, terminal_safe_text
from ..i18n import _
from ..utils.git_command import run_git_command
from .fixup_create import fixup_analysis_record, fixup_reason_text


def _commit_details(commit: str) -> dict[str, str]:
    """Return stable metadata for one frozen candidate commit."""
    try:
        result = run_git_command(
            [
                "show",
                "--no-patch",
                "--format=%H%x00%s%x00%an%x00%ae%x00%aI",
                commit,
            ],
            requires_index_lock=False,
        )
        fields = result.stdout.rstrip("\n").split("\0")
        if len(fields) == 5:
            return {
                "id": fields[0],
                "subject": fields[1],
                "author_name": fields[2],
                "author_email": fields[3],
                "authored_at": fields[4],
            }
    except subprocess.CalledProcessError:
        pass
    return {
        "id": commit,
        "subject": "",
        "author_name": "",
        "author_email": "",
        "authored_at": "",
    }


def _show_commit_diff_for_file(commit: str, file_path: str) -> None:
    """Print the candidate's diff for the selected literal path."""
    try:
        result = run_git_command(
            [
                "show",
                "--format=",
                "--color=always" if sys.stdout.isatty() else "--color=never",
                commit,
                "--",
                file_path,
            ],
            requires_index_lock=False,
            literal_pathspecs=True,
        )
    except subprocess.CalledProcessError:
        return
    if result.stdout.strip():
        print()
        print(result.stdout.rstrip())
        print()


def _range_records(
    ranges: tuple[tuple[int, int], ...] | None,
) -> list[dict[str, int]] | None:
    if ranges is None:
        return None
    return [
        {"start": start, "end": end}
        for start, end in ranges
    ]


def _porcelain_record(
    *,
    commit_range: FixupRange,
    hunk_hash: str,
    line_id_ranges: tuple[tuple[int, int], ...] | None,
    analysis: FixupUnitAnalysis,
    history: LineageHistoryEvidence,
    candidate_commits: tuple[str, ...],
    candidate: SuggestFixupCandidate | None,
    result: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "operation": "fixup-suggest",
        "object_format": commit_range.object_format,
        "range": {
            "base": commit_range.base_commit,
            "head": commit_range.head_commit,
        },
        "selection": {
            "hunk_hash": hunk_hash,
            "line_id_ranges": _range_records(line_id_ranges),
        },
        "unit": fixup_analysis_record(analysis),
        "history": {
            "candidate_commits": list(history.candidates),
            "queried_ranges": _range_records(history.queried_ranges),
            "completed_range_count": history.completed_range_count,
            "complete": history.complete,
        },
        "candidates": list(candidate_commits),
        "candidate": (
            _commit_details(candidate.commit)
            if candidate is not None
            else None
        ),
        "candidate_sources": (
            list(candidate.sources) if candidate is not None else []
        ),
        "iteration": (
            {
                "index": candidate.iteration,
                "total": candidate.total,
            }
            if candidate is not None
            else None
        ),
        "result": result,
    }


def _print_lineage(analysis: FixupUnitAnalysis) -> None:
    lineage = analysis.lineage
    if lineage.conclusive and lineage.unique_target is not None:
        print(
            _("Lineage: exact source lines resolve to {commit}.").format(
                commit=lineage.unique_target[:12]
            )
        )
    elif len(lineage.candidates) > 1:
        print(
            _("Lineage: multiple in-range owners ({commits}).").format(
                commits=", ".join(
                    commit[:12] for commit in lineage.candidates
                )
            )
        )
    elif lineage.candidates:
        print(
            _("Lineage: incomplete evidence includes {commit}.").format(
                commit=lineage.candidates[0][:12]
            )
        )
    else:
        print(_("Lineage: no in-range owner was proven."))


def _print_placement(analysis: FixupUnitAnalysis) -> None:
    placement = analysis.placement
    if placement.status == "barrier" and placement.barrier is not None:
        print(
            _("Placement: patch first stops at {commit}.").format(
                commit=placement.barrier[:12]
            )
        )
    elif placement.status == "commutes-through":
        print(_("Placement: patch commutes through the entire range."))
    else:
        print(
            _("Placement: unknown ({detail}).").format(
                detail=placement.detail or "placement-analysis-failed"
            )
        )


def print_fixup_suggestion(
    *,
    commit_range: FixupRange,
    hunk_hash: str,
    line_id_ranges: tuple[tuple[int, int], ...] | None,
    analysis: FixupUnitAnalysis,
    history: LineageHistoryEvidence,
    candidate_commits: tuple[str, ...],
    candidate: SuggestFixupCandidate | None,
    result: str,
    porcelain: bool,
) -> None:
    """Print one complete suggestion result."""
    if porcelain:
        print(
            json.dumps(
                _porcelain_record(
                    commit_range=commit_range,
                    hunk_hash=hunk_hash,
                    line_id_ranges=line_id_ranges,
                    analysis=analysis,
                    history=history,
                    candidate_commits=candidate_commits,
                    candidate=candidate,
                    result=result,
                ),
                indent=2,
            )
        )
        return

    print(
        _("Selected unit {unit} in {path} [{status}]").format(
            unit=analysis.unit.unit_id[:12],
            path=display_path(analysis.unit.path),
            status=analysis.status,
        )
    )
    _print_lineage(analysis)
    _print_placement(analysis)
    print(
        _("Decision: {reason}.").format(
            reason=fixup_reason_text(analysis)
        )
    )

    if candidate is None:
        return

    details = _commit_details(candidate.commit)
    print()
    print(
        _("Candidate {iteration} of {total}: {commit} {subject}").format(
            iteration=candidate.iteration,
            total=candidate.total,
            commit=candidate.commit[:12],
            subject=terminal_safe_text(details["subject"]),
        )
    )
    print(
        _("Candidate evidence: {sources}").format(
            sources=", ".join(candidate.sources)
        )
    )
    _show_commit_diff_for_file(candidate.commit, analysis.unit.path)
    print(
        _("Run: git commit --fixup={commit}").format(
            commit=candidate.commit[:12]
        )
    )
