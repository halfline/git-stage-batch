"""Inspect selected replacement spans and retained line content."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ...batch.source.annotation import acquire_batch_source_mapping
from ...batch.line_matching.match_workspace import MatcherWorkspace
from ...batch.line_matching.occurrence_index import (
    LinePayloadOccurrenceIndex,
    normalized_line_payload,
)
from ...core.line_selection import LineRangeBuilder, LineRanges
from ...core.coordinates import LineBoundary, LineSpan, WorktreeSpace
from ...core.models import LineLevelChange
from ...batch.source.cache import get_session_source_hint


@dataclass(frozen=True, slots=True)
class _ReplacementSpanRefinement:
    """A narrower worktree span and the displayed rows left outside it."""

    worktree_span: LineSpan[WorktreeSpace]
    excluded_display_ids: LineRanges


def _exclude_next_change_after_retained_suffix(
    line_changes: LineLevelChange,
    requested_ids: set[int],
    payload_lines: Sequence[bytes],
) -> set[int]:
    """Keep a retained edge line from selecting the next change."""
    if len(requested_ids) < 2 or not payload_lines:
        return requested_ids

    trailing_id = max(requested_ids)
    trailing_index = next(
        (
            index
            for index, line in enumerate(line_changes.lines)
            if line.id == trailing_id
        ),
        None,
    )
    if trailing_index is None or line_changes.lines[trailing_index].kind != "-":
        return requested_ids

    run_start = trailing_index
    while run_start and line_changes.lines[run_start - 1].kind in ("+", "-"):
        run_start -= 1
    if run_start != trailing_index:
        return requested_ids

    previous_selected_index = next(
        (
            index
            for index in range(run_start - 1, -1, -1)
            if line_changes.lines[index].id in requested_ids
        ),
        None,
    )
    if previous_selected_index is None:
        return requested_ids
    retained_context = line_changes.lines[previous_selected_index + 1 : run_start]
    if not retained_context or any(line.kind != " " for line in retained_context):
        return requested_ids
    if retained_context[-1].text_bytes != _line_body(payload_lines[-1]):
        return requested_ids

    return requested_ids - {trailing_id}


def _line_through_last_identifier(content: bytes) -> bytes:
    """Remove indentation and punctuation after the last name character."""
    body = _line_body(content).strip()
    for index in range(len(body) - 1, -1, -1):
        byte = body[index]
        if (
            byte == ord("_")
            or ord("0") <= byte <= ord("9")
            or ord("A") <= byte <= ord("Z")
            or ord("a") <= byte <= ord("z")
            or byte >= 0x80
        ):
            return body[: index + 1]
    return b""


def _refine_restoration_after_hidden_prefix(
    line_changes: LineLevelChange,
    selected_ids: set[int],
    payload_lines: Sequence[bytes],
    baseline_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    *,
    baseline_start: int,
    baseline_end: int,
    worktree_start: int,
    worktree_end: int,
) -> _ReplacementSpanRefinement | None:
    """Leave earlier added lines alone when a prior peel separates them."""
    baseline_count = baseline_end - baseline_start
    if (
        baseline_count <= 0
        or len(payload_lines) != baseline_count
        or worktree_end - worktree_start <= baseline_count
        or any(
            payload_lines[offset] != _line_body(baseline_lines[baseline_start + offset])
            for offset in range(baseline_count)
        )
    ):
        return None

    first_baseline_line = _line_body(baseline_lines[baseline_start])
    first_line_key = _line_through_last_identifier(first_baseline_line)
    if not first_line_key:
        return None

    candidate: int | None = None
    for working_index in range(worktree_start, worktree_end):
        if (
            _line_through_last_identifier(working_lines[working_index])
            != first_line_key
        ):
            continue
        if candidate is not None:
            return None
        candidate = working_index
    if (
        candidate is None
        or candidate == worktree_start
        or _line_body(working_lines[candidate]) == first_baseline_line
        or worktree_end - candidate <= baseline_count
    ):
        return None

    excluded_ids = LineRangeBuilder()
    excluded_count = 0
    candidate_is_selected_addition = False
    for line in line_changes.lines:
        new_line = line.new_line_number
        if line.kind != "+" or new_line is None or line.id is None:
            continue
        if new_line == candidate + 1:
            candidate_is_selected_addition = line.id in selected_ids
        elif worktree_start < new_line <= candidate:
            if line.id not in selected_ids:
                return None
            excluded_ids.add_line(line.id)
            excluded_count += 1
    if (
        not candidate_is_selected_addition
        or excluded_count != candidate - worktree_start
    ):
        return None

    source_hint = get_session_source_hint(line_changes.path)
    if source_hint is None:
        return None
    with acquire_batch_source_mapping(
        line_changes.path,
        batch_source_commit=source_hint.commit,
        working_lines=working_lines,
    ) as mapping:
        if mapping is None:
            return None
        preceding_source_line = mapping.get_source_line_from_target_line(candidate)
        candidate_source_line = mapping.get_source_line_from_target_line(candidate + 1)
        if (
            preceding_source_line is None
            or candidate_source_line is None
            or candidate_source_line <= preceding_source_line + 1
        ):
            return None

    return _ReplacementSpanRefinement(
        worktree_span=LineSpan(
            LineBoundary(candidate),
            LineBoundary(worktree_end),
        ),
        excluded_display_ids=excluded_ids.finish(),
    )


def _line_body(line: bytes) -> bytes:
    """Return one line without its source line ending."""
    if line.endswith(b"\r\n"):
        return line[:-2]
    if line.endswith(b"\n"):
        return line[:-1]
    return line


def _requires_explicit_added_side_alternative(
    replacement_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    *,
    working_start: int,
    working_end: int,
    baseline_file_exists: bool,
    has_deletion_peer: bool,
    destination_has_file: bool,
    no_edge_overlap: bool,
) -> bool:
    """Check whether the source must store both versions of the text.

    A shorter replacement always needs both. A longer replacement needs both
    unless it begins with all selected text. For equal line counts, keep the
    older behavior only when each new line still contains its selected text.
    """
    working_count = working_end - working_start
    replacement_count = len(replacement_lines)
    if replacement_count == 0:
        return working_count > 0
    if replacement_count > working_count:
        if baseline_file_exists or has_deletion_peer:
            return False
        return any(
            replacement_lines[index] != _line_body(working_lines[working_start + index])
            for index in range(working_count)
        )
    if replacement_count < working_count:
        return True
    if has_deletion_peer:
        return False
    for index in range(working_count):
        replacement_line = replacement_lines[index]
        working_line = _line_body(working_lines[working_start + index])
        if replacement_line == working_line:
            continue
        if no_edge_overlap:
            return True
        if replacement_line not in working_line and (
            destination_has_file or not baseline_file_exists
        ):
            return True
    return False


def _replacement_payload_matches_line_span(
    replacement_lines: Sequence[bytes],
    lines: Sequence[bytes],
    *,
    start: int,
    end: int,
) -> bool:
    """Return whether the payload equals one span, ignoring line endings."""
    return len(replacement_lines) == end - start and all(
        replacement_lines[offset] == _line_body(lines[start + offset])
        for offset in range(len(replacement_lines))
    )


def _replacement_payload_retains_selected_addition(
    line_changes: LineLevelChange,
    selected_ids: set[int],
    replacement_lines: Sequence[bytes],
    baseline_lines: Sequence[bytes],
) -> bool:
    """Check whether the replacement keeps selected text absent from the old file.

    This replacement is the version to leave in the worktree, not just new text
    for the batch. Store both versions so undo cannot overwrite it with the old
    file. For large files, keep the indexes in temporary mapped files instead
    of Python objects for every line.
    """
    with MatcherWorkspace() as workspace:
        replacement_occurrences = LinePayloadOccurrenceIndex(
            workspace,
            replacement_lines,
            ignore_indentation=True,
        )
        baseline_occurrences: LinePayloadOccurrenceIndex | None = None
        for line in line_changes.lines:
            if line.kind != "+" or line.id is None or line.id not in selected_ids:
                continue
            content = normalized_line_payload(line.text_bytes)
            if (
                _line_is_delimiter_only(content)
                or replacement_occurrences.occurrence_count(content) == 0
            ):
                continue
            if baseline_occurrences is None:
                baseline_occurrences = LinePayloadOccurrenceIndex(
                    workspace,
                    baseline_lines,
                    ignore_indentation=True,
                )
            if baseline_occurrences.occurrence_count(content) == 0:
                return True
    return False


def _matching_discard_prefix_context_count(
    payload_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    *,
    prefix_count: int,
    working_suffix_start: int,
    allow_content: bool = False,
) -> int:
    """Count matching delimiter lines immediately after the selected prefix."""
    payload_index = prefix_count
    working_index = working_suffix_start
    matched = 0
    while payload_index < len(payload_lines) - 1 and working_index < len(working_lines):
        payload_line = payload_lines[payload_index]
        if (
            not allow_content
            and (not payload_line.strip() or not _line_is_delimiter_only(payload_line))
        ) or payload_line != _line_body(working_lines[working_index]):
            break
        matched += 1
        payload_index += 1
        working_index += 1
    return matched


def _matching_baseline_prefix_context_count(
    baseline_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    *,
    baseline_suffix_start: int,
    working_suffix_start: int,
    maximum_count: int,
) -> int:
    """Count following lines that are unchanged from the baseline."""
    matched = 0
    while (
        matched < maximum_count
        and baseline_suffix_start + matched < len(baseline_lines)
        and working_suffix_start + matched < len(working_lines)
        and baseline_lines[baseline_suffix_start + matched]
        == working_lines[working_suffix_start + matched]
    ):
        matched += 1
    return matched


def _verified_explicit_alternative_end(
    *,
    selection_lines: Sequence[bytes],
    payload_lines: Sequence[bytes],
    owned_prefix_count: int,
    alternative_start: int,
    fallback_end: int,
) -> int:
    """Extend the live version across following text when it matches."""
    alternative_count = len(payload_lines) - owned_prefix_count
    if alternative_count <= 0:
        return fallback_end
    candidate_end = alternative_start + alternative_count - 1
    if candidate_end > len(selection_lines):
        return fallback_end
    if all(
        _line_body(selection_lines[alternative_start + offset - 1])
        == payload_lines[owned_prefix_count + offset]
        for offset in range(alternative_count)
    ):
        return candidate_end
    return fallback_end


def _selects_complete_old_partial_new_prefix(
    line_changes: LineLevelChange,
    selected_ids: set[int],
) -> bool:
    """Return whether a mixed run selects all old rows and a new prefix."""
    deletion_count = 0
    selected_deletion_count = 0
    addition_count = 0
    selected_addition_prefix = 0
    addition_prefix_ended = False
    selected_after_prefix = False

    def matches() -> bool:
        return (
            deletion_count > 0
            and selected_deletion_count == deletion_count
            and 0 < selected_addition_prefix < addition_count
            and not selected_after_prefix
        )

    for line in line_changes.lines:
        if line.kind == "-":
            deletion_count += 1
            if line.id is not None and line.id in selected_ids:
                selected_deletion_count += 1
            continue
        if line.kind == "+":
            addition_count += 1
            is_selected = line.id is not None and line.id in selected_ids
            if is_selected and not addition_prefix_ended:
                selected_addition_prefix += 1
            elif is_selected:
                selected_after_prefix = True
            else:
                addition_prefix_ended = True
            continue
        if matches():
            return True
        deletion_count = 0
        selected_deletion_count = 0
        addition_count = 0
        selected_addition_prefix = 0
        addition_prefix_ended = False
        selected_after_prefix = False
    return matches()


def _selected_run_has_deletion(
    line_changes: LineLevelChange,
    selected_ids: set[int],
) -> bool:
    """Return whether the selected changed block also deletes lines."""
    line_index = 0
    while line_index < len(line_changes.lines):
        if line_changes.lines[line_index].kind not in ("+", "-"):
            line_index += 1
            continue
        run_has_selection = False
        run_has_deletion = False
        while line_index < len(line_changes.lines) and line_changes.lines[
            line_index
        ].kind in ("+", "-"):
            line = line_changes.lines[line_index]
            if line.id is not None and line.id in selected_ids:
                run_has_selection = True
            if line.kind == "-":
                run_has_deletion = True
            line_index += 1
        if run_has_selection:
            return run_has_deletion
    return False


def _selected_run_has_unselected_addition(
    line_changes: LineLevelChange,
    selected_ids: set[int],
) -> bool:
    """Return whether the selection is a proper subspan of its added side."""
    line_index = 0
    while line_index < len(line_changes.lines):
        if line_changes.lines[line_index].kind not in ("+", "-"):
            line_index += 1
            continue
        run_has_selection = False
        run_has_unselected_addition = False
        while line_index < len(line_changes.lines) and line_changes.lines[
            line_index
        ].kind in ("+", "-"):
            line = line_changes.lines[line_index]
            if line.id is not None and line.id in selected_ids:
                run_has_selection = True
            elif line.kind == "+":
                run_has_unselected_addition = True
            line_index += 1
        if run_has_selection:
            return run_has_unselected_addition
    return False


def _selects_added_side_prefix(
    line_changes: LineLevelChange,
    selected_ids: set[int],
) -> bool:
    """Return True when the selection starts an added block but does not end it."""
    addition_count = 0
    selected_prefix_count = 0
    addition_prefix_ended = False
    selected_after_prefix = False

    def matches() -> bool:
        return 0 < selected_prefix_count < addition_count and not selected_after_prefix

    for line in line_changes.lines:
        if line.kind == "+":
            addition_count += 1
            is_selected = line.id is not None and line.id in selected_ids
            if is_selected and not addition_prefix_ended:
                selected_prefix_count += 1
            elif is_selected:
                selected_after_prefix = True
            else:
                addition_prefix_ended = True
            continue
        if line.kind == "-":
            continue
        if matches():
            return True
        addition_count = 0
        selected_prefix_count = 0
        addition_prefix_ended = False
        selected_after_prefix = False
    return matches()


def _contiguous_selected_addition_count(
    line_changes: LineLevelChange,
    selected_ids: set[int],
) -> int | None:
    """Return the count when selected rows are one contiguous added span."""
    selected_count = 0
    previous_new_line: int | None = None
    for line in line_changes.lines:
        if line.id is None or line.id not in selected_ids:
            continue
        new_line = line.new_line_number
        if (
            line.kind != "+"
            or new_line is None
            or (previous_new_line is not None and new_line != previous_new_line + 1)
        ):
            return None
        selected_count += 1
        previous_new_line = new_line
    return selected_count or None


def _selected_additions_cover_working_span(
    line_changes: LineLevelChange,
    selected_ids: set[int],
    *,
    replacement_start: int,
    replacement_end: int,
) -> bool:
    """Return whether the selected additions exactly cover the worktree span.

    Deleted rows may appear before them in the same block. Unchanged gaps are
    never included.
    """
    if replacement_start >= replacement_end:
        return False
    expected_new_line = replacement_start + 1
    addition_seen = False
    for line in line_changes.lines:
        if line.id is None or line.id not in selected_ids:
            continue
        if line.kind == "-":
            if addition_seen:
                return False
            continue
        if line.kind != "+" or line.new_line_number != expected_new_line:
            return False
        addition_seen = True
        expected_new_line += 1
    return expected_new_line == replacement_end + 1


def _line_is_delimiter_only(content: bytes) -> bool:
    """Return whether a line has no identifier-like payload."""
    return not any(
        byte == ord("_")
        or ord("0") <= byte <= ord("9")
        or ord("A") <= byte <= ord("Z")
        or ord("a") <= byte <= ord("z")
        or byte >= 0x80
        for byte in content
    )
