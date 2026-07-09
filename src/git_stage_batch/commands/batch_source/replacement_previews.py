"""Replacement preview rendering for batch-source commands."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..selection import replacement_selection
from ...batch.operation_candidates import render_candidate_buffer_diff
from ...batch.replacement import build_replacement_batch_view_from_lines
from ...batch.selection import acquire_batch_ownership_for_display_ids_from_lines
from ...batch.submodule_pointer import is_batch_submodule_pointer
from ...core.buffer import LineBuffer
from ...core.replacement import ReplacementPayload, coerce_replacement_payload
from ...data.file_review.batch_selection import (
    translate_batch_file_gutter_ids_to_selection_ids,
)
from ...data.file_review.records import FileReviewAction
from ...utils.repository_buffers import load_git_object_as_buffer
from ...exceptions import exit_with_error
from ...i18n import _
from ...utils.paths import get_context_lines


SelectionTranslator = Callable[
    [str, str, set[int], FileReviewAction],
    tuple[set[int], Any],
]


def print_batch_source_replacement_preview(
    *,
    batch_name: str,
    files: dict,
    file_path: str,
    selected_ids: set[int],
    replacement_text: str | ReplacementPayload,
    translate_selection_ids: SelectionTranslator = (
        translate_batch_file_gutter_ids_to_selection_ids
    ),
) -> None:
    """Print a diff preview for replacing selected batch-source lines."""
    file_meta = files[file_path]
    if file_meta.get("file_type") == "binary":
        exit_with_error(_("Cannot preview replacement text for binary files."))
    if is_batch_submodule_pointer(file_meta):
        exit_with_error(_("Cannot preview replacement text for submodule pointers."))

    replacement_selection.require_contiguous_display_selection(selected_ids)
    batch_source_commit = file_meta["batch_source_commit"]
    batch_source_buffer = load_git_object_as_buffer(f"{batch_source_commit}:{file_path}")
    if batch_source_buffer is None:
        exit_with_error(
            _("Batch source content is missing for {file}.").format(file=file_path)
        )

    with batch_source_buffer as batch_source_lines:
        selection_ids, _rendered = translate_selection_ids(
            batch_name,
            file_path,
            selected_ids,
            FileReviewAction.INCLUDE_FROM_BATCH,
        )
        with acquire_batch_ownership_for_display_ids_from_lines(
            file_meta,
            batch_source_lines,
            selection_ids,
        ) as ownership:
            try:
                replacement_view = build_replacement_batch_view_from_lines(
                    batch_source_lines,
                    ownership,
                    coerce_replacement_payload(replacement_text),
                )
            except ValueError as e:
                exit_with_error(str(e))
            with replacement_view:
                before = LineBuffer.from_bytes(batch_source_buffer.to_bytes())
                try:
                    diff_text = render_candidate_buffer_diff(
                        file_path,
                        before,
                        replacement_view.source_buffer,
                        label_before="batch",
                        label_after="replacement-preview",
                        context_lines=get_context_lines(),
                    )
                    if diff_text:
                        print(
                            diff_text,
                            end="" if diff_text.endswith("\n") else "\n",
                        )
                finally:
                    before.close()
