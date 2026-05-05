"""Shared actionable selection derivation for file reviews and validation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from .models import LineEntry, LineLevelChange


class ActionableSelectionReason(str, Enum):
    """Why a file-review selection is treated as one actionable atom."""

    SIMPLE = "simple"
    REPLACEMENT = "replacement"
    STRUCTURAL_RUN = "structural-run"


@dataclass(frozen=True)
class ActionableSelection:
    """One atomic line selection that can be suggested as a complete change."""

    display_ids: tuple[int, ...]
    selection_ids: tuple[int, ...]
    reason: ActionableSelectionReason
    note: str | None = None
    actions: tuple[str, ...] = ()


def derive_actionable_selections(
    line_changes: LineLevelChange,
    *,
    gutter_to_selection_id: Mapping[int, int] | None = None,
) -> tuple[ActionableSelection, ...]:
    """Derive conservative complete selection atoms from a rendered file diff.

    The initial shared policy keeps each contiguous changed run inside a
    displayed hunk together. Mixed deletion/addition runs are marked as
    replacements so renderers can use "select together" wording.
    """
    selections: list[ActionableSelection] = []
    changed_run: list[LineEntry] = []
    selection_to_gutter = (
        {
            selection_id: gutter_id
            for gutter_id, selection_id in gutter_to_selection_id.items()
        }
        if gutter_to_selection_id is not None else
        None
    )

    def flush_changed_run() -> None:
        nonlocal changed_run
        if not changed_run:
            return

        selection_ids = tuple(
            line.id
            for line in changed_run
            if line.id is not None
        )
        if not selection_ids:
            changed_run = []
            return

        if selection_to_gutter is None:
            display_ids = selection_ids
        else:
            if any(selection_id not in selection_to_gutter for selection_id in selection_ids):
                changed_run = []
                return
            display_ids = tuple(
                selection_to_gutter[selection_id]
                for selection_id in selection_ids
            )

        if display_ids:
            changed_kinds = {line.kind for line in changed_run}
            reason: ActionableSelectionReason = (
                ActionableSelectionReason.REPLACEMENT
                if {"-", "+"}.issubset(changed_kinds)
                else ActionableSelectionReason.SIMPLE
            )
            selections.append(
                ActionableSelection(
                    display_ids=display_ids,
                    selection_ids=selection_ids,
                    reason=reason,
                    note=None,
                )
            )

        changed_run = []

    for line in line_changes.lines:
        if line.kind in ("+", "-") and line.id is not None:
            if selection_to_gutter is not None and line.id not in selection_to_gutter:
                flush_changed_run()
                continue
            changed_run.append(line)
            continue
        flush_changed_run()

    flush_changed_run()
    return tuple(selections)
