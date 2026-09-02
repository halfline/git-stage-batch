"""Select batch lines for commands."""

from __future__ import annotations

from collections.abc import Collection, Iterator, Sequence
from dataclasses import dataclass

from ...batch.line_matching.occurrence_index import normalized_line_payload
from ...batch.ownership_update import SourceBoundLineSelection
from ...batch.selection import require_line_selection_in_view
from ...core.line_selection import parse_line_selection
from ...core.models import LineEntry, LineLevelChange
from ...exceptions import CommandError


@dataclass(frozen=True)
class BatchLineSelection:
    """Line IDs and matching changed lines selected for a batch action."""

    requested_ids: set[int]
    selected_lines: list[LineEntry]


@dataclass(frozen=True, slots=True)
class WorktreeDiscardSelection(Collection[int]):
    """Requested IDs plus an optional blank chosen during preparation."""

    requested_ids: set[int]
    owned_blank_id: int | None = None
    cleanup_blank_id: int | None = None

    def __post_init__(self) -> None:
        blank_ids = tuple(
            blank_id
            for blank_id in (self.owned_blank_id, self.cleanup_blank_id)
            if blank_id is not None
        )
        if len(blank_ids) > 1:
            raise ValueError("discard selection has more than one extra blank")
        if blank_ids and (blank_ids[0] <= 0 or blank_ids[0] in self.requested_ids):
            raise ValueError("extra blank must extend the requested IDs")

    def __contains__(self, line_id: object) -> bool:
        return (
            line_id == self.owned_blank_id
            or line_id == self.cleanup_blank_id
            or line_id in self.requested_ids
        )

    def __iter__(self) -> Iterator[int]:
        yield from self.requested_ids
        if self.owned_blank_id is not None:
            yield self.owned_blank_id
        if self.cleanup_blank_id is not None:
            yield self.cleanup_blank_id

    def __len__(self) -> int:
        return (
            len(self.requested_ids)
            + (self.owned_blank_id is not None)
            + (self.cleanup_blank_id is not None)
        )


def select_lines_for_batch_action(
    line_changes: LineLevelChange,
    line_id_specification: str,
) -> BatchLineSelection:
    """Validate line IDs against a view and return matching changed lines."""
    try:
        requested_ids = set(parse_line_selection(line_id_specification))
    except ValueError as error:
        raise CommandError(str(error)) from error
    require_line_selection_in_view(
        line_changes,
        requested_ids,
        line_id_specification=line_id_specification,
    )
    return BatchLineSelection(
        requested_ids=requested_ids,
        selected_lines=[
            line for line in line_changes.lines if line.id in requested_ids
        ],
    )


def include_single_line_replacement_peer(
    line_changes: LineLevelChange,
    selection: BatchLineSelection,
) -> BatchLineSelection:
    """Include the old line when one selected new line replaces it."""

    def leading_identifier(line: LineEntry) -> bytes:
        content = normalized_line_payload(line.text_bytes).lstrip()
        start = 0
        while start < len(content) and not (
            content[start] == ord("_")
            or ord("0") <= content[start] <= ord("9")
            or ord("A") <= content[start] <= ord("Z")
            or ord("a") <= content[start] <= ord("z")
            or content[start] >= 0x80
        ):
            start += 1
        end = start
        while end < len(content) and (
            content[end] == ord("_")
            or ord("0") <= content[end] <= ord("9")
            or ord("A") <= content[end] <= ord("Z")
            or ord("a") <= content[end] <= ord("z")
            or content[end] >= 0x80
        ):
            end += 1
        return content[start:end]
    selected_ids = set(selection.requested_ids)
    line_index = 0
    changed = False
    while line_index < len(line_changes.lines):
        if line_changes.lines[line_index].kind not in ("+", "-"):
            line_index += 1
            continue
        run_start = line_index
        while (
            line_index < len(line_changes.lines)
            and line_changes.lines[line_index].kind in ("+", "-")
        ):
            line_index += 1
        run = line_changes.lines[run_start:line_index]
        deletions = [line for line in run if line.kind == "-"]
        additions = [line for line in run if line.kind == "+"]
        if (
            len(deletions) == 1
            and additions
            and additions[0].id in selected_ids
            and bool(leading_identifier(deletions[0]))
            and leading_identifier(deletions[0])
            == leading_identifier(additions[0])
            and deletions[0].id is not None
            and deletions[0].id not in selected_ids
        ):
            selected_ids.add(deletions[0].id)
            changed = True
    if not changed:
        return selection
    return BatchLineSelection(
        requested_ids=selected_ids,
        selected_lines=[
            line for line in line_changes.lines if line.id in selected_ids
        ],
    )


