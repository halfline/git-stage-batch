"""Construct staged and working-tree content buffers from line selections."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from ..core.models import LineEntry, LineLevelChange
from ..core.replacement import (
    ReplacementPayload,
    coerce_replacement_payload,
    replacement_line_bodies,
)
from ..core.buffer import (
    LineBuffer,
)
from ..editor.line_endings import detect_line_ending
from ..editor.line_export import ensure_line_chunk_boundaries


def _line_payload(line: bytes) -> bytes:
    if line.endswith(b"\r\n"):
        return line[:-2]
    if line.endswith(b"\n"):
        return line[:-1]
    return line


def _line_payload_at(lines: Sequence[bytes], index: int) -> bytes:
    return _line_payload(lines[index])


def _line_entry_content(
    line_entry: LineEntry,
) -> bytes:
    payload = _line_entry_payload(line_entry)
    if not line_entry.has_trailing_newline:
        return payload
    line_ending = b"\r\n" if line_entry.text_bytes.endswith(b"\r") else b"\n"
    return payload + line_ending


def _line_entry_payload(
    line_entry: LineEntry,
) -> bytes:
    """Return patch-row content without its CRLF carriage return."""
    if line_entry.has_trailing_newline and line_entry.text_bytes.endswith(b"\r"):
        return line_entry.text_bytes[:-1]
    return line_entry.text_bytes


def _line_ending_from_line(line: bytes) -> bytes | None:
    """Return an LF-based ending carried by one indexed source line."""
    if line.endswith(b"\r\n"):
        return b"\r\n"
    if line.endswith(b"\n"):
        return b"\n"
    return None


def _replacement_line_ending(
    source_lines: Sequence[bytes],
    selection_start: int,
    selection_end: int,
) -> bytes:
    """Choose an ending near the replaced source span."""
    source_line_count = len(source_lines)
    for index in range(selection_start, min(selection_end, source_line_count)):
        line_ending = _line_ending_from_line(source_lines[index])
        if line_ending is not None:
            return line_ending
    for index in range(min(selection_start, source_line_count) - 1, -1, -1):
        line_ending = _line_ending_from_line(source_lines[index])
        if line_ending is not None:
            return line_ending
    for index in range(selection_end, source_line_count):
        line_ending = _line_ending_from_line(source_lines[index])
        if line_ending is not None:
            return line_ending
    return detect_line_ending(source_lines) or b"\n"


def _edit_lines_preserving_source_endings_as_buffer(
    source_lines: Sequence[bytes],
    edited_lines: Sequence[bytes],
    *,
    selection_start: int,
    selection_end: int,
    has_trailing_newline: bool,
    add_trailing_newline_when_nonempty: bool = False,
) -> LineBuffer:
    """Replace full lines while retaining each unchanged source line exactly."""
    source_line_count = len(source_lines)
    if (
        selection_start < 0
        or selection_end < selection_start
        or selection_end > source_line_count
    ):
        raise ValueError("invalid line selection")

    edited_line_count = len(edited_lines)
    output_line_count = (
        selection_start
        + edited_line_count
        + source_line_count
        - selection_end
    )
    generated_line_ending = _replacement_line_ending(
        source_lines,
        selection_start,
        selection_end,
    )

    def output_lines() -> Iterator[tuple[bytes, bool]]:
        for index in range(selection_start):
            yield source_lines[index], True
        for line in edited_lines:
            yield line, False
        for index in range(selection_end, source_line_count):
            yield source_lines[index], True

    def output_chunks() -> Iterator[bytes]:
        for output_index, (line, is_source_line) in enumerate(output_lines()):
            is_last = output_index == output_line_count - 1
            needs_line_ending = (
                not is_last
                or has_trailing_newline
                or add_trailing_newline_when_nonempty
            )
            payload = _line_payload(line)
            if not needs_line_ending:
                yield payload
                continue

            source_line_ending = (
                _line_ending_from_line(line) if is_source_line else None
            )
            yield payload + (source_line_ending or generated_line_ending)

    return LineBuffer.from_chunks(output_chunks())


def _line_content_at(lines: Sequence[bytes], index: int) -> bytes:
    """Return exact index bytes so untouched lines retain their endings."""
    return lines[index]


def _working_tree_line_content_at(
    lines: Sequence[bytes],
    index: int,
) -> bytes:
    """Return exact worktree bytes so untouched lines retain their endings."""
    return lines[index]


def _line_payloads(
    lines: Sequence[bytes],
    start: int,
    end: int,
) -> list[bytes]:
    return [_line_payload_at(lines, index) for index in range(start, end)]


def _is_synthetic_gap_line(line_entry: LineEntry) -> bool:
    return (
        line_entry.kind == " "
        and line_entry.old_line_number is None
        and line_entry.new_line_number is None
    )


def _old_index_for_new_anchor(
    line_changes: LineLevelChange,
    new_anchor: int,
    before_index: int,
) -> int:
    """Translate a new-file anchor to an old-file insertion index.

    File-scoped views can concatenate separate hunks and omit their individual
    zero-length headers. Counting earlier changed entries restores the line
    number delta at the selected row.
    """
    old_index = new_anchor
    for line_entry in line_changes.lines[:before_index]:
        if line_entry.kind == "+":
            old_index -= 1
        elif line_entry.kind == "-":
            old_index += 1
    return max(old_index, 0)


def _new_index_for_old_anchor(
    line_changes: LineLevelChange,
    old_anchor: int,
    before_index: int,
) -> int:
    """Translate an old-file anchor to a new-file insertion index."""
    new_index = old_anchor
    for line_entry in line_changes.lines[:before_index]:
        if line_entry.kind == "-":
            new_index -= 1
        elif line_entry.kind == "+":
            new_index += 1
    return max(new_index, 0)


def _target_index_line_contents(
    line_changes: LineLevelChange,
    include_ids: set[int],
    base_lines: Sequence[bytes],
    base_line_count: int,
) -> Iterator[bytes]:
    pending_additions: list[bytes] = []

    base_pointer = line_changes.header.old_prefix_line_count()

    def flush_pending_additions() -> Iterator[bytes]:
        if pending_additions:
            yield from pending_additions
            pending_additions.clear()

    def base_line_matches(line_entry: LineEntry) -> bool:
        return (
            base_pointer < base_line_count
            and _line_payload_at(base_lines, base_pointer)
            == _line_entry_payload(line_entry)
        )

    def copy_unchanged_lines_before(old_line_number: int | None) -> Iterator[bytes]:
        nonlocal base_pointer
        if old_line_number is None:
            return
        target_index = max(old_line_number - 1, 0)
        while base_pointer < min(target_index, base_line_count):
            yield _line_content_at(base_lines, base_pointer)
            base_pointer += 1

    for index in range(0, min(base_pointer, base_line_count)):
        yield _line_content_at(base_lines, index)

    for line_entry in line_changes.lines:
        if _is_synthetic_gap_line(line_entry):
            yield from flush_pending_additions()
            continue

        if line_entry.kind == " ":
            yield from copy_unchanged_lines_before(line_entry.old_line_number)
            yield from flush_pending_additions()
            if not base_line_matches(line_entry):
                raise ValueError("Index content no longer matches the selected line view")
            yield _line_content_at(base_lines, base_pointer)
            base_pointer += 1
        elif line_entry.kind == "-":
            yield from copy_unchanged_lines_before(line_entry.old_line_number)
            yield from flush_pending_additions()
            if not base_line_matches(line_entry):
                raise ValueError("Index content no longer matches the selected line view")
            if line_entry.id in include_ids:
                base_pointer += 1
            else:
                yield _line_content_at(base_lines, base_pointer)
                base_pointer += 1
        elif line_entry.kind == "+":
            if line_entry.id in include_ids:
                pending_additions.append(_line_entry_content(line_entry))

    yield from flush_pending_additions()
    while 0 <= base_pointer < base_line_count:
        yield _line_content_at(base_lines, base_pointer)
        base_pointer += 1


def build_target_index_buffer_from_lines(
    line_changes: LineLevelChange,
    include_ids: set[int],
    base_lines: Sequence[bytes],
    *,
    base_has_trailing_newline: bool,
) -> LineBuffer:
    """Build target index content from indexed base content lines."""
    base_line_count = len(base_lines)
    detected_line_ending = detect_line_ending(base_lines)
    default_line_ending = (
        detected_line_ending
        if detected_line_ending in (b"\n", b"\r\n")
        else b"\n"
    )
    return LineBuffer.from_chunks(
        ensure_line_chunk_boundaries(
            _target_index_line_contents(
                line_changes,
                include_ids,
                base_lines,
                base_line_count,
            ),
            default_line_ending=default_line_ending,
        )
    )


def build_target_index_buffer_with_replaced_lines(
    line_changes: LineLevelChange,
    replace_ids: set[int],
    replacement_text: str | ReplacementPayload,
    base_lines: Sequence[bytes],
    *,
    base_has_trailing_newline: bool,
    trim_unchanged_edge_anchors: bool = True,
) -> LineBuffer:
    """Build target index content by replacing a span in indexed base lines."""
    replacement_payload = coerce_replacement_payload(replacement_text)
    with replacement_line_bodies(replacement_payload) as replacement_lines:
        return _build_target_index_buffer_with_replaced_lines(
            line_changes,
            replace_ids,
            replacement_payload,
            replacement_lines,
            base_lines,
            base_has_trailing_newline=base_has_trailing_newline,
            trim_unchanged_edge_anchors=trim_unchanged_edge_anchors,
        )


def _build_target_index_buffer_with_replaced_lines(
    line_changes: LineLevelChange,
    replace_ids: set[int],
    replacement_payload: ReplacementPayload,
    replacement_lines: Sequence[bytes],
    base_lines: Sequence[bytes],
    *,
    base_has_trailing_newline: bool,
    trim_unchanged_edge_anchors: bool,
) -> LineBuffer:
    """Build index content while replacement line storage is open."""
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
        return _edit_lines_preserving_source_endings_as_buffer(
            base_lines,
            [],
            selection_start=0,
            selection_end=0,
            has_trailing_newline=base_has_trailing_newline,
        )

    changed_ids = sorted(line_changes.changed_line_ids())
    selected_ids = sorted(replace_ids)
    if any(line_id not in changed_ids for line_id in selected_ids):
        raise ValueError("Replacement selection contains line IDs outside the current hunk")

    expected_range = list(range(selected_ids[0], selected_ids[-1] + 1))
    if selected_ids != expected_range:
        raise ValueError("Replacement selection must be one contiguous line range")

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
            if _is_synthetic_gap_line(line_entry):
                return None
            if line_entry.old_line_number is not None:
                return line_entry.old_line_number
        return None

    def find_previous_old_line_number(end_index: int) -> int | None:
        for line_entry in reversed(line_changes.lines[:end_index + 1]):
            if _is_synthetic_gap_line(line_entry):
                return None
            if line_entry.old_line_number is not None:
                return line_entry.old_line_number
        return None

    def old_insertion_index(index: int) -> int:
        line_entry = line_changes.lines[index]
        if line_entry.kind == "+" and line_entry.new_line_number is not None:
            return min(
                _old_index_for_new_anchor(
                    line_changes,
                    line_entry.new_line_number - 1,
                    index,
                ),
                base_line_count,
            )

        previous_old_line_number = find_previous_old_line_number(index - 1)
        if previous_old_line_number is not None:
            return min(previous_old_line_number, base_line_count)

        next_old_line_number = find_next_old_line_number(index + 1)
        if next_old_line_number is not None:
            return max(next_old_line_number - 1, 0)

        return min(line_changes.header.old_prefix_line_count(), base_line_count)

    first_selected_line = line_changes.lines[span_start_index]
    if first_selected_line.old_line_number is not None:
        replace_start = max(first_selected_line.old_line_number - 1, 0)
    else:
        replace_start = old_insertion_index(span_start_index)

    replace_end = base_line_count
    for line_entry in reversed(line_changes.lines[span_start_index:span_end_index + 1]):
        if line_entry.old_line_number is not None:
            replace_end = line_entry.old_line_number
            break
    else:
        replace_end = replace_start

    if trim_unchanged_edge_anchors:
        before_context = _line_payloads(base_lines, 0, replace_start)
        after_context = _line_payloads(base_lines, replace_end, base_line_count)

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

    if replacement_payload.exact:
        trailing_newline = (
            replacement_payload.has_trailing_lf
            if replace_end == base_line_count
            else base_has_trailing_newline
        )
    else:
        trailing_newline = replacement_payload.has_trailing_lf or base_has_trailing_newline
    return _edit_lines_preserving_source_endings_as_buffer(
        base_lines,
        replacement_lines,
        selection_start=replace_start,
        selection_end=replace_end,
        has_trailing_newline=trailing_newline,
        add_trailing_newline_when_nonempty=base_line_count == 0,
    )


def _target_working_tree_line_contents(
    line_changes: LineLevelChange,
    discard_ids: set[int],
    working_lines: Sequence[bytes],
    working_line_count: int,
) -> Iterator[bytes]:
    working_pointer = line_changes.header.new_prefix_line_count()

    def copy_unchanged_lines_before(new_line_number: int | None) -> Iterator[bytes]:
        nonlocal working_pointer
        if new_line_number is None:
            return
        target_index = max(new_line_number - 1, 0)
        yield from copy_unchanged_lines_until_index(target_index)

    def copy_unchanged_lines_until_index(target_index: int) -> Iterator[bytes]:
        nonlocal working_pointer
        while working_pointer < min(target_index, working_line_count):
            yield _working_tree_line_content_at(working_lines, working_pointer)
            working_pointer += 1

    def copy_remaining_lines_before_deletion(index: int) -> Iterator[bytes]:
        line_entry = line_changes.lines[index]
        if line_entry.old_line_number is not None:
            yield from copy_unchanged_lines_until_index(
                _new_index_for_old_anchor(
                    line_changes,
                    line_entry.old_line_number - 1,
                    index,
                )
            )

        for next_entry in line_changes.lines[index + 1:]:
            if _is_synthetic_gap_line(next_entry):
                return
            if next_entry.new_line_number is not None:
                yield from copy_unchanged_lines_before(next_entry.new_line_number)
                return

    for index in range(0, min(working_pointer, working_line_count)):
        yield _working_tree_line_content_at(working_lines, index)

    for index, line_entry in enumerate(line_changes.lines):
        if _is_synthetic_gap_line(line_entry):
            continue

        if line_entry.kind == " ":
            yield from copy_unchanged_lines_before(line_entry.new_line_number)
            if working_pointer < working_line_count:
                yield _working_tree_line_content_at(working_lines, working_pointer)
                working_pointer += 1
        elif line_entry.kind == "-":
            if line_entry.id in discard_ids:
                yield from copy_remaining_lines_before_deletion(index)
                yield _line_entry_content(line_entry)
        elif line_entry.kind == "+":
            yield from copy_unchanged_lines_before(line_entry.new_line_number)
            if working_pointer < working_line_count:
                if line_entry.id in discard_ids:
                    working_pointer += 1
                else:
                    yield _working_tree_line_content_at(working_lines, working_pointer)
                    working_pointer += 1

    while 0 <= working_pointer < working_line_count:
        yield _working_tree_line_content_at(working_lines, working_pointer)
        working_pointer += 1


def build_target_working_tree_buffer_from_lines(
    line_changes: LineLevelChange,
    discard_ids: set[int],
    working_lines: Sequence[bytes],
    *,
    working_has_trailing_newline: bool,
) -> LineBuffer:
    """Build target working tree content from indexed working tree lines."""
    working_line_count = len(working_lines)
    return LineBuffer.from_chunks(
        _target_working_tree_line_contents(
            line_changes,
            discard_ids,
            working_lines,
            working_line_count,
        )
    )


def build_target_working_tree_buffer_with_replaced_lines(
    line_changes: LineLevelChange,
    replace_ids: set[int],
    replacement_text: str | ReplacementPayload,
    working_lines: Sequence[bytes],
    *,
    working_has_trailing_newline: bool,
    trim_unchanged_edge_anchors: bool = True,
) -> LineBuffer:
    """Build working tree content by replacing a span in indexed lines."""
    replacement_payload = coerce_replacement_payload(replacement_text)
    with replacement_line_bodies(replacement_payload) as replacement_lines:
        return _build_target_working_tree_buffer_with_replaced_lines(
            line_changes,
            replace_ids,
            replacement_payload,
            replacement_lines,
            working_lines,
            working_has_trailing_newline=working_has_trailing_newline,
            trim_unchanged_edge_anchors=trim_unchanged_edge_anchors,
        )


def _build_target_working_tree_buffer_with_replaced_lines(
    line_changes: LineLevelChange,
    replace_ids: set[int],
    replacement_payload: ReplacementPayload,
    replacement_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    *,
    working_has_trailing_newline: bool,
    trim_unchanged_edge_anchors: bool,
) -> LineBuffer:
    """Build working content while replacement line storage is open."""
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
        return _edit_lines_preserving_source_endings_as_buffer(
            working_lines,
            [],
            selection_start=0,
            selection_end=0,
            has_trailing_newline=working_has_trailing_newline,
        )

    changed_ids = sorted(line_changes.changed_line_ids())
    selected_ids = sorted(replace_ids)
    if any(line_id not in changed_ids for line_id in selected_ids):
        raise ValueError("Replacement selection contains line IDs outside the current hunk")

    expected_range = list(range(selected_ids[0], selected_ids[-1] + 1))
    if selected_ids != expected_range:
        raise ValueError("Replacement selection must be one contiguous line range")

    working_line_count = len(working_lines)
    selected_indices = [
        index
        for index, line in enumerate(line_changes.lines)
        if line.id in replace_ids
    ]
    span_start_index = min(selected_indices)
    span_end_index = max(selected_indices)

    def find_next_new_line_number(start_index: int) -> int | None:
        for line_entry in line_changes.lines[start_index:]:
            if _is_synthetic_gap_line(line_entry):
                return None
            if line_entry.new_line_number is not None:
                return line_entry.new_line_number
        return None

    def find_previous_new_line_number(end_index: int) -> int | None:
        for line_entry in reversed(line_changes.lines[:end_index + 1]):
            if _is_synthetic_gap_line(line_entry):
                return None
            if line_entry.new_line_number is not None:
                return line_entry.new_line_number
        return None

    def new_insertion_index(index: int) -> int:
        line_entry = line_changes.lines[index]
        if line_entry.kind == "-" and line_entry.old_line_number is not None:
            return min(
                _new_index_for_old_anchor(
                    line_changes,
                    line_entry.old_line_number - 1,
                    index,
                ),
                working_line_count,
            )

        previous_new_line_number = find_previous_new_line_number(index - 1)
        if previous_new_line_number is not None:
            return min(previous_new_line_number, working_line_count)

        next_new_line_number = find_next_new_line_number(index + 1)
        if next_new_line_number is not None:
            return max(next_new_line_number - 1, 0)

        return min(line_changes.header.new_prefix_line_count(), working_line_count)

    first_selected_line = line_changes.lines[span_start_index]
    if first_selected_line.new_line_number is not None:
        replace_start = max(first_selected_line.new_line_number - 1, 0)
    else:
        replace_start = new_insertion_index(span_start_index)

    replace_end = working_line_count
    for line_entry in reversed(line_changes.lines[span_start_index:span_end_index + 1]):
        if line_entry.new_line_number is not None:
            replace_end = line_entry.new_line_number
            break
    else:
        replace_end = replace_start

    if trim_unchanged_edge_anchors:
        before_context = _line_payloads(working_lines, 0, replace_start)
        after_context = _line_payloads(working_lines, replace_end, working_line_count)

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

    if replacement_payload.exact:
        trailing_newline = (
            replacement_payload.has_trailing_lf
            if replace_end == working_line_count
            else working_has_trailing_newline
        )
    else:
        trailing_newline = replacement_payload.has_trailing_lf or working_has_trailing_newline
    return _edit_lines_preserving_source_endings_as_buffer(
        working_lines,
        replacement_lines,
        selection_start=replace_start,
        selection_end=replace_end,
        has_trailing_newline=trailing_newline,
        add_trailing_newline_when_nonempty=True,
    )
