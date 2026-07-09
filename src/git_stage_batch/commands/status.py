"""Status command implementation."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from ..data.progress import format_id_range
from ..data.selected_change.store import (
    SelectedChangeKind,
)
from ..data.session import session_is_active
from ..data.status_summary import read_status_summary as _read_status_summary
from ..exceptions import CommandError
from ..i18n import _
from ..output.status_prompt import prompt_needs_status_summary, render_prompt_status
from ..utils.git import require_git_repository, run_git_command


def _selected_kind_label(selected_kind: str | None) -> str:
    labels = {
        SelectedChangeKind.HUNK.value: _("Current hunk:"),
        SelectedChangeKind.FILE.value: _("Current file review:"),
        SelectedChangeKind.BATCH_FILE.value: _("Current batch file review:"),
        SelectedChangeKind.RENAME.value: _("Current rename:"),
        SelectedChangeKind.DELETION.value: _("Current text file deletion:"),
        SelectedChangeKind.BINARY.value: _("Current binary file:"),
        SelectedChangeKind.BATCH_BINARY.value: _("Current batch binary file:"),
        SelectedChangeKind.GITLINK.value: _("Current submodule pointer:"),
        SelectedChangeKind.BATCH_GITLINK.value: _("Current batch submodule pointer:"),
    }
    return labels.get(selected_kind or SelectedChangeKind.HUNK.value, _("Current selection:"))


def _git_directory_for_prompt() -> Path | None:
    """Return the git directory for prompt rendering, or None outside a repo."""
    try:
        result = run_git_command(["rev-parse", "--absolute-git-dir"], check=False, requires_index_lock=False)
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    git_dir = result.stdout.strip()
    return Path(git_dir) if git_dir else None


def command_status(*, porcelain: bool = False, prompt_format: str | None = None) -> None:
    """Show session progress and selected state.

    Args:
        porcelain: If True, output JSON for scripting instead of human-readable text
        prompt_format: If set, render this format string only for active sessions
    """
    if porcelain and prompt_format is not None:
        raise CommandError(_("Cannot use --porcelain with --for-prompt."))

    if prompt_format is not None:
        git_dir = _git_directory_for_prompt()
        if git_dir is None or not session_is_active(git_dir):
            return
    else:
        require_git_repository()

    # Only treat an active abort marker as a live session. The state directory
    # can persist after cleanup because batch metadata is intentionally kept.
    if prompt_format is None and not session_is_active():
        if porcelain:
            print(json.dumps({"session": {"active": False}}))
        else:
            print(_("No batch staging session in progress."), file=sys.stderr)
            print(_("Run 'git-stage-batch start' to begin."), file=sys.stderr)
        return

    if prompt_format is not None:
        output = (
            _read_status_summary()
            if prompt_needs_status_summary(prompt_format)
            else None
        )
        print(render_prompt_status(prompt_format, output), end="")
        return

    output = _read_status_summary()

    if porcelain:
        print(json.dumps(output, indent=2))
    else:
        # Human-readable progress report
        iteration = output["session"]["iteration"]
        status_value = output["session"]["status"]
        selected_summary = output["selected_change"]
        file_review_summary = output["file_review"]
        progress = output["progress"]
        skipped_hunks = output["skipped_hunks"]
        status_label = _("in progress") if status_value == "in_progress" else _("complete")
        print(_("Session: iteration {iteration} ({status})").format(
            iteration=iteration,
            status=status_label,
        ))
        print()

        if selected_summary:
            ids_str = format_id_range(selected_summary["ids"])
            print(_selected_kind_label(selected_summary.get("kind")))
            if selected_summary.get("line") is None:
                print(_("  {file}").format(file=selected_summary["file"]))
            else:
                print(_("  {file}:{line}").format(
                    file=selected_summary["file"],
                    line=selected_summary["line"],
                ))
            if ids_str:
                print(_("  [#{ids}]").format(ids=ids_str))
            if selected_summary.get("change_type"):
                print(_("  {change_type}").format(change_type=selected_summary["change_type"]))
            print()

        if file_review_summary:
            print(_("Last file review:"))
            source = file_review_summary["source"]
            if file_review_summary["batch_name"]:
                source = _("batch {name}").format(name=file_review_summary["batch_name"])
            print(_("  source: {source}").format(source=source))
            print(
                _("  pages: {pages}/{count}").format(
                    pages=format_id_range(file_review_summary["shown_pages"]),
                    count=file_review_summary["page_count"],
                )
            )
            if not file_review_summary["entire_file_shown"]:
                print(_("  partial review; bare whole-file actions will require confirmation by command"))
            if not file_review_summary["fresh"]:
                print(_("  stale; run show again before using pathless line actions"))
            print()

        print(_("Progress this iteration:"))
        print(_("  Included:  {count} hunks").format(count=progress["included"]))
        print(_("  Skipped:   {count} hunks").format(count=len(skipped_hunks)))
        print(_("  Discarded: {count} hunks").format(count=progress["discarded"]))
        print(_("  Remaining: ~{count} hunks").format(count=progress["remaining"]))

        if skipped_hunks:
            print()
            print(_("Skipped hunks:"))
            for hunk in skipped_hunks:
                ids_str = format_id_range(hunk.get("ids", []))
                if hunk.get("line") is None:
                    print(_("  {file}").format(file=hunk["file"]))
                elif ids_str:
                    print(_("  {file}:{line} [#{ids}]").format(
                        file=hunk["file"],
                        line=hunk["line"],
                        ids=ids_str,
                    ))
                else:
                    print(_("  {file}:{line}").format(file=hunk["file"], line=hunk["line"]))
