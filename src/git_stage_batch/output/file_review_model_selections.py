"""Actionable selection derivation for file review model construction."""

from __future__ import annotations

from ..core.actionable_changes import (
    ActionableSelection,
    ActionableSelectionReason,
    derive_actionable_selections,
)
from ..core.models import LineLevelChange, ReviewActionGroup


def _partly_selects_ownership_group(
    selection: ActionableSelection,
    ownership_group_sets: list[set[int]],
) -> bool:
    """Return whether a review selection splits a complete batch ownership group."""
    selected_ids = set(selection.selection_ids)
    return any(
        selected_ids & ownership_group
        and not ownership_group.issubset(selected_ids)
        for ownership_group in ownership_group_sets
    )


def _reason_for_selection_ids(
    line_changes: LineLevelChange,
    selection_ids: tuple[int, ...],
) -> ActionableSelectionReason:
    selected_id_set = set(selection_ids)
    saw_addition = False
    saw_deletion = False
    for line in line_changes.lines:
        if line.id not in selected_id_set or line.kind not in ("+", "-"):
            continue
        if line.kind == "+":
            saw_addition = True
        else:
            saw_deletion = True
        if saw_addition and saw_deletion:
            return ActionableSelectionReason.REPLACEMENT
    return ActionableSelectionReason.SIMPLE


def _actionable_selections_from_selection_groups(
    line_changes: LineLevelChange,
    selection_groups: tuple[tuple[int, ...], ...],
    display_id_by_selection_id: dict[int, int] | None,
) -> tuple[ActionableSelection, ...]:
    """Create review selections directly from complete ownership groups."""
    selections: list[ActionableSelection] = []
    for group in selection_groups:
        selection_ids = tuple(
            selection_id
            for selection_id in group
            if selection_id is not None
        )
        if not selection_ids:
            continue
        if display_id_by_selection_id is None:
            display_ids = selection_ids
        else:
            if any(
                selection_id not in display_id_by_selection_id
                for selection_id in selection_ids
            ):
                continue
            display_ids = tuple(
                display_id_by_selection_id[selection_id]
                for selection_id in selection_ids
            )
        selections.append(
            ActionableSelection(
                display_ids=display_ids,
                selection_ids=selection_ids,
                reason=_reason_for_selection_ids(line_changes, selection_ids),
            )
        )
    return tuple(selections)


def _display_actionable_selections_from_review_action_groups(
    line_changes: LineLevelChange,
    review_action_groups: tuple[ReviewActionGroup, ...],
    gutter_to_selection_id: dict[int, int] | None,
) -> tuple[ActionableSelection, ...]:
    """Derive user-facing batch review changes without splitting reset atoms."""
    action_group_by_selection_id = {
        selection_id: group
        for group in review_action_groups
        for selection_id in group.selection_ids
    }
    atomic_group_sets = [
        set(group.selection_ids)
        for group in review_action_groups
        if group.reason in (
            ActionableSelectionReason.REPLACEMENT.value,
            ActionableSelectionReason.STRUCTURAL_RUN.value,
        )
    ]
    selections = []
    for selection in derive_actionable_selections(
        line_changes,
        gutter_to_selection_id=gutter_to_selection_id,
    ):
        chunk_display_ids: list[int] = []
        chunk_selection_ids: list[int] = []
        chunk_actions: tuple[str, ...] | None = None

        def flush_chunk() -> None:
            nonlocal chunk_display_ids, chunk_selection_ids, chunk_actions
            if not chunk_display_ids or chunk_actions is None:
                chunk_display_ids = []
                chunk_selection_ids = []
                chunk_actions = None
                return
            chunk = ActionableSelection(
                display_ids=tuple(chunk_display_ids),
                selection_ids=tuple(chunk_selection_ids),
                reason=_reason_for_selection_ids(
                    line_changes,
                    tuple(chunk_selection_ids),
                ),
                actions=chunk_actions,
            )
            if not _partly_selects_ownership_group(chunk, atomic_group_sets):
                selections.append(chunk)
            chunk_display_ids = []
            chunk_selection_ids = []
            chunk_actions = None

        for display_id, selection_id in zip(
            selection.display_ids,
            selection.selection_ids,
        ):
            action_group = action_group_by_selection_id.get(selection_id)
            if action_group is None:
                flush_chunk()
                continue
            actions = action_group.actions
            if chunk_actions is not None and actions != chunk_actions:
                flush_chunk()
            chunk_display_ids.append(display_id)
            chunk_selection_ids.append(selection_id)
            chunk_actions = actions
        flush_chunk()
    return tuple(selections)


def derive_file_review_actionable_selections(
    line_changes: LineLevelChange,
    *,
    gutter_to_selection_id: dict[int, int] | None,
    actionable_selection_groups: tuple[tuple[int, ...], ...] | None,
    review_action_groups: tuple[ReviewActionGroup, ...] | None,
    display_id_by_selection_id: dict[int, int] | None,
) -> tuple[ActionableSelection, ...]:
    """Return selections that file-review model construction can display."""
    if review_action_groups is not None:
        return _display_actionable_selections_from_review_action_groups(
            line_changes,
            review_action_groups,
            gutter_to_selection_id,
        )
    if actionable_selection_groups is not None:
        actionable_selections = _actionable_selections_from_selection_groups(
            line_changes,
            actionable_selection_groups,
            display_id_by_selection_id,
        )
        ownership_group_sets = [
            set(group)
            for group in actionable_selection_groups
            if group
        ]
        return tuple(
            selection
            for selection in actionable_selections
            if not _partly_selects_ownership_group(selection, ownership_group_sets)
        )
    return derive_actionable_selections(
        line_changes,
        gutter_to_selection_id=gutter_to_selection_id,
    )
