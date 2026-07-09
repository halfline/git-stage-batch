"""File-scoped include support."""

from __future__ import annotations

import sys

from ...core.buffer import LineBuffer
from ...core.diff_parser import acquire_unified_diff, patch_is_file_deletion
from ...core.hashing import (
    compute_binary_file_hash,
    compute_gitlink_change_hash,
    compute_rename_change_hash,
    compute_stable_hunk_hash_from_lines,
    compute_text_file_deletion_hash,
)
from ...core.models import BinaryFileChange, GitlinkChange, RenameChange, TextFileDeletionChange
from ...data.file_tracking import auto_add_untracked_files
from ...data.live_diff import stream_live_git_diff
from ...data.progress import record_hunk_included
from ...data.selected_change.paths import get_selected_change_file_path
from ...data.undo import undo_checkpoint
from ...i18n import _, ngettext
from ...staging.operations import update_index_with_blob_buffer
from ...utils.git import run_git_command
from ...utils.git_index import git_add_paths, git_apply_to_index
from ..selection import selected_change_staging as _selected_change_staging
from ..selection.action_completion import finish_selected_change_action


def include_file_changes(
    file: str,
    *,
    quiet: bool = False,
    advance: bool = True,
    auto_advance: bool | None = None,
) -> int:
    """Include all changes from one resolved file scope."""
    if file == "":
        target_file = get_selected_change_file_path()
        if target_file is None:
            diff_result = run_git_command(
                [
                    "-c",
                    "diff.ignoreSubmodules=none",
                    "diff",
                    "--ignore-submodules=none",
                    "--quiet",
                ],
                check=False,
                requires_index_lock=False,
            )
            if diff_result.returncode == 0:
                print(_("No changes to stage."), file=sys.stderr)
            else:
                print(
                    _("No selected hunk. Run 'show' first or specify file path."),
                    file=sys.stderr,
                )
            return 0
    else:
        target_file = file

    auto_add_untracked_files([target_file])
    with undo_checkpoint(f"include --file {file}".rstrip()):
        hunks_staged = 0
        submodule_pointers_staged = 0
        renames_staged = 0
        staged_rename_pairs: set[tuple[str, str]] = set()
        with acquire_unified_diff(
            stream_live_git_diff(
                full_index=True,
                ignore_submodules="none",
                submodule_format="short",
            )
        ) as patches:
            for patch in patches:
                if isinstance(patch, RenameChange):
                    if target_file not in (patch.old_path, patch.new_path):
                        continue

                    _selected_change_staging.stage_rename_change(patch)
                    result = git_add_paths([patch.new_path], check=False)
                    if result.returncode != 0:
                        print(
                            _("Failed to stage renamed file: {error}").format(
                                error=result.stderr,
                            ),
                            file=sys.stderr,
                        )
                        break
                    record_hunk_included(compute_rename_change_hash(patch))
                    hunks_staged += 1
                    renames_staged += 1
                    staged_rename_pairs.add((patch.old_path, patch.new_path))
                    continue

                if isinstance(patch, TextFileDeletionChange):
                    if patch.path() != target_file:
                        continue

                    _selected_change_staging.stage_text_deletion_change(patch)
                    record_hunk_included(compute_text_file_deletion_hash(patch))
                    hunks_staged += 1
                    continue

                if isinstance(patch, GitlinkChange):
                    if patch.path() == target_file:
                        result = _selected_change_staging.stage_gitlink_change(patch)
                        if result.returncode != 0:
                            print(
                                _("Failed to stage submodule pointer: {error}").format(
                                    error=result.stderr,
                                ),
                                file=sys.stderr,
                            )
                            break
                        record_hunk_included(compute_gitlink_change_hash(patch))
                        hunks_staged += 1
                        submodule_pointers_staged += 1
                    continue

                if isinstance(patch, BinaryFileChange):
                    file_path = patch.new_path if patch.new_path != "/dev/null" else patch.old_path
                    if file_path != target_file:
                        continue

                    result = git_add_paths([file_path], check=False)
                    if result.returncode != 0:
                        print(
                            _("Failed to stage binary file: {error}").format(
                                error=result.stderr,
                            ),
                            file=sys.stderr,
                        )
                        break

                    record_hunk_included(compute_binary_file_hash(patch))
                    hunks_staged += 1
                    continue

                patch_paths = {
                    path
                    for path in (patch.old_path, patch.new_path)
                    if path != "/dev/null"
                }
                if target_file not in patch_paths:
                    continue

                if (patch.old_path, patch.new_path) in staged_rename_pairs:
                    continue

                patch_hash = compute_stable_hunk_hash_from_lines(patch.lines)

                if patch.old_path != patch.new_path:
                    result = git_add_paths(sorted(patch_paths), check=False)
                    if result.returncode != 0:
                        print(
                            _("Failed to stage file: {error}").format(
                                error=result.stderr,
                            ),
                            file=sys.stderr,
                        )
                        break

                    record_hunk_included(patch_hash)
                    hunks_staged += 1
                    continue

                if patch_is_file_deletion(patch.lines):
                    with LineBuffer.from_bytes(b"") as empty_buffer:
                        update_index_with_blob_buffer(target_file, empty_buffer)
                    apply_result = None
                else:
                    apply_result = git_apply_to_index(patch.lines, check=False)
                if apply_result is None or apply_result.returncode == 0:
                    record_hunk_included(patch_hash)
                    hunks_staged += 1
                else:
                    print(
                        _("Failed to apply hunk: {error}").format(
                            error=apply_result.stderr,
                        ),
                        file=sys.stderr,
                    )
                    break

    if hunks_staged == 0:
        if not quiet:
            print(_("No hunks staged from {file}").format(file=target_file), file=sys.stderr)
        return 0

    if quiet and advance:
        finish_selected_change_action(quiet=True, auto_advance=auto_advance)
    if quiet:
        return hunks_staged

    if renames_staged == hunks_staged:
        msg = ngettext(
            "✓ Staged {count} rename from {file}",
            "✓ Staged {count} renames from {file}",
            hunks_staged,
        ).format(count=hunks_staged, file=target_file)
    elif submodule_pointers_staged == hunks_staged:
        msg = ngettext(
            "✓ Staged {count} submodule pointer from {file}",
            "✓ Staged {count} submodule pointers from {file}",
            hunks_staged,
        ).format(count=hunks_staged, file=target_file)
    else:
        msg = ngettext(
            "✓ Staged {count} hunk from {file}",
            "✓ Staged {count} hunks from {file}",
            hunks_staged,
        ).format(count=hunks_staged, file=target_file)
    print(msg, file=sys.stderr)

    if advance:
        finish_selected_change_action(quiet=False, auto_advance=auto_advance)
    return hunks_staged