def build_worktree_discard_selection(
    line_changes: LineLevelChange,
    ownership_ids: set[int],
    working_lines: Sequence[bytes],
    source_selection: SourceBoundLineSelection,
) -> WorktreeDiscardSelection:
    """Remove a blank that would be left behind by the selected block."""
    following_blank_id = _owned_following_blank_id(
        ownership_ids,
        working_lines,
        source_selection,
    )
    if following_blank_id is not None:
        return WorktreeDiscardSelection(
            ownership_ids,
            owned_blank_id=following_blank_id,
        )

    redundant_blank_id = _redundant_blank_beside_selected_block(
        line_changes,
        ownership_ids,
        working_lines,
    )
    if redundant_blank_id is not None:
        return WorktreeDiscardSelection(
            ownership_ids,
            cleanup_blank_id=redundant_blank_id,
        )

    trailing_blank_count = 0
    last_nonblank_index = len(working_lines) - 1
    while last_nonblank_index >= 0 and not normalized_line_payload(
        working_lines[last_nonblank_index]
    ):
        trailing_blank_count += 1
        last_nonblank_index -= 1
    if last_nonblank_index < 0:
        return WorktreeDiscardSelection(ownership_ids)

    last_nonblank_line = last_nonblank_index + 1
    selected_tail_index: int | None = None
    for line_index in range(len(line_changes.lines) - 1, -1, -1):
        line = line_changes.lines[line_index]
        new_line = line.new_line_number
        if new_line is None or new_line > last_nonblank_line:
            continue
        if new_line < last_nonblank_line:
            break
        if line.kind == "+" and line.id in ownership_ids:
            selected_tail_index = line_index
        break
    if selected_tail_index is None:
        return WorktreeDiscardSelection(ownership_ids)

    run_start_index = selected_tail_index
    expected_new_line = last_nonblank_line
    while run_start_index >= 0:
        line = line_changes.lines[run_start_index]
        if (
            line.kind != "+"
            or line.new_line_number != expected_new_line
            or line.id not in ownership_ids
        ):
            break
        expected_new_line -= 1
        run_start_index -= 1
    if run_start_index < 0 or expected_new_line <= 0:
        return WorktreeDiscardSelection(ownership_ids)

    separator = line_changes.lines[run_start_index]
    if (
        separator.kind != "+"
        or separator.new_line_number != expected_new_line
        or separator.id is None
        or separator.id in ownership_ids
        or normalized_line_payload(separator.text_bytes)
        or normalized_line_payload(working_lines[expected_new_line - 1])
    ):
        return WorktreeDiscardSelection(ownership_ids)
    selected_tail_start = expected_new_line + 1
    uses_source_alternative = source_selection.addition_run_uses_source_alternative(
        first_new_line=selected_tail_start,
        last_new_line=last_nonblank_line,
    )
    remove_separator = (
        trailing_blank_count >= 2
        or uses_source_alternative
        or (
            trailing_blank_count == 0
            and source_selection.addition_run_has_later_source_lines(
                first_new_line=selected_tail_start,
                last_new_line=last_nonblank_line,
            )
        )
    )
    if not remove_separator:
        return WorktreeDiscardSelection(ownership_ids)
    return WorktreeDiscardSelection(
        ownership_ids,
        cleanup_blank_id=separator.id,
    )


