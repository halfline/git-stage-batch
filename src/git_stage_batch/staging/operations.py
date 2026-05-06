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
    pending_additions: list[str] = []

    base_pointer = line_changes.header.old_prefix_line_count()
    base_line_count = len(base_lines)

    def push_output(line: str) -> None:
        output_lines.append(line)

    def flush_pending_additions() -> None:
        if pending_additions:
            output_lines.extend(pending_additions)
            pending_additions.clear()

    def base_line_matches(text: str) -> bool:
        return base_pointer < base_line_count and base_lines[base_pointer] == text

    def copy_unchanged_lines_before(old_line_number: int | None) -> None:
        nonlocal base_pointer
        if old_line_number is None:
            return
        target_index = max(old_line_number - 1, 0)
        while base_pointer < min(target_index, base_line_count):
            push_output(base_lines[base_pointer])
            base_pointer += 1

    for index in range(0, min(base_pointer, base_line_count)):
        push_output(base_lines[index])

    for line_entry in line_changes.lines:
        is_gap_line = (
            line_entry.kind == " "
            and line_entry.old_line_number is None
            and line_entry.new_line_number is None
        )
        if is_gap_line:
            flush_pending_additions()
            continue

        if line_entry.kind == " ":
            copy_unchanged_lines_before(line_entry.old_line_number)
            flush_pending_additions()
            if base_pointer < base_line_count:
                push_output(base_lines[base_pointer])
                base_pointer += 1
        elif line_entry.kind == "-":
            copy_unchanged_lines_before(line_entry.old_line_number)
            flush_pending_additions()
            if line_entry.id in include_ids:
                if base_line_matches(line_entry.text):
                    base_pointer += 1
            elif base_line_matches(line_entry.text):
                push_output(base_lines[base_pointer])
                base_pointer += 1
        elif line_entry.kind == "+":
            if base_line_matches(line_entry.text):
                flush_pending_additions()
                push_output(base_lines[base_pointer])
                base_pointer += 1
            elif line_entry.id in include_ids:
                pending_additions.append(line_entry.text)

    flush_pending_additions()
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
    pending_additions: list[bytes] = []

    base_pointer = line_changes.header.old_prefix_line_count()
    base_line_count = len(base_lines)

    def push_output(line: bytes) -> None:
        output_lines.append(line)

    def flush_pending_additions() -> None:
        if pending_additions:
            output_lines.extend(pending_additions)
            pending_additions.clear()

    def base_line_matches(text: bytes) -> bool:
        return base_pointer < base_line_count and base_lines[base_pointer] == text

    def copy_unchanged_lines_before(old_line_number: int | None) -> None:
        nonlocal base_pointer
        if old_line_number is None:
            return
        target_index = max(old_line_number - 1, 0)
        while base_pointer < min(target_index, base_line_count):
            push_output(base_lines[base_pointer])
            base_pointer += 1

    for index in range(0, min(base_pointer, base_line_count)):
        push_output(base_lines[index])

    for line_entry in line_changes.lines:
        is_gap_line = (
            line_entry.kind == " "
            and line_entry.old_line_number is None
            and line_entry.new_line_number is None
        )
        if is_gap_line:
            flush_pending_additions()
            continue

        if line_entry.kind == " ":
            copy_unchanged_lines_before(line_entry.old_line_number)
            flush_pending_additions()
            if base_pointer < base_line_count:
                push_output(base_lines[base_pointer])
                base_pointer += 1
        elif line_entry.kind == "-":
            copy_unchanged_lines_before(line_entry.old_line_number)
            flush_pending_additions()
            if line_entry.id in include_ids:
                if base_line_matches(line_entry.text_bytes):
                    base_pointer += 1
            elif base_line_matches(line_entry.text_bytes):
                push_output(base_lines[base_pointer])
                base_pointer += 1
        elif line_entry.kind == "+":
            if base_line_matches(line_entry.text_bytes):
                flush_pending_additions()
                push_output(base_lines[base_pointer])
                base_pointer += 1
            elif line_entry.id in include_ids:
                pending_additions.append(line_entry.text_bytes)

    flush_pending_additions()
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
    *,
    trim_unchanged_edge_anchors: bool = True,
) -> bytes:
    """Build target index content by replacing one contiguous selected span.

    Unlike ordinary single-hunk views, file-scoped displays can concatenate
    multiple real hunks and insert synthetic gap markers between them. This
    implementation therefore replaces the underlying file span from the first
    selected changed line to the last selected changed line, even when the
    displayed selection crosses omitted gap markers.
    """
    def longest_prefix_context_match(
        candidate_lines: list[bytes],
        context_lines: list[bytes],
    ) -> int:
        max_count = min(len(candidate_lines), len(context_lines))
        for count in range(max_count, 0, -1):
            if candidate_lines[:count] == context_lines[-count:]:
                return count
        return 0

    def longest_suffix_context_match(
        candidate_lines: list[bytes],
        context_lines: list[bytes],
    ) -> int:
        max_count = min(len(candidate_lines), len(context_lines))
        for count in range(max_count, 0, -1):
            if candidate_lines[-count:] == context_lines[:count]:
                return count
        return 0

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
    base_line_count = len(base_lines)
    selected_indices = [
        index
        for index, line in enumerate(line_changes.lines)
        if line.id in replace_ids
    ]
    span_start_index = min(selected_indices)
    span_end_index = max(selected_indices)

    def find_next_old_line_number(start_index: int) -> int | None:
        for line_entry in line_changes.lines[start_index:]:
            if line_entry.old_line_number is not None:
                return line_entry.old_line_number
        return None

    first_selected_line = line_changes.lines[span_start_index]
    if first_selected_line.old_line_number is not None:
        replace_start = max(first_selected_line.old_line_number - 1, 0)
    else:
        next_old_line_number = find_next_old_line_number(span_start_index + 1)
        replace_start = (
            max(next_old_line_number - 1, 0)
            if next_old_line_number is not None
            else base_line_count
        )

    replace_end = base_line_count
    for line_entry in reversed(line_changes.lines[span_start_index:span_end_index + 1]):
        if line_entry.old_line_number is not None:
            replace_end = line_entry.old_line_number
            break
    else:
        next_old_line_number = find_next_old_line_number(span_end_index + 1)
        replace_end = (
            max(next_old_line_number - 1, 0)
            if next_old_line_number is not None
            else base_line_count
        )

    if trim_unchanged_edge_anchors:
        before_context = base_lines[:replace_start]
        after_context = base_lines[replace_end:]

        prefix_trim = longest_prefix_context_match(replacement_lines, before_context)
        if prefix_trim:
            replacement_lines = replacement_lines[prefix_trim:]

        suffix_trim = longest_suffix_context_match(replacement_lines, after_context)
        if suffix_trim:
            replacement_lines = replacement_lines[:-suffix_trim]

        if longest_prefix_context_match(replacement_lines, before_context) >= 2:
            raise ValueError(
                "Replacement text still includes unchanged anchor lines before the selected span. "
                "Provide replacement text only for the selected span, use --file --as for a full-file replacement, "
                "or pass --no-edge-overlap to keep the edge-overlap text."
            )

        if longest_suffix_context_match(replacement_lines, after_context) >= 2:
            raise ValueError(
                "Replacement text still includes unchanged anchor lines after the selected span. "
                "Provide replacement text only for the selected span, use --file --as for a full-file replacement, "
                "or pass --no-edge-overlap to keep the edge-overlap text."
            )

    output_lines = (
        base_lines[:replace_start]
        + replacement_lines
        + base_lines[replace_end:]
    )

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

    working_pointer = max(line_changes.header.new_start - 1, 0)
    working_line_count = len(working_lines)

    def push_output(line: str) -> None:
        output_lines.append(line)

    def copy_unchanged_lines_before(new_line_number: int | None) -> None:
        nonlocal working_pointer
        if new_line_number is None:
            return
        target_index = max(new_line_number - 1, 0)
        while working_pointer < min(target_index, working_line_count):
            push_output(working_lines[working_pointer])
            working_pointer += 1

    def copy_remaining_lines_before_deletion(index: int) -> None:
        for next_entry in line_changes.lines[index + 1:]:
            if next_entry.new_line_number is not None:
                copy_unchanged_lines_before(next_entry.new_line_number)
                return
        copy_unchanged_lines_before(working_line_count + 1)

    # Copy lines before the hunk
    for index in range(0, min(working_pointer, working_line_count)):
        push_output(working_lines[index])

    # Process hunk lines
    for index, line_entry in enumerate(line_changes.lines):
        is_gap_line = (
            line_entry.kind == " "
            and line_entry.old_line_number is None
            and line_entry.new_line_number is None
        )
        if is_gap_line:
            continue

        if line_entry.kind == " ":
            copy_unchanged_lines_before(line_entry.new_line_number)
            if working_pointer < working_line_count:
                push_output(working_lines[working_pointer])
                working_pointer += 1
        elif line_entry.kind == "-":
            if line_entry.id in discard_ids:
                copy_remaining_lines_before_deletion(index)
                push_output(line_entry.text)
        elif line_entry.kind == "+":
            copy_unchanged_lines_before(line_entry.new_line_number)
            if working_pointer < working_line_count:
                if line_entry.id in discard_ids:
                    working_pointer += 1
                else:
                    push_output(working_lines[working_pointer])
                    working_pointer += 1

    while 0 <= working_pointer < working_line_count:
        push_output(working_lines[working_pointer])
        working_pointer += 1

    return "\n".join(output_lines) + ("\n" if output_lines else "")


