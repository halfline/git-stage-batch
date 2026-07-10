"""Discard-from execution for batch-source action commands."""

from __future__ import annotations

import sys

from . import action_selection as _action_selection
from . import binary_file_actions as _binary_file_actions
from . import text_file_actions as _text_file_actions
from . import text_plan_builders as _text_plan_builders
from ...batch.metadata_validation import get_validated_baseline_commit
from ...batch.selection import translate_atomic_unit_error_to_gutter_ids
from ...batch.submodule_pointer import (
    discard_submodule_pointer_from_batch,
    is_batch_submodule_pointer,
)
from ...data.session import snapshot_file_if_untracked
from ...data.undo import undo_checkpoint
from ...exceptions import (
    AtomicUnitError,
    BatchMetadataError,
    CommandError,
    MergeError,
    exit_with_error,
)
from ...i18n import _


def _print_binary_discard_result(
    file_path: str,
    action: _binary_file_actions.BinaryWorktreeAction,
) -> None:
    """Print discard-from status for a binary working-tree action."""
    if action is _binary_file_actions.BinaryWorktreeAction.REPLACED:
        print(
            _("✓ Restored binary file to baseline: {file}").format(
                file=file_path,
            ),
            file=sys.stderr,
        )
    elif action is _binary_file_actions.BinaryWorktreeAction.DELETED:
        print(
            _("✓ Removed binary file (not in baseline): {file}").format(
                file=file_path,
            ),
            file=sys.stderr,
        )


def execute_discard_action(
    *,
    batch_name: str,
    selection: _action_selection.BatchSourceActionSelection,
) -> None:
    """Discard selected batch-source changes from the working tree."""
    try:
        baseline_commit = get_validated_baseline_commit(batch_name)
    except BatchMetadataError as e:
        exit_with_error(str(e))

    files = selection.files
    selected_ids = selection.selected_ids
    selection_ids_to_discard = selection.selection_ids
    rendered = selection.rendered
    operation_parts = list(selection.operation_parts)

    with undo_checkpoint(" ".join(operation_parts), worktree_paths=list(files)):
        failed_files = []

        for file_path, file_meta in files.items():
            try:
                if file_meta.get("file_type") == "binary":
                    snapshot_file_if_untracked(file_path)
                    binary_action = (
                        _binary_file_actions.discard_binary_file_to_worktree(
                            file_path,
                            baseline_commit,
                        )
                    )
                    _print_binary_discard_result(file_path, binary_action)
                    continue

                if is_batch_submodule_pointer(file_meta):
                    discard_submodule_pointer_from_batch(file_path, file_meta)
                    continue

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
                print(
                    _("Error discarding {file}: {error}").format(
                        file=file_path,
                        error=str(e),
                    ),
                    file=sys.stderr,
                )
                failed_files.append(file_path)
            except Exception as e:
                print(
                    _("Error discarding {file}: {error}").format(
                        file=file_path,
                        error=str(e),
                    ),
                    file=sys.stderr,
                )
                failed_files.append(file_path)

    if failed_files:
        exit_with_error(
            _("Failed to discard changes for some files: {files}").format(
                files=", ".join(failed_files),
            )
        )
