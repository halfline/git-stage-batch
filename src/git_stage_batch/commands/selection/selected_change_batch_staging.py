"""Selected-change include-to-batch support."""

from __future__ import annotations

import sys

from ...batch.display import annotate_with_batch_source
from ...batch.operations import create_batch
from ...batch.query import read_batch_metadata
from ...batch.source_refresh import acquire_batch_ownership_update_for_selection
from ...batch.text_file_storage import add_file_to_batch
from ...batch.validation import batch_exists
from ...core.diff_parser import (
    acquire_unified_diff,
    build_line_changes_from_patch_lines,
)
from ...core.hashing import (
    compute_binary_file_hash,
    compute_gitlink_change_hash,
    compute_stable_hunk_hash_from_lines,
    compute_text_file_deletion_hash,
)
from ...core.models import (
    BinaryFileChange,
    GitlinkChange,
    RenameChange,
    TextFileDeletionChange,
)
from ...data.file_modes import detect_file_mode
from ...data.live_diff import stream_live_git_diff
from ...data.progress import record_hunk_skipped
from ...exceptions import exit_with_error
from ...i18n import _
from ...utils.file_io import append_lines_to_file, read_text_file_line_set
from ...utils.paths import get_block_list_file_path, get_context_lines
from . import whole_file_batch_staging as _whole_file_batch_staging
from .action_completion import finish_selected_change_action


def include_selected_change_to_batch(
    batch_name: str,
    *,
    quiet: bool = False,
    auto_advance: bool | None = None,
) -> None:
    """Save the next selected change to a batch."""
    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    blocklist_path = get_block_list_file_path()
    blocked_hashes = read_text_file_line_set(blocklist_path)

    selected_file_path = None
    selected_line_changes = None
    selected_hash = None
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
                continue

            if isinstance(patch, TextFileDeletionChange):
                patch_hash = compute_text_file_deletion_hash(patch)
                if patch_hash not in blocked_hashes:
                    _whole_file_batch_staging.include_text_deletion_to_batch(
                        batch_name,
                        patch,
                        quiet=quiet,
                        auto_advance=auto_advance,
                    )
                    return
                continue

            if isinstance(patch, GitlinkChange):
                patch_hash = compute_gitlink_change_hash(patch)
                if patch_hash not in blocked_hashes:
                    _whole_file_batch_staging.include_gitlink_to_batch(
                        batch_name,
                        patch,
                        quiet=quiet,
                        auto_advance=auto_advance,
                    )
                    return
                continue

            if isinstance(patch, BinaryFileChange):
                patch_hash = compute_binary_file_hash(patch)
                if patch_hash not in blocked_hashes:
                    _whole_file_batch_staging.include_binary_to_batch(
                        batch_name,
                        patch,
                        quiet=quiet,
                        auto_advance=auto_advance,
                    )
                    return
                continue

            patch_hash = compute_stable_hunk_hash_from_lines(patch.lines)
            if patch_hash in blocked_hashes:
                continue

            selected_file_path = patch.new_path
            selected_line_changes = build_line_changes_from_patch_lines(
                patch.lines,
                annotator=annotate_with_batch_source,
            )
            selected_hash = patch_hash
            break

    if selected_file_path is None or selected_line_changes is None or selected_hash is None:
        print(_("No changes to process."), file=sys.stderr)
        return

    file_path = selected_file_path
    all_lines_to_batch = selected_line_changes.lines
    file_mode = detect_file_mode(file_path)
    metadata = read_batch_metadata(batch_name)
    file_metadata = metadata.get("files", {}).get(file_path)

    try:
        with acquire_batch_ownership_update_for_selection(
            batch_name=batch_name,
            file_path=file_path,
            file_metadata=file_metadata,
            selected_lines=all_lines_to_batch,
        ) as update:
            add_file_to_batch(
                batch_name,
                file_path,
                update.ownership_after,
                file_mode,
                batch_source_commit=update.batch_source_commit,
            )
    except ValueError as error:
        exit_with_error(
            _("Cannot include to batch: batch source is stale and remapping failed.\n"
              "File: {file}\nBatch: {batch}\nError: {error}").format(
                file=file_path,
                batch=batch_name,
                error=str(error),
            )
        )

    append_lines_to_file(blocklist_path, [selected_hash])
    record_hunk_skipped(selected_line_changes, selected_hash)

    if not quiet:
        print(_("✓ Hunk saved to batch '{name}'").format(name=batch_name), file=sys.stderr)

    finish_selected_change_action(quiet=quiet, auto_advance=auto_advance)
