"""Shared file and line selection for batch-source action commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...batch.selection import (
    require_single_file_context_for_line_selection,
)
from ...batch.submodule_pointer import (
    is_batch_submodule_pointer,
    refuse_batch_submodule_pointer_lines,
)
from ...core.replacement import ReplacementPayload
from ...data.file_review.batch_selection import (
    translate_batch_file_gutter_ids_to_selection_ids,
)
from ...data.batch_file_scope import (
    resolve_batch_file_scope,
    resolve_current_batch_atomic_file_scope,
)
from ...data.file_review.records import FileReviewAction
from ...exceptions import exit_with_error
from ...i18n import _
from ..selection import replacement_selection
from .action_context import BatchSourceActionContext

if TYPE_CHECKING:
    from ...core.models import RenderedBatchDisplay


@dataclass(frozen=True)
class BatchSourceActionSelection:
    """Resolved files, line IDs, and command text for an action command."""

    file: str | None
    files: dict
    selected_ids: set[int] | None
    selection_ids: set[int] | None
    rendered: "RenderedBatchDisplay | None"
    operation_parts: tuple[str, ...]


def resolve_apply_action_selection(
    context: BatchSourceActionContext,
    *,
    line_ids: str | None,
    patterns: list[str] | None,
) -> BatchSourceActionSelection:
    """Resolve apply-from file scope and optional line selection."""
    file, files, selected_ids = _resolve_batch_source_action_files(
        context,
        line_ids=line_ids,
        patterns=patterns,
        command_name="apply",
    )
    _refuse_line_selection_for_atomic_files(
        files,
        selected_ids,
        binary_message=_("Cannot use --lines with binary files. Apply the whole file instead."),
        submodule_action=_("Apply"),
    )
    selection_ids, rendered = _translate_selected_ids(
        context.batch_name,
        files,
        selected_ids,
        FileReviewAction.APPLY_FROM_BATCH,
    )
    return BatchSourceActionSelection(
        file=file,
        files=files,
        selected_ids=selected_ids,
        selection_ids=selection_ids,
        rendered=rendered,
        operation_parts=_operation_parts(
            "apply",
            context.raw_selector,
            line_ids=line_ids,
            file=file,
        ),
    )


def resolve_include_action_selection(
    context: BatchSourceActionContext,
    *,
    line_ids: str | None,
    patterns: list[str] | None,
    replacement_payload: ReplacementPayload | None,
) -> BatchSourceActionSelection:
    """Resolve include-from file scope and optional line selection."""
    file, files, selected_ids = _resolve_batch_source_action_files(
        context,
        line_ids=line_ids,
        patterns=patterns,
        command_name="include",
    )
    if replacement_payload is not None and not selected_ids:
        exit_with_error(_("`include --from --as` requires `--line`."))
    _refuse_line_selection_for_atomic_files(
        files,
        selected_ids,
        binary_message=_("Cannot use --lines with binary files. Include the whole file instead."),
        submodule_action=_("Include"),
    )
    if selected_ids and replacement_payload is not None:
        replacement_selection.require_contiguous_display_selection(selected_ids)
    selection_ids, rendered = _translate_selected_ids(
        context.batch_name,
        files,
        selected_ids,
        FileReviewAction.INCLUDE_FROM_BATCH,
    )
    return BatchSourceActionSelection(
        file=file,
        files=files,
        selected_ids=selected_ids,
        selection_ids=selection_ids,
        rendered=rendered,
        operation_parts=_operation_parts(
            "include",
            context.raw_selector,
            line_ids=line_ids,
            file=file,
            replacement_payload=replacement_payload,
        ),
    )


def resolve_discard_action_selection(
    context: BatchSourceActionContext,
    *,
    line_ids: str | None,
    patterns: list[str] | None,
) -> BatchSourceActionSelection:
    """Resolve discard-from file scope and optional line selection."""
    file, files, selected_ids = _resolve_batch_source_action_files(
        context,
        line_ids=line_ids,
        patterns=patterns,
        command_name="discard",
    )
    _refuse_line_selection_for_atomic_files(
        files,
        selected_ids,
        binary_message=_(
            "Cannot use --lines with binary files. Discard the whole file instead."
        ),
        submodule_action=_("Discard"),
    )
    selection_ids, rendered = _translate_selected_ids(
        context.batch_name,
        files,
        selected_ids,
        FileReviewAction.DISCARD_FROM_BATCH,
    )
    return BatchSourceActionSelection(
        file=file,
        files=files,
        selected_ids=selected_ids,
        selection_ids=selection_ids,
        rendered=rendered,
        operation_parts=_operation_parts(
            "discard",
            context.raw_selector,
            line_ids=line_ids,
            file=file,
        ),
    )


def _resolve_batch_source_action_files(
    context: BatchSourceActionContext,
    *,
    line_ids: str | None,
    patterns: list[str] | None,
    command_name: str,
) -> tuple[str | None, dict, set[int] | None]:
    file = resolve_current_batch_atomic_file_scope(
        context.batch_name,
        context.all_files,
        context.file,
        patterns,
        line_ids,
    )
    files = resolve_batch_file_scope(
        context.batch_name,
        context.all_files,
        file,
        patterns,
    )
    selected_ids = require_single_file_context_for_line_selection(
        context.batch_name,
        files,
        line_ids,
        command_name,
    )
    return file, files, selected_ids


def _refuse_line_selection_for_atomic_files(
    files: dict,
    selected_ids: set[int] | None,
    *,
    binary_message: str,
    submodule_action: str,
) -> None:
    if not selected_ids:
        return
    file_path = next(iter(files))
    file_meta = files[file_path]
    if file_meta.get("file_type") == "binary":
        exit_with_error(binary_message)
    if file_meta.get("file_type") == "mode":
        exit_with_error(
            _("Cannot use --lines with file mode actions. Use the whole action instead.")
        )
    if is_batch_submodule_pointer(file_meta):
        refuse_batch_submodule_pointer_lines(submodule_action)


def _translate_selected_ids(
    batch_name: str,
    files: dict,
    selected_ids: set[int] | None,
    action: FileReviewAction,
) -> tuple[set[int] | None, "RenderedBatchDisplay | None"]:
    if not selected_ids:
        return selected_ids, None
    file_path = next(iter(files))
    return translate_batch_file_gutter_ids_to_selection_ids(
        batch_name,
        file_path,
        selected_ids,
        action,
    )


def _operation_parts(
    command_name: str,
    raw_selector: str,
    *,
    line_ids: str | None,
    file: str | None,
    replacement_payload: ReplacementPayload | None = None,
) -> tuple[str, ...]:
    parts = [command_name, "--from", raw_selector]
    if line_ids is not None:
        parts.extend(["--line", line_ids])
    if file is not None:
        parts.extend(["--file", file])
    if replacement_payload is not None:
        parts.extend(["--as", replacement_payload.display_text or "<stdin>"])
    return tuple(parts)
