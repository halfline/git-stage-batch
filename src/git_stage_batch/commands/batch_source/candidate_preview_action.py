"""Show-from candidate preview action orchestration."""

from __future__ import annotations

from ...batch.operation_candidates import save_candidate_preview_state
from ...core.replacement import ReplacementPayload
from ...data.file_review.batch_selection import (
    translate_batch_file_gutter_ids_to_selection_ids,
)
from ...exceptions import MergeError, exit_with_error
from ...i18n import _
from ...output.candidate_preview import (
    render_operation_candidate,
    render_operation_candidate_overview,
)
from . import candidate_preview_builders as _candidate_preview_builders
from . import candidate_previews as _candidate_previews


def show_batch_source_candidate_preview(
    *,
    selector,
    batch_name: str,
    files: dict,
    selected_ids: set[int] | None,
    replacement_text: str | ReplacementPayload | None,
    patterns: list[str] | None,
    porcelain: bool,
    note: str | None,
) -> None:
    """Render a candidate preview for a show-from batch source selector."""
    if patterns is not None or len(files) != 1:
        exit_with_error(_("Candidate preview requires exactly one file."))
    file_path = list(files.keys())[0]
    try:
        previews = _candidate_preview_builders.build_batch_source_candidate_previews(
            selector=selector,
            files=files,
            file_path=file_path,
            selected_ids=selected_ids,
            replacement_text=replacement_text,
            translate_selection_ids=(
                translate_batch_file_gutter_ids_to_selection_ids
            ),
        )
    except ValueError as error:
        exit_with_error(str(error))
    except MergeError as error:
        exit_with_error(str(error))

    if not previews:
        exit_with_error(
            _("Batch '{batch}' has no {operation} candidates for {file}.").format(
                batch=batch_name,
                operation=selector.candidate_operation,
                file=file_path,
            )
        )

    if selector.candidate_ordinal is None:
        try:
            reviewed_previews = render_operation_candidate_overview(
                previews,
                porcelain=porcelain,
                note=note,
            )
            for preview in reviewed_previews:
                save_candidate_preview_state(preview)
        finally:
            _candidate_previews.close_candidate_previews(previews)
        return

    try:
        preview = _candidate_previews.require_candidate_preview_for_ordinal(
            previews,
            selector.candidate_ordinal,
            batch_name=batch_name,
            operation=selector.candidate_operation,
            file_path=file_path,
        )
        render_operation_candidate(
            preview,
            porcelain=porcelain,
            note=note,
        )
        save_candidate_preview_state(preview)
    finally:
        _candidate_previews.close_candidate_previews(previews)
