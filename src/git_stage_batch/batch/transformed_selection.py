"""Atomic ownership and rollback projection for explicit transformed edits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar, Union

from ..core.coordinates import (
    BaselineSpace,
    DiffNewSpace,
    DiffOldSpace,
    FileSnapshot,
    LineSpan,
    RewrittenWorktreeSpace,
    SnapshotDescriptor,
    SnapshotSpans,
    WorktreeSpace,
    require_snapshot_role,
)
from ..core.edit_plan import AppliedReplacementEdit
from ..core.selection_geometry import ResolvedSelection


@dataclass(frozen=True, slots=True)
class NoRollback:
    """The explicit rewrite is already the desired live worktree."""


@dataclass(frozen=True, slots=True)
class RollbackSelection:
    """Semantic rewritten-diff selection removed from the live worktree."""

    selection: ResolvedSelection


RollbackProjection = Union[NoRollback, RollbackSelection]

_OldEnvelopeSpace = TypeVar("_OldEnvelopeSpace")
_NewEnvelopeSpace = TypeVar("_NewEnvelopeSpace")
_SelectionSpace = TypeVar("_SelectionSpace")
_EnvelopeSpace = TypeVar("_EnvelopeSpace")


@dataclass(frozen=True, slots=True)
class TransformedSelectionProjection:
    """Both meanings of one transformed selection, derived from one witness.

    ``ownership_selection`` describes what the batch saves in the rewritten
    diff. ``rollback_selection`` describes what the live worktree removes or
    restores.  They intentionally remain separate, but are inseparable from
    the original selection and explicit edit that produced them.
    """

    original_selection: ResolvedSelection
    explicit_edit: AppliedReplacementEdit
    rewritten_snapshot: FileSnapshot[RewrittenWorktreeSpace]
    ownership_selection: ResolvedSelection
    rollback: RollbackProjection

    def __post_init__(self) -> None:
        require_snapshot_role(self.rewritten_snapshot, RewrittenWorktreeSpace)
        original_view = self.original_selection.view
        edit_plan = self.explicit_edit.plan
        if edit_plan.path != original_view.old_snapshot.path:
            raise ValueError("explicit edit path differs from original selection")
        if not _same_content_snapshot(
            edit_plan.baseline_snapshot,
            original_view.old_snapshot,
            left_role=BaselineSpace,
            right_role=DiffOldSpace,
        ):
            raise ValueError("explicit edit baseline differs from selection")
        if not _same_content_snapshot(
            edit_plan.worktree_snapshot,
            original_view.new_snapshot,
            left_role=WorktreeSpace,
            right_role=DiffNewSpace,
        ):
            raise ValueError("explicit edit worktree differs from selection")
        _require_selection_within_edit(
            self.original_selection,
            old_envelope=edit_plan.baseline_span,
            new_envelope=edit_plan.worktree_span,
            label="original selection",
        )
        if isinstance(self.rollback, RollbackSelection):
            if self.ownership_selection.view != self.rollback.selection.view:
                raise ValueError(
                    "ownership and rollback use different rewritten views"
                )
            if not self.rollback.selection.display_ids.is_subset_of(
                self.ownership_selection.display_ids
            ):
                raise ValueError("rollback selection exceeds ownership projection")
        rewritten_view = self.ownership_selection.view
        if rewritten_view.old_snapshot != original_view.old_snapshot:
            raise ValueError("rewritten projection has a different baseline")
        if not _same_content_snapshot(
            rewritten_view.new_snapshot,
            self.rewritten_snapshot,
            left_role=DiffNewSpace,
            right_role=RewrittenWorktreeSpace,
        ):
            raise ValueError("rewritten projection has a different target snapshot")
        if self.explicit_edit.rewritten_snapshot != self.rewritten_snapshot:
            raise ValueError("rewritten projection lacks the explicit edit transform")
        _require_selection_within_edit(
            self.ownership_selection,
            old_envelope=edit_plan.baseline_span,
            new_envelope=self.explicit_edit.rewritten_span,
            label="ownership projection",
        )
        if isinstance(self.rollback, RollbackSelection):
            _require_selection_within_edit(
                self.rollback.selection,
                old_envelope=edit_plan.baseline_span,
                new_envelope=self.explicit_edit.rewritten_span,
                label="rollback projection",
            )


def _require_selection_within_edit(
    selection: ResolvedSelection,
    *,
    old_envelope: LineSpan[_OldEnvelopeSpace],
    new_envelope: LineSpan[_NewEnvelopeSpace],
    label: str,
) -> None:
    """Reject a projection that escaped the one recorded explicit edit."""
    if not _spans_within(selection.old_spans, old_envelope):
        raise ValueError(f"{label} old coordinates exceed the explicit edit")
    if not _spans_within(selection.new_spans, new_envelope):
        raise ValueError(f"{label} new coordinates exceed the explicit edit")


def _spans_within(
    spans: SnapshotSpans[_SelectionSpace],
    envelope: LineSpan[_EnvelopeSpace],
) -> bool:
    return all(
        envelope.start.offset <= start and end <= envelope.end.offset
        for start, end in spans.ranges.ranges()
    )


def _same_content_snapshot(
    left: SnapshotDescriptor,
    right: SnapshotDescriptor,
    *,
    left_role: type[object],
    right_role: type[object],
) -> bool:
    return (
        left.role is left_role
        and right.role is right_role
        and left.path == right.path
        and left.identity == right.identity
        and left.line_count == right.line_count
    )
