"""Multi-file command action helpers."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
import shlex
import sys
from typing import Protocol

from ...data.file_target_identity import (
    IndexIdentity,
    WorktreeIdentity,
    capture_worktree_identities,
    index_identity_from_entry,
)
from ...data.hunk_tracking import select_next_change_after_action
from ...data.index_entries import read_index_entries
from ...data.live_diff import paths_for_live_changes
from ...data.session import require_session_started
from ...data.undo_checkpoints import undo_checkpoint
from ...exceptions import CommandError
from ...i18n import _, ngettext
from ...utils.git_repository import require_git_repository
from ...utils.paths import ensure_state_directory_exists
from . import discard_file as _discard_file
from .discard_to_batch import discard_files_to_batch
from . import include_file as _include_file
from .target_path import checkpoint_paths_for_live_files
from ..selection.selected_change_display import show_selected_change
from . import skip_file as _skip_file


class ResolvedFileScope(Protocol):
    """Resolved file scope interface needed by command action dispatch."""

    @property
    def is_multiple(self) -> bool:
        """Return whether the scope contains more than one concrete file."""

    @property
    def files(self) -> Sequence[str]:
        """Return the concrete resolved files for a pattern scope."""

    def optional_file(self) -> str | None:
        """Return the optional single-file path for command callbacks."""


@dataclass(frozen=True, slots=True)
class _LiveActionTargetSnapshot:
    """Worktree and index identities behind one prepared live diff."""

    index_by_path: dict[str, IndexIdentity]
    worktree_by_path: dict[str, WorktreeIdentity]


def _format_multi_file_operation(command: str, files: Sequence[str]) -> str:
    """Return a readable undo operation for a resolved multi-file command."""
    return f"{command} --files {' '.join(shlex.quote(file) for file in files)}"


def _multi_file_undo_checkpoint(
    command: str,
    files: Sequence[str],
    *,
    worktree_paths: Sequence[str] | None = None,
) -> AbstractContextManager[None]:
    """Create one undo checkpoint for a resolved multi-file command."""
    paths = list(worktree_paths) if worktree_paths is not None else list(files)
    return undo_checkpoint(
        _format_multi_file_operation(command, files),
        worktree_paths=paths,
        rollback_on_error=True,
    )


def run_for_each_resolved_file(
    file_scope: ResolvedFileScope,
    callback: Callable[[str | None], None],
    *,
    line_ids: str | None = None,
    undo_operation: str | None = None,
    worktree_paths: Sequence[str] | None = None,
) -> None:
    """Run a command callback once per resolved file argument."""
    if file_scope.is_multiple and line_ids is not None:
        raise CommandError(_("Cannot use --lines with multiple files."))
    if file_scope.is_multiple:
        checkpoint = (
            _multi_file_undo_checkpoint(
                undo_operation,
                file_scope.files,
                worktree_paths=worktree_paths,
            )
            if undo_operation is not None else
            nullcontext()
        )
        with checkpoint:
            for file_path in file_scope.files:
                callback(file_path)
        return
    callback(file_scope.optional_file())


def discard_each_resolved_file(
    files: Sequence[str],
    *,
    auto_advance: bool | None = None,
) -> None:
    """Discard a multi-file live scope under one rename-complete checkpoint."""
    _prepare_live_multi_file_action()
    checkpoint_paths = checkpoint_paths_for_live_files(list(files))
    with _multi_file_undo_checkpoint(
        "discard",
        files,
        worktree_paths=checkpoint_paths,
    ):
        for file_path in files:
            _discard_file.discard_file_changes(
                file_path,
                auto_advance=auto_advance,
            )


def _format_file_summary(files: Sequence[str]) -> str:
    """Return a single path or plural file count for command output."""
    if len(files) == 1:
        return files[0]
    return ngettext(
        "{count} file",
        "{count} files",
        len(files),
    ).format(count=len(files))


def _prepare_live_multi_file_action() -> None:
    """Run command setup shared by live multi-file actions."""
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()


def _require_prepared_change_paths_covered(
    files: Sequence[str],
    changes_by_file: dict[str, tuple],
    checkpoint_paths: Sequence[str],
) -> None:
    """Reject prepared changes that reach beyond the undo before-image."""
    prepared_paths = paths_for_live_changes(
        change
        for file_path in files
        for change in changes_by_file[file_path]
    )
    missing_paths = sorted(set(prepared_paths) - set(checkpoint_paths))
    if missing_paths:
        raise CommandError(
            _(
                "The matched files changed while the undo checkpoint was being "
                "prepared. Review them again and retry. Uncaptured path(s): {paths}"
            ).format(paths=", ".join(missing_paths))
        )


def _capture_live_action_targets(
    paths: Sequence[str],
) -> _LiveActionTargetSnapshot:
    """Capture repository identities used to prepare a mutating live action."""
    unique_paths = tuple(dict.fromkeys(paths))
    index_entries = read_index_entries(unique_paths)
    return _LiveActionTargetSnapshot(
        index_by_path={
            path: index_identity_from_entry(index_entries.get(path))
            for path in unique_paths
        },
        worktree_by_path=capture_worktree_identities(unique_paths),
    )


def _require_live_action_targets_unchanged(
    *,
    operation: str,
    expected: _LiveActionTargetSnapshot,
    paths: Sequence[str] | None = None,
) -> None:
    """Reject a prepared mutation when its index or worktree input is stale."""
    target_paths = tuple(
        dict.fromkeys(paths if paths is not None else expected.index_by_path)
    )
    current_index_entries = read_index_entries(target_paths)
    current_worktree = capture_worktree_identities(target_paths)
    for path in target_paths:
        current_index = index_identity_from_entry(current_index_entries.get(path))
        if current_index != expected.index_by_path[path]:
            raise _live_action_target_changed_error(
                operation=operation,
                path=path,
                target="index",
            )
        if current_worktree[path] != expected.worktree_by_path[path]:
            raise _live_action_target_changed_error(
                operation=operation,
                path=path,
                target="worktree",
            )


def _live_action_target_changed_error(
    *,
    operation: str,
    path: str,
    target: str,
) -> CommandError:
    label = _("Index") if target == "index" else _("Working tree file")
    return CommandError(
        _(
            "{label} changed while multi-file {operation} was being prepared: "
            "{path}. Review the current changes and retry."
        ).format(
            label=label,
            operation=operation,
            path=path,
        )
    )


def include_each_resolved_file(
    files: Sequence[str],
    *,
    auto_advance: bool | None = None,
) -> None:
    """Stage a multi-file live scope and report one aggregate summary."""
    _prepare_live_multi_file_action()
    total_hunks = 0
    staged_files: list[str] = []

    checkpoint_paths = checkpoint_paths_for_live_files(list(files))
    with _multi_file_undo_checkpoint(
        "include",
        files,
        worktree_paths=checkpoint_paths,
    ):
        for file_path in files:
            staged_hunks = _include_file.include_file_changes(
                file_path,
                quiet=True,
                advance=False,
            )
            if staged_hunks > 0:
                total_hunks += staged_hunks
                staged_files.append(file_path)

    if total_hunks == 0:
        print(_("No hunks staged from matched files."), file=sys.stderr)
        return

    should_show_next = select_next_change_after_action(auto_advance=auto_advance)
    file_summary = _format_file_summary(staged_files)

    print(
        ngettext(
            "✓ Staged {count} hunk from {files}",
            "✓ Staged {count} hunks from {files}",
            total_hunks,
        ).format(count=total_hunks, files=file_summary),
        file=sys.stderr,
    )
    if should_show_next:
        show_selected_change()


def skip_each_resolved_file(
    files: Sequence[str],
    *,
    auto_advance: bool | None = None,
) -> None:
    """Skip a multi-file live scope and report one aggregate summary."""
    _prepare_live_multi_file_action()
    total_hunks = 0
    skipped_files: list[str] = []

    with _multi_file_undo_checkpoint("skip", files):
        for file_path in files:
            skipped_hunks = _skip_file.skip_file_changes(
                file_path,
                quiet=True,
                advance=False,
            )
            if skipped_hunks > 0:
                total_hunks += skipped_hunks
                skipped_files.append(file_path)

    if total_hunks == 0:
        print(_("No hunks skipped from matched files."), file=sys.stderr)
        return

    should_show_next = select_next_change_after_action(auto_advance=auto_advance)
    file_summary = _format_file_summary(skipped_files)

    print(
        ngettext(
            "✓ Skipped {count} hunk from {files}",
            "✓ Skipped {count} hunks from {files}",
            total_hunks,
        ).format(count=total_hunks, files=file_summary),
        file=sys.stderr,
    )
    if should_show_next:
        show_selected_change()


def discard_to_batch_each_resolved_file(
    batch_name: str,
    files: Sequence[str],
    *,
    auto_advance: bool | None = None,
) -> None:
    """Save a multi-file live scope to a batch and report one aggregate summary."""
    operation = f"discard --to {shlex.quote(batch_name)}"
    checkpoint_paths = checkpoint_paths_for_live_files(list(files))
    with _multi_file_undo_checkpoint(
        operation,
        files,
        worktree_paths=checkpoint_paths,
    ):
        result = discard_files_to_batch(
            batch_name,
            list(files),
            quiet=True,
            advance=False,
            auto_advance=auto_advance,
        )

    total_hunks = result.discarded_hunks
    discarded_files = result.discarded_files
    if total_hunks == 0:
        print(_("No hunks saved to batch from matched files."), file=sys.stderr)
        return

    should_show_next = select_next_change_after_action(auto_advance=auto_advance)
    file_summary = _format_file_summary(discarded_files)

    print(
        ngettext(
            "✓ Saved {count} hunk from {files} to batch '{batch}' and discarded it",
            "✓ Saved {count} hunks from {files} to batch '{batch}' and discarded them",
            total_hunks,
        ).format(count=total_hunks, files=file_summary, batch=batch_name),
        file=sys.stderr,
    )
    if should_show_next:
        show_selected_change()