def _redundant_blank_beside_selected_block(
    line_changes: LineLevelChange,
    ownership_ids: set[int],
    working_lines: Sequence[bytes],
) -> int | None:
    """Return an added blank when removing a block would leave two blanks."""
    deletion_is_selected = False
    deletion_is_retained = False
    for line in line_changes.lines:
        if line.kind != "-":
            continue
        if line.id in ownership_ids:
            deletion_is_selected = True
        else:
            deletion_is_retained = True
        if deletion_is_selected and deletion_is_retained:
            break
    if deletion_is_retained and not deletion_is_selected:
        return None

    previous_live: LineEntry | None = None
    run_before: LineEntry | None = None
    run_start: int | None = None
    run_end: int | None = None
    run_has_text = False

    def finish_run(following: LineEntry | None) -> int | None:
        if (
            run_start is None
            or run_end is None
            or not run_has_text
            or run_before is None
            or following is None
            or run_before.new_line_number != run_start - 1
            or following.new_line_number != run_end + 1
            or run_start <= 1
            or run_end + 1 >= len(working_lines)
            or run_before.kind != "+"
            or following.kind != "+"
            or normalized_line_payload(working_lines[run_start - 2])
            or normalized_line_payload(working_lines[run_end])
            or not normalized_line_payload(working_lines[run_end + 1])
        ):
            return None
        for candidate in (following, run_before):
            if (
                candidate.kind == "+"
                and candidate.id is not None
                and candidate.id not in ownership_ids
                and not normalized_line_payload(candidate.text_bytes)
            ):
                return candidate.id
        return None

    for line in line_changes.lines:
        new_line = line.new_line_number
        if line.kind not in {" ", "+"} or new_line is None:
            continue
        is_selected = line.kind == "+" and line.id in ownership_ids
        if is_selected and (run_end is None or new_line == run_end + 1):
            if run_start is None:
                run_before = previous_live
                run_start = new_line
            run_end = new_line
            run_has_text = run_has_text or bool(
                normalized_line_payload(line.text_bytes)
            )
        else:
            redundant = finish_run(line)
            if redundant is not None:
                return redundant
            run_before = previous_live if is_selected else None
            run_start = new_line if is_selected else None
            run_end = new_line if is_selected else None
            run_has_text = is_selected and bool(
                normalized_line_payload(line.text_bytes)
            )
        previous_live = line

    return finish_run(None)


def include_redundant_blank_in_batch_selection(
    line_changes: LineLevelChange,
    selection: BatchLineSelection,
    working_lines: Sequence[bytes],
) -> BatchLineSelection:
    """Own one adjacent blank when the selected block separates two blanks."""
    selected_kinds = {line.kind for line in selection.selected_lines}
    if not {"+", "-"}.issubset(selected_kinds):
        return selection
    blank_id = _redundant_blank_beside_selected_block(
        line_changes,
        selection.requested_ids,
        working_lines,
    )
    if blank_id is None:
        return selection

    requested_ids = {*selection.requested_ids, blank_id}
    return BatchLineSelection(
        requested_ids=requested_ids,
        selected_lines=[
            line for line in line_changes.lines if line.id in requested_ids
        ],
    )


def _owned_following_blank_id(
    ownership_ids: set[int],
    working_lines: Sequence[bytes],
    source_selection: SourceBoundLineSelection,
) -> int | None:
    """Return the blank added to ownership after the requested block."""
    selected_new_lines = [
        line.new_line_number
        for line in source_selection.lines
        if (
            line.kind == "+"
            and line.id in ownership_ids
            and line.new_line_number is not None
        )
    ]
    if not selected_new_lines:
        return None
    first_new_line = min(selected_new_lines)
    last_new_line = max(selected_new_lines)
    if len(
        selected_new_lines
    ) != last_new_line - first_new_line + 1 or last_new_line + 1 >= len(working_lines):
        return None
    separator_new_line = last_new_line + 1
    separator = next(
        (
            line
            for line in source_selection.lines
            if line.kind == "+"
            and line.id is not None
            and line.id not in ownership_ids
            and line.new_line_number == separator_new_line
        ),
        None,
    )
    if (
        separator is None
        or separator.kind != "+"
        or separator.id is None
        or separator.id in ownership_ids
        or normalized_line_payload(separator.text_bytes)
        or normalized_line_payload(working_lines[separator_new_line - 1])
        or not normalized_line_payload(working_lines[separator_new_line])
    ):
        return None
    return separator.id
