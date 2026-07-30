"""Passive records for page-aware file review."""

from __future__ import annotations

from dataclasses import dataclass

from ...core.actionable_changes import ActionableSelectionReason
from ...core.models import HunkHeader, LineEntry, LineLevelChange, ReviewActionGroup


@dataclass(frozen=True)
class ReviewChange:
    """A complete actionable change group in a file review."""

    index: int
    total: int
    path: str
    hunk_header: HunkHeader
    old_start: int | None
    old_end: int | None
    new_start: int | None
    new_end: int | None
    rows: tuple[LineEntry, ...]
    display_ids: tuple[int, ...]
    selection_ids: tuple[int, ...]
    select_as: str | None
    reason: ActionableSelectionReason
    is_oversized: bool
    note: str | None
    actions: tuple[str, ...] = ()
    first_page: int = 1
    last_page: int = 1


@dataclass(frozen=True)
class ReviewChangeFragment:
    """A rendered fragment of a review change on one page."""

    change: ReviewChange
    rows: tuple[LineEntry, ...]
    is_first_fragment: bool
    is_last_fragment: bool


@dataclass(frozen=True)
class FileReviewPage:
    """One semantic review page."""

    page: int
    changes: tuple[ReviewChangeFragment, ...]


@dataclass(frozen=True)
class FileReviewModel:
    """A paginated file review model."""

    line_changes: LineLevelChange
    changes: tuple[ReviewChange, ...]
    pages: tuple[FileReviewPage, ...]
    display_id_by_selection_id: dict[int, int] | None = None
    review_action_groups: tuple[ReviewActionGroup, ...] = ()
