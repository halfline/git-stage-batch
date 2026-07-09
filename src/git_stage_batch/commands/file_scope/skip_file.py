"""File-scoped skip support."""

from __future__ import annotations

import sys

from ...core.diff_parser import acquire_unified_diff, build_line_changes_from_patch_lines
from ...core.hashing import (
    compute_binary_file_hash,
    compute_gitlink_change_hash,
    compute_rename_change_hash,
    compute_stable_hunk_hash_from_lines,
    compute_text_file_deletion_hash,
)
from ...core.models import (
    BinaryFileChange,
    GitlinkChange,
    RenameChange,
    TextFileDeletionChange,
)
from ...data.file_tracking import auto_add_untracked_files
from ...data.live_diff import stream_live_git_diff
from ...data.progress import (
    record_binary_hunk_skipped,
    record_gitlink_hunk_skipped,
    record_hunk_skipped,
    record_rename_hunk_skipped,
    record_text_deletion_hunk_skipped,
)
from ...data.selected_change.paths import get_selected_change_file_path
from ...data.undo import undo_checkpoint
from ...i18n import _, ngettext
from ...utils.file_io import append_lines_to_file, read_text_file_line_set
from ...utils.paths import get_block_list_file_path, get_context_lines
from ..selection.action_completion import finish_selected_change_action


def skip_file_changes(
    file: str = "",
    *,
    quiet: bool = False,
    advance: bool = True,
    auto_advance: bool | None = None,
) -> int:
    """Skip all changes from one resolved file scope."""
    if file == "":
        target_file = get_selected_change_file_path()
        if target_file is None:
            if not quiet:
                print(
                    _("No selected hunk. Run 'show' first or specify file path."),
                    file=sys.stderr,
                )
            return 0
    else:
        target_file = file

    auto_add_untracked_files([target_file])
    with undo_checkpoint(f"skip --file {file}".rstrip()):
        blocklist_path = get_block_list_file_path()
        blocked_hashes = read_text_file_line_set(blocklist_path)

        hunks_skipped = 0
        with acquire_unified_diff(
            stream_live_git_diff(
                context_lines=get_context_lines(),
                full_index=True,
                ignore_submodules="none",
                submodule_format="short",
            )
        ) as patches:
            for patch in patches:
                if isinstance(patch, RenameChange):
                    if target_file not in (patch.old_path, patch.new_path):
                        continue

                    patch_hash = compute_rename_change_hash(patch)
                    if patch_hash in blocked_hashes:
                        continue

                    append_lines_to_file(blocklist_path, [patch_hash])
                    blocked_hashes.add(patch_hash)
                    record_rename_hunk_skipped(patch, patch_hash)
                    hunks_skipped += 1
                    continue

                if isinstance(patch, TextFileDeletionChange):
                    if patch.path() != target_file:
                        continue

                    patch_hash = compute_text_file_deletion_hash(patch)
                    if patch_hash in blocked_hashes:
                        continue

                    append_lines_to_file(blocklist_path, [patch_hash])
                    blocked_hashes.add(patch_hash)
                    record_text_deletion_hunk_skipped(patch, patch_hash)
                    hunks_skipped += 1
                    continue

                if isinstance(patch, GitlinkChange):
                    if patch.path() != target_file:
                        continue

                    patch_hash = compute_gitlink_change_hash(patch)
                    if patch_hash in blocked_hashes:
                        continue

                    append_lines_to_file(blocklist_path, [patch_hash])
                    blocked_hashes.add(patch_hash)
                    record_gitlink_hunk_skipped(patch, patch_hash)
                    hunks_skipped += 1
                    continue

                if isinstance(patch, BinaryFileChange):
                    file_path = (
                        patch.new_path
                        if patch.new_path != "/dev/null"
                        else patch.old_path
                    )
                    if file_path != target_file:
                        continue

                    patch_hash = compute_binary_file_hash(patch)
                    if patch_hash in blocked_hashes:
                        continue

                    append_lines_to_file(blocklist_path, [patch_hash])
                    blocked_hashes.add(patch_hash)
                    record_binary_hunk_skipped(patch, patch_hash)
                    hunks_skipped += 1
                    continue

                patch_paths = {
                    path
                    for path in (patch.old_path, patch.new_path)
                    if path != "/dev/null"
                }
                if target_file not in patch_paths:
                    continue

                patch_hash = compute_stable_hunk_hash_from_lines(patch.lines)
                if patch_hash in blocked_hashes:
                    continue

                append_lines_to_file(blocklist_path, [patch_hash])
                blocked_hashes.add(patch_hash)
                record_hunk_skipped(
                    build_line_changes_from_patch_lines(patch.lines),
                    patch_hash,
                )
                hunks_skipped += 1

        if quiet and advance:
            finish_selected_change_action(quiet=True, auto_advance=auto_advance)
        if quiet:
            return hunks_skipped

        msg = ngettext(
            "✓ Skipped {count} hunk from {file}",
            "✓ Skipped {count} hunks from {file}",
            hunks_skipped,
        ).format(count=hunks_skipped, file=target_file)
        print(msg, file=sys.stderr)

        if advance:
            finish_selected_change_action(quiet=False, auto_advance=auto_advance)
        return hunks_skipped
