"""Selected-change discard-to-batch support."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import ExitStack
import sys

from ...batch.display import annotate_with_batch_source
from ...batch.operations import create_batch
from ...batch.query import read_batch_metadata
from ...batch.source_refresh import acquire_batch_ownership_update_for_selection
from ...batch.storage import add_file_to_batch
from ...batch.validation import batch_exists
from ...core.buffer import LineBuffer
from ...core.diff_parser import (
    acquire_unified_diff,
    build_line_changes_from_patch_lines,
    patch_is_empty_file_change,
    patch_is_new_file,
)
from ...core.hashing import compute_stable_hunk_hash_from_lines
from ...core.models import (
    BinaryFileChange,
    GitlinkChange,
    RenameChange,
    TextFileDeletionChange,
)
from ...data.file_modes import detect_file_mode
from ...data.hunk_tracking import fetch_next_change
from ...data.live_diff import stream_live_git_diff
from ...data.progress import record_hunk_discarded
from ...data.session import snapshot_file_if_untracked
from ...exceptions import NoMoreHunks, exit_with_error
from ...i18n import _, ngettext
from ...utils.file_io import (
    append_lines_to_file,
    path_is_empty,
    read_text_file_contents,
    read_text_file_line_set,
)
from ...utils.git import (
    git_apply_to_worktree,
    git_remove_paths,
)
from ...utils.git_repository import get_git_repository_root_path
from ...utils.journal import log_journal
from ...utils.paths import (
    get_block_list_file_path,
    get_context_lines,
    get_selected_hunk_hash_file_path,
    get_selected_hunk_patch_file_path,
)
from .action_completion import finish_selected_change_action
from . import whole_file_batch_discarding as _whole_file_batch_discarding


def discard_selected_change_to_batch(
    batch_name: str,
    file_only: bool = False,
    *,
    quiet: bool = False,
    auto_advance: bool | None = None,
) -> int:
    """Save the selected change or file to a batch and discard it."""
    log_journal(
        "discard_hunk_to_batch_start",
        batch_name=batch_name,
        file_only=file_only,
        quiet=quiet,
    )

    if not batch_exists(batch_name):
        log_journal("discard_hunk_to_batch_creating_batch", batch_name=batch_name)
        create_batch(batch_name, "Auto-created")

    try:
        selected_item = fetch_next_change()
    except NoMoreHunks:
        print(_("No changes to process."), file=sys.stderr)
        return 0

    if isinstance(selected_item, RenameChange):
        exit_with_error(
            _(
                "Cannot discard rename '{old} -> {new}' to a batch yet. "
                "Discard, skip, or stage the rename first."
            ).format(old=selected_item.old_path, new=selected_item.new_path)
        )
    if isinstance(selected_item, GitlinkChange):
        exit_with_error(_("Discarding submodule pointer changes to a batch is not supported yet."))
    if isinstance(selected_item, BinaryFileChange):
        return _whole_file_batch_discarding.discard_binary_to_batch(
            batch_name,
            selected_item,
            quiet=quiet,
            auto_advance=auto_advance,
        )
    if isinstance(selected_item, TextFileDeletionChange):
        return _whole_file_batch_discarding.discard_text_deletion_to_batch(
            batch_name,
            selected_item,
            quiet=quiet,
            auto_advance=auto_advance,
        )

    patch_hash = read_text_file_contents(get_selected_hunk_hash_file_path()).strip()
    with LineBuffer.from_path(get_selected_hunk_patch_file_path()) as selected_patch_lines:
        return _discard_text_hunk_to_batch(
            batch_name=batch_name,
            selected_patch_lines=selected_patch_lines,
            selected_patch_hash=patch_hash,
            file_only=file_only,
            quiet=quiet,
            auto_advance=auto_advance,
        )


def _discard_text_hunk_to_batch(
    *,
    batch_name: str,
    selected_patch_lines: Sequence[bytes],
    selected_patch_hash: str,
    file_only: bool,
    quiet: bool,
    auto_advance: bool | None = None,
) -> int:
    """Save one cached text patch selection to a batch, then discard it."""
    line_changes = build_line_changes_from_patch_lines(
        selected_patch_lines,
        annotator=annotate_with_batch_source,
    )
    file_path = line_changes.path

    blocklist_path = get_block_list_file_path()
    blocked_hashes = read_text_file_line_set(blocklist_path)
    file_mode = detect_file_mode(file_path)

    with ExitStack() as patch_stack:
        all_lines_to_batch = []
        patches_to_discard: list[tuple[Sequence[bytes], str]] = []

        if file_only:
            with acquire_unified_diff(
                stream_live_git_diff(context_lines=get_context_lines())
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

                    if patch.new_path != file_path:
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
        else:
            all_lines_to_batch = line_changes.lines
            patches_to_discard = [(selected_patch_lines, selected_patch_hash)]

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
                    _("Cannot discard to batch: batch source is stale and remapping failed.\n"
                      "File: {file}\n"
                      "Batch: {batch}\n"
                      "Error: {error}").format(file=file_path, batch=batch_name, error=str(e))
                )

            snapshot_file_if_untracked(file_path)

            log_journal(
                "discard_hunk_to_batch_before_add",
                batch_name=batch_name,
                file_path=file_path,
                num_patches=len(patches_to_discard),
            )

            add_file_to_batch(
                batch_name,
                file_path,
                update.ownership_after,
                file_mode,
                batch_source_commit=update.batch_source_commit,
            )

        log_journal("discard_hunk_to_batch_after_add", batch_name=batch_name, file_path=file_path)

        is_new_file = any(
            patch_is_new_file(patch_lines_item)
            for patch_lines_item, _ in patches_to_discard
        )

        for patch_lines_item, patch_hash in patches_to_discard:
            is_empty_file_patch = patch_is_empty_file_change(patch_lines_item)

            if not is_empty_file_patch:
                log_journal(
                    "discard_hunk_to_batch_before_git_apply",
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

                exit_code = apply_result.returncode
                stderr_text = apply_result.stderr or ""
                log_journal(
                    "discard_hunk_to_batch_after_git_apply",
                    batch_name=batch_name,
                    patch_hash=patch_hash,
                    exit_code=exit_code,
                    stderr_len=len(stderr_text),
                )

                if exit_code != 0:
                    log_journal(
                        "discard_hunk_to_batch_git_apply_failed",
                        batch_name=batch_name,
                        patch_hash=patch_hash,
                        exit_code=exit_code,
                        stderr=stderr_text,
                    )
                    exit_with_error(_("Failed to apply reverse patch: {error}").format(error=stderr_text))
            else:
                log_journal(
                    "discard_hunk_to_batch_skipping_empty_patch",
                    batch_name=batch_name,
                    patch_hash=patch_hash,
                )

            append_lines_to_file(blocklist_path, [patch_hash])
            record_hunk_discarded(patch_hash)

        if is_new_file or file_only:
            absolute_path = get_git_repository_root_path() / file_path

            if not absolute_path.exists():
                git_remove_paths([file_path], cached=True, quiet=True, check=False)
            elif is_new_file:
                if path_is_empty(absolute_path):
                    absolute_path.unlink()
                    git_remove_paths([file_path], cached=True, quiet=True, check=False)

        log_journal(
            "discard_hunk_to_batch_success",
            batch_name=batch_name,
            file_path=file_path,
            num_patches=len(patches_to_discard),
        )

        if not quiet:
            if file_only:
                msg = ngettext(
                    "✓ {count} hunk from {file} saved to batch '{name}' and discarded",
                    "✓ {count} hunks from {file} saved to batch '{name}' and discarded",
                    len(patches_to_discard)
                ).format(count=len(patches_to_discard), file=file_path, name=batch_name)
                print(msg, file=sys.stderr)
            else:
                print(
                    _("✓ Hunk saved to batch '{name}' and discarded from working tree").format(
                        name=batch_name,
                    ),
                    file=sys.stderr,
                )

        finish_selected_change_action(quiet=quiet, auto_advance=auto_advance)
        return len(patches_to_discard)
