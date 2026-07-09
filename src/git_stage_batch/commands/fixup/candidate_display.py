"""Suggest-fixup candidate display helpers."""

from __future__ import annotations

import json
import subprocess

from ...exceptions import exit_with_error
from ...i18n import _
from ...utils.git_command import run_git_command
from .history import get_commit_details, show_commit_diff_for_file


def show_last_suggest_fixup_candidate(
    *,
    state: dict | None,
    effective_boundary: str,
    file_path: str,
    porcelain: bool,
) -> None:
    """Display the last persisted suggest-fixup candidate."""
    if not state or not state.get("last_shown_commit"):
        exit_with_error(
            "No previous candidate to show.\n"
            + "Run suggest-fixup without --last to find a candidate."
        )

    display_suggest_fixup_candidate(
        candidate_commit=state["last_shown_commit"],
        iteration=state["iteration"],
        boundary=state.get("boundary", effective_boundary),
        file_path=file_path,
        porcelain=porcelain,
    )


def display_suggest_fixup_candidate(
    *,
    candidate_commit: str,
    iteration: int,
    boundary: str,
    file_path: str,
    porcelain: bool,
) -> None:
    """Display a suggest-fixup candidate for porcelain or human output."""
    if porcelain:
        commit_details = get_commit_details(candidate_commit)
        output = {
            "candidate": commit_details,
            "iteration": iteration,
            "boundary": boundary,
        }
        print(json.dumps(output, indent=2))
        return

    try:
        show_result = run_git_command(
            ["show", "--no-patch", "--format=%h %s", candidate_commit],
            check=True,
            requires_index_lock=False,
        )
        commit_info = show_result.stdout.strip()
    except subprocess.CalledProcessError:
        commit_info = candidate_commit[:7]

    print(
        _("Candidate {iteration}: {info}").format(
            iteration=iteration,
            info=commit_info,
        )
    )
    show_commit_diff_for_file(candidate_commit, file_path)
    print(_("Run: git commit --fixup={commit}").format(commit=candidate_commit[:7]))
