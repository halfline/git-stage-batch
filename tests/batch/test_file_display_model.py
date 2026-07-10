"""Tests for rendered batch file display model assembly."""

from __future__ import annotations

from dataclasses import dataclass

from git_stage_batch.batch.file_display_model import (
    build_rendered_batch_display_model,
)
from git_stage_batch.core.line_selection import LineRanges


@dataclass
class _UnitKind:
    value: str


@dataclass
class _Unit:
    display_line_ids: LineRanges
    kind: _UnitKind


def test_build_rendered_batch_display_model_handles_empty_file_change():
    """Empty file additions get a placeholder rendered change."""
    rendered = build_rendered_batch_display_model(
        file_path="empty.txt",
        file_meta={"change_type": "added"},
        display_lines=[],
        mergeable_id_ranges=LineRanges.empty(),
        units=[],
    )

    assert rendered is not None
    assert rendered.line_changes.path == "empty.txt"
    assert [line.kind for line in rendered.line_changes.lines] == ["+"]
    assert rendered.line_changes.lines[0].text_bytes == b"<empty file>"
    assert rendered.gutter_to_selection_id == {}
    assert rendered.actionable_selection_groups == ()


def test_build_rendered_batch_display_model_groups_review_actions():
    """Display rows become line changes, gutter maps, and review groups."""
    rendered = build_rendered_batch_display_model(
        file_path="file.txt",
        file_meta={"change_type": "modified"},
        display_lines=[
            {
                "id": 1,
                "type": "claimed",
                "source_line": 1,
                "content": "claimed\n",
            },
            {
                "id": 2,
                "type": "deletion",
                "content": "deleted\n",
            },
            {
                "id": None,
                "type": "context",
                "source_line": 2,
                "content": "context\n",
            },
            {
                "id": None,
                "type": "gap",
                "content": "... 2 more lines ...\n",
            },
        ],
        mergeable_id_ranges=LineRanges.from_lines([1]),
        units=[
            _Unit(
                display_line_ids=LineRanges.from_lines([1]),
                kind=_UnitKind("presence_only"),
            ),
            _Unit(
                display_line_ids=LineRanges.from_lines([2]),
                kind=_UnitKind("deletion_only"),
            ),
        ],
    )

    assert rendered is not None
    assert [line.kind for line in rendered.line_changes.lines] == ["+", "-", " ", " "]
    assert [line.source_line for line in rendered.line_changes.lines] == [
        1,
        None,
        2,
        None,
    ]
    assert rendered.gutter_to_selection_id == {1: 1}
    assert rendered.selection_id_to_gutter == {1: 1}
    assert rendered.review_gutter_to_selection_id == {1: 1, 2: 2}
    assert rendered.review_selection_id_to_gutter == {1: 1, 2: 2}
    assert rendered.actionable_selection_groups == ((1,),)
    assert [group.selection_ids for group in rendered.review_action_groups] == [
        (1,),
        (2,),
    ]
    assert rendered.review_action_groups[0].actions == (
        "include-from-batch",
        "discard-from-batch",
        "apply-from-batch",
        "reset-from-batch",
    )
    assert rendered.review_action_groups[0].reason == "simple"
    assert rendered.review_action_groups[1].actions == ("reset-from-batch",)
    assert rendered.review_action_groups[1].reason == "structural-run"
