"""Show-from single-file display action orchestration."""

from __future__ import annotations

import sys

from ...batch.atomic_file_changes import (
    binary_change_from_batch_file_metadata,
    gitlink_change_from_batch_file_metadata,
)
from ...batch.file_display import render_batch_file_display
from ...core.models import LineLevelChange
from ...data.batch_hunk_display import cache_rendered_batch_file_display
from ...data.batch_selected_changes import (
    compute_batch_binary_fingerprint,
    compute_batch_gitlink_fingerprint,
)
from ...data.file_review.pages import normalize_page_spec
from ...data.file_review.records import ReviewSource
from ...data.file_review.state import (
    clear_last_file_review_state,
    write_last_file_review_state,
)
from ...data.selected_change.file_changes import (
    cache_binary_file_change,
    cache_gitlink_change,
)
from ...data.selected_change.lifecycle import clear_selected_change_state_files
from ...data.selected_change.store import SelectedChangeKind
from ...exceptions import exit_with_error
from ...i18n import _
from ...output.file_review import print_file_review
from ...output.file_review_model_builder import build_file_review_model
from ...output.file_review_state_builder import (
    make_file_review_state,
    resolve_default_review_pages,
)
from ...output.hunk import print_line_level_changes
from ...output.patch import (
    print_binary_file_change,
    print_gitlink_change,
)


def _shown_pages_for_display_ids(review_model, display_ids: set[int]) -> tuple[int, ...]:
    """Return review pages that contain the selected display IDs."""
    return tuple(
        sorted(
            {
                change.first_page
                for change in review_model.changes
                if set(change.display_ids) & display_ids
            }
        )
    )


