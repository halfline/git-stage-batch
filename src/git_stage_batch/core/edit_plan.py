"""Snapshot-bound editing plans consumed by staging implementations."""

from __future__ import annotations

from dataclasses import dataclass

from .coordinates import (
    BaselineSpace,
    FileSnapshot,
    LineBoundary,
    LineSpan,
    RewrittenWorktreeSpace,
    SnapshotBoundary,
    SnapshotSpan,
    WorktreeSpace,
    require_same_snapshot,
    require_snapshot_role,
)


@dataclass(frozen=True, slots=True)
class ReplacementEditPlan:
    """Exact baseline/worktree spans replaced by one user operation."""

    path: str
    baseline_snapshot: FileSnapshot[BaselineSpace]
    worktree_snapshot: FileSnapshot[WorktreeSpace]
    baseline_span: LineSpan[BaselineSpace]
    worktree_span: LineSpan[WorktreeSpace]

    def __post_init__(self) -> None:
        require_snapshot_role(self.baseline_snapshot, BaselineSpace)
        require_snapshot_role(self.worktree_snapshot, WorktreeSpace)
        if self.path != self.baseline_snapshot.path:
            raise ValueError("baseline edit snapshot has the wrong path")
        if self.path != self.worktree_snapshot.path:
            raise ValueError("worktree edit snapshot has the wrong path")
        if self.baseline_span.end.offset > self.baseline_snapshot.line_count:
            raise ValueError("baseline edit span is outside its snapshot")
        if self.worktree_span.end.offset > self.worktree_snapshot.line_count:
            raise ValueError("worktree edit span is outside its snapshot")

    def bind_result(
        self,
        rewritten_snapshot: FileSnapshot[RewrittenWorktreeSpace],
        *,
        replacement_line_count: int,
    ) -> AppliedReplacementEdit:
        """Bind the explicit worktree edit to the exact produced snapshot."""
        if type(replacement_line_count) is not int or replacement_line_count < 0:
            raise ValueError("replacement line count must be non-negative")
        return AppliedReplacementEdit(
            plan=self,
            rewritten_snapshot=rewritten_snapshot,
            rewritten_span=LineSpan(
                LineBoundary(self.worktree_span.start.offset),
                LineBoundary(
                    self.worktree_span.start.offset + replacement_line_count
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class AppliedReplacementEdit:
    """Exact B→C transform recorded by one explicit replacement operation."""

    plan: ReplacementEditPlan
    rewritten_snapshot: FileSnapshot[RewrittenWorktreeSpace]
    rewritten_span: LineSpan[RewrittenWorktreeSpace]

    def __post_init__(self) -> None:
        require_snapshot_role(self.rewritten_snapshot, RewrittenWorktreeSpace)
        if self.rewritten_snapshot.path != self.plan.path:
            raise ValueError("rewritten edit snapshot has the wrong path")
        expected_count = (
            self.plan.worktree_snapshot.line_count
            - len(self.plan.worktree_span)
            + len(self.rewritten_span)
        )
        if self.rewritten_snapshot.line_count != expected_count:
            raise ValueError("rewritten snapshot extent does not match explicit edit")
        if (
            self.rewritten_span.start.offset
            != self.plan.worktree_span.start.offset
            or self.rewritten_span.end.offset > self.rewritten_snapshot.line_count
        ):
            raise ValueError("rewritten span does not match explicit edit boundary")

    @property
    def source_snapshot(self) -> FileSnapshot[WorktreeSpace]:
        return self.plan.worktree_snapshot

    @property
    def target_snapshot(self) -> FileSnapshot[RewrittenWorktreeSpace]:
        return self.rewritten_snapshot

    def translate_boundary(
        self,
        boundary: SnapshotBoundary[WorktreeSpace],
    ) -> SnapshotBoundary[RewrittenWorktreeSpace] | None:
        """Translate boundaries whose provenance survives the explicit edit."""
        require_same_snapshot(boundary.snapshot, self.source_snapshot)
        offset = boundary.boundary.offset
        old_start = self.plan.worktree_span.start.offset
        old_end = self.plan.worktree_span.end.offset
        new_end = self.rewritten_span.end.offset
        if old_start == old_end and new_end > old_start and offset == old_start:
            # One source boundary becomes both the before-insertion and
            # after-insertion boundary.  A bare boundary has no side affinity,
            # so claiming either placement as authoritative would be wrong.
            return None
        if offset <= old_start:
            target_offset = offset
        elif offset >= old_end:
            target_offset = new_end + (offset - old_end)
        else:
            return None
        return SnapshotBoundary(
            self.target_snapshot,
            LineBoundary(target_offset),
        )

    def translate_span(
        self,
        span: SnapshotSpan[WorktreeSpace],
    ) -> SnapshotSpan[RewrittenWorktreeSpace] | None:
        """Translate preserved spans or the exact replaced span atomically."""
        require_same_snapshot(span.snapshot, self.source_snapshot)
        if span.span == self.plan.worktree_span:
            return SnapshotSpan(self.target_snapshot, self.rewritten_span)
        old_start = self.plan.worktree_span.start.offset
        old_end = self.plan.worktree_span.end.offset
        if old_start == old_end and len(self.rewritten_span):
            if span.span.end.offset <= old_start:
                return SnapshotSpan(
                    self.target_snapshot,
                    LineSpan(
                        LineBoundary(span.span.start.offset),
                        LineBoundary(span.span.end.offset),
                    ),
                )
            if span.span.start.offset >= old_end:
                inserted_count = len(self.rewritten_span)
                return SnapshotSpan(
                    self.target_snapshot,
                    LineSpan(
                        LineBoundary(span.span.start.offset + inserted_count),
                        LineBoundary(span.span.end.offset + inserted_count),
                    ),
                )
            return None
        start = self.translate_boundary(
            SnapshotBoundary(self.source_snapshot, span.span.start)
        )
        end = self.translate_boundary(
            SnapshotBoundary(self.source_snapshot, span.span.end)
        )
        if start is None or end is None:
            return None
        return SnapshotSpan(
            self.target_snapshot,
            LineSpan(start.boundary, end.boundary),
        )
