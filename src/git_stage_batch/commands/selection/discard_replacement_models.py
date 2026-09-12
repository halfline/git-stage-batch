"""Values shared by discard replacement preparation and persistence."""

from __future__ import annotations

from enum import Enum, auto
from dataclasses import dataclass
from pathlib import Path

from ...batch.replacement_alternatives import (
    ExplicitReplacementAlternatives,
    ReplacementAlternativeOwnership,
)
from ...batch.transformed_selection import (
    RollbackSelection,
    TransformedSelectionProjection,
)
from ...core.buffer import LineBuffer
from ...core.line_selection import LineRanges
from ...core.models import LineLevelChange


@dataclass(frozen=True)
class DiscardLineReplacementSelection:
    """Prepared replacement selection for discard-to-batch."""

    line_changes: LineLevelChange
    transformed_projection: TransformedSelectionProjection
    file_path: str
    working_file_path: Path
    rewritten_line_changes: LineLevelChange
    rewritten_selection_runs: tuple[_RewrittenSelectionRun, ...]
    rewritten_selected_ids: LineRanges
    rewritten_worktree_discard_ids: LineRanges
    rewritten_working_lines: LineBuffer
    destination: _ReplacementDestinationState
    replacement_alternatives: ExplicitReplacementAlternatives | None = None

    def __post_init__(self) -> None:
        ownership_ids = (
            self.transformed_projection.ownership_selection.display_ids.to_line_ranges()
        )
        if ownership_ids != self.rewritten_selected_ids:
            raise ValueError("ownership IDs differ from transformed projection")
        rollback_ids = LineRanges.empty()
        if isinstance(self.transformed_projection.rollback, RollbackSelection):
            rollback_ids = self.transformed_projection.rollback.selection.display_ids.to_line_ranges()
        if rollback_ids != self.rewritten_worktree_discard_ids:
            raise ValueError("rollback IDs differ from transformed projection")
        if (
            self.replacement_alternatives is not None
            and self.replacement_alternatives.edit
            != self.transformed_projection.explicit_edit
        ):
            raise ValueError("replacement alternatives differ from explicit edit")


@dataclass(frozen=True)
class _RewrittenSelectionRun:
    """One original changed run projected into the rewritten diff."""

    original_old_lines: LineRanges
    original_new_lines: LineRanges
    rewritten_deletion_ids: LineRanges
    rewritten_addition_ids: LineRanges
    restore_deletions: bool


@dataclass(frozen=True, slots=True)
class _ReplacementDestinationState:
    """What the destination batch already contains."""

    batch_name: str
    file_exists: bool


class _ReplacementBufferMode(Enum):
    """How the selected content and replacement payload form the new file."""

    SELECTED_LINES = auto()
    EXPLICIT_SPAN = auto()
    SAVED_THEN_LIVE = auto()


@dataclass(frozen=True, slots=True)
class _SavedReplacementPrefix:
    """An owned prefix and the context retained with its replacement parent."""

    line_count: int
    prepend_selected_lines: bool = False
    discard_context_count: int = 0
    parent_context_count: int = 0
    ownership_scope: ReplacementAlternativeOwnership = (
        ReplacementAlternativeOwnership.TRANSLATED_SELECTION
    )


@dataclass(frozen=True, slots=True)
class _ReplacementDecision:
    """Resolved geometry and content strategy, without policy scratch state."""

    effective_ids: set[int]
    baseline_start: int
    baseline_end: int
    replacement_start: int
    replacement_end: int
    saved_prefix: _SavedReplacementPrefix | None
    buffer_mode: _ReplacementBufferMode
    trim_edge_anchors: bool
    preserve_selected_addition_wording: bool
