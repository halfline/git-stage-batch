"""Single-file discard-to-batch support."""

from __future__ import annotations

from contextlib import ExitStack
import sys

from ...batch.source_annotation import annotate_with_batch_source
from ...batch.lifecycle import create_batch
from ...batch.ownership import BatchOwnership
from ...batch.ownership_update import acquire_batch_ownership_update_for_selection
from ...batch.query import read_batch_metadata
from ...batch.text_file_storage import add_file_to_batch
from ...batch.validation import batch_exists
from ...core.buffer import LineBuffer
from ...core.diff_parser import acquire_unified_diff, build_line_changes_from_patch_lines
from ...core.hashing import compute_stable_hunk_hash_from_lines
from ...core.models import BinaryFileChange, GitlinkChange, RenameChange, TextFileDeletionChange
from ...core.text_lifecycle import TextFileChangeType
from ...data.file_change_display import (
    render_binary_file_change,
    render_gitlink_change,
    render_text_deletion_change,
)
from ...data.file_modes import detect_file_mode
from ...data.file_tracking import auto_add_untracked_files
from ...data.live_diff import stream_live_git_diff
from ...data.progress import record_hunk_discarded
from ...data.session import snapshot_file_if_untracked
from ...data.text_lifecycle_detection import detect_empty_text_lifecycle_change
from ...exceptions import exit_with_error
from ...i18n import _
from ...utils.file_io import read_text_file_line_set
from ...utils.git_worktree import (
    git_apply_to_worktree,
    git_checkout_paths,
    git_remove_paths,
)
from ...utils.git_repository import get_git_repository_root_path
from ...utils.journal import log_journal
from ...utils.paths import get_block_list_file_path, get_context_lines
from ..selection.action_completion import finish_selected_change_action
from ..selection import whole_file_batch_discarding as _whole_file_batch_discarding


def discard_file_to_batch(
    batch_name: str,
    file_path: str,
    *,
    quiet: bool = False,
    advance: bool = True,
    auto_advance: bool | None = None,
) -> int:
    """Discard one file to a batch."""
    auto_add_untracked_files([file_path])

    log_journal("discard_file_to_batch_start", batch_name=batch_name, file_path=file_path, quiet=quiet)

    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    deletion_change = render_text_deletion_change(file_path)
    if deletion_change is not None:
        return _whole_file_batch_discarding.discard_text_deletion_to_batch(
            batch_name,
            deletion_change,
            quiet=quiet,
            advance=advance,
            auto_advance=auto_advance,
        )

    binary_change = render_binary_file_change(file_path)
    if binary_change is not None:
        return _whole_file_batch_discarding.discard_binary_to_batch(
            batch_name,
            binary_change,
            quiet=quiet,
            advance=advance,
            auto_advance=auto_advance,
        )
    if render_gitlink_change(file_path) is not None:
        exit_with_error(_("Discarding submodule pointer changes to a batch is not supported yet."))

    blocklist_path = get_block_list_file_path()
    blocked_hashes = read_text_file_line_set(blocklist_path)
    file_mode = detect_file_mode(file_path)

    with ExitStack() as patch_stack:
        all_lines_to_batch = []
        patches_to_discard = []

        with acquire_unified_diff(
            stream_live_git_diff(
                base="HEAD",
                context_lines=get_context_lines(),
                paths=[file_path],
            )
        ) as patches:
            for patch in patches:
                if isinstance(patch, RenameChange):
                    continue

                if isinstance(patch, TextFileDeletionChange):
                    continue

                if isinstance(patch, GitlinkChange):
                    exit_with_error(_("Discarding submodule pointer changes to a batch is not supported yet."))
                if isinstance(patch, BinaryFileChange):
                    continue

                patch_hash = compute_stable_hunk_hash_from_lines(patch.lines)

                if patch_hash in blocked_hashes:
                    continue

                hunk_lines = build_line_changes_from_patch_lines(
                    patch.lines,
                    annotator=annotate_with_batch_source,
                )
                all_lines_to_batch.extend(hunk_lines.lines)
                patches_to_discard.append((
                    patch_stack.enter_context(LineBuffer.from_chunks(patch.lines)),
                    patch_hash,
                ))

        if not all_lines_to_batch:
            repo_root = get_git_repository_root_path()
            full_path = repo_root / file_path
            lifecycle_change_type = detect_empty_text_lifecycle_change(file_path)
            if lifecycle_change_type is not None:
                snapshot_file_if_untracked(file_path)
                add_file_to_batch(
                    batch_name,
                    file_path,
                    BatchOwnership([], []),
                    file_mode,
                    change_type=lifecycle_change_type,
                )

                if lifecycle_change_type == TextFileChangeType.ADDED:
                    full_path.unlink()
                    git_remove_paths([file_path], cached=True, quiet=True, check=False)
                else:
                    git_checkout_paths("HEAD", [file_path], check=False)

                if not quiet:
                    print(
                        _("Discarded file '{file}' to batch '{batch}'").format(
                            file=file_path,
                            batch=batch_name,
                        ),
                        file=sys.stderr,
                    )

                log_journal("discard_file_to_batch_end", batch_name=batch_name, file_path=file_path)
                return 1

            if not quiet:
                print(_("No changes in file '{file}' to discard.").format(file=file_path), file=sys.stderr)
            return 0

        metadata = read_batch_metadata(batch_name)
        file_metadata = metadata.get("files", {}).get(file_path)

        with ExitStack() as ownership_stack:
            try:
                update = ownership_stack.enter_context(
                    acquire_batch_ownership_update_for_selection(
                        batch_name=batch_name,
                        file_path=file_path,
                        file_metadata=file_metadata,
                        selected_lines=all_lines_to_batch,
                    )
                )
            except ValueError as e:
                exit_with_error(
                    _("Cannot discard file to batch: batch source is stale and remapping failed.\n"
                      "File: {file}\n"
                      "Batch: {batch}\n"
                      "Error: {error}").format(file=file_path, batch=batch_name, error=str(e))
                )

            snapshot_file_if_untracked(file_path)

            add_file_to_batch(
                batch_name,
                file_path,
                update.ownership_after,
                file_mode,
                batch_source_commit=update.batch_source_commit,
            )

        for _patch_lines, patch_hash in patches_to_discard:
            record_hunk_discarded(patch_hash)

        for patch_lines_item, patch_hash in patches_to_discard:
            log_journal(
                "discard_file_to_batch_before_git_apply",
                batch_name=batch_name,
                patch_hash=patch_hash,
                file_path=file_path,
                patch_line_count=len(patch_lines_item),
            )

            apply_result = git_apply_to_worktree(
                patch_lines_item,
                reverse=True,
                unidiff_zero=True,
                check=False,
            )

            if apply_result.returncode != 0:
                exit_with_error(_("Failed to discard changes from file: {err}").format(err=apply_result.stderr))

            log_journal(
                "discard_file_to_batch_after_git_apply",
                batch_name=batch_name,
                patch_hash=patch_hash,
                exit_code=apply_result.returncode,
            )

        repo_root = get_git_repository_root_path()
        full_path = repo_root / file_path
        if not full_path.exists():
            git_remove_paths([file_path], cached=True, quiet=True, check=False)

        if not quiet:
            print(
                _("Discarded file '{file}' to batch '{batch}'").format(
                    file=file_path,
                    batch=batch_name,
                ),
                file=sys.stderr,
            )

        log_journal("discard_file_to_batch_end", batch_name=batch_name, file_path=file_path)

        if advance:
            finish_selected_change_action(quiet=quiet, auto_advance=auto_advance)
        return len(patches_to_discard)
