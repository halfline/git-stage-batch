"""Rendered batch file display model assembly."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Optional

from ..core.line_selection import LineRanges
from ..core.models import (
    HunkHeader,
    LineEntry,
    LineLevelChange,
    RenderedBatchDisplay,
    ReviewActionGroup,
)
from .ownership_unit_types import OwnershipUnit


_BATCH_MERGE_REVIEW_ACTIONS = (
    "include-from-batch",
    "discard-from-batch",
    "apply-from-batch",
)
_BATCH_RESET_REVIEW_ACTION = "reset-from-batch"


def build_rendered_batch_display_model(
    *,
    file_path: str,
    file_meta: dict,
    display_lines: list[dict],
    mergeable_id_ranges: LineRanges,
    units: Sequence[OwnershipUnit],
) -> Optional[RenderedBatchDisplay]:
    """Build the rendered batch display model from display rows and units."""
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

    line_entries = []
    new_line_num = 1

    for display_line in display_lines:
        line_id = display_line["id"]
        content = display_line["content"]

        content_bytes = content.encode('utf-8')
        text_bytes = content_bytes.rstrip(b'\n')
        has_trailing_newline = content_bytes.endswith(b'\n')

        if display_line["type"] == "claimed":
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
            line_entries.append(LineEntry(
                id=line_id,
                kind="-",
                old_line_number=None,
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

    addition_count = sum(1 for e in line_entries if e.kind == "+")
    deletion_count = sum(1 for e in line_entries if e.kind == "-")
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
