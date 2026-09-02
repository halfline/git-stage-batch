"""Describe the text saved in a batch and the text left in the worktree."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from ..core.coordinates import (
    BaselineSpace,
    LineBoundary,
    LineSpan,
    RewrittenWorktreeSpace,
    SnapshotSpan,
    WorktreeSpace,
    require_same_snapshot,
    require_snapshot_role,
)
from ..core.edit_plan import AppliedReplacementEdit
from .ownership.replacement_line_runs import ReplacementLineRun


class ReplacementAlternativeOwnership(Enum):
    """How much source text the replacement owns."""

    TRANSLATED_SELECTION = auto()
    EXACT_SAVED_SPAN = auto()
    SOURCE_WITHOUT_LIVE = auto()
    UNTRACKED_SOURCE = auto()


@dataclass(frozen=True, slots=True)
class ExplicitReplacementParent:
    """The old and new locations of a replacement in a tracked file."""

    baseline: SnapshotSpan[BaselineSpace]
    worktree: SnapshotSpan[WorktreeSpace]

    def __post_init__(self) -> None:
        require_snapshot_role(self.baseline.snapshot, BaselineSpace)
        require_snapshot_role(self.worktree.snapshot, WorktreeSpace)
        if self.baseline.snapshot.path != self.worktree.snapshot.path:
            raise ValueError("replacement parent spans have different paths")
        if len(self.baseline.span) == 0 or len(self.worktree.span) == 0:
            raise ValueError("replacement parent spans must be non-empty")

    def as_line_run(self) -> ReplacementLineRun:
        """Convert the parent to one-based line ranges."""
        return ReplacementLineRun(
            old_start=self.baseline.span.start.offset + 1,
            old_end=self.baseline.span.end.offset,
            new_start=self.worktree.span.start.offset + 1,
            new_end=self.worktree.span.end.offset,
        )


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
    parent: ExplicitReplacementParent | None
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
        if self.parent is not None:
            require_same_snapshot(
                self.parent.baseline.snapshot,
                self.edit.plan.baseline_snapshot,
            )
            require_same_snapshot(
                self.parent.worktree.snapshot,
                self.edit.plan.worktree_snapshot,
            )
            baseline_extension = (
                self.parent.baseline.span.end.offset
                - self.edit.plan.baseline_span.end.offset
            )
            worktree_extension = (
                self.parent.worktree.span.end.offset
                - self.edit.plan.worktree_span.end.offset
            )
            if (
                self.parent.baseline.span.start != self.edit.plan.baseline_span.start
                or self.parent.worktree.span.start != self.edit.plan.worktree_span.start
                or baseline_extension < 0
                or baseline_extension != worktree_extension
            ):
                raise ValueError("replacement parent does not extend the edit equally")
        if (
            self.ownership_scope
            in (
                ReplacementAlternativeOwnership.EXACT_SAVED_SPAN,
                ReplacementAlternativeOwnership.SOURCE_WITHOUT_LIVE,
                ReplacementAlternativeOwnership.UNTRACKED_SOURCE,
            )
            and self.parent is not None
        ):
            raise ValueError("presence-scoped replacement cannot have a tracked parent")
        if (
            self.ownership_scope
            in (
                ReplacementAlternativeOwnership.SOURCE_WITHOUT_LIVE,
                ReplacementAlternativeOwnership.UNTRACKED_SOURCE,
            )
            and self.live is None
        ):
            raise ValueError("source-scoped replacement requires a live alternative")

    @property
    def requires_exact_saved_presence(self) -> bool:
        """Return whether ownership must contain only the saved span."""
        return self.ownership_scope is ReplacementAlternativeOwnership.EXACT_SAVED_SPAN

    @property
    def owns_source_without_live(self) -> bool:
        """Return whether all source content except the live side is owned."""
        return (
            self.ownership_scope is ReplacementAlternativeOwnership.SOURCE_WITHOUT_LIVE
        )

    @property
    def uses_untracked_source(self) -> bool:
        """Return whether the replacement has no file in its baseline."""
        return self.ownership_scope is ReplacementAlternativeOwnership.UNTRACKED_SOURCE

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
