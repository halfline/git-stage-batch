"""Typed schemas for machine-readable session status."""

from __future__ import annotations

from typing import TypedDict


class ChangeSummary(TypedDict, total=False):
    """One selected or skipped change in a status response."""

    hash: str
    kind: str
    file: str
    line: int | None
    ids: list[int]
    type: str
    change_type: str
    old_path: str
    new_path: str
    old_mode: str
    new_mode: str
    old_oid: str | None
    new_oid: str | None


class FileReviewSummary(TypedDict):
    """Last file-review state included in status."""

    source: str
    batch_name: str | None
    file: str
    page_spec: str
    shown_pages: list[int]
    page_count: int
    entire_file_shown: bool
    fresh: bool


class SessionSummary(TypedDict):
    """Active session state included in status."""

    active: bool
    iteration: int
    status: str
    in_progress: bool


class ProgressSummary(TypedDict):
    """Per-iteration progress counters."""

    included: int
    skipped: int
    discarded: int
    remaining: int


class StatusSummary(TypedDict):
    """Complete machine-readable status response."""

    session: SessionSummary
    selected_change: ChangeSummary | None
    file_review: FileReviewSummary | None
    progress: ProgressSummary
    skipped_hunks: list[ChangeSummary]
