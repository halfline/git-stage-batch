"""Show command implementation."""

from __future__ import annotations

import sys

from ..core.diff_parser import build_current_lines_from_patch_bytes, parse_unified_diff_streaming
from ..core.hashing import compute_stable_hunk_hash
from ..data.session import require_session_started
from ..i18n import _
from ..output import print_annotated_hunk_with_aligned_gutter
from ..utils.file_io import read_text_file_contents, write_text_file_contents
from ..utils.git import require_git_repository, stream_git_command
from ..utils.paths import (
    ensure_state_directory_exists,
    get_block_list_file_path,
    get_context_lines,
    get_current_hunk_hash_file_path,
    get_current_hunk_patch_file_path,
)


def command_show(*, porcelain: bool = False) -> None:
    """Show the first unprocessed hunk.

    Args:
        porcelain: If True, produce no output and exit with code 0 if hunk found, 1 if none
    """
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()

    # Load blocklist
    blocklist_path = get_block_list_file_path()
    blocklist_text = read_text_file_contents(blocklist_path)
    blocked_hashes = set(blocklist_text.splitlines())

    # Stream diff and show first unblocked hunk
    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
        patch_bytes = patch.to_patch_bytes()
        patch_hash = compute_stable_hunk_hash(patch_bytes)
        if patch_hash not in blocked_hashes:
            # Cache current hunk state for status and other commands
            # Decode to text for storage (with errors='replace' for non-UTF-8)
            patch_text = patch_bytes.decode('utf-8', errors='replace')
            write_text_file_contents(get_current_hunk_patch_file_path(), patch_text)
            write_text_file_contents(get_current_hunk_hash_file_path(), patch_hash)

            # Parse and cache current_lines for batch filtering
            from ..batch.display import annotate_with_batch_source
            from ..data.line_state import convert_current_lines_to_serializable_dict
            from ..core.diff_parser import write_snapshots_for_current_file_path
            import json

            current_lines = build_current_lines_from_patch_bytes(patch_bytes, annotator=annotate_with_batch_source)
            from ..utils.paths import get_current_lines_json_file_path
            write_text_file_contents(get_current_lines_json_file_path(),
                                    json.dumps(convert_current_lines_to_serializable_dict(current_lines),
                                              ensure_ascii=False, indent=0))
            write_snapshots_for_current_file_path(current_lines.path)

            # Apply line-level batch filtering
            from ..data.hunk_tracking import apply_line_level_batch_filter_to_cached_hunk
            from ..data.line_state import load_current_lines_from_state

            if apply_line_level_batch_filter_to_cached_hunk():
                # All lines in this hunk are batched, skip to next
                continue

            # Display this unprocessed hunk (unless porcelain mode)
            if not porcelain:
                current_lines = load_current_lines_from_state()
                if current_lines is not None:
                    print_annotated_hunk_with_aligned_gutter(current_lines)
            return

    # Either no changes or all hunks are blocked
    if porcelain:
        # Exit with code 1 for scripts
        sys.exit(1)
    else:
        print(_("No more hunks to process."), file=sys.stderr)
