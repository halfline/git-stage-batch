"""Batch display and line selection utilities."""

from __future__ import annotations

from ..core.line_selection import parse_line_selection
from ..core.models import CurrentLines, LineEntry
from ..utils.git import read_git_blob


def build_display_lines_from_batch_source(
    batch_source_content: str,
    ownership: 'BatchOwnership'
) -> list[dict]:
    """Build display representation of batch content with ephemeral line IDs.

    Returns list of dicts with:
        - id: ephemeral display ID (1, 2, 3, ...)
        - type: "claimed" or "deletion"
        - source_line: int (for claimed lines only)
        - deletion_index: int (for deletions only)
        - content: str (line content)
    """
    source_lines = batch_source_content.splitlines(keepends=True)
    # Parse claimed lines: join list of range strings, then parse as selection
    claimed_set = set(parse_line_selection(",".join(ownership.claimed_lines))) if ownership.claimed_lines else set()

    display_lines = []
    display_id = 1

    # Build map of deletion positions
    deletions_by_position: dict[int | None, list[tuple[int, dict]]] = {}
    for idx, deletion in enumerate(ownership.deletions):
        after_line = deletion.get("after_source_line")
        if after_line not in deletions_by_position:
            deletions_by_position[after_line] = []
        deletions_by_position[after_line].append((idx, deletion))

    # Add deletions at start of file
    if None in deletions_by_position:
        for idx, deletion in deletions_by_position[None]:
            blob_sha = deletion["blob"]
            deletion_content = b"".join(read_git_blob(blob_sha)).decode("utf-8")
            for line in deletion_content.splitlines(keepends=True):
                display_lines.append({
                    "id": display_id,
                    "type": "insertion",
                    "deletion_index": idx,
                    "content": line
                })
                display_id += 1

    # Collect all positions (claimed lines + insertion positions)
    all_positions = set(claimed_set)
    for pos in deletions_by_position.keys():
        if pos is not None:
            all_positions.add(pos)

    # Add claimed lines and insertions in batch source order
    for batch_line_num in sorted(all_positions):
        # Add claimed line if it's claimed
        if batch_line_num in claimed_set:
            if 1 <= batch_line_num <= len(source_lines):
                display_lines.append({
                    "id": display_id,
                    "type": "claimed",
                    "source_line": batch_line_num,
                    "content": source_lines[batch_line_num - 1]
                })
                display_id += 1

        # Add deletions after this line
        if batch_line_num in deletions_by_position:
            for idx, deletion in deletions_by_position[batch_line_num]:
                blob_sha = deletion["blob"]
                deletion_content = b"".join(read_git_blob(blob_sha)).decode("utf-8")
                for line in deletion_content.splitlines(keepends=True):
                    display_lines.append({
                        "id": display_id,
                        "type": "deletion",
                        "deletion_index": idx,
                        "content": line
                    })
                    display_id += 1

    return display_lines


def filter_batch_by_display_ids(
    ownership: 'BatchOwnership',
    batch_source_content: str,
    selected_ids: set[int]
) -> 'BatchOwnership':
    """Filter batch content to only selected display line IDs.

    Returns:
        BatchOwnership with filtered claimed lines and deletions
    """
    from .ownership import BatchOwnership

    # Build display representation
    display_lines = build_display_lines_from_batch_source(batch_source_content, ownership)

    # Filter to selected IDs
    selected_lines = [line for line in display_lines if line["id"] in selected_ids]

    # Reconstruct claimed_lines
    filtered_claimed_set = set()
    for line in selected_lines:
        if line["type"] == "claimed":
            filtered_claimed_set.add(line["source_line"])

    # Convert to range format
    filtered_claimed_lines = []
    if filtered_claimed_set:
        sorted_lines = sorted(filtered_claimed_set)
        range_start = sorted_lines[0]
        range_end = sorted_lines[0]

        for line_num in sorted_lines[1:]:
            if line_num == range_end + 1:
                range_end = line_num
            else:
                if range_start == range_end:
                    filtered_claimed_lines.append(str(range_start))
                else:
                    filtered_claimed_lines.append(f"{range_start}-{range_end}")
                range_start = line_num
                range_end = line_num

        if range_start == range_end:
            filtered_claimed_lines.append(str(range_start))
        else:
            filtered_claimed_lines.append(f"{range_start}-{range_end}")

    # Reconstruct deletions (only selected lines from each deletion)
    from collections import defaultdict
    from ..utils.git import create_git_blob

    selected_deletion_lines: dict[int, list[str]] = defaultdict(list)
    for line in selected_lines:
        if line["type"] == "deletion":
            selected_deletion_lines[line["deletion_index"]].append(line["content"])

    filtered_deletions = []
    for idx in sorted(selected_deletion_lines.keys()):
        original_deletion = ownership.deletions[idx]
        # Create new blob with only selected lines from this deletion
        selected_content = "".join(selected_deletion_lines[idx])
        blob_sha = create_git_blob([selected_content.encode("utf-8")])
        filtered_deletions.append({
            "after_source_line": original_deletion["after_source_line"],
            "blob": blob_sha
        })

    return BatchOwnership(claimed_lines=filtered_claimed_lines, deletions=filtered_deletions)


