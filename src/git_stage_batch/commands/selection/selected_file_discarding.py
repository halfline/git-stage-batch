"""Selected file-scope discard support."""

from __future__ import annotations

import sys

from ...data.selected_change.paths import get_selected_change_file_path
from ...data.session import snapshot_file_if_untracked
from ...data.undo_checkpoints import undo_checkpoint
from ...i18n import _
from ...utils.git_command import run_git_command
from ...utils.git_worktree import git_checkout_paths
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

    with undo_checkpoint("discard", worktree_paths=[target_file]):
        snapshot_file_if_untracked(target_file)

        head_result = run_git_command(
            ["show", f"HEAD:{target_file}"],
            check=False,
            text_output=False,
            requires_index_lock=False,
        )
        if head_result.returncode == 0:
            result = git_checkout_paths("HEAD", [target_file], check=False)
            if result.returncode != 0:
                if not quiet:
                    print(_("Failed to discard file: {}").format(result.stderr), file=sys.stderr)
                return
        else:
            absolute_path = get_git_repository_root_path() / target_file
            if absolute_path.exists():
                absolute_path.unlink()

        if quiet:
            finish_selected_change_action(quiet=True, auto_advance=auto_advance)
        else:
            print(_("✓ File discarded: {}").format(target_file), file=sys.stderr)
            finish_selected_change_action(quiet=False, auto_advance=auto_advance)
