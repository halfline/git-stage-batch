"""Hunk navigation and state management."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Optional

from ..core.hashing import compute_stable_hunk_hash
from ..core.models import LineLevelChange
from ..core.diff_parser import build_line_changes_from_patch_text, parse_unified_diff_streaming
from ..i18n import _
from ..output.hunk import print_line_level_changes
from ..utils.file_io import read_file_paths_file, read_text_file_contents, write_text_file_contents
from ..utils.git import stream_git_command
from ..utils.paths import (
    get_block_list_file_path,
    get_blocked_files_file_path,
    get_context_lines,
    get_selected_hunk_hash_file_path,
    get_selected_hunk_patch_file_path,
    get_line_changes_json_file_path,
    get_index_snapshot_file_path,
    get_processed_include_ids_file_path,
    get_working_tree_snapshot_file_path,
)
from .line_state import convert_line_changes_to_serializable_dict


def clear_selected_change_state_files() -> None:
    """Clear all cached selected hunk state files."""
    get_selected_hunk_patch_file_path().unlink(missing_ok=True)
    get_selected_hunk_hash_file_path().unlink(missing_ok=True)
    get_line_changes_json_file_path().unlink(missing_ok=True)
    get_index_snapshot_file_path().unlink(missing_ok=True)
    get_working_tree_snapshot_file_path().unlink(missing_ok=True)
    get_processed_include_ids_file_path().unlink(missing_ok=True)


def fetch_next_change() -> Optional[LineLevelChange]:
    """Find the next hunk that isn't blocked and cache it as selected.

    Returns:
        LineLevelChange for the hunk if found, None otherwise
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
            line_changes = build_line_changes_from_patch_text(patch_text)
            if line_changes.path in blocked_files:
                continue

            write_text_file_contents(get_selected_hunk_patch_file_path(), patch_text)
            write_text_file_contents(get_selected_hunk_hash_file_path(), hunk_hash)

            write_text_file_contents(get_line_changes_json_file_path(),
                                     json.dumps(convert_line_changes_to_serializable_dict(line_changes),
                                                ensure_ascii=False, indent=0))

            return line_changes
    except subprocess.CalledProcessError:
        # Git diff failed (e.g., no changes)
        pass

    return None


def advance_to_next_change() -> None:
    """Clear selected hunk state and advance to the next unblocked hunk."""
    clear_selected_change_state_files()
    fetch_next_change()


def show_selected_change() -> None:
    """Display the currently cached hunk.

    This is a helper for commands that need to display the cached hunk
    without advancing (e.g., start, again).
    """
    patch_path = get_selected_hunk_patch_file_path()
    if patch_path.exists():
        patch_text = read_text_file_contents(patch_path)
        line_changes = build_line_changes_from_patch_text(patch_text)
        print_line_level_changes(line_changes)


def advance_to_and_show_next_change() -> None:
    """Advance to next hunk and display it (CLI workflow helper).

    This is a convenience wrapper for CLI commands that combines advancing
    to the next hunk with displaying it. If no more hunks exist, prints
    a message to stderr.
    """
    advance_to_next_change()

    # Check if a hunk was cached
    patch_path = get_selected_hunk_patch_file_path()
    if patch_path.exists():
        patch_text = read_text_file_contents(patch_path)
        line_changes = build_line_changes_from_patch_text(patch_text)
        print_line_level_changes(line_changes)
    else:
        print(_("No more hunks to process."), file=sys.stderr)
