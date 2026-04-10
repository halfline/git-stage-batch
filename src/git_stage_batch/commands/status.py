"""Status command implementation."""

from __future__ import annotations

import json
import sys

from ..core.hashing import compute_stable_hunk_hash
from ..core.diff_parser import parse_unified_diff_streaming
from ..data.hunk_tracking import format_id_range, snapshots_are_stale
from ..data.line_state import load_current_lines_from_state
from ..data.session import get_iteration_count
from ..i18n import _
from ..core.line_selection import format_line_ids
from ..utils.file_io import read_file_paths_file, read_text_file_contents
from ..utils.git import require_git_repository, stream_git_command
from ..utils.paths import (
    get_block_list_file_path,
    get_blocked_files_file_path,
    get_context_lines,
    get_current_hunk_patch_file_path,
    get_current_lines_json_file_path,
    get_discarded_hunks_file_path,
    get_included_hunks_file_path,
    get_skipped_hunks_jsonl_file_path,
    get_state_directory_path,
)


def estimate_remaining_hunks() -> int:
    """Estimate number of remaining unprocessed hunks.

    Returns:
        Number of hunks not yet included, skipped, or discarded
    """
    # Filter out blocked hunks
    blocklist_content = read_text_file_contents(get_block_list_file_path())
    blocked_hashes = set(blocklist_content.splitlines()) if blocklist_content else set()

    # Filter out hunks from blocked files
    blocked_files = read_file_paths_file(get_blocked_files_file_path())

    remaining = 0
    try:
        for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
            hunk_hash = compute_stable_hunk_hash(patch.to_patch_bytes())
            file_path = patch.old_path if patch.old_path != "/dev/null" else patch.new_path
            file_path = file_path.removeprefix("a/").removeprefix("b/")

            if hunk_hash not in blocked_hashes and file_path not in blocked_files:
                remaining += 1
    except Exception:
        return 0

    return remaining


def command_status(*, porcelain: bool = False) -> None:
    """Show session progress and current state.

    Args:
        porcelain: If True, output JSON for scripting instead of human-readable text
    """
    require_git_repository()

    # Check if session is active
    state_dir = get_state_directory_path()
    if not state_dir.exists():
        if porcelain:
            print(json.dumps({"session_active": False}))
        else:
            print(_("No batch staging session in progress."), file=sys.stderr)
            print(_("Run 'git-stage-batch start' to begin."), file=sys.stderr)
        return

    # Gather metrics
    iteration = get_iteration_count()

    # Count processed hunks this iteration
    included_content = read_text_file_contents(get_included_hunks_file_path())
    included_count = len([h for h in included_content.splitlines() if h.strip()])

    discarded_content = read_text_file_contents(get_discarded_hunks_file_path())
    discarded_count = len([h for h in discarded_content.splitlines() if h.strip()])

    # Parse skipped hunks JSONL
    skipped_hunks = []
    jsonl_path = get_skipped_hunks_jsonl_file_path()
    if jsonl_path.exists():
        for line in read_text_file_contents(jsonl_path).splitlines():
            if line.strip():
                try:
                    skipped_hunks.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # Skip malformed lines

    # Check for current hunk
    has_current = get_current_hunk_patch_file_path().exists()
    current_summary = None
    if has_current:
        if get_current_lines_json_file_path().exists():
            try:
                data = json.loads(read_text_file_contents(get_current_lines_json_file_path()))
                file_path = data["path"]
                if snapshots_are_stale(file_path):
                    # Stale cache, no current hunk
                    has_current = False
                else:
                    current_lines = load_current_lines_from_state()
                    current_summary = {
                        "file": current_lines.path,
                        "line": current_lines.header.old_start,
                        "ids": current_lines.changed_line_ids()
                    }
            except (json.JSONDecodeError, KeyError):
                has_current = False

    # Estimate remaining hunks
    remaining_estimate = estimate_remaining_hunks()

    if porcelain:
        # Machine-readable JSON output
        output = {
            "session_active": True,
            "iteration": iteration,
            "status": "in_progress" if has_current else "complete",
            "current_hunk": current_summary,
            "progress": {
                "included": included_count,
                "skipped": len(skipped_hunks),
                "discarded": discarded_count,
                "remaining": remaining_estimate
            },
            "skipped_hunks": skipped_hunks
        }
        print(json.dumps(output, indent=2))
    else:
        # Human-readable progress report
        status = _("in progress") if has_current else _("complete")
        print(_("Session: iteration {} ({})").format(iteration, status))
        print()

        if current_summary:
            ids_str = format_id_range(current_summary["ids"])
            print(_("Current hunk:"))
            print(_("  {}:{}").format(current_summary['file'], current_summary['line']))
            print(_("  [#{}]").format(ids_str))
            print()

        print(_("Progress this iteration:"))
        print(_("  Included:  {} hunks").format(included_count))
        print(_("  Skipped:   {} hunks").format(len(skipped_hunks)))
        print(_("  Discarded: {} hunks").format(discarded_count))
        print(_("  Remaining: ~{} hunks").format(remaining_estimate))

        if skipped_hunks:
            print()
            print(_("Skipped hunks:"))
            for hunk in skipped_hunks:
                ids_str = format_id_range(hunk["ids"])
                print(_("  {}:{} [#{}]").format(hunk['file'], hunk['line'], ids_str))
