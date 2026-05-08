"""Batch display and line selection utilities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..core.models import LineLevelChange, LineEntry
from ..data.batch_sources import get_batch_source_for_file
from ..i18n import ngettext
from ..utils.file_io import read_text_file_contents
from ..utils.git import get_git_repository_root_path, run_git_command
from .match import match_lines
from ..exceptions import MergeError
from .merge import merge_batch
from .selection import select_batch_ownership_for_display_ids

if TYPE_CHECKING:
    from .match import LineMapping
    from .ownership import BatchOwnership, DeletionClaim


def is_display_line_individually_mergeable(
    file_meta: dict,
    batch_source_content: bytes,
    working_content: bytes,
    display_id: int,
) -> bool:
    """Probe whether a single display line is individually mergeable right now.

    This tests if selecting just this one display line with `--line N` would
    succeed against the current working tree. It does not test whether ranges
    or combinations are safe - only individual line mergeability.

    Args:
        file_meta: Batch metadata for this file
        batch_source_content: Batch source content as bytes
        working_content: Current working tree content as bytes
        display_id: Single display line ID to test

    Returns:
        True if this single line can be merged individually, False otherwise
    """
    try:
        # Derive the ownership that selecting this one display ID would produce
        ownership = select_batch_ownership_for_display_ids(
            file_meta,
            batch_source_content,
            {display_id}  # Test just this one ID
        )

        # If selection produced empty ownership, it's not individually selectable
        if ownership.is_empty():
            return False

        # Try the merge with this single-line selection
        merge_batch(batch_source_content, ownership, working_content)

        # Merge succeeded - this line is individually mergeable
        return True

    except (MergeError, ValueError, KeyError, Exception):
        # Any error means this line is not individually mergeable
        # This includes:
        # - AtomicUnitError: line is part of atomic unit
        # - MergeError: merge conflict
        # - ValueError/KeyError: invalid data
        # - Other exceptions: unexpected errors
        return False


def build_display_lines_from_batch_source(
    batch_source_content: str,
    ownership: 'BatchOwnership',
    context_lines: int | None = None,
) -> list[dict]:
    """Build display representation of batch content with ephemeral line IDs.

    Shows both claimed lines (presence) and deletions (suppression constraints).
    When applying by line ID, deletions are excluded (metadata), but when viewing
    the batch, users need to see what was deleted.

    Returns list of dicts with:
        - id: ephemeral display ID (1, 2, 3, ...) for changed lines, None for context
        - type: "claimed", "deletion", or "context"
        - source_line: int (batch source line number, for claimed lines only)
        - deletion_index: int (for deletions only)
        - content: str (line content)
    """
    source_lines = batch_source_content.splitlines(keepends=True)
    if context_lines is None:
        context_lines = 0
    claimed_set = ownership.presence_line_set()

    display_lines = []
    display_id = 1

    # Build map of deletion claim positions
    deletions_by_position: dict[int | None, list[tuple[int, 'DeletionClaim']]] = {}
    for idx, claim in enumerate(ownership.deletions):
        anchor = claim.anchor_line
        if anchor not in deletions_by_position:
            deletions_by_position[anchor] = []
        deletions_by_position[anchor].append((idx, claim))

    # Add deletions at start of file (anchor=None)
    if None in deletions_by_position:
        for idx, claim in deletions_by_position[None]:
            for line_bytes in claim.content_lines:
                line_str = line_bytes.decode("utf-8", errors="replace")
                display_lines.append({
                    "id": display_id,
                    "type": "deletion",
                    "deletion_index": idx,
                    "content": line_str
                })
                display_id += 1

    # Collect all positions (claimed lines + deletion positions)
    all_positions = set(claimed_set)
    for pos in deletions_by_position.keys():
        if pos is not None:
            all_positions.add(pos)

    ranges: list[tuple[int, int]] = []
    for position in sorted(all_positions):
        start = max(1, position - context_lines)
        end = min(len(source_lines), position + context_lines)
        if start <= end:
            if ranges and start <= ranges[-1][1] + 1:
                ranges[-1] = (ranges[-1][0], max(ranges[-1][1], end))
            else:
                ranges.append((start, end))

    if None in deletions_by_position and context_lines > 0 and source_lines:
        start = 1
        end = min(len(source_lines), context_lines)
        if ranges and end >= ranges[0][0] - 1:
            ranges[0] = (start, max(ranges[0][1], end))
        else:
            ranges.insert(0, (start, end))

    # Add claimed lines, source context, and deletions in batch source order.
    # Context prevents unrelated owned lines from being visually glued together
    # (for example, showing a function header followed by its closing paren while
    # omitting the unchanged signature/body between them).
    previous_range_end: int | None = None
    for range_start, range_end in ranges:
        if previous_range_end is not None:
            omitted_line_count = range_start - previous_range_end - 1
            if omitted_line_count > 0:
                display_lines.append({
                    "id": None,
                    "type": "gap",
                    "omitted_line_count": omitted_line_count,
                    "content": ngettext(
                        "... {count} more line ...",
                        "... {count} more lines ...",
                        omitted_line_count,
                    ).format(count=omitted_line_count) + "\n"
                })

        for batch_line_num in range(range_start, range_end + 1):
            if 1 <= batch_line_num <= len(source_lines):
                if batch_line_num in claimed_set:
                    display_lines.append({
                        "id": display_id,
                        "type": "claimed",
                        "source_line": batch_line_num,
                        "content": source_lines[batch_line_num - 1]
                    })
                    display_id += 1
                else:
                    display_lines.append({
                        "id": None,
                        "type": "context",
                        "source_line": batch_line_num,
                        "content": source_lines[batch_line_num - 1]
                    })

            # Add deletions after this line
            if batch_line_num in deletions_by_position:
                for idx, claim in deletions_by_position[batch_line_num]:
                    for line_bytes in claim.content_lines:
                        line_str = line_bytes.decode("utf-8", errors="replace")
                        display_lines.append({
                            "id": display_id,
                            "type": "deletion",
                            "deletion_index": idx,
                            "content": line_str
                        })
                        display_id += 1

        previous_range_end = range_end

    return display_lines


def _apply_batch_source_mapping(
    line_changes: LineLevelChange,
    mapping: LineMapping,
) -> LineLevelChange:
    """Apply batch source line mapping to LineLevelChange.

    Uses the mapping to translate working tree line numbers to batch source line numbers.
    For deletions, uses the last known batch source line as insertion position.
    """
    last_source_line: int | None = None
    new_lines: list[LineEntry] = []

    for line in line_changes.lines:
        source_line = None

        if line.kind in {" ", "+"}:
            # Context and addition lines: map via working tree line number
            if line.new_line_number is not None:
                source_line = mapping.get_source_line_from_target_line(
                    line.new_line_number
                )
            if source_line is not None:
                last_source_line = source_line

        elif line.kind == "-":
            # Deletion: use last known batch source line as insertion position
            source_line = last_source_line
            if source_line is None and line.old_line_number is not None and line.old_line_number > 1:
                source_line = mapping.get_source_line_from_target_line(
                    line.old_line_number - 1
                )

        new_lines.append(
            LineEntry(
                id=line.id,
                kind=line.kind,
                old_line_number=line.old_line_number,
                new_line_number=line.new_line_number,
                text_bytes=line.text_bytes,
                text=line.text,
                source_line=source_line,
            )
        )

    return LineLevelChange(
        path=line_changes.path,
        header=line_changes.header,
        lines=new_lines,
    )


def _fill_source_from_working_tree(line_changes: LineLevelChange) -> LineLevelChange:
    """Fill source_line with working tree line numbers.

    Used when no batch source exists yet - the working tree will become
    the batch source when changes are saved.
    """
    last_source_line: int | None = None
    new_lines: list[LineEntry] = []

    for line in line_changes.lines:
        source_line = None

        if line.kind in {" ", "+"}:
            source_line = line.new_line_number
            if source_line is not None:
                last_source_line = source_line
        elif line.kind == "-":
            source_line = last_source_line
            if source_line is None and line.old_line_number is not None and line.old_line_number > 1:
                source_line = line.old_line_number - 1

        new_lines.append(
            LineEntry(
                id=line.id,
                kind=line.kind,
                old_line_number=line.old_line_number,
                new_line_number=line.new_line_number,
                text_bytes=line.text_bytes,
                text=line.text,
                source_line=source_line,
            )
        )

    return LineLevelChange(
        path=line_changes.path,
        header=line_changes.header,
        lines=new_lines,
    )


def annotate_with_batch_source(
    path_value: str,
    line_changes: LineLevelChange,
) -> LineLevelChange:
    """Annotate LineLevelChange with batch source line numbers.

    This reads the working tree and batch source content, computes a line mapping,
    and populates source_line fields on LineEntry objects.

    If batch source doesn't exist (first time batching changes for this file),
    uses working tree line numbers as source_line since the working tree will
    become the batch source.

    Use as annotator parameter to build_line_changes_from_patch_text when
    you need batch source mapping for saving changes to a batch.
    """
    repo_root = get_git_repository_root_path()
    file_full_path = repo_root / path_value
    if not file_full_path.exists():
        return _fill_source_from_working_tree(line_changes)

    working_content = read_text_file_contents(file_full_path)
    return annotate_with_batch_source_content(path_value, line_changes, working_content)


def annotate_with_batch_source_content(
    path_value: str,
    line_changes: LineLevelChange,
    working_content: str,
) -> LineLevelChange:
    """Annotate LineLevelChange with batch source lines for arbitrary content."""
    batch_source_commit = get_batch_source_for_file(path_value)
    if not batch_source_commit:
        return _fill_source_from_working_tree(line_changes)

    batch_source_result = run_git_command(
        ["show", f"{batch_source_commit}:{path_value}"],
        check=False,
    )
    if batch_source_result.returncode != 0:
        return _fill_source_from_working_tree(line_changes)

    source_lines = batch_source_result.stdout.splitlines(keepends=True)
    working_lines = working_content.splitlines(keepends=True)
    mapping = match_lines(source_lines, working_lines)
    return _apply_batch_source_mapping(line_changes, mapping)
