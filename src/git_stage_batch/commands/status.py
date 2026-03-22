"""Status command implementation."""

from __future__ import annotations

import sys

from ..core.hashing import compute_stable_hunk_hash
from ..core.diff_parser import parse_unified_diff_streaming
from ..i18n import _
from ..utils.file_io import read_text_file_contents
from ..utils.git import require_git_repository, stream_git_command
from ..utils.paths import (
    get_block_list_file_path,
    get_context_lines,
    get_state_directory_path,
)


def command_status() -> None:
    """Show selected session status."""
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

    # Count remaining hunks and find selected file
    remaining_hunks = 0
    selected_file = None
    total_hunks = 0
    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
        total_hunks += 1
        patch_text = patch.to_patch_text()
        patch_hash = compute_stable_hunk_hash(patch_text)
        if patch_hash not in blocked_hashes:
            if selected_file is None:
                selected_file = patch.new_path
            remaining_hunks += 1

    # Display status
    print(_("Session active"))
    print(_("Processed: {} hunks").format(processed_count))
    print(_("Remaining: {} hunks").format(remaining_hunks))

    if selected_file:
        print(_("Current file: {}").format(selected_file))
    elif total_hunks == 0:
        print(_("No changes in working tree"))
    else:
        print(_("All hunks processed"))
