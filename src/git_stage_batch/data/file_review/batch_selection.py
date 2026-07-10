"""Review-aware batch file selection translation."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from ...batch.file_display import render_batch_file_display
from ...batch.selection import require_single_file_context_for_line_selection_ranges
from ...batch.submodule_pointer import (
    is_batch_submodule_pointer,
    refuse_batch_submodule_pointer_lines,
)
from ...core.line_selection import LineRanges
from ...exceptions import CommandError
from ...i18n import _
from ..batch_file_scope import resolve_batch_file_scope
from .batch_selection_freshness import fresh_batch_review_selections_for_action
from .records import FileReviewAction
from .selection_validation import validate_review_scoped_line_selection

if TYPE_CHECKING:
    from ...core.models import RenderedBatchDisplay


@dataclass(frozen=True)
class _SelectionForValidation:
    display_ids: tuple[int, ...]
    is_splittable: bool = False


def _current_action_selections_for_validation(
    rendered: 'RenderedBatchDisplay',
    action: 'FileReviewAction | str',
) -> list[_SelectionForValidation]:
    review_action = FileReviewAction(action).value
    return [
        _SelectionForValidation(
            display_ids=group.display_ids,
            is_splittable=group.reason in {"replacement", "structural-run"},
        )
        for group in rendered.review_action_groups
        if group.display_ids and review_action in group.actions
    ]


def _selection_ids_from_gutter_ids(
    selected_ids: Iterable[int],
    display_id_map: Mapping[int, int],
) -> Iterator[int]:
    for gutter_id in selected_ids:
        if gutter_id in display_id_map:
            yield display_id_map[gutter_id]
        else:
            raise CommandError(
                _("Line ID {id} is not available for this action. Select one of the numbered lines shown for this batch file.").format(
                    id=gutter_id
                )
            )


def translate_batch_file_gutter_ids_to_selection_ids(
    batch_name: str,
    file_path: str,
    selected_ids: set[int] | None,
    action: 'FileReviewAction | str',
) -> tuple[set[int] | None, 'RenderedBatchDisplay | None']:
    """Translate displayed batch-file gutter IDs to internal selection IDs.

    If the IDs came after a fresh matching file review, validate them against
    the complete actions shown by that review before consulting the full batch
    display. Without a matching review, validate against the current review
    action groups so reset-only review IDs cannot be reinterpreted as compact
    mergeable IDs.
    """
    if selected_ids is None:
        return None, None

    review_selections = fresh_batch_review_selections_for_action(
        batch_name,
        file_path,
        action,
    )
    rendered = render_batch_file_display(batch_name, file_path)
    if rendered is None:
        return selected_ids, None

    selection_ids: set[int] | None = None
    if review_selections is not None:
        display_id_map = (
            rendered.review_gutter_to_selection_id
            or rendered.gutter_to_selection_id
        )
        selection_ids = set(_selection_ids_from_gutter_ids(selected_ids, display_id_map))
        validate_review_scoped_line_selection(selected_ids, review_selections)
    else:
        current_action_selections = _current_action_selections_for_validation(
            rendered,
            action,
        )
        if current_action_selections:
            display_id_map = (
                rendered.review_gutter_to_selection_id
                or rendered.gutter_to_selection_id
            )
            selection_ids = set(_selection_ids_from_gutter_ids(selected_ids, display_id_map))
            validate_review_scoped_line_selection(
                selected_ids,
                current_action_selections,
            )
        else:
            display_id_map = rendered.gutter_to_selection_id

    rendered_for_messages = (
        replace(
            rendered,
            gutter_to_selection_id=dict(display_id_map),
            selection_id_to_gutter={
                selection_id: gutter_id
                for gutter_id, selection_id in display_id_map.items()
            },
        )
        if review_selections is not None else
        rendered
    )
    if selection_ids is None:
        selection_ids = set(_selection_ids_from_gutter_ids(selected_ids, display_id_map))
    return selection_ids, rendered_for_messages


def translate_reset_batch_file_gutter_ids_to_selection_ranges(
    batch_name: str,
    all_files: dict[str, dict],
    file: str | None,
    patterns: list[str] | None,
    line_id_specification: str,
) -> LineRanges:
    """Translate fresh reset file-review gutter IDs to batch selection IDs.

    Reset is a metadata operation, so explicit reset line IDs must keep working
    even when a batch change is not currently mergeable into the worktree. Only
    translate through the mergeability-filtered gutter map when a fresh batch
    file review is in scope; otherwise leave the batch display IDs untouched.
    """
    files = resolve_batch_file_scope(batch_name, all_files, file, patterns)
    selected_ids = require_single_file_context_for_line_selection_ranges(
        batch_name,
        files,
        line_id_specification,
        "reset",
    )
    if selected_ids is None:
        return LineRanges.empty()

    file_path = list(files.keys())[0]
    if files[file_path].get("file_type") == "binary":
        raise CommandError(_("Cannot use --lines with binary files. Reset the whole file instead."))
    if is_batch_submodule_pointer(files[file_path]):
        refuse_batch_submodule_pointer_lines(_("Reset"))

    review_selections = fresh_batch_review_selections_for_action(
        batch_name,
        file_path,
        FileReviewAction.RESET_FROM_BATCH,
    )
    if review_selections is None:
        return selected_ids
    validate_review_scoped_line_selection(selected_ids, review_selections)

    rendered = render_batch_file_display(batch_name, file_path)
    if rendered is None:
        raise CommandError(
            _("No changes for file '{file}' in batch '{name}'.").format(
                file=file_path,
                name=batch_name,
            )
        )

    display_id_map = rendered.review_gutter_to_selection_id or rendered.gutter_to_selection_id
    return LineRanges.from_lines(
        _selection_ids_from_gutter_ids(selected_ids, display_id_map)
    )
