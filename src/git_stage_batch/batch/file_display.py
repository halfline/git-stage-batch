"""Batch file display rendering without selected-state mutation."""

from __future__ import annotations

from typing import Optional

from . import display as batch_display
from . import merge as batch_merge
from .match import match_lines
from .ownership import (
    BatchOwnership,
    build_ownership_units_from_display_lines,
    rebuild_ownership_from_units,
    validate_ownership_units,
)
from .query import read_batch_metadata
from ..core.line_selection import LineRanges
from ..core.models import (
    HunkHeader,
    LineEntry,
    LineLevelChange,
    RenderedBatchDisplay,
    ReviewActionGroup,
)
from ..editor import load_git_object_as_buffer, load_working_tree_file_as_buffer
from ..exceptions import MergeError
from ..utils.paths import get_context_lines
from ..utils.text import normalize_line_sequence_endings


_BATCH_MERGE_REVIEW_ACTIONS = (
    "include-from-batch",
    "discard-from-batch",
    "apply-from-batch",
)
_BATCH_RESET_REVIEW_ACTION = "reset-from-batch"


def render_batch_file_display(
    batch_name: str,
    file_path: str,
    metadata: dict | None = None,
    *,
    probe_mergeability: bool = True,
) -> Optional['RenderedBatchDisplay']:
    """Pure function to render batch file display with gutter ID translation.

    This is a side-effect-free helper that:
    - Reads batch metadata
    - Reads batch source content
    - Reads current working tree content
    - Probes individual line mergeability
    - Builds LineLevelChange with original selection IDs
    - Builds gutter ID mappings

    It does not:
    - Write cache files
    - Mutate selected hunk state
    - Compute patch hashes

    Args:
        batch_name: Name of the batch
        file_path: Specific file to render
        probe_mergeability: If True, compute which batch lines are currently
            mergeable. Multi-file navigational previews can set this to False
            because they do not cache or act on individual lines.

    Returns:
        RenderedBatchDisplay with line changes and gutter ID translation, or None if file not found.
    """
    # Read batch metadata
    if metadata is None:
        metadata = read_batch_metadata(batch_name)
    files = metadata.get("files", {})

    if not files or file_path not in files:
        return None

    file_meta = files[file_path]

    # Get batch source commit and ownership
    batch_source_commit = file_meta["batch_source_commit"]
    with BatchOwnership.acquire_for_metadata_dict(file_meta) as ownership:
        return _render_batch_file_display_from_ownership(
            batch_source_commit=batch_source_commit,
            file_path=file_path,
            file_meta=file_meta,
            ownership=ownership,
            probe_mergeability=probe_mergeability,
        )


