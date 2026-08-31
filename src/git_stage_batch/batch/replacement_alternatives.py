"""Describe the text saved in a batch and the text left in the worktree."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from ..core.coordinates import (
    LineBoundary,
    LineSpan,
    RewrittenWorktreeSpace,
    SnapshotSpan,
    require_same_snapshot,
    require_snapshot_role,
)
from ..core.edit_plan import AppliedReplacementEdit

class ReplacementAlternativeOwnership(Enum):
    """How much source text the replacement owns."""

    TRANSLATED_SELECTION = auto()
    EXACT_SAVED_SPAN = auto()


@dataclass(frozen=True, slots=True)
class ExplicitReplacementAlternatives:
    """The text saved in the batch and the text left in the worktree.

    ``edit`` describes the change, and the batch owns ``saved``. When an exact
    match proves that following lines belong to the worktree version, ``live``
    includes them too.
    """

    edit: AppliedReplacementEdit
    saved: SnapshotSpan[RewrittenWorktreeSpace]
    live: SnapshotSpan[RewrittenWorktreeSpace] | None
    ownership_scope: ReplacementAlternativeOwnership

    def __post_init__(self) -> None:
        require_snapshot_role(self.saved.snapshot, RewrittenWorktreeSpace)
        require_same_snapshot(self.saved.snapshot, self.edit.rewritten_snapshot)
        if len(self.saved.span) == 0:
            raise ValueError("saved replacement span must be non-empty")
        if self.saved.span.start != self.edit.rewritten_span.start:
            raise ValueError("saved replacement starts outside the explicit edit")
        if self.saved.span.end.offset > self.edit.rewritten_span.end.offset:
            raise ValueError("saved replacement exceeds the explicit edit")
        if self.live is not None:
            require_snapshot_role(self.live.snapshot, RewrittenWorktreeSpace)
            require_same_snapshot(self.live.snapshot, self.saved.snapshot)
            if len(self.live.span) == 0:
                raise ValueError("live replacement span must be non-empty")
            if self.live.span.start != self.saved.span.end:
                raise ValueError("saved and live replacement spans are not adjacent")

    @property
    def requires_exact_saved_presence(self) -> bool:
        """Return whether ownership must contain only the saved span."""
        return self.ownership_scope is ReplacementAlternativeOwnership.EXACT_SAVED_SPAN

    @property
    def saved_range(self) -> tuple[int, int]:
        """Return the one-based inclusive saved range."""
        return self.saved.span.start.offset + 1, self.saved.span.end.offset

    @property
    def live_range(self) -> tuple[int, int] | None:
        """Return the one-based inclusive live range, when present."""
        if self.live is None:
            return None
        return self.live.span.start.offset + 1, self.live.span.end.offset

    @property
    def boundary_search_span(self) -> SnapshotSpan[RewrittenWorktreeSpace]:
        """Return the span covering both stored versions."""
        end = self.edit.rewritten_span.end
        if self.live is not None and self.live.span.end.offset > end.offset:
            end = self.live.span.end
        return SnapshotSpan(
            self.saved.snapshot,
            LineSpan(self.edit.rewritten_span.start, LineBoundary(end.offset)),
        )
