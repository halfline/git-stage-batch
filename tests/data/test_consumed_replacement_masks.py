"""Tests for consumed replacement-mask filtering."""

from __future__ import annotations

import git_stage_batch.data.consumed_replacement_masks as replacement_masks
from git_stage_batch.core.models import HunkHeader, LineEntry, LineLevelChange


def _line(
    kind: str,
    text: str,
    *,
    line_id: int | None = 1,
) -> LineEntry:
    return LineEntry(
        id=line_id,
        kind=kind,
        old_line_number=1 if kind != "+" else None,
        new_line_number=1 if kind != "-" else None,
        text_bytes=text.encode(),
        text=text,
    )


def _line_changes(*lines: LineEntry) -> LineLevelChange:
    return LineLevelChange(
        path="file.txt",
        header=HunkHeader(old_start=1, old_len=1, new_start=1, new_len=1),
        lines=list(lines),
    )


def test_filter_consumed_replacement_masks_returns_original_without_metadata(
    monkeypatch,
):
    line_changes = _line_changes(_line("-", "old"), _line("+", "new"))

    monkeypatch.setattr(
        replacement_masks,
        "read_consumed_file_metadata",
        lambda _path: None,
    )

    assert (
        replacement_masks.filter_consumed_replacement_masks(line_changes)
        is line_changes
    )


def test_filter_consumed_replacement_masks_removes_matching_run(monkeypatch):
    line_changes = _line_changes(
        _line(" ", "context", line_id=None),
        _line("-", "old"),
        _line("+", "new"),
        _line(" ", "middle", line_id=None),
        _line("+", "other"),
    )

    monkeypatch.setattr(
        replacement_masks,
        "read_consumed_file_metadata",
        lambda _path: {
            "replacement_masks": [
                {
                    "deleted_lines": ["old"],
                    "added_lines": ["new"],
                },
            ],
        },
    )

    result = replacement_masks.filter_consumed_replacement_masks(line_changes)

    assert result is not None
    assert [
        (line.kind, line.display_text())
        for line in result.lines
    ] == [
        (" ", "context"),
        (" ", "middle"),
        ("+", "other"),
    ]


def test_filter_consumed_replacement_masks_returns_none_without_visible_changes(
    monkeypatch,
):
    line_changes = _line_changes(
        _line("-", "old"),
        _line("+", "new"),
    )

    monkeypatch.setattr(
        replacement_masks,
        "read_consumed_file_metadata",
        lambda _path: {
            "replacement_masks": [
                {
                    "deleted_lines": ["old"],
                    "added_lines": ["new"],
                },
            ],
        },
    )

    assert replacement_masks.filter_consumed_replacement_masks(line_changes) is None
