"""Discard command implementation."""

from __future__ import annotations

import subprocess
import sys

from ..core.hashing import compute_stable_hunk_hash
from ..core.diff_parser import get_first_matching_file_from_diff, parse_unified_diff_streaming
from ..data.session import snapshot_file_if_untracked
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

        # Extract filename for user feedback and snapshotting
        filename = patch.new_path if patch.new_path else "unknown"
        old_path = patch.old_path if patch.old_path else None

        # Snapshot file if untracked before discarding
        # Use new path unless it's a deletion (where new path is /dev/null)
        file_path = filename if filename != "/dev/null" else old_path
        if file_path and file_path != "/dev/null":
            snapshot_file_if_untracked(file_path)

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


def command_discard_file() -> None:
    """Discard the entire current file from the working tree."""
    require_git_repository()
    ensure_state_directory_exists()

    # Load blocklist
    blocklist_path = get_block_list_file_path()
    blocklist_text = read_text_file_contents(blocklist_path)
    blocked_hashes = set(blocklist_text.splitlines())

    # Find first non-blocked hunk to get the target file
    def is_unblocked(patch_text: str) -> bool:
        return compute_stable_hunk_hash(patch_text) not in blocked_hashes

    target_file = get_first_matching_file_from_diff(
        context_lines=get_context_lines(),
        predicate=is_unblocked
    )

    if target_file is None:
        print(_("No changes to discard."), file=sys.stderr)
        return

    # Snapshot the file if it's untracked (for abort functionality)
    snapshot_file_if_untracked(target_file)

    # Stream through hunks and collect hashes from target file BEFORE removing it
    # (git rm -f will stage the deletion, making hunks disappear from git diff)
    hashes_to_block = []
    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
        if patch.new_path != target_file:
            continue

        patch_text = patch.to_patch_text()
        patch_hash = compute_stable_hunk_hash(patch_text)

        if patch_hash not in blocked_hashes:
            hashes_to_block.append(patch_hash)

    # Remove the file from working tree
    try:
        subprocess.run(
            ["git", "rm", "-f", target_file],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        print(_("Failed to discard file: {}").format(e.stderr.decode()), file=sys.stderr)
        return

    # Mark all collected hashes as processed
    for patch_hash in hashes_to_block:
        append_lines_to_file(blocklist_path, [patch_hash])

    print(_("✓ File discarded: {}").format(target_file), file=sys.stderr)
