"""Selected hunk recalculation after line-level changes."""

from __future__ import annotations

import subprocess
from enum import Enum

from ...batch.display import annotate_with_batch_source
from ...core.diff_parser import (
    acquire_unified_diff,
    build_line_changes_from_patch_lines,
)
from ...core.hashing import (
    compute_gitlink_change_hash,
    compute_rename_change_hash,
    compute_stable_hunk_hash_from_lines,
    compute_text_file_deletion_hash,
)
from ...core.line_identity import preserve_line_ids_from_previous_view
from ...core.line_selection import write_line_ids_file
from ...core.models import (
    BinaryFileChange,
    GitlinkChange,
    RenameChange,
    TextFileDeletionChange,
)
from ...utils.file_io import read_text_file_line_set
from ...utils.paths import (
    get_block_list_file_path,
    get_context_lines,
    get_processed_include_ids_file_path,
    get_processed_skip_ids_file_path,
)
from ..auto_advance import resolve_auto_advance
from .. import change_freshness as _change_freshness
from .. import file_hunk_display as _file_hunk_display
from .. import line_state as _line_state
from .. import live_diff as _live_diff
from . import store as _selected_store
from . import hunk_filtering as _selected_hunk_filtering
from .lifecycle import (
    clear_selected_change_state_files as _clear_selected_change_state_files,
)


class RecalculateSelectedHunkResult(str, Enum):
    """Outcome from refreshing the selected hunk for one file."""

    RECALCULATED = "recalculated"
    CLEARED = "cleared"
    SHOW_NEXT_CHANGE = "show-next-change"
    NO_MORE_LINES = "no-more-lines"
    NO_PENDING_HUNKS = "no-pending-hunks"


def recalculate_selected_hunk_for_file(
    file_path: str,
    *,
    auto_advance: bool | None = None,
) -> RecalculateSelectedHunkResult:
    """Recalculate the selected hunk for a specific file after modifications.

    After discard --line or include --line changes the working tree or index,
    the cached hunk is stale. This recalculates it for the same file.

    Args:
        file_path: Repository-relative path to recalculate hunk for
    """
    selected_kind = _selected_store.read_selected_change_kind()
    previous_line_changes = _line_state.load_line_changes_from_state()
    if previous_line_changes is not None and previous_line_changes.path != file_path:
        previous_line_changes = None

    write_line_ids_file(get_processed_include_ids_file_path(), set())
    write_line_ids_file(get_processed_skip_ids_file_path(), set())

    if selected_kind == _selected_store.SelectedChangeKind.FILE:
        line_changes = _file_hunk_display.cache_unstaged_file_as_single_hunk(file_path)
        if line_changes is None:
            _clear_selected_change_state_files()
            if resolve_auto_advance(auto_advance):
                return RecalculateSelectedHunkResult.SHOW_NEXT_CHANGE
            _selected_store.mark_selected_change_cleared_by_auto_advance_disabled()
            return RecalculateSelectedHunkResult.CLEARED

        line_changes = preserve_line_ids_from_previous_view(
            previous_line_changes,
            line_changes,
        )
        _selected_store.write_line_changes_state(line_changes)

        if (
            _selected_hunk_filtering.apply_line_level_batch_filter_to_cached_hunk()
        ):
            _clear_selected_change_state_files()
            if resolve_auto_advance(auto_advance):
                return RecalculateSelectedHunkResult.SHOW_NEXT_CHANGE
            _selected_store.mark_selected_change_cleared_by_auto_advance_disabled()
            return RecalculateSelectedHunkResult.CLEARED

        return RecalculateSelectedHunkResult.RECALCULATED

    blocked_hashes = read_text_file_line_set(get_block_list_file_path())

    try:
        with acquire_unified_diff(
            _live_diff.stream_live_git_diff(
                context_lines=get_context_lines(),
                full_index=True,
                ignore_submodules="none",
                submodule_format="short",
            )
        ) as patches:
            for single_hunk in patches:
                if single_hunk.old_path != file_path and single_hunk.new_path != file_path:
                    continue

                if isinstance(single_hunk, RenameChange):
                    rename_hash = compute_rename_change_hash(single_hunk)
                    if rename_hash in blocked_hashes:
                        continue
                    _selected_store.cache_rename_change(single_hunk)
                    return RecalculateSelectedHunkResult.RECALCULATED

                if isinstance(single_hunk, TextFileDeletionChange):
                    deletion_hash = compute_text_file_deletion_hash(single_hunk)
                    if (
                        deletion_hash in blocked_hashes
                        or _change_freshness.text_deletion_change_is_batched(
                            single_hunk
                        )
                    ):
                        continue
                    _selected_store.cache_text_deletion_change(single_hunk)
                    return RecalculateSelectedHunkResult.RECALCULATED

                if isinstance(single_hunk, GitlinkChange):
                    gitlink_hash = compute_gitlink_change_hash(single_hunk)
                    if gitlink_hash in blocked_hashes:
                        continue
                    _selected_store.cache_gitlink_change(single_hunk)
                    return RecalculateSelectedHunkResult.RECALCULATED

                if isinstance(single_hunk, BinaryFileChange):
                    continue

                if single_hunk.old_path != single_hunk.new_path:
                    rename_hash = compute_rename_change_hash(
                        RenameChange(
                            old_path=single_hunk.old_path,
                            new_path=single_hunk.new_path,
                        )
                    )
                    if rename_hash in blocked_hashes:
                        continue

                hunk_hash = compute_stable_hunk_hash_from_lines(single_hunk.lines)

                if hunk_hash in blocked_hashes:
                    continue

                line_changes = build_line_changes_from_patch_lines(
                    single_hunk.lines,
                    annotator=annotate_with_batch_source,
                )
                line_changes = preserve_line_ids_from_previous_view(
                    previous_line_changes,
                    line_changes,
                )
                _selected_store.cache_hunk_change(
                    single_hunk.lines,
                    hunk_hash,
                    line_changes,
                )

                if (
                    _selected_hunk_filtering.apply_line_level_batch_filter_to_cached_hunk()
                ):
                    _clear_selected_change_state_files()
                    return RecalculateSelectedHunkResult.NO_MORE_LINES

                return RecalculateSelectedHunkResult.RECALCULATED
    except subprocess.CalledProcessError:
        _clear_selected_change_state_files()
        return RecalculateSelectedHunkResult.NO_PENDING_HUNKS

    _clear_selected_change_state_files()
    if resolve_auto_advance(auto_advance):
        return RecalculateSelectedHunkResult.SHOW_NEXT_CHANGE
    _selected_store.mark_selected_change_cleared_by_auto_advance_disabled()
    return RecalculateSelectedHunkResult.CLEARED
