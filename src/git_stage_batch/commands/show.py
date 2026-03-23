"""Show command implementation."""

from __future__ import annotations

import json

from ..core.hashing import compute_stable_hunk_hash
from ..core.diff_parser import build_current_lines_from_patch_text, parse_unified_diff_streaming
from ..data.line_state import convert_current_lines_to_serializable_dict
from ..i18n import _
from ..output.patch import print_colored_patch
from ..core.diff_parser import write_snapshots_for_current_file_path
from ..utils.file_io import read_text_file_contents, write_text_file_contents
from ..utils.git import require_git_repository, stream_git_command
from ..utils.paths import (
    ensure_state_directory_exists,
    get_block_list_file_path,
    get_context_lines,
    get_current_hunk_hash_file_path,
    get_current_hunk_patch_file_path,
    get_current_lines_json_file_path,
)


def command_show(*, porcelain: bool = False) -> None:
    """Show the first unprocessed hunk.

    Args:
        porcelain: If True, produce no output and exit with code 0 if hunk found, 1 if none
    """
    require_git_repository()
    ensure_state_directory_exists()

    # Load blocklist
    blocklist_path = get_block_list_file_path()
    blocklist_text = read_text_file_contents(blocklist_path)
    blocked_hashes = set(blocklist_text.splitlines())

    # Stream diff and show first unblocked hunk
    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
        patch_text = patch.to_patch_text()
        patch_hash = compute_stable_hunk_hash(patch_text)
        if patch_hash not in blocked_hashes:
            # Cache current hunk state for status and other commands
            current_lines = build_current_lines_from_patch_text(patch_text)
            write_text_file_contents(get_current_hunk_patch_file_path(), patch_text)
            write_text_file_contents(get_current_hunk_hash_file_path(), patch_hash)
            write_text_file_contents(
                get_current_lines_json_file_path(),
                json.dumps(convert_current_lines_to_serializable_dict(current_lines),
                          ensure_ascii=False, indent=0)
            )
            write_snapshots_for_current_file_path(current_lines.path)

            # Display this unprocessed hunk (unless porcelain mode)
            if not porcelain:
                print_colored_patch(patch_text)
            return

    # Either no changes or all hunks are blocked
    if porcelain:
        # Exit with code 1 for scripts
        import sys
        sys.exit(1)
    else:
        print(_("No more hunks to process."))
