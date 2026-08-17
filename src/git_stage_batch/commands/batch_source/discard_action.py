"""Discard-from execution for batch-source action commands."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from pathlib import Path
import sys

from . import action_plans as _action_plans
from . import action_selection as _action_selection
from . import atomic_unit_refusals as _atomic_unit_refusals
from . import binary_file_actions as _binary_file_actions
from . import file_mode_actions as _file_mode_actions
from . import text_file_actions as _text_file_actions
from . import text_plan_builders as _text_plan_builders
from ...batch.state.validation import get_validated_baseline_commit
from ...batch.state.query import read_batch_metadata_for_batches
from ...batch.submodule_pointer import (
    discard_submodule_pointer_from_batch,
    is_batch_submodule_pointer,
    validate_discard_submodule_pointer,
)
from ...data.session import snapshot_file_if_untracked
from ...data.session_marker import session_is_active
from ...core.line_selection import LineRanges
from ...batch.state.metadata_types import (
    BatchFileMetadataDict,
    BatchMetadataDict,
)
from ...data.applied_batch_overlays import (
    AppliedBatchOverlaySnapshot,
    fresh_applied_batch_overlay_for_path,
    load_applied_batch_overlay_snapshot,
)
from ...data.file_modes import detect_file_mode_in_commit
from ...data.file_target_identity import (
    IndexIdentity,
    WorktreeIdentity,
    capture_worktree_identities,
    capture_worktree_identity,
    read_index_identities,
)
from ...data.undo.checkpoints import transaction_checkpoint
from ...exceptions import (
    AtomicUnitError,
    BatchMetadataError,
    CommandError,
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


def _print_binary_discard_results(
    results: tuple[
        tuple[str, _binary_file_actions.BinaryWorktreeAction | None],
        ...,
    ],
) -> None:
    """Print binary discard results after the outer transaction commits."""
    for file_path, action in results:
        _print_binary_discard_result(file_path, action)


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
    operation = terminal_safe_shell_join(selection.operation_parts)
    applied_overlay_snapshot = load_applied_batch_overlay_snapshot()
    overlay_batch_metadata = _load_overlay_batch_metadata(
        files,
        applied_overlay_snapshot,
    )

    workspace = FileJobWorkspace()
    with _action_plans.resource_cleanup((workspace,)) as close_workspace:
        capture = _build_discard_action_plans(
            batch_name=batch_name,
            baseline_commit=baseline_commit,
            selection=selection,
            applied_overlay_snapshot=applied_overlay_snapshot,
            overlay_batch_metadata=overlay_batch_metadata,
            workspace=workspace,
        )
        with _action_plans.resource_cleanup(capture.plans) as close_discard_plans:
            if not capture.plans:
                return
            _require_unchanged_discard_targets(
                capture.index_identities,
                capture.worktree_identities,
            )
            with transaction_checkpoint(
                operation,
                worktree_paths=list(
                    dict.fromkeys(plan.file_path for plan in capture.plans)
                ),
                index_paths=list(capture.index_mutation_paths),
            ) as checkpoint_status:
                _require_unchanged_discard_targets(
                    capture.index_identities,
                    capture.worktree_identities,
                )
                checkpoint_status.arm_rollback()
                binary_worktree_results: list[
                    tuple[
                        str,
                        _binary_file_actions.BinaryWorktreeAction | None,
                    ]
                ] = []
                for plan in capture.plans:
                    action = _publish_discard_action_plan(plan)
                    if isinstance(
                        plan,
                        _action_plans.BinaryFileActionPlan,
                    ):
                        binary_worktree_results.append((plan.file_path, action))
                close_discard_plans()
                close_workspace()
            if binary_worktree_results:
                checkpoint_status.defer_success(
                    partial(
                        _print_binary_discard_results,
                        tuple(binary_worktree_results),
                    )
                )


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


def _build_discard_action_plans(
    *,
    batch_name: str,
    baseline_commit: str,
    selection: _action_selection.BatchSourceActionSelection,
    applied_overlay_snapshot: AppliedBatchOverlaySnapshot,
    overlay_batch_metadata: dict[str, BatchMetadataDict],
    workspace: FileJobWorkspace,
) -> _DiscardPlanCapture:
    """Plan every selected discard before opening its transaction."""
    files = selection.files
    (
        text_inputs,
        worktree_identities,
        index_identities,
        capture_errors,
    ) = _capture_discard_targets(
        files,
        applied_overlay_snapshot,
        workspace,
    )
    text_inputs_by_path = {
        text_input.file_path: text_input for text_input in text_inputs
    }
    plans: list[_action_plans.BatchSourceActionPlan] = []
    failed_files: list[str] = []
    for file_path, error in capture_errors:
        print(
            _("Error discarding {file}: {error}").format(
                file=display_path(file_path),
                error=error,
            ),
            file=sys.stderr,
        )
        failed_files.append(file_path)
    index_validation_paths: list[str] = []
    index_mutation_paths: list[str] = []
    try:
        for ordinal, (file_path, file_meta) in enumerate(files.items()):
            if file_path not in worktree_identities:
                continue
            try:
                if _file_mode_actions.is_file_mode_action(file_meta):
                    plans.append(
                        _DiscardModeActionPlan(
                            file_path,
                            _file_mode_actions.old_file_mode(file_meta),
                        )
                    )
                    continue
                if file_meta.get("file_type") == "binary":
                    plans.append(
                        _build_discard_binary_action_plan(
                            ordinal=ordinal,
                            file_path=file_path,
                            baseline_commit=baseline_commit,
                            workspace=workspace,
                        )
                    )
                    continue
                if is_batch_submodule_pointer(file_meta):
                    _validate_discard_submodule_pointer_target(
                        file_path,
                        file_meta,
                        index_identities[file_path],
                    )
                    plans.append(
                        _action_plans.SubmodulePointerActionPlan(
                            file_path,
                            file_meta,
                        )
                    )
                    if file_meta.get("change_type") == "added":
                        index_validation_paths.append(file_path)
                        index_mutation_paths.append(file_path)
                    continue

                text_input = text_inputs_by_path[file_path]
                applied_overlay = fresh_applied_batch_overlay_for_path(
                    file_path,
                    batch_metadata_by_name=overlay_batch_metadata,
                    snapshot=applied_overlay_snapshot,
                    worktree_identity=text_input.identity,
                )
                trusted_presence_lines = LineRanges.from_ranges(
                    applied_overlay.source_line_ranges_by_batch.get(
                        batch_name,
                        (),
                    )
                )
                applied_presence_lines = LineRanges.from_ranges(
                    applied_overlay.applied_source_line_ranges_by_batch.get(
                        batch_name,
                        (),
                    )
                )
                index_preimage_presence_lines = LineRanges.from_ranges(
                    applied_overlay.index_preimage_source_line_ranges_by_batch.get(
                        batch_name,
                        (),
                    )
                )
                uses_index_preimage = bool(
                    applied_presence_lines or index_preimage_presence_lines
                )
                if uses_index_preimage:
                    index_validation_paths.append(file_path)
                text_plan_result = (
                    _text_plan_builders.build_discard_text_file_action_plan(
                        file_path=file_path,
                        file_meta=file_meta,
                        baseline_commit=baseline_commit,
                        selected_ids=selection.selected_ids,
                        selection_ids_to_discard=selection.selection_ids,
                        trusted_presence_lines=trusted_presence_lines,
                        applied_presence_lines=applied_presence_lines,
                        index_preimage_presence_lines=(index_preimage_presence_lines),
                        captured_index_identity=index_identities[file_path],
                        working_tree_artifact_path=(text_input.worktree_artifact),
                        captured_working_tree_exists=(text_input.identity.exists),
                        spool_dir=text_input.scratch_directory,
                    )
                )
                if text_plan_result.missing_source:
                    failed_files.append(file_path)
                elif text_plan_result.plan is not None:
                    plans.append(text_plan_result.plan)
            except AtomicUnitError as error:
                if selection.rendered:
                    _atomic_unit_refusals.translate_atomic_unit_error_to_gutter_ids(
                        error,
                        selection.rendered,
                        pgettext("batch failure operation", "discard from"),
                        batch_name,
                    )
                exit_with_error(
                    _("Failed to discard from batch '{name}': {error}").format(
                        name=batch_name,
                        error=str(error),
                    )
                )
            except CommandError:
                raise
            except Exception as error:
                print(
                    _("Error discarding {file}: {error}").format(
                        file=display_path(file_path),
                        error=str(error),
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
        return _DiscardPlanCapture(
            plans=plans,
            worktree_identities=worktree_identities,
            index_identities={
                path: index_identities[path]
                for path in dict.fromkeys(index_validation_paths)
            },
            index_mutation_paths=tuple(dict.fromkeys(index_mutation_paths)),
        )
    except BaseException:
        _action_plans.close_action_plans(plans)
        raise


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


def _publish_discard_action_plan(
    plan: _action_plans.BatchSourceActionPlan | _DiscardModeActionPlan,
) -> _binary_file_actions.BinaryWorktreeAction | None:
    """Publish one already planned discard action."""
    try:
        if session_is_active():
            snapshot_file_if_untracked(plan.file_path)
        if isinstance(plan, _DiscardModeActionPlan):
            _file_mode_actions.apply_file_mode(plan.file_path, plan.file_mode)
        elif isinstance(plan, _action_plans.DiscardTextFileActionPlan):
            _text_file_actions.write_discarded_text_file_to_worktree(
                plan.file_path,
                plan.buffer,
                plan.file_mode,
                plan.change_type,
            )
        elif isinstance(plan, _action_plans.BinaryFileActionPlan):
            return _binary_file_actions.write_binary_file_to_worktree(
                plan.file_path,
                plan.file_meta,
                plan.buffer,
            )
        elif isinstance(plan, _action_plans.SubmodulePointerActionPlan):
            discard_submodule_pointer_from_batch(
                plan.file_path,
                plan.file_meta,
            )
        else:
            raise TypeError("unsupported discard action plan")
        return None
    except CommandError:
        raise
    except Exception as error:
        raise CommandError(
            _("Error discarding {file}: {error}").format(
                file=display_path(plan.file_path),
                error=str(error),
            )
        ) from error


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
