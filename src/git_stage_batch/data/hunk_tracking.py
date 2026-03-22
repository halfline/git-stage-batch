"""Hunk navigation and state management."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Optional

from ..core.hashing import compute_stable_hunk_hash
from ..core.models import CurrentLines
from ..core.diff_parser import build_current_lines_from_patch_text, parse_unified_diff_streaming
from ..i18n import _
from ..output.patch import print_colored_patch
from ..utils.file_io import read_file_paths_file, read_text_file_contents, write_text_file_contents
from ..utils.git import stream_git_command
from ..utils.paths import (
    get_block_list_file_path,
    get_blocked_files_file_path,
    get_context_lines,
    get_current_hunk_hash_file_path,
    get_current_hunk_patch_file_path,
    get_current_lines_json_file_path,
    get_index_snapshot_file_path,
    get_processed_include_ids_file_path,
    get_working_tree_snapshot_file_path,
)
from .line_state import convert_current_lines_to_serializable_dict


def clear_current_hunk_state_files() -> None:
    """Clear all cached current hunk state files."""
    get_current_hunk_patch_file_path().unlink(missing_ok=True)
    get_current_hunk_hash_file_path().unlink(missing_ok=True)
    get_current_lines_json_file_path().unlink(missing_ok=True)
    get_index_snapshot_file_path().unlink(missing_ok=True)
    get_working_tree_snapshot_file_path().unlink(missing_ok=True)
    get_processed_include_ids_file_path().unlink(missing_ok=True)


def find_and_cache_next_unblocked_hunk() -> Optional[CurrentLines]:
    """Find the next hunk that isn't blocked and cache it as current.

    Returns:
        CurrentLines for the hunk if found, None otherwise
    """
    # Get list of blocked files
    blocked_files = set(read_file_paths_file(get_blocked_files_file_path()))

    # Load blocklist
    blocklist_content = read_text_file_contents(get_block_list_file_path())
    blocked_hashes = set(blocklist_content.splitlines())

    # Stream git diff and parse incrementally - stops after first unblocked hunk found
    try:
        for single_hunk in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
            patch_text = single_hunk.to_patch_text()
            hunk_hash = compute_stable_hunk_hash(patch_text)
            if hunk_hash in blocked_hashes:
                continue

            # Skip hunks from blocked files
            current_lines = build_current_lines_from_patch_text(patch_text)
            if current_lines.path in blocked_files:
                continue

            write_text_file_contents(get_current_hunk_patch_file_path(), patch_text)
            write_text_file_contents(get_current_hunk_hash_file_path(), hunk_hash)

            write_text_file_contents(get_current_lines_json_file_path(),
                                     json.dumps(convert_current_lines_to_serializable_dict(current_lines),
                                                ensure_ascii=False, indent=0))

            return current_lines
    except subprocess.CalledProcessError:
        # Git diff failed (e.g., no changes)
        pass

    return None


def advance_to_next_hunk() -> None:
    """Clear current hunk state and advance to the next unblocked hunk."""
    clear_current_hunk_state_files()
    find_and_cache_next_unblocked_hunk()


def show_current_hunk() -> None:
    """Display the currently cached hunk.

    This is a helper for commands that need to display the cached hunk
    without advancing (e.g., start, again).
    """
    patch_path = get_current_hunk_patch_file_path()
    if patch_path.exists():
        patch_text = read_text_file_contents(patch_path)
        print_colored_patch(patch_text)


def advance_to_and_show_next_hunk() -> None:
    """Advance to next hunk and display it (CLI workflow helper).

    This is a convenience wrapper for CLI commands that combines advancing
    to the next hunk with displaying it. If no more hunks exist, prints
    a message to stderr.
    """
    advance_to_next_hunk()

    # Check if a hunk was cached
    patch_path = get_current_hunk_patch_file_path()
    if patch_path.exists():
        patch_text = read_text_file_contents(patch_path)
        print_colored_patch(patch_text)
    else:
        print(_("No more hunks to process."), file=sys.stderr)
