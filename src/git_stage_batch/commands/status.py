"""Status command implementation."""

from __future__ import annotations

import json
import sys

from ..core.hashing import compute_stable_hunk_hash
from ..core.diff_parser import parse_unified_diff_streaming
from ..data.line_state import load_current_lines_from_state
from ..i18n import _
from ..core.line_selection import format_line_ids
from ..utils.file_io import read_text_file_contents
from ..utils.git import require_git_repository, stream_git_command
from ..utils.paths import (
    get_block_list_file_path,
    get_context_lines,
    get_current_lines_json_file_path,
    get_state_directory_path,
)


def command_status() -> None:
    """Show current session status."""
    require_git_repository()

    # Check if session is active
    state_dir = get_state_directory_path()
    if not state_dir.exists():
        print(_("No batch staging session in progress."), file=sys.stderr)
        print(_("Run 'git-stage-batch start' to begin."), file=sys.stderr)
        return

    # Load blocklist
    blocklist_path = get_block_list_file_path()
    blocklist_text = read_text_file_contents(blocklist_path)
    blocked_hashes = set(blocklist_text.splitlines()) if blocklist_text else set()
    processed_count = len(blocked_hashes)

    # Count remaining hunks and find current file
    remaining_hunks = 0
    current_file = None
    total_hunks = 0
    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
        total_hunks += 1
        patch_text = patch.to_patch_text()
        patch_hash = compute_stable_hunk_hash(patch_text)
        if patch_hash not in blocked_hashes:
            if current_file is None:
                current_file = patch.new_path
            remaining_hunks += 1

    # Display status
    print(_("Session active"))
    print(_("Processed: {} hunks").format(processed_count))
    print(_("Remaining: {} hunks").format(remaining_hunks))

    if current_file:
        print(_("Current file: {}").format(current_file))

        # Try to load cached hunk location if available
        current_lines_path = get_current_lines_json_file_path()
        if current_lines_path.exists():
            try:
                current_lines = load_current_lines_from_state()
                if current_lines and current_lines.path == current_file:
                    # Extract line IDs from current_lines
                    line_ids = []
                    for entry in current_lines.lines:
                        if entry.old_line_number is not None:
                            line_ids.append(str(entry.old_line_number))

                    if line_ids:
                        # Format as line range
                        line_range = format_line_ids(line_ids)
                        print(_("  Lines: {line_range}").format(line_range=line_range))
            except (json.JSONDecodeError, KeyError):
                # Cached state is corrupted, skip
                pass
    elif total_hunks == 0:
        print(_("No changes in working tree"))
    else:
        print(_("All hunks processed"))