def _render_batch_file_display_from_ownership(
    *,
    batch_source_commit: str,
    file_path: str,
    file_meta: dict,
    ownership: BatchOwnership,
    probe_mergeability: bool,
) -> Optional['RenderedBatchDisplay']:
    """Render batch file display from already-acquired ownership metadata."""

    batch_source_buffer = load_git_object_as_buffer(
        f"{batch_source_commit}:{file_path}"
    )
    if batch_source_buffer is None:
        return None

    mergeable_id_range_parts: list[tuple[int, int]] = []
    mergeable_id_ranges = LineRanges.empty()
    units = []

    with batch_source_buffer as batch_source_lines:
        # Build display lines (already has correct line IDs matching ownership)
        display_lines = batch_display.build_display_lines_from_batch_source_lines(
            batch_source_lines,
            ownership,
            context_lines=get_context_lines(),
        )

        if probe_mergeability and display_lines:
            source_match_lines = normalize_line_sequence_endings(batch_source_lines)
            working_tree_buffer = load_working_tree_file_as_buffer(file_path)
            with working_tree_buffer as working_tree_lines:
                working_match_lines = normalize_line_sequence_endings(working_tree_lines)
                with match_lines(
                    source_match_lines,
                    working_match_lines,
                ) as source_to_working_mapping:

                    units = build_ownership_units_from_display_lines(
                        ownership,
                        display_lines,
                    )

                    # Check each ownership unit once. All lines in an atomic unit
                    # share the same mergeability result.
                    for unit in units:
                        try:
                            validate_ownership_units([unit])
                            ownership_for_unit = rebuild_ownership_from_units([unit])
                            if ownership_for_unit.is_empty():
                                continue
                            if not batch_merge.can_merge_batch_from_line_sequences(
                                source_match_lines,
                                ownership_for_unit,
                                working_match_lines,
                                source_to_working_mapping=source_to_working_mapping,
                            ):
                                continue
                            mergeable_id_range_parts.extend(unit.display_line_ids.ranges())
                        except (MergeError, ValueError, KeyError, Exception):
                            # Unit not mergeable - exclude all its lines
                            pass

                    mergeable_id_ranges = LineRanges.from_ranges(mergeable_id_range_parts)

    if not display_lines:
        change_type = file_meta.get("change_type", "modified")
        if change_type in {"added", "deleted"}:
            marker_kind = "+" if change_type == "added" else "-"
            line_changes = LineLevelChange(
                path=file_path,
                header=HunkHeader(
                    old_start=0 if change_type == "added" else 1,
                    old_len=0 if change_type == "added" else 1,
                    new_start=1 if change_type == "added" else 0,
                    new_len=1 if change_type == "added" else 0,
                ),
                lines=[
                    LineEntry(
                        id=1,
                        kind=marker_kind,
                        old_line_number=1 if change_type == "deleted" else None,
                        new_line_number=1 if change_type == "added" else None,
                        text_bytes=b"<empty file>",
                        source_line=None,
                    )
                ],
            )
            return RenderedBatchDisplay(
                line_changes=line_changes,
                gutter_to_selection_id={},
                selection_id_to_gutter={},
                actionable_selection_groups=(),
            )
        return None

    # Keep original selection IDs; mergeability is stored separately.
    line_entries = []
    new_line_num = 1

    for display_line in display_lines:
        line_id = display_line["id"]  # Keep original selection ID
        content = display_line["content"]

        # Convert string content to bytes (encode as UTF-8)
        content_bytes = content.encode('utf-8')
        # Strip only the newline terminator, preserve \r
        text_bytes = content_bytes.rstrip(b'\n')
        has_trailing_newline = content_bytes.endswith(b'\n')

        if display_line["type"] == "claimed":
            # Claimed line from batch source
            source_line = display_line["source_line"]
            line_entries.append(LineEntry(
                id=line_id,
                kind="+",
                old_line_number=None,
                new_line_number=new_line_num,
                text_bytes=text_bytes,
                source_line=source_line,
                has_trailing_newline=has_trailing_newline,
            ))
            new_line_num += 1
        elif display_line["type"] == "deletion":
            # Deletion (suppression constraint - show as deletion for display)
            line_entries.append(LineEntry(
                id=line_id,
                kind="-",
                old_line_number=None,  # Not from old file (it's a constraint)
                new_line_number=None,
                text_bytes=text_bytes,
                source_line=None,
                has_trailing_newline=has_trailing_newline,
            ))
        elif display_line["type"] == "context":
            line_entries.append(LineEntry(
                id=None,
                kind=" ",
                old_line_number=None,
                new_line_number=new_line_num,
                text_bytes=text_bytes,
                source_line=display_line["source_line"],
                has_trailing_newline=has_trailing_newline,
            ))
            new_line_num += 1
        elif display_line["type"] == "gap":
            line_entries.append(LineEntry(
                id=None,
                kind=" ",
                old_line_number=None,
                new_line_number=None,
                text_bytes=text_bytes,
                source_line=None,
                has_trailing_newline=has_trailing_newline,
            ))

    # Compute header based on actual line types
    addition_count = sum(1 for e in line_entries if e.kind == "+")
    deletion_count = sum(1 for e in line_entries if e.kind == "-")

    # Create hunk header
    header = HunkHeader(
        old_start=0 if deletion_count == 0 else 1,
        old_len=deletion_count,
        new_start=0 if addition_count == 0 else 1,
        new_len=addition_count
    )

    line_changes = LineLevelChange(
        path=file_path,
        header=header,
        lines=line_entries
    )

    # Build gutter ID mappings
    # Only mergeable lines get consecutive gutter IDs (1, 2, 3...)
    gutter_to_selection_id = {}
    selection_id_to_gutter = {}
    gutter_num = 1
    for entry in line_entries:
        if entry.id is not None and entry.id in mergeable_id_ranges:
            gutter_to_selection_id[gutter_num] = entry.id
            selection_id_to_gutter[entry.id] = gutter_num
            gutter_num += 1

    line_id_display_order = [
        entry.id
        for entry in line_entries
        if entry.id is not None
    ]
    resettable_ids = LineRanges.from_ranges(
        display_id_range
        for unit in units
        for display_id_range in unit.display_line_ids.ranges()
    )
    review_gutter_to_selection_id = {}
    review_selection_id_to_gutter = {}
    review_gutter_num = 1
    for entry in line_entries:
        if entry.id is not None and entry.id in resettable_ids:
            review_gutter_to_selection_id[review_gutter_num] = entry.id
            review_selection_id_to_gutter[entry.id] = review_gutter_num
            review_gutter_num += 1

    actionable_selection_groups = []
    review_action_groups = []
    for unit in units:
        if not unit.display_line_ids:
            continue
        ordered_group = tuple(
            line_id
            for line_id in line_id_display_order
            if line_id in unit.display_line_ids
        )
        if len(ordered_group) != len(unit.display_line_ids):
            continue

        actions = [_BATCH_RESET_REVIEW_ACTION]
        if unit.display_line_ids.intersection(mergeable_id_ranges) == unit.display_line_ids:
            actionable_selection_groups.append(ordered_group)
            actions = [
                *_BATCH_MERGE_REVIEW_ACTIONS,
                _BATCH_RESET_REVIEW_ACTION,
            ]

        review_display_ids = tuple(
            review_selection_id_to_gutter[line_id]
            for line_id in ordered_group
            if line_id in review_selection_id_to_gutter
        )
        if len(review_display_ids) == len(ordered_group):
            if unit.kind.value == "replacement":
                reason = "replacement"
            elif unit.kind.value == "deletion_only":
                reason = "structural-run"
            else:
                reason = "simple"
            review_action_groups.append(
                ReviewActionGroup(
                    display_ids=review_display_ids,
                    selection_ids=ordered_group,
                    actions=tuple(actions),
                    reason=reason,
                )
            )
    return RenderedBatchDisplay(
        line_changes=line_changes,
        gutter_to_selection_id=gutter_to_selection_id,
        selection_id_to_gutter=selection_id_to_gutter,
        actionable_selection_groups=tuple(actionable_selection_groups),
        review_gutter_to_selection_id=review_gutter_to_selection_id,
        review_selection_id_to_gutter=review_selection_id_to_gutter,
        review_action_groups=tuple(review_action_groups),
    )
