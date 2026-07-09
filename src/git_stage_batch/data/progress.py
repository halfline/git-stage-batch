"""Progress tracking for session state."""

from __future__ import annotations

import json

from ..core.models import (
    BinaryFileChange,
    GitlinkChange,
    LineLevelChange,
    RenameChange,
    TextFileDeletionChange,
)
from ..utils.file_io import (
    count_nonblank_text_file_lines,
    read_text_file_contents,
    read_text_file_line_set,
    write_text_file_contents,
)
from ..utils.git_command import run_git_command
from ..utils.paths import (
    get_line_changes_json_file_path,
    get_discarded_hunks_file_path,
    get_included_hunks_file_path,
    get_skipped_hunks_jsonl_file_path,
)


def record_hunk_included(hunk_hash: str) -> None:
    """Record that a hunk was included (staged)."""
    included_path = get_included_hunks_file_path()
    existing = read_text_file_line_set(included_path)
    existing.add(hunk_hash)
    write_text_file_contents(included_path, "\n".join(sorted(existing)) + "\n" if existing else "")


def record_hunk_discarded(hunk_hash: str) -> None:
    """Record that a hunk was discarded (removed from working tree)."""
    record_hunks_discarded([hunk_hash])


def record_hunks_discarded(hunk_hashes: list[str]) -> None:
    """Record that hunks were discarded (removed from working tree)."""
    new_hashes = {hunk_hash for hunk_hash in hunk_hashes if hunk_hash}
    if not new_hashes:
        return
    discarded_path = get_discarded_hunks_file_path()
    existing = read_text_file_line_set(discarded_path)
    existing.update(new_hashes)
    write_text_file_contents(discarded_path, "\n".join(sorted(existing)) + "\n" if existing else "")


def record_hunk_skipped(line_changes: LineLevelChange, hunk_hash: str) -> None:
    """Record that a hunk was skipped with metadata for display."""
    first_changed_line = None
    for entry in line_changes.lines:
        if entry.kind != " ":
            first_changed_line = entry.old_line_number or entry.new_line_number
            break

    metadata = {
        "hash": hunk_hash,
        "file": line_changes.path,
        "line": first_changed_line or 0,
        "ids": line_changes.changed_line_ids(),
    }
    _append_skipped_hunk_metadata(metadata)


def record_binary_hunk_skipped(binary_change: BinaryFileChange, hunk_hash: str) -> None:
    """Record that a binary change was skipped with file-level metadata."""
    file_path = binary_change.new_path if binary_change.new_path != "/dev/null" else binary_change.old_path
    metadata = {
        "hash": hunk_hash,
        "file": file_path,
        "line": None,
        "ids": [],
        "change_type": binary_change.change_type,
    }
    _append_skipped_hunk_metadata(metadata)


def record_gitlink_hunk_skipped(gitlink_change: GitlinkChange, hunk_hash: str) -> None:
    """Record that a gitlink change was skipped with file-level metadata."""
    metadata = {
        "hash": hunk_hash,
        "file": gitlink_change.path(),
        "line": None,
        "ids": [],
        "type": "submodule",
        "change_type": gitlink_change.change_type,
        "old_oid": gitlink_change.old_oid,
        "new_oid": gitlink_change.new_oid,
    }
    _append_skipped_hunk_metadata(metadata)


def record_rename_hunk_skipped(rename_change: RenameChange, hunk_hash: str) -> None:
    """Record that a rename change was skipped with file-level metadata."""
    metadata = {
        "hash": hunk_hash,
        "file": rename_change.new_path,
        "line": None,
        "ids": [],
        "type": "rename",
        "old_path": rename_change.old_path,
        "new_path": rename_change.new_path,
    }
    _append_skipped_hunk_metadata(metadata)


def record_text_deletion_hunk_skipped(
    deletion_change: TextFileDeletionChange,
    hunk_hash: str,
) -> None:
    """Record that a whole-text-file deletion was skipped with file-level metadata."""
    metadata = {
        "hash": hunk_hash,
        "file": deletion_change.path(),
        "line": None,
        "ids": [],
        "type": "text-deletion",
        "change_type": "deleted",
    }
    _append_skipped_hunk_metadata(metadata)


def _append_skipped_hunk_metadata(metadata: dict) -> None:
    jsonl_path = get_skipped_hunks_jsonl_file_path()
    with jsonl_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(metadata) + "\n")


def format_id_range(ids: list[int]) -> str:
    """Format list of IDs as compact range string (e.g., '1-5,7,9-11')."""
    if not ids:
        return ""

    ids = sorted(ids)
    ranges = []
    start = ids[0]
    end = ids[0]

    for i in range(1, len(ids)):
        if ids[i] == end + 1:
            end = ids[i]
        else:
            if start == end:
                ranges.append(str(start))
            else:
                ranges.append(f"{start}-{end}")
            start = end = ids[i]

    if start == end:
        ranges.append(str(start))
    else:
        ranges.append(f"{start}-{end}")

    return ",".join(ranges)


def get_hunk_counts() -> dict[str, int]:
    """Get counts of hunks in various states.

    Returns:
        Dictionary with keys:
        - included: Number of hunks staged to index
        - skipped: Number of hunks explicitly skipped
        - discarded: Number of hunks discarded from working tree
        - remaining: Number of hunks still pending (approximation)
    """
    included_file = get_included_hunks_file_path()
    skipped_file = get_skipped_hunks_jsonl_file_path()
    discarded_file = get_discarded_hunks_file_path()

    # Count included hunks (one per line)
    included_count = count_nonblank_text_file_lines(included_file)

    # Count skipped hunks (JSONL format)
    skipped_count = count_nonblank_text_file_lines(skipped_file)

    # Count discarded hunks (one per line)
    discarded_count = count_nonblank_text_file_lines(discarded_file)

    # Remaining is harder to determine without running diff, so we set to 0
    # The caller should provide this if needed
    remaining_count = 0

    return {
        "included": included_count,
        "skipped": skipped_count,
        "discarded": discarded_count,
        "remaining": remaining_count,
    }


def get_file_progress() -> tuple[int, int]:
    """Get selected file progress.

    Returns:
        Tuple of (selected_file_index, total_files)
        Returns (0, 0) if no selected file is cached
    """
    # Read selected lines to get the file path
    line_changes_file = get_line_changes_json_file_path()
    if not line_changes_file.exists():
        return (0, 0)

    try:
        content = read_text_file_contents(line_changes_file)
        data = json.loads(content)
        selected_path = data.get("path", "")
        if not selected_path:
            return (0, 0)

        # Get all changed files from git diff
        result = run_git_command(
            [
                "-c",
                "diff.ignoreSubmodules=none",
                "diff",
                "--ignore-submodules=none",
                "--name-only",
                "HEAD",
            ],
            check=False,
            requires_index_lock=False,
        )
        if result.returncode != 0:
            return (0, 0)

        files = [f for f in result.stdout.strip().splitlines() if f.strip()]
        total = len(files)

        if selected_path in files:
            # 1-based index
            selected_index = files.index(selected_path) + 1
            return (selected_index, total)
        else:
            # File not in diff
            return (0, total)

    except (json.JSONDecodeError, KeyError):
        return (0, 0)
