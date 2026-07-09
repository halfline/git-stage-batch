"""Record types for persisted file review state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ...core.actionable_changes import ActionableSelectionReason as _ActionableSelectionReason
from ..selected_change.store import SelectedChangeKind as _SelectedChangeKind


class ReviewSource(str, Enum):
    """Source of the selected file review."""

    FILE_VS_HEAD = "file-vs-head"
    UNSTAGED = "unstaged"
    BATCH = "batch"


class FileReviewAction(str, Enum):
    """Commands that may act on a file-review selection."""

    INCLUDE = "include"
    SKIP = "skip"
    DISCARD = "discard"
    INCLUDE_TO_BATCH = "include-to-batch"
    DISCARD_TO_BATCH = "discard-to-batch"
    INCLUDE_FROM_BATCH = "include-from-batch"
    DISCARD_FROM_BATCH = "discard-from-batch"
    APPLY_FROM_BATCH = "apply-from-batch"
    RESET_FROM_BATCH = "reset-from-batch"


def coerce_review_source(source: ReviewSource | str) -> ReviewSource:
    """Return a file-review source enum for persisted or caller-provided values."""
    return source if isinstance(source, ReviewSource) else ReviewSource(source)


def coerce_review_action(action: FileReviewAction | str) -> FileReviewAction:
    """Return a file-review action enum for persisted or caller-provided values."""
    return (
        action
        if isinstance(action, FileReviewAction) else
        FileReviewAction(action)
    )


@dataclass(frozen=True)
class FileReviewSelectionState:
    """One actionable selection shown by a file review."""

    display_ids: tuple[int, ...]
    selection_ids: tuple[int, ...]
    change_index: int
    first_page: int
    last_page: int
    reason: _ActionableSelectionReason
    actions: tuple[FileReviewAction, ...]
    is_splittable: bool = False


@dataclass(frozen=True)
class FileReviewState:
    """Persisted identity and safety state for the last file review."""

    source: ReviewSource
    batch_name: str | None
    file_path: str
    page_spec: str
    shown_pages: tuple[int, ...]
    page_count: int
    entire_file_shown: bool
    selections: tuple[FileReviewSelectionState, ...]
    selected_change_kind: _SelectedChangeKind
    selected_file_fingerprint: str
    diff_fingerprint: str


@dataclass(frozen=True)
class ImplicitLiveToBatchFileActionResult:
    """Validated target for `--to --file` with no path."""

    reviewed_file: str | None = None
    review_state: FileReviewState | None = None
    should_stop: bool = False


@dataclass(frozen=True)
class ActionScopeResolution:
    """Resolved file-review scope for a command prologue."""

    file: str | None
    review_state: FileReviewState | None = None
    should_stop: bool = False