def build_target_working_tree_content_bytes_with_discarded_lines(
    line_changes: LineLevelChange,
    discard_ids: set[int],
    working_content: bytes
) -> bytes:
    """Bytes-preserving variant of build_target_working_tree_content_with_discarded_lines."""
    working_lines = working_content.splitlines()
    output_lines: list[bytes] = []

    working_pointer = max(line_changes.header.new_start - 1, 0)
    working_line_count = len(working_lines)

    def push_output(line: bytes) -> None:
        output_lines.append(line)

    def copy_unchanged_lines_before(new_line_number: int | None) -> None:
        nonlocal working_pointer
        if new_line_number is None:
            return
        target_index = max(new_line_number - 1, 0)
        while working_pointer < min(target_index, working_line_count):
            push_output(working_lines[working_pointer])
            working_pointer += 1

    def copy_remaining_lines_before_deletion(index: int) -> None:
        for next_entry in line_changes.lines[index + 1:]:
            if next_entry.new_line_number is not None:
                copy_unchanged_lines_before(next_entry.new_line_number)
                return
        copy_unchanged_lines_before(working_line_count + 1)

    for index in range(0, min(working_pointer, working_line_count)):
        push_output(working_lines[index])

    for index, line_entry in enumerate(line_changes.lines):
        is_gap_line = (
            line_entry.kind == " "
            and line_entry.old_line_number is None
            and line_entry.new_line_number is None
        )
        if is_gap_line:
            continue

        if line_entry.kind == " ":
            copy_unchanged_lines_before(line_entry.new_line_number)
            if working_pointer < working_line_count:
                push_output(working_lines[working_pointer])
                working_pointer += 1
        elif line_entry.kind == "-":
            if line_entry.id in discard_ids:
                copy_remaining_lines_before_deletion(index)
                push_output(line_entry.text_bytes)
        elif line_entry.kind == "+":
            copy_unchanged_lines_before(line_entry.new_line_number)
            if working_pointer < working_line_count:
                if line_entry.id in discard_ids:
                    working_pointer += 1
                else:
                    push_output(working_lines[working_pointer])
                    working_pointer += 1

    while 0 <= working_pointer < working_line_count:
        push_output(working_lines[working_pointer])
        working_pointer += 1

    trailing_newline = bool(output_lines)
    return b"\n".join(output_lines) + (b"\n" if trailing_newline else b"")


