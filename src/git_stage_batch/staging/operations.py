"""Line-level staging operations for applying selective changes to index and working tree."""

from __future__ import annotations

from ..core.models import LineLevelChange
from ..utils.git import create_git_blob, run_git_command
from ..utils.journal import log_journal


def build_target_index_content_with_selected_lines(
    line_changes: LineLevelChange,
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

    base_pointer = line_changes.header.old_start - 1  # 0-based
    base_line_count = len(base_lines)

    def push_output(line: str) -> None:
        output_lines.append(line)

    # Copy lines before the hunk
    for index in range(0, min(base_pointer, base_line_count)):
        push_output(base_lines[index])

    # Process hunk lines
    for line_entry in line_changes.lines:
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

    trailing_newline = base_text.endswith("\n") or (not base_text and bool(output_lines))
    return "\n".join(output_lines) + ("\n" if trailing_newline else "")


def build_target_index_content_bytes_with_selected_lines(
    line_changes: LineLevelChange,
    include_ids: set[int],
    base_content: bytes
) -> bytes:
    """Bytes-preserving variant of build_target_index_content_with_selected_lines."""
    base_lines = base_content.splitlines()
    output_lines: list[bytes] = []

    base_pointer = line_changes.header.old_start - 1
    base_line_count = len(base_lines)

    def push_output(line: bytes) -> None:
        output_lines.append(line)

    for index in range(0, min(base_pointer, base_line_count)):
        push_output(base_lines[index])

    for line_entry in line_changes.lines:
        if line_entry.kind == " ":
            if base_pointer < base_line_count:
                push_output(base_lines[base_pointer])
                base_pointer += 1
        elif line_entry.kind == "-":
            if base_pointer < base_line_count:
                if line_entry.id in include_ids:
                    base_pointer += 1
                else:
                    push_output(base_lines[base_pointer])
                    base_pointer += 1
        elif line_entry.kind == "+":
            if line_entry.id in include_ids:
                push_output(line_entry.text_bytes)

    while 0 <= base_pointer < base_line_count:
        push_output(base_lines[base_pointer])
        base_pointer += 1

    trailing_newline = base_content.endswith(b"\n") or (not base_content and bool(output_lines))
    return b"\n".join(output_lines) + (b"\n" if trailing_newline else b"")


def build_target_index_content_bytes_with_replaced_lines(
    line_changes: LineLevelChange,
    replace_ids: set[int],
    replacement_text: str,
    base_content: bytes,
) -> bytes:
    """Build target index content by replacing one contiguous changed region."""
    if not replace_ids:
        return base_content

    changed_ids = sorted(line_changes.changed_line_ids())
    selected_ids = sorted(replace_ids)
    if any(line_id not in changed_ids for line_id in selected_ids):
        raise ValueError("Replacement selection contains line IDs outside the current hunk")

    expected_range = list(range(selected_ids[0], selected_ids[-1] + 1))
    if selected_ids != expected_range:
        raise ValueError("Replacement selection must be one contiguous line range")

    base_lines = base_content.splitlines()
    replacement_bytes = replacement_text.encode("utf-8", errors="surrogateescape")
    replacement_lines = replacement_bytes.splitlines()
    output_lines: list[bytes] = []

    base_pointer = line_changes.header.old_start - 1
    base_line_count = len(base_lines)
    inserted_replacement = False

    def push_output(line: bytes) -> None:
        output_lines.append(line)

    def push_replacement_once() -> None:
        nonlocal inserted_replacement
        if inserted_replacement:
            return
        output_lines.extend(replacement_lines)
        inserted_replacement = True

    for index in range(0, min(base_pointer, base_line_count)):
        push_output(base_lines[index])

    for line_entry in line_changes.lines:
        is_selected = line_entry.id in replace_ids if line_entry.id is not None else False

        if is_selected:
            push_replacement_once()
            if line_entry.kind in (" ", "-") and base_pointer < base_line_count:
                base_pointer += 1
            continue

        if line_entry.kind == " ":
            if base_pointer < base_line_count:
                push_output(base_lines[base_pointer])
                base_pointer += 1
        elif line_entry.kind == "-":
            if base_pointer < base_line_count:
                push_output(base_lines[base_pointer])
                base_pointer += 1
        elif line_entry.kind == "+":
            continue

    while 0 <= base_pointer < base_line_count:
        push_output(base_lines[base_pointer])
        base_pointer += 1

    trailing_newline = (
        replacement_text.endswith("\n")
        or base_content.endswith(b"\n")
        or (not base_content and bool(output_lines))
    )
    return b"\n".join(output_lines) + (b"\n" if trailing_newline else b"")


def build_target_working_tree_content_with_discarded_lines(
    line_changes: LineLevelChange,
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

    working_pointer = line_changes.header.new_start - 1  # 0-based
    working_line_count = len(working_lines)

    def push_output(line: str) -> None:
        output_lines.append(line)

    # Copy lines before the hunk
    for index in range(0, min(working_pointer, working_line_count)):
        push_output(working_lines[index])

    # Process hunk lines
    for line_entry in line_changes.lines:
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


def build_target_working_tree_content_bytes_with_discarded_lines(
    line_changes: LineLevelChange,
    discard_ids: set[int],
    working_content: bytes
) -> bytes:
    """Bytes-preserving variant of build_target_working_tree_content_with_discarded_lines."""
    working_lines = working_content.splitlines()
    output_lines: list[bytes] = []

    working_pointer = line_changes.header.new_start - 1
    working_line_count = len(working_lines)

    def push_output(line: bytes) -> None:
        output_lines.append(line)

    for index in range(0, min(working_pointer, working_line_count)):
        push_output(working_lines[index])

    for line_entry in line_changes.lines:
        if line_entry.kind == " ":
            if working_pointer < working_line_count:
                push_output(working_lines[working_pointer])
                working_pointer += 1
        elif line_entry.kind == "-":
            if line_entry.id in discard_ids:
                push_output(line_entry.text_bytes)
        elif line_entry.kind == "+":
            if working_pointer < working_line_count:
                if line_entry.id in discard_ids:
                    working_pointer += 1
                else:
                    push_output(working_lines[working_pointer])
                    working_pointer += 1

    while 0 <= working_pointer < working_line_count:
        push_output(working_lines[working_pointer])
        working_pointer += 1

    trailing_newline = working_content.endswith(b"\n") or bool(output_lines)
    return b"\n".join(output_lines) + (b"\n" if trailing_newline else b"")


def build_target_working_tree_content_bytes_with_replaced_lines(
    line_changes: LineLevelChange,
    replace_ids: set[int],
    replacement_text: str,
    working_content: bytes,
) -> bytes:
    """Build working tree content by replacing one contiguous changed region."""
    if not replace_ids:
        return working_content

    changed_ids = sorted(line_changes.changed_line_ids())
    selected_ids = sorted(replace_ids)
    if any(line_id not in changed_ids for line_id in selected_ids):
        raise ValueError("Replacement selection contains line IDs outside the current hunk")

    expected_range = list(range(selected_ids[0], selected_ids[-1] + 1))
    if selected_ids != expected_range:
        raise ValueError("Replacement selection must be one contiguous line range")

    working_lines = working_content.splitlines()
    output_lines: list[bytes] = []

    working_pointer = line_changes.header.new_start - 1
    working_line_count = len(working_lines)
    replacement_bytes = replacement_text.encode("utf-8", errors="surrogateescape")
    replacement_lines = replacement_bytes.splitlines()
    inserted_replacement = False

    def push_output(line: bytes) -> None:
        output_lines.append(line)

    def push_replacement_once() -> None:
        nonlocal inserted_replacement
        if inserted_replacement:
            return
        output_lines.extend(replacement_lines)
        inserted_replacement = True

    for index in range(0, min(working_pointer, working_line_count)):
        push_output(working_lines[index])

    for line_entry in line_changes.lines:
        is_selected = line_entry.id in replace_ids if line_entry.id is not None else False

        if is_selected:
            push_replacement_once()
            if line_entry.kind in (" ", "+") and working_pointer < working_line_count:
                working_pointer += 1
            continue

        if line_entry.kind == " ":
            if working_pointer < working_line_count:
                push_output(working_lines[working_pointer])
                working_pointer += 1
        elif line_entry.kind == "-":
            continue
        elif line_entry.kind == "+":
            if working_pointer < working_line_count:
                push_output(working_lines[working_pointer])
                working_pointer += 1

    while 0 <= working_pointer < working_line_count:
        push_output(working_lines[working_pointer])
        working_pointer += 1

    trailing_newline = (
        replacement_text.endswith("\n")
        or working_content.endswith(b"\n")
        or bool(output_lines)
    )
    return b"\n".join(output_lines) + (b"\n" if trailing_newline else b"")


def update_index_with_blob_content(path: str, content: bytes) -> None:
    """
    Update the git index with new content for a file.

    Creates a temporary blob, hashes it, and updates the index entry.
    Preserves the file mode from the existing index entry if available.
    """
    # Log before state
    ls_before = run_git_command(["ls-files", "--stage", "--", path], check=False).stdout.strip()

    blob_hash = create_git_blob([content])

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

    # Log after state
    ls_after = run_git_command(["ls-files", "--stage", "--", path], check=False).stdout.strip()
    log_journal(
        "update_index_with_blob_content",
        path=path,
        content_len=len(content),
        content_preview=content[:200].decode('utf-8', errors='replace') if content else "(empty)",
        blob_hash=blob_hash,
        file_mode=file_mode,
        index_before=ls_before,
        index_after=ls_after
    )
