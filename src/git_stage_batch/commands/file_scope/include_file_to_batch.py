"""Single-file include-to-batch support."""

from __future__ import annotations

from contextlib import ExitStack
import sys

from ...batch.source_annotation import annotate_with_batch_source
from ...batch.state.lifecycle import create_batch
from ...batch.ownership_update import acquire_batch_ownership_update_for_selection
from ...batch.state.query import read_batch_metadata
from ...batch.text_file_storage import add_file_to_batch
from ...batch.state.batch_names import batch_exists
from ...core.diff_parser import acquire_unified_diff, build_line_changes_from_patch_lines
from ...core.models import FileModeChange, RenameChange, TextFileDeletionChange
from ...data.file_change_display import (
    render_binary_file_change,
    render_gitlink_change,
    render_text_deletion_change,
)
from ...data.file_modes import detect_file_mode
from ...data.file_tracking import auto_add_untracked_files
from ...data.live_diff import stream_live_git_diff
from ...utils.session_start_point import session_comparison_base
from ...data.session import snapshot_file_if_untracked
from ...exceptions import exit_with_error
from ...i18n import _
from ...utils.paths import get_context_lines
from ..selection import whole_file_batch_staging as _whole_file_batch_staging
from ..selection.action_completion import finish_selected_change_action


def include_file_to_batch(
    batch_name: str,
    file_path: str,
    *,
    quiet: bool = False,
    auto_advance: bool | None = None,
) -> None:
    """Include one entire file to a batch."""
    auto_add_untracked_files([file_path])

    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    deletion_change = render_text_deletion_change(file_path)
    if deletion_change is not None:
        _whole_file_batch_staging.include_text_deletion_to_batch(
            batch_name,
            deletion_change,
            quiet=quiet,
            auto_advance=auto_advance,
        )
        return

    binary_change = render_binary_file_change(file_path)
    if binary_change is not None:
        _whole_file_batch_staging.include_binary_to_batch(
            batch_name,
            binary_change,
            quiet=quiet,
            auto_advance=auto_advance,
        )
        return
    gitlink_change = render_gitlink_change(file_path)
    if gitlink_change is not None:
        _whole_file_batch_staging.include_gitlink_to_batch(
            batch_name,
            gitlink_change,
            quiet=quiet,
            auto_advance=auto_advance,
        )
        return

    file_mode = detect_file_mode(file_path)
    all_lines_to_batch = []
    mode_change = None

    with acquire_unified_diff(
        stream_live_git_diff(
            base=session_comparison_base(),
            context_lines=get_context_lines(),
            paths=[file_path],
        )
    ) as patches:
        for patch in patches:
            if isinstance(patch, FileModeChange):
                mode_change = patch
                continue
            if isinstance(patch, (RenameChange, TextFileDeletionChange)):
                continue
            hunk_lines = build_line_changes_from_patch_lines(
                patch.lines,
                annotator=annotate_with_batch_source,
            )
            all_lines_to_batch.extend(hunk_lines.lines)

    if not all_lines_to_batch:
        if mode_change is not None:
            _whole_file_batch_staging.include_mode_to_batch(
                batch_name,
                mode_change,
                quiet=quiet,
                auto_advance=auto_advance,
            )
            return
        if (
            _whole_file_batch_staging.save_empty_text_lifecycle_to_batch(
                batch_name,
                file_path,
                file_mode,
            )
            is not None
        ):
            if not quiet:
                print(
                    _("Included file '{file}' to batch '{batch}'").format(
                        file=file_path,
                        batch=batch_name,
                    ),
                    file=sys.stderr,
                )
            finish_selected_change_action(quiet=quiet, auto_advance=auto_advance)
            return

        if not quiet:
            print(
                _("No changes in file '{file}' to include.").format(file=file_path),
                file=sys.stderr,
            )
        return

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
        except ValueError as error:
            exit_with_error(
                _("Cannot include file to batch: batch source is stale and remapping failed.\n"
                  "File: {file}\nBatch: {batch}\nError: {error}").format(
                    file=file_path,
                    batch=batch_name,
                    error=str(error),
                )
            )

        snapshot_file_if_untracked(file_path)
        add_file_to_batch(
            batch_name,
            file_path,
            update.ownership_after,
            file_mode,
            batch_source_commit=update.batch_source_commit,
        )

    if not quiet:
        print(
            _("Included file '{file}' to batch '{batch}'").format(
                file=file_path,
                batch=batch_name,
            ),
            file=sys.stderr,
        )

    finish_selected_change_action(quiet=quiet, auto_advance=auto_advance)
