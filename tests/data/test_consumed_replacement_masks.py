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


def test_explicit_consumed_metadata_does_not_read_session_state(monkeypatch):
    line_changes = _line_changes(_line("-", "old"), _line("+", "new"))
    result = replacement_masks.filter_consumed_replacement_masks_with_metadata(
        line_changes,
        file_metadata={
            "replacement_masks": [
                {"deleted_lines": ["old"], "added_lines": ["new"]}
            ]
        },
    )

    assert result is None
