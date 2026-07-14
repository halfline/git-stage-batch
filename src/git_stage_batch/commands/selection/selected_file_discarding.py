"""Selected file-scope discard support."""

from __future__ import annotations

import sys

from ...data.selected_change.paths import get_selected_change_file_path
from ...data.selected_change.loading import require_selected_hunk
from ...data.index_entries import read_index_entry
from ...data.session import path_is_intent_to_add, snapshot_file_if_untracked
from ...data.undo_checkpoints import undo_checkpoint
from ...exceptions import exit_with_error
from ...i18n import _
from ...utils.git_worktree import git_checkout_index_paths
from ...utils.git_index import git_update_index
from ...utils.git_repository import get_git_repository_root_path
from .action_completion import finish_selected_change_action


def discard_selected_file(
    *,
    quiet: bool = False,
    auto_advance: bool | None = None,
) -> None:
    """Discard all changes from the currently selected file-scoped view."""
    target_file = get_selected_change_file_path()
    if target_file is None:
        if not quiet:
            print(_("No selected hunk. Run 'show' first or specify file path."), file=sys.stderr)
        return
    require_selected_hunk()
    with undo_checkpoint("discard", worktree_paths=[target_file]):
        snapshot_file_if_untracked(target_file)

        index_entry = read_index_entry(target_file)
        is_intent_to_add = path_is_intent_to_add(target_file)
        if index_entry is None or is_intent_to_add:
            absolute_path = get_git_repository_root_path() / target_file
            if absolute_path.exists() or absolute_path.is_symlink():
                absolute_path.unlink()
            if is_intent_to_add:
                remove_result = git_update_index(
                    file_path=target_file,
                    force_remove=True,
                    check=False,
                )
                if remove_result.returncode != 0:
                    exit_with_error(
                        _("Failed to discard file: {}").format(remove_result.stderr)
                    )
        else:
            result = git_checkout_index_paths([target_file], check=False)
            if result.returncode != 0:
                exit_with_error(
                    _("Failed to discard file: {}").format(result.stderr)
                )

        if quiet:
            finish_selected_change_action(quiet=True, auto_advance=auto_advance)
        else:
            print(_("✓ File discarded: {}").format(target_file), file=sys.stderr)
            finish_selected_change_action(quiet=False, auto_advance=auto_advance)
