"""Show command implementation."""

from __future__ import annotations

from ..core.hashing import compute_stable_hunk_hash
from ..core.diff_parser import parse_unified_diff_streaming
from ..i18n import _
from ..output.patch import print_colored_patch
from ..utils.file_io import read_text_file_contents
from ..utils.git import require_git_repository, stream_git_command
from ..utils.paths import ensure_state_directory_exists, get_block_list_file_path, get_context_lines


def command_show() -> None:
    """Show the first unprocessed hunk."""
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
            # Display this unprocessed hunk
            print_colored_patch(patch_text)
            return

    # Either no changes or all hunks are blocked
    print(_("No more hunks to process."))