def show_batch_source_file_display(
    *,
    batch_name: str,
    file_path: str,
    files: dict[str, dict],
    metadata: dict,
    selected_ids: set[int] | None,
    selectable: bool,
    page: str | None,
    command_source_args: str,
) -> None:
    """Show one file from a batch source selection."""
    file_meta = files[file_path]
    binary_change = binary_change_from_batch_file_metadata(
        file_path,
        file_meta,
    )
    if binary_change is not None:
        if selected_ids:
            exit_with_error(
                _(
                    "Cannot use --lines with binary files. Run without --lines "
                    "to view the binary change summary."
                )
            )
        if page is not None:
            exit_with_error(_("File review pages are only available for text changes."))
        if selectable:
            clear_selected_change_state_files()
            cache_binary_file_change(
                binary_change,
                kind=SelectedChangeKind.BATCH_BINARY,
                batch_name=batch_name,
                batch_binary_fingerprint=compute_batch_binary_fingerprint(
                    batch_name,
                    file_path,
                    file_meta,
                ),
            )
        print_binary_file_change(binary_change)
        return

    gitlink_change = gitlink_change_from_batch_file_metadata(
        file_path,
        file_meta,
    )
    if gitlink_change is not None:
        if selected_ids:
            exit_with_error(
                _(
                    "Cannot use --lines with submodule pointers. Run without "
                    "--lines to view the submodule pointer summary."
                )
            )
        if page is not None:
            exit_with_error(_("File review pages are only available for text changes."))
        if selectable:
            clear_selected_change_state_files()
            cache_gitlink_change(
                gitlink_change,
                kind=SelectedChangeKind.BATCH_GITLINK,
                batch_name=batch_name,
                batch_gitlink_fingerprint=compute_batch_gitlink_fingerprint(
                    file_path,
                    file_meta,
                ),
            )
        print_gitlink_change(gitlink_change)
        return

    rendered = render_batch_file_display(batch_name, file_path, metadata=metadata)
    if rendered is None:
        print(
            _("No changes for file '{file}' in batch '{name}'.").format(
                file=file_path,
                name=batch_name,
            ),
            file=sys.stderr,
        )
        return

    review_model = None
    review_gutter_to_selection_id = (
        rendered.review_gutter_to_selection_id
        or rendered.gutter_to_selection_id
    )
    review_selection_id_to_gutter = (
        rendered.review_selection_id_to_gutter
        or rendered.selection_id_to_gutter
    )
    review_action_groups = rendered.review_action_groups or None

    def get_review_model():
        nonlocal review_model
        if review_model is None:
            review_model = build_file_review_model(
                rendered.line_changes,
                gutter_to_selection_id=review_gutter_to_selection_id,
                actionable_selection_groups=rendered.actionable_selection_groups,
                review_action_groups=review_action_groups,
            )
        return review_model

    if selectable and page is not None:
        resolve_default_review_pages(
            get_review_model(),
            requested_page_spec=page,
            previous_selection=None,
        )

    if page is not None or (selectable and not selected_ids):
        review_model = get_review_model()
        shown_pages = resolve_default_review_pages(
            review_model,
            requested_page_spec=page,
            previous_selection=None,
        )
        page_spec = normalize_page_spec(shown_pages, len(review_model.pages))
        if selectable:
            clear_last_file_review_state()
            cache_rendered_batch_file_display(file_path, rendered)
            write_last_file_review_state(
                make_file_review_state(
                    review_model,
                    source=ReviewSource.BATCH,
                    batch_name=batch_name,
                    shown_pages=shown_pages,
                    selected_change_kind=SelectedChangeKind.BATCH_FILE,
                    gutter_to_selection_id=review_gutter_to_selection_id,
                    actionable_selection_groups=rendered.actionable_selection_groups,
                    review_action_groups=review_action_groups,
                )
            )
        print_file_review(
            review_model,
            shown_pages=shown_pages,
            source_label=_("Changes: batch {name}").format(name=batch_name),
            page_spec=page_spec,
            command_source_args=command_source_args,
            source=ReviewSource.BATCH,
            batch_name=batch_name,
            note=metadata.get("note") or None,
        )
        return

    if selected_ids:
        line_gutter_to_selection_id = (
            review_gutter_to_selection_id
            if selectable
            else rendered.gutter_to_selection_id
        )

        selection_ids = set()
        for gutter_id in selected_ids:
            if gutter_id in line_gutter_to_selection_id:
                selection_ids.add(line_gutter_to_selection_id[gutter_id])
            else:
                exit_with_error(
                    _(
                        "Line ID {id} is not available for this action. "
                        "Select one of the numbered lines shown for this "
                        "batch file."
                    ).format(id=gutter_id)
                )

        if selectable:
            clear_last_file_review_state()
            cache_rendered_batch_file_display(file_path, rendered)
            review_model = get_review_model()
            visible_review_display_ids = {
                review_selection_id_to_gutter[selection_id]
                for selection_id in selection_ids
                if selection_id in review_selection_id_to_gutter
            }
            shown_pages = _shown_pages_for_display_ids(
                review_model,
                visible_review_display_ids,
            )
            if shown_pages:
                write_last_file_review_state(
                    make_file_review_state(
                        review_model,
                        source=ReviewSource.BATCH,
                        batch_name=batch_name,
                        shown_pages=shown_pages,
                        selected_change_kind=SelectedChangeKind.BATCH_FILE,
                        gutter_to_selection_id=review_gutter_to_selection_id,
                        actionable_selection_groups=rendered.actionable_selection_groups,
                        review_action_groups=review_action_groups,
                        visible_display_ids=visible_review_display_ids,
                        entire_file_shown=False,
                    )
                )

        filtered_lines = [
            line for line in rendered.line_changes.lines if line.id in selection_ids
        ]
        if filtered_lines:
            filtered_line_changes = LineLevelChange(
                path=rendered.line_changes.path,
                lines=filtered_lines,
                header=rendered.line_changes.header,
            )
            print_line_level_changes(
                filtered_line_changes,
                gutter_to_selection_id=line_gutter_to_selection_id,
            )
    else:
        print_line_level_changes(
            rendered.line_changes,
            gutter_to_selection_id=(
                review_gutter_to_selection_id
                if selectable
                else {}
            ),
        )
