"""Core editing logic for applying line-level changes to index and working tree."""

from __future__ import annotations

import os
from pathlib import Path

from .models import CurrentLines
from .state import get_state_directory_path, run_git_command, write_text_file_contents


def build_target_index_content_with_selected_lines(
    current_lines: CurrentLines,
    include_ids: set[int],
    base_text: str
) -> str:
    """
    Build the target index content by applying only the selected line changes.

    For each line in the hunk:
    - Context lines: always included
    - Deleted lines ('-'): removed from base if their ID is in include_ids
    - Added lines ('+'): inserted if their ID is in include_ids

    Lines not in include_ids are treated as if they don't exist in the diff.
    """
    base_lines = base_text.splitlines()
    output_lines: list[str] = []

    base_pointer = current_lines.header.old_start - 1  # 0-based
    base_line_count = len(base_lines)

    def push_output(line: str) -> None:
        output_lines.append(line)

    # Copy lines before the hunk
    for index in range(0, min(base_pointer, base_line_count)):
        push_output(base_lines[index])

    # Process hunk lines
    for line_entry in current_lines.lines:
        if line_entry.kind == " ":
            # Context line: always include
            if base_pointer < base_line_count:
                push_output(base_lines[base_pointer])
                base_pointer += 1
        elif line_entry.kind == "-":
            # Deletion: remove from base if included
            if base_pointer < base_line_count:
                if line_entry.id in include_ids:
                    base_pointer += 1      # drop deletion target
                else:
                    push_output(base_lines[base_pointer])
                    base_pointer += 1
        elif line_entry.kind == "+":
            # Addition: insert if included
            if line_entry.id in include_ids:
                push_output(line_entry.text)

    # Copy remaining lines after the hunk
    while 0 <= base_pointer < base_line_count:
        push_output(base_lines[base_pointer])
        base_pointer += 1

    return "\n".join(output_lines) + ("\n" if (base_text.endswith("\n") or output_lines) else "")


def build_target_working_tree_content_with_discarded_lines(
    current_lines: CurrentLines,
    discard_ids: set[int],
    working_text: str
) -> str:
    """
    Build the target working tree content by discarding selected line changes.

    This applies the inverse of changes:
    - Added lines ('+'): removed from working tree if their ID is in discard_ids
    - Deleted lines ('-'): reinserted into working tree if their ID is in discard_ids
    """
    working_lines = working_text.splitlines()
    output_lines: list[str] = []

    working_pointer = current_lines.header.new_start - 1  # 0-based
    working_line_count = len(working_lines)

    def push_output(line: str) -> None:
        output_lines.append(line)

    # Copy lines before the hunk
    for index in range(0, min(working_pointer, working_line_count)):
        push_output(working_lines[index])

    # Process hunk lines
    for line_entry in current_lines.lines:
        if line_entry.kind == " ":
            # Context line: always include
            if working_pointer < working_line_count:
                push_output(working_lines[working_pointer])
                working_pointer += 1
        elif line_entry.kind == "-":
            # Deletion: reinsert if discarding
            if line_entry.id in discard_ids:
                push_output(line_entry.text)   # reinsert deleted line
            else:
                # keep deletion as-is (no output, no pointer advance)
                pass
        elif line_entry.kind == "+":
            # Addition: remove if discarding
            if working_pointer < working_line_count:
                if line_entry.id in discard_ids:
                    working_pointer += 1       # drop inserted line
                else:
                    push_output(working_lines[working_pointer])
                    working_pointer += 1
            else:
                # addition beyond EOF
                if line_entry.id in discard_ids:
                    # nothing to drop
                    pass
                else:
                    # addition kept would already be in file if beyond EOF was materialized;
                    # nothing to emit here
                    pass

    # Copy remaining lines after the hunk
    while 0 <= working_pointer < working_line_count:
        push_output(working_lines[working_pointer])
        working_pointer += 1

    return "\n".join(output_lines) + ("\n" if (working_text.endswith("\n") or output_lines) else "")


def update_index_with_blob_content(path: str, content: str) -> None:
    """
    Update the git index with new content for a file.

    Creates a temporary blob, hashes it, and updates the index entry.
    Preserves the file mode from the existing index entry if available.
    """
    temporary_blob_path = Path(os.path.join(get_state_directory_path(), ".temporary_index_blob"))
    write_text_file_contents(temporary_blob_path, content)
    blob_hash = run_git_command(["hash-object", "-w", str(temporary_blob_path)]).stdout.strip()

    file_mode = ""
    try:
        ls_output = run_git_command(["ls-files", "-s", "--", path], check=False).stdout.strip()
        if ls_output:
            file_mode = ls_output.split()[0]
    except Exception:
        file_mode = ""

    if not file_mode:
        file_mode = "100644"

    run_git_command(["update-index", "--add", "--cacheinfo", f"{file_mode},{blob_hash},{path}"])

    try:
        temporary_blob_path.unlink(missing_ok=True)
    except Exception:
        pass
