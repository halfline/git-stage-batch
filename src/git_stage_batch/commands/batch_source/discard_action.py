"""Discard-from execution for batch-source action commands."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

from . import action_plans as _action_plans
from . import action_selection as _action_selection
from . import atomic_unit_refusals as _atomic_unit_refusals
from . import binary_file_actions as _binary_file_actions
from . import file_mode_actions as _file_mode_actions
from . import text_file_actions as _text_file_actions
from . import text_plan_builders as _text_plan_builders
from ...batch.state.query import read_batch_metadata_for_batches
from ...batch.state.validation import get_validated_baseline_commit
from ...batch.submodule_pointer import (
    discard_submodule_pointer_from_batch,
    is_batch_submodule_pointer,
    validate_discard_submodule_pointer,
)
from ...data.session import snapshot_file_if_untracked
from ...data.undo.checkpoints import undo_checkpoint
from ...batch.state.metadata_types import (
    BatchFileMetadataDict,
    BatchMetadataDict,
)
from ...data.applied_batch_overlays import (
    AppliedBatchOverlaySnapshot,
    fresh_applied_batch_overlay_for_path,
    load_applied_batch_overlay_snapshot,
)
from ...data.file_target_identity import (
    IndexIdentity,
    WorktreeIdentity,
    capture_worktree_identities,
    capture_worktree_identity,
    read_index_identities,
)
from ...data.file_modes import detect_file_mode_in_commit
from ...data.undo.checkpoints import transaction_checkpoint, undo_checkpoint
from ...exceptions import (
    AtomicUnitError,
    BatchMetadataError,
    CommandError,
    MergeError,
    exit_with_error,
)
from ...git_paths import display_path, terminal_safe_shell_join
from ...i18n import _, pgettext
from ...utils.file_job_workspace import FileJobWorkspace
from ...utils.repository_buffers import read_git_object_buffer_or_none


@dataclass(frozen=True, slots=True)
class _DiscardTextInput:
    """Captured immutable worktree input for one text discard plan."""

    ordinal: int
    file_path: str
    file_meta: BatchFileMetadataDict
    identity: WorktreeIdentity
    worktree_artifact: Path
    scratch_directory: Path


@dataclass(frozen=True, slots=True)
class _DiscardModeActionPlan:
    """Deferred worktree mode restoration."""

    file_path: str
    file_mode: str

    def close(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class _DiscardPlanCapture:
    """All plans and target identities captured before publication."""

    plans: list[_action_plans.BatchSourceActionPlan]
    worktree_identities: dict[str, WorktreeIdentity]
    index_identities: dict[str, IndexIdentity]
    index_mutation_paths: tuple[str, ...]


def _validate_discard_submodule_pointer_target(
    file_path: str,
    file_meta: BatchFileMetadataDict,
    index_identity: IndexIdentity,
) -> None:
    """Validate submodule metadata and command-owned index preconditions."""
    validate_discard_submodule_pointer(file_path, file_meta)
    if file_meta.get("change_type") != "added":
        return
    if index_identity.unmerged_entries:
        raise CommandError(
            _(
                "Cannot discard added submodule pointer for {file}: "
                "the index has unmerged entries."
            ).format(file=display_path(file_path))
        )
    if index_identity.exists and not index_identity.intent_to_add:
        raise CommandError(
            _(
                "Cannot discard added submodule pointer for {file}: "
                "the index already contains staged content."
            ).format(file=display_path(file_path))
        )


def _print_binary_discard_result(
    file_path: str,
    action: _binary_file_actions.BinaryWorktreeAction | None,
) -> None:
    """Print discard-from status for a binary working-tree action."""
    if action is _binary_file_actions.BinaryWorktreeAction.REPLACED:
        print(
            _("✓ Restored binary file to baseline: {file}").format(
                file=display_path(file_path),
            ),
            file=sys.stderr,
        )
    elif action is _binary_file_actions.BinaryWorktreeAction.DELETED:
        print(
            _("✓ Removed binary file (not in baseline): {file}").format(
                file=display_path(file_path),
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

    with undo_checkpoint(
        terminal_safe_shell_join(operation_parts),
        worktree_paths=list(files),
        rollback_on_error=True,
    ):
        failed_files = []

        for file_path, file_meta in files.items():
            try:
                if _file_mode_actions.is_file_mode_action(file_meta):
                    _file_mode_actions.apply_old_file_mode(file_path, file_meta)
                    continue
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
                        _atomic_unit_refusals.translate_atomic_unit_error_to_gutter_ids(
                            e,
                            rendered,
                            pgettext("batch failure operation", "discard from"),
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
                        file=display_path(file_path),
                        error=str(e),
                    ),
                    file=sys.stderr,
                )
                failed_files.append(file_path)
            except Exception as e:
                print(
                    _("Error discarding {file}: {error}").format(
                        file=display_path(file_path),
                        error=str(e),
                    ),
                    file=sys.stderr,
                )
                failed_files.append(file_path)

        if failed_files:
            exit_with_error(
                _("Failed to discard changes for some files: {files}").format(
                    files=", ".join(display_path(path) for path in failed_files),
                )
            )
def _capture_discard_targets(
    files: dict[str, BatchFileMetadataDict],
    applied_overlay_snapshot: AppliedBatchOverlaySnapshot,
    workspace: FileJobWorkspace,
) -> tuple[
    tuple[_DiscardTextInput, ...],
    dict[str, WorktreeIdentity],
    dict[str, IndexIdentity],
    tuple[tuple[str, str], ...],
]:
    """Capture every selected target before any discard planning begins."""
    overlay_paths = applied_overlay_snapshot.state["files"]
    uncaptured_paths = tuple(
        file_path for file_path in files if file_path not in overlay_paths
    )
    current_index_identities = read_index_identities(uncaptured_paths)
    index_identities = {
        file_path: (
            applied_overlay_snapshot.index_identities[file_path]
            if file_path in overlay_paths
            else current_index_identities[file_path]
        )
        for file_path in files
    }
    worktree_identities: dict[str, WorktreeIdentity] = {}
    text_inputs: list[_DiscardTextInput] = []
    capture_errors: list[tuple[str, str]] = []
    for ordinal, (file_path, file_meta) in enumerate(files.items()):
        try:
            if (
                _file_mode_actions.is_file_mode_action(file_meta)
                or file_meta.get("file_type") == "binary"
                or is_batch_submodule_pointer(file_meta)
            ):
                worktree_identities[file_path] = capture_worktree_identity(file_path)
                continue
            worktree_artifact = workspace.artifact_path(
                ordinal,
                "worktree-input",
            )
            identity = capture_worktree_identity(
                file_path,
                content_artifact_path=worktree_artifact,
            )
            worktree_identities[file_path] = identity
            text_inputs.append(
                _DiscardTextInput(
                    ordinal=ordinal,
                    file_path=file_path,
                    file_meta=file_meta,
                    identity=identity,
                    worktree_artifact=worktree_artifact,
                    scratch_directory=workspace.scratch_directory(ordinal),
                )
            )
        except CommandError:
            raise
        except Exception as error:
            capture_errors.append((file_path, str(error)))
    return (
        tuple(text_inputs),
        worktree_identities,
        index_identities,
        tuple(capture_errors),
    )
def _require_unchanged_discard_targets(
    expected_index_identities: dict[str, IndexIdentity],
    expected_worktree_identities: dict[str, WorktreeIdentity],
) -> None:
    """Refuse every plan when any captured publication target changed."""
    current_index_identities = read_index_identities(expected_index_identities)
    for file_path, expected_index_identity in expected_index_identities.items():
        if (
            expected_index_identity.unmerged_entries
            or current_index_identities[file_path] != expected_index_identity
        ):
            raise _discard_target_changed_error(file_path, target="index")
    current_worktree_identities = capture_worktree_identities(
        expected_worktree_identities
    )
    for file_path, expected_worktree_identity in expected_worktree_identities.items():
        if current_worktree_identities[file_path] != expected_worktree_identity:
            raise _discard_target_changed_error(file_path, target="worktree")


def _discard_target_changed_error(
    file_path: str,
    *,
    target: str,
) -> CommandError:
    """Return the established complete stale-discard refusal message."""
    if target == "index":
        return CommandError(
            _(
                "Index changed while discard was being calculated: "
                "{file}. Retry the discard command."
            ).format(file=display_path(file_path))
        )
    return CommandError(
        _(
            "Working tree file changed while discard was being "
            "calculated: {file}. Retry the discard command."
        ).format(file=display_path(file_path))
    )


def _print_binary_discard_results(
    results: tuple[
        tuple[str, _binary_file_actions.BinaryWorktreeAction | None],
        ...,
    ],
) -> None:
    """Print binary discard results after the outer transaction commits."""
    for file_path, action in results:
        _print_binary_discard_result(file_path, action)


def _load_overlay_batch_metadata(
    files: dict[str, BatchFileMetadataDict],
    applied_overlay_snapshot: AppliedBatchOverlaySnapshot,
) -> dict[str, BatchMetadataDict]:
    """Load metadata for overlay owners relevant to selected paths."""
    overlay_batch_names: set[str] = set()
    for file_path in files:
        overlay_entry = applied_overlay_snapshot.state["files"].get(file_path)
        if overlay_entry is None:
            continue
        overlay_batch_names.update(
            application["batch"] for application in overlay_entry["applications"]
        )
    return read_batch_metadata_for_batches(sorted(overlay_batch_names))


def _build_discard_binary_action_plan(
    *,
    ordinal: int,
    file_path: str,
    baseline_commit: str,
    workspace: FileJobWorkspace,
) -> _action_plans.BinaryFileActionPlan:
    """Load a binary baseline into a deferred worktree action."""
    buffer = read_git_object_buffer_or_none(
        f"{baseline_commit}:{file_path}",
        spool_dir=workspace.scratch_directory(ordinal),
    )
    try:
        metadata: BatchFileMetadataDict = {
            "file_type": "binary",
            "change_type": "deleted" if buffer is None else "modified",
        }
        if buffer is not None:
            mode = detect_file_mode_in_commit(baseline_commit, file_path)
            if mode is None:
                raise ValueError("binary baseline object omitted its file mode")
            metadata["mode"] = mode
        return _action_plans.BinaryFileActionPlan(file_path, metadata, buffer)
    except BaseException:
        if buffer is not None:
            buffer.close()
        raise
