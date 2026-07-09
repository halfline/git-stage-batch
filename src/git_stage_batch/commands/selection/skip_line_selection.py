"""Line-selection support for skip commands."""

from __future__ import annotations

from dataclasses import replace
import json
import sys

from ...batch.selection import require_line_selection_in_view
from ...core.line_selection import parse_line_selection
from ...data.file_hunk_display import render_unstaged_file_as_single_hunk
from ...data.selected_change.file_hunk_cache import cache_unstaged_file_as_single_hunk
from ...data.file_review.action_scope import finish_review_scoped_line_action
from ...data.file_tracking import auto_add_untracked_files
from ...data.line_id_files import read_line_ids_file, write_line_ids_file
from ...data.line_state import (
    convert_line_changes_to_serializable_dict,
    load_line_changes_from_state,
)
from ...data.progress import record_hunk_skipped
from ...data.selected_change.loading import require_selected_hunk
from ...data.selected_change.paths import get_selected_change_file_path
from ...data.selected_change.store import (
    SelectedChangeKind,
    read_selected_change_kind,
)
from ...data.undo import undo_checkpoint
from ...exceptions import NoMoreHunks, exit_with_error
from ...i18n import _
from ...output.hunk import (
    print_line_level_changes,
    print_remaining_line_changes_header,
)
from ...utils.file_io import (
    append_lines_to_file,
    read_text_file_contents,
    write_text_file_contents,
)
from ...utils.paths import (
    get_block_list_file_path,
    get_line_changes_json_file_path,
    get_processed_skip_ids_file_path,
    get_selected_hunk_hash_file_path,
)
from ..file_scope import skip_file as _file_scope_skip_file
from .action_completion import finish_selected_change_action


def skip_line_selection(
    line_id_specification: str,
    *,
    file: str | None = None,
    review_state=None,
    auto_advance: bool | None = None,
) -> None:
    """Mark selected line IDs as skipped."""
    target_file = None
    reuse_selected_file_view = False
    if file is None:
        require_selected_hunk()
    else:
        if file == "":
            target_file = get_selected_change_file_path()
            if target_file is None:
                exit_with_error(
                    _("No selected hunk. Run 'show' first or specify file path.")
                )
        else:
            target_file = file
        auto_add_untracked_files([target_file])

        reuse_selected_file_view = (
            read_selected_change_kind() == SelectedChangeKind.FILE
            and get_selected_change_file_path() == target_file
        )
        if (
            not reuse_selected_file_view
            and render_unstaged_file_as_single_hunk(target_file) is None
        ):
            exit_with_error(_("No changes in file '{file}'.").format(file=target_file))

    requested_ids = parse_line_selection(line_id_specification)
    operation_parts = ["skip", "--line", line_id_specification]
    if file is not None:
        operation_parts.extend(["--file", file])
    with undo_checkpoint(" ".join(operation_parts)):
        if file is not None:
            if not reuse_selected_file_view:
                if cache_unstaged_file_as_single_hunk(target_file) is None:
                    exit_with_error(
                        _("No changes in file '{file}'.").format(file=target_file)
                    )
            require_selected_hunk()

        line_changes = load_line_changes_from_state()
        if line_changes is None:
            raise NoMoreHunks()
        require_line_selection_in_view(
            line_changes,
            set(requested_ids),
            line_id_specification=line_id_specification,
        )

        already_skipped_ids = (
            set(read_line_ids_file(get_processed_skip_ids_file_path()))
            if file is None or reuse_selected_file_view
            else set()
        )
        combined_skip_ids = already_skipped_ids | set(requested_ids)
        write_line_ids_file(get_processed_skip_ids_file_path(), combined_skip_ids)

        visible_changed_ids = [
            changed_id
            for changed_id in line_changes.changed_line_ids()
            if changed_id not in combined_skip_ids
        ]

        if not visible_changed_ids:
            if read_selected_change_kind() == SelectedChangeKind.FILE:
                print(
                    _("✓ Skipped line(s): {lines}").format(
                        lines=line_id_specification
                    ),
                    file=sys.stderr,
                )
                finish_review_scoped_line_action(review_state)
                _file_scope_skip_file.skip_file_changes(
                    "",
                    auto_advance=auto_advance,
                )
                return

            patch_hash = read_text_file_contents(
                get_selected_hunk_hash_file_path()
            ).strip()
            blocklist_path = get_block_list_file_path()
            append_lines_to_file(blocklist_path, [patch_hash])
            record_hunk_skipped(line_changes, patch_hash)
            print(
                _("✓ Skipped line(s): {lines}").format(lines=line_id_specification),
                file=sys.stderr,
            )
            finish_review_scoped_line_action(review_state)
            finish_selected_change_action(
                quiet=False,
                auto_advance=auto_advance,
            )
            return

        filtered_lines = [
            replace(line_entry, id=None)
            if line_entry.id in combined_skip_ids
            else line_entry
            for line_entry in line_changes.lines
        ]
        filtered_line_changes = replace(line_changes, lines=filtered_lines)
        write_text_file_contents(
            get_line_changes_json_file_path(),
            json.dumps(
                convert_line_changes_to_serializable_dict(filtered_line_changes),
                ensure_ascii=False,
                indent=0,
            ),
        )

    print(
        _("✓ Skipped line(s): {lines}").format(lines=line_id_specification),
        file=sys.stderr,
    )
    print_remaining_line_changes_header(filtered_line_changes.path)
    print_line_level_changes(filtered_line_changes)
    finish_review_scoped_line_action(review_state)
