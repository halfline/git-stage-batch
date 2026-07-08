"""Merge refusal helpers for batch-source commands."""

from __future__ import annotations

from collections.abc import Sequence

from ...batch.file_display import render_batch_file_display
from ...exceptions import exit_with_error
from ...i18n import _


def refuse_batch_source_merge_failures(
    *,
    batch_name: str,
    failed_files: Sequence[str],
) -> None:
    """Exit for merge failures without enumerable candidate details."""
    if len(failed_files) == 1:
        file_path = failed_files[0]
        rendered = render_batch_file_display(batch_name, file_path)
        has_mergeable_lines = (
            rendered is not None and len(rendered.gutter_to_selection_id) > 0
        )

        if has_mergeable_lines:
            error_msg = _(
                "Batch '{batch}' contains changes to {file} that are "
                "incompatible with the current working tree. "
                "Use 'git-stage-batch show --from {batch}' to review the batch, "
                "or use '--lines' to apply only specific changes."
            ).format(batch=batch_name, file=file_path)
        else:
            error_msg = _(
                "Batch '{batch}' contains changes to {file} that are "
                "incompatible with the current working tree. "
                "Use 'git-stage-batch show --from {batch}' to review the batch."
            ).format(batch=batch_name, file=file_path)
        exit_with_error(error_msg)

    exit_with_error(
        _(
            "Batch '{batch}' contains changes to one or more files that are "
            "incompatible with the current working tree. "
            "Failed for: {files}. "
            "Use 'git-stage-batch show --from {batch}' to review the batch, "
            "or use '--lines' to apply only specific changes."
        ).format(
            batch=batch_name,
            files=", ".join(failed_files),
        )
    )
