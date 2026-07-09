"""Show-from multi-file list action orchestration."""

from __future__ import annotations

import sys

from ...batch.atomic_file_changes import (
    binary_change_from_batch_file_metadata,
    gitlink_change_from_batch_file_metadata,
)
from ...batch.file_display import render_batch_file_display
from ...data.file_review.records import ReviewSource
from ...data.selected_change.clear_reasons import (
    mark_selected_change_cleared_by_file_list,
)
from ...data.selected_change.lifecycle import clear_selected_change_state_files
from ...i18n import _
from ...output.file_review_list import (
    make_binary_file_review_list_entry,
    make_file_review_list_entry,
    make_gitlink_file_review_list_entry,
    print_file_review_list,
)


def show_batch_source_file_list(
    *,
    batch_name: str,
    files: dict[str, dict],
    metadata: dict,
    selectable: bool,
    command_source_args: str,
) -> None:
    """Show a navigational list for multiple files from a batch."""
    entries = []
    for file_path, file_meta in files.items():
        binary_change = binary_change_from_batch_file_metadata(file_path, file_meta)
        if binary_change is not None:
            entries.append(make_binary_file_review_list_entry(binary_change))
            continue
        gitlink_change = gitlink_change_from_batch_file_metadata(file_path, file_meta)
        if gitlink_change is not None:
            entries.append(make_gitlink_file_review_list_entry(gitlink_change))
            continue
        rendered = render_batch_file_display(
            batch_name,
            file_path,
            metadata=metadata,
            probe_mergeability=False,
        )
        if rendered is not None:
            entries.append(
                make_file_review_list_entry(
                    rendered.line_changes,
                )
            )

    if entries:
        if selectable:
            clear_selected_change_state_files()
            mark_selected_change_cleared_by_file_list(
                source=ReviewSource.BATCH.value,
                batch_name=batch_name,
            )
        print_file_review_list(
            source_label=_("Changes: batch {name}").format(name=batch_name),
            entries=entries,
            command_source_args=command_source_args,
        )
    else:
        print(_("Batch '{name}' is empty").format(name=batch_name), file=sys.stderr)