def build_target_working_tree_content_bytes_with_replaced_lines(
    line_changes: LineLevelChange,
    replace_ids: set[int],
    replacement_text: str,
    working_content: bytes,
    *,
    trim_unchanged_edge_anchors: bool = True,
) -> bytes:
    """Build working tree content by replacing one contiguous selected span."""
    def longest_prefix_context_match(
        candidate_lines: list[bytes],
        context_lines: list[bytes],
    ) -> int:
        max_count = min(len(candidate_lines), len(context_lines))
        for count in range(max_count, 0, -1):
            if candidate_lines[:count] == context_lines[-count:]:
                return count
        return 0

    def longest_suffix_context_match(
        candidate_lines: list[bytes],
        context_lines: list[bytes],
    ) -> int:
        max_count = min(len(candidate_lines), len(context_lines))
        for count in range(max_count, 0, -1):
            if candidate_lines[-count:] == context_lines[:count]:
                return count
        return 0

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
    working_line_count = len(working_lines)
    replacement_bytes = replacement_text.encode("utf-8", errors="surrogateescape")
    replacement_lines = replacement_bytes.splitlines()
    selected_indices = [
        index
        for index, line in enumerate(line_changes.lines)
        if line.id in replace_ids
    ]
    span_start_index = min(selected_indices)
    span_end_index = max(selected_indices)

    def find_next_new_line_number(start_index: int) -> int | None:
        for line_entry in line_changes.lines[start_index:]:
            if line_entry.new_line_number is not None:
                return line_entry.new_line_number
        return None

    first_selected_line = line_changes.lines[span_start_index]
    if first_selected_line.new_line_number is not None:
        replace_start = max(first_selected_line.new_line_number - 1, 0)
    else:
        next_new_line_number = find_next_new_line_number(span_start_index + 1)
        replace_start = (
            max(next_new_line_number - 1, 0)
            if next_new_line_number is not None
            else working_line_count
        )

    replace_end = working_line_count
    for line_entry in reversed(line_changes.lines[span_start_index:span_end_index + 1]):
        if line_entry.new_line_number is not None:
            replace_end = line_entry.new_line_number
            break
    else:
        next_new_line_number = find_next_new_line_number(span_end_index + 1)
        replace_end = (
            max(next_new_line_number - 1, 0)
            if next_new_line_number is not None
            else working_line_count
        )

    if trim_unchanged_edge_anchors:
        before_context = working_lines[:replace_start]
        after_context = working_lines[replace_end:]

        prefix_trim = longest_prefix_context_match(replacement_lines, before_context)
        if prefix_trim:
            replacement_lines = replacement_lines[prefix_trim:]

        suffix_trim = longest_suffix_context_match(replacement_lines, after_context)
        if suffix_trim:
            replacement_lines = replacement_lines[:-suffix_trim]

        if longest_prefix_context_match(replacement_lines, before_context) >= 2:
            raise ValueError(
                "Replacement text still includes unchanged anchor lines before the selected span. "
                "Provide replacement text only for the selected span, use --file --as for a full-file replacement, "
                "or pass --no-edge-overlap to keep the edge-overlap text."
            )

        if longest_suffix_context_match(replacement_lines, after_context) >= 2:
            raise ValueError(
                "Replacement text still includes unchanged anchor lines after the selected span. "
                "Provide replacement text only for the selected span, use --file --as for a full-file replacement, "
                "or pass --no-edge-overlap to keep the edge-overlap text."
            )

    output_lines = (
        working_lines[:replace_start]
        + replacement_lines
        + working_lines[replace_end:]
    )

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