def _apply_batch_source_mapping(
    current_lines: CurrentLines,
    mapping: LineMapping,
) -> CurrentLines:
    """Apply batch source line mapping to CurrentLines.

    Uses the mapping to translate working tree line numbers to batch source line numbers.
    For deletions, uses the last known batch source line as insertion position.
    """
    last_source_line: int | None = None
    new_lines: list[LineEntry] = []

    for line in current_lines.lines:
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

    return CurrentLines(
        path=current_lines.path,
        header=current_lines.header,
        lines=new_lines,
    )


def _fill_source_from_working_tree(current_lines: CurrentLines) -> CurrentLines:
    """Fill source_line with working tree line numbers.

    Used when no batch source exists yet - the working tree will become
    the batch source when changes are saved.
    """
    last_source_line: int | None = None
    new_lines: list[LineEntry] = []

    for line in current_lines.lines:
        source_line = None

        if line.kind in {" ", "+"}:
            source_line = line.new_line_number
            if source_line is not None:
                last_source_line = source_line
        elif line.kind == "-":
            source_line = last_source_line

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

    return CurrentLines(
        path=current_lines.path,
        header=current_lines.header,
        lines=new_lines,
    )


def annotate_with_batch_source(
    path_value: str,
    current_lines: CurrentLines,
) -> CurrentLines:
    """Annotate CurrentLines with batch source line numbers.

    This reads the working tree and batch source content, computes a line mapping,
    and populates source_line fields on LineEntry objects.

    If batch source doesn't exist (first time batching changes for this file),
    uses working tree line numbers as source_line since the working tree will
    become the batch source.

    Use as annotator parameter to build_current_lines_from_patch_text when
    you need batch source mapping for saving changes to a batch.
    """
    from ..data.batch_sources import get_batch_source_for_file
    from ..utils.file_io import read_text_file_contents
    from ..utils.git import get_git_repository_root_path, run_git_command
    from .match import match_lines

    batch_source_commit = get_batch_source_for_file(path_value)
    if not batch_source_commit:
        # No batch source yet - working tree will become batch source
        return _fill_source_from_working_tree(current_lines)

    repo_root = get_git_repository_root_path()
    file_full_path = repo_root / path_value
    if not file_full_path.exists():
        # File doesn't exist - can't compute mapping
        return _fill_source_from_working_tree(current_lines)

    working_content = read_text_file_contents(file_full_path)
    working_lines = working_content.splitlines(keepends=True)

    batch_source_result = run_git_command(
        ["show", f"{batch_source_commit}:{path_value}"],
        check=False,
    )
    if batch_source_result.returncode != 0:
        # Can't read batch source - fallback to working tree
        return _fill_source_from_working_tree(current_lines)

    source_lines = batch_source_result.stdout.splitlines(keepends=True)
    mapping = match_lines(source_lines, working_lines, strict=False)

    return _apply_batch_source_mapping(current_lines, mapping)
