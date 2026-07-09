"""Discard from batch command implementation."""

from __future__ import annotations

import sys
from typing import Optional

from .batch_source import action_context as _action_context
from .batch_source import action_selection as _action_selection
from .batch_source import binary_file_actions as _binary_file_actions
from .batch_source import text_file_actions as _text_file_actions
from .batch_source import text_plan_builders as _text_plan_builders
from ..batch.metadata_validation import get_validated_baseline_commit
from ..batch.selection import (
    translate_atomic_unit_error_to_gutter_ids,
)
from ..batch.submodule_pointer import (
    discard_submodule_pointer_from_batch,
    is_batch_submodule_pointer,
)
from ..data.file_review.records import FileReviewAction
from ..data.session import snapshot_file_if_untracked
from ..data.undo import undo_checkpoint
from ..exceptions import exit_with_error, AtomicUnitError, CommandError, MergeError, BatchMetadataError
from ..i18n import _
from ..utils.git_repository import require_git_repository


def command_discard_from_batch(
    batch_name: str,
    line_ids: Optional[str] = None,
    file: Optional[str] = None,
    patterns: Optional[list[str]] = None,
) -> None:
    """Remove batch changes from working tree using structural merge.

    Args:
        batch_name: Name of batch to discard from
        line_ids: Optional line IDs to discard (requires single-file context)
        file: Optional file path to select from batch.
              If None, discards all files in batch.
        patterns: Optional gitignore-style file patterns to filter batch files.
    """
    require_git_repository()
    raw_selector = batch_name
    context = _action_context.resolve_plain_batch_source_action_context(
        raw_selector,
        review_action=FileReviewAction.DISCARD_FROM_BATCH,
        command_name="discard",
        line_ids=line_ids,
        file=file,
        patterns=patterns,
    )
    batch_name = context.batch_name

    selection = _action_selection.resolve_discard_action_selection(
        context,
        line_ids=line_ids,
        patterns=patterns,
    )
    file = selection.file
    files = selection.files
    selected_ids = selection.selected_ids
    selection_ids_to_discard = selection.selection_ids
    rendered = selection.rendered

    # Get baseline commit (raises BatchMetadataError with clear message if missing)
    try:
        baseline_commit = get_validated_baseline_commit(batch_name)
    except BatchMetadataError as e:
        exit_with_error(str(e))
    operation_parts = list(selection.operation_parts)
    with undo_checkpoint(" ".join(operation_parts), worktree_paths=list(files)):
        # Discard all files in batch
        failed_files = []

        for file_path, file_meta in files.items():
            try:
                # Binary files are atomic units - handle separately without ownership/merge logic
                if file_meta.get("file_type") == "binary":
                    snapshot_file_if_untracked(file_path)
                    binary_action = (
                        _binary_file_actions.discard_binary_file_to_worktree(
                            file_path,
                            baseline_commit,
                        )
                    )
                    if (
                        binary_action
                        is _binary_file_actions.BinaryWorktreeAction.REPLACED
                    ):
                        print(
                            _("✓ Restored binary file to baseline: {file}").format(
                                file=file_path,
                            ),
                            file=sys.stderr,
                        )
                    elif (
                        binary_action
                        is _binary_file_actions.BinaryWorktreeAction.DELETED
                    ):
                        print(
                            _(
                                "✓ Removed binary file (not in baseline): {file}"
                            ).format(file=file_path),
                            file=sys.stderr,
                        )
                    continue
                if is_batch_submodule_pointer(file_meta):
                    discard_submodule_pointer_from_batch(file_path, file_meta)
                    continue

                # Snapshot file before modifying
                snapshot_file_if_untracked(file_path)

                try:
                    text_plan_result = (
                        _text_plan_builders.build_discard_text_file_action_plan(
                            file_path=file_path,
                            file_meta=file_meta,
                            baseline_commit=baseline_commit,
                            selected_ids=selected_ids,
                            selection_ids_to_discard=selection_ids_to_discard,
                        )
                    )
                except AtomicUnitError as e:
                    if rendered:
                        translate_atomic_unit_error_to_gutter_ids(
                            e,
                            rendered,
                            "discard from",
                            batch_name,
                        )
                    exit_with_error(
                        _("Failed to discard from batch '{name}': {error}").format(
                            name=batch_name,
                            error=str(e),
                        )
                    )

                if text_plan_result.missing_source:
                    failed_files.append(file_path)
                    continue
                if text_plan_result.plan is None:
                    continue

                try:
                    _text_file_actions.write_discarded_text_file_to_worktree(
                        text_plan_result.plan.file_path,
                        text_plan_result.plan.buffer,
                        text_plan_result.plan.file_mode,
                        text_plan_result.plan.change_type,
                    )
                finally:
                    text_plan_result.plan.close()

            except CommandError:
                raise
            except MergeError as e:
                print(_("Error discarding {file}: {error}").format(file=file_path, error=str(e)), file=sys.stderr)
                failed_files.append(file_path)
            except Exception as e:
                print(_("Error discarding {file}: {error}").format(file=file_path, error=str(e)), file=sys.stderr)
                failed_files.append(file_path)

    if failed_files:
        exit_with_error(
            _("Failed to discard changes for some files: {files}").format(files=", ".join(failed_files))
        )

    # Success message
    if line_ids:
        print(_("✓ Discarded selected lines from batch '{name}'").format(name=batch_name), file=sys.stderr)
    elif file is not None:
        print(_("✓ Discarded changes for {file} from batch '{name}'").format(file=list(files.keys())[0], name=batch_name), file=sys.stderr)
    else:
        print(_("✓ Discarded changes from batch '{name}'").format(name=batch_name), file=sys.stderr)

    print(_("Note: Batch '{name}' still exists (use 'drop' to delete it)").format(name=batch_name), file=sys.stderr)
