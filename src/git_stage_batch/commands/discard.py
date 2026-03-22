"""Discard command implementation."""

from __future__ import annotations

import subprocess
import sys

from ..core.hashing import compute_stable_hunk_hash
from ..core.diff_parser import parse_unified_diff_streaming
from ..i18n import _
from ..utils.file_io import append_lines_to_file, read_text_file_contents
from ..utils.git import get_git_repository_root_path, require_git_repository, stream_git_command
from ..utils.paths import (
    ensure_state_directory_exists,
    get_block_list_file_path,
    get_context_lines,
)


def command_discard(*, quiet: bool = False) -> None:
    """Discard the current hunk from the working tree."""
    require_git_repository()
    ensure_state_directory_exists()

    # Load blocklist to skip already-processed hunks
    blocklist_path = get_block_list_file_path()
    if blocklist_path.exists():
        blocklist_text = read_text_file_contents(blocklist_path)
        blocked_hashes = set(blocklist_text.splitlines())
    else:
        blocked_hashes = set()

    # Stream diff and find first unblocked hunk
    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
        patch_text = patch.to_patch_text()
        patch_hash = compute_stable_hunk_hash(patch_text)

        if patch_hash in blocked_hashes:
            continue

        # Extract filename for user feedback
        filename = patch.new_path if patch.new_path else "unknown"

        # Apply the hunk in reverse to discard from working tree
        try:
            subprocess.run(
                ["git", "apply", "--reverse"],
                input=patch_text,
                text=True,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            print(_("Failed to discard hunk: {}").format(e.stderr), file=sys.stderr)
            return

        # After reverse-applying a new file, delete it if it became empty
        # (git apply -R on new files empties them but doesn't delete them)
        is_new_file = "--- /dev/null" in patch_text
        if is_new_file:
            absolute_path = get_git_repository_root_path() / filename
            if absolute_path.exists():
                content = read_text_file_contents(absolute_path)
                if not content.strip():  # File is empty
                    absolute_path.unlink()

        # Add hash to blocklist
        append_lines_to_file(blocklist_path, [patch_hash])

        if not quiet:
            print(_("✓ Hunk discarded from {}").format(filename), file=sys.stderr)
        break

    if not quiet:
        print(_("No more hunks to process."), file=sys.stderr)
