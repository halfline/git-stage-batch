"""Selected-change skip support for skip commands."""

from __future__ import annotations

import sys

from ...core.models import (
    BinaryFileChange,
    GitlinkChange,
    RenameChange,
    TextFileDeletionChange,
)
from ...data.hunk_tracking import fetch_next_change
from ...data.progress import (
    record_binary_hunk_skipped,
    record_gitlink_hunk_skipped,
    record_hunk_skipped,
    record_rename_hunk_skipped,
    record_text_deletion_hunk_skipped,
)
from ...data.selected_change.loading import load_selected_change
from ...data.selected_change.paths import worktree_paths_for_selected_change
from ...data.undo_checkpoints import undo_checkpoint
from ...exceptions import NoMoreHunks
from ...i18n import _
from ...utils.file_io import append_lines_to_file, read_text_file_contents
from ...utils.paths import (
    get_block_list_file_path,
    get_selected_hunk_hash_file_path,
)
from .action_completion import finish_selected_change_action


def skip_selected_change(
    *,
    quiet: bool = False,
    auto_advance: bool | None = None,
) -> None:
    """Skip the currently selected change without staging it."""
    item = load_selected_change()
    if item is None:
        try:
            item = fetch_next_change()
        except NoMoreHunks:
            if not quiet:
                print(_("No more hunks to process."), file=sys.stderr)
            return

    with undo_checkpoint(
        "skip",
        worktree_paths=worktree_paths_for_selected_change(item),
    ):
        _skip_loaded_selected_change(
            item,
            quiet=quiet,
            auto_advance=auto_advance,
        )


def _skip_loaded_selected_change(
    item,
    *,
    quiet: bool,
    auto_advance: bool | None,
) -> None:
    """Skip one loaded selected change."""
    patch_hash = read_text_file_contents(get_selected_hunk_hash_file_path()).strip()
    blocklist_path = get_block_list_file_path()

    if isinstance(item, RenameChange):
        append_lines_to_file(blocklist_path, [patch_hash])
        record_rename_hunk_skipped(item, patch_hash)

        if not quiet:
            print(
                _("✓ Rename skipped: {old} -> {new}").format(
                    old=item.old_path,
                    new=item.new_path,
                ),
                file=sys.stderr,
            )

        finish_selected_change_action(
            quiet=quiet,
            auto_advance=auto_advance,
        )
        return

    if isinstance(item, TextFileDeletionChange):
        append_lines_to_file(blocklist_path, [patch_hash])
        record_text_deletion_hunk_skipped(item, patch_hash)

        if not quiet:
            print(
                _("✓ Text file deletion skipped: {file}").format(file=item.path()),
                file=sys.stderr,
            )

        finish_selected_change_action(
            quiet=quiet,
            auto_advance=auto_advance,
        )
        return

    if isinstance(item, GitlinkChange):
        append_lines_to_file(blocklist_path, [patch_hash])
        record_gitlink_hunk_skipped(item, patch_hash)

        if not quiet:
            print(
                _("✓ Submodule pointer {desc} skipped: {file}").format(
                    desc=item.change_type,
                    file=item.path(),
                ),
                file=sys.stderr,
            )

        finish_selected_change_action(
            quiet=quiet,
            auto_advance=auto_advance,
        )
        return

    if isinstance(item, BinaryFileChange):
        file_path = item.path()

        append_lines_to_file(blocklist_path, [patch_hash])
        record_binary_hunk_skipped(item, patch_hash)

        if not quiet:
            if item.is_new_file():
                change_desc = "added"
            elif item.is_deleted_file():
                change_desc = "deleted"
            else:
                change_desc = "modified"
            print(
                _("✓ Binary file {desc} skipped: {file}").format(
                    desc=change_desc,
                    file=file_path,
                ),
                file=sys.stderr,
            )

        finish_selected_change_action(
            quiet=quiet,
            auto_advance=auto_advance,
        )
        return

    file_path = item.path

    append_lines_to_file(blocklist_path, [patch_hash])
    record_hunk_skipped(item, patch_hash)

    if not quiet:
        print(_("✓ Hunk skipped from {file}").format(file=file_path), file=sys.stderr)

    finish_selected_change_action(
        quiet=quiet,
        auto_advance=auto_advance,
    )
