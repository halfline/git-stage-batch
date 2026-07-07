"""Selected-change loading and stale-cache validation."""

from __future__ import annotations

import json
from typing import Optional, Union

from ...core.models import (
    BinaryFileChange,
    GitlinkChange,
    LineLevelChange,
    RenameChange,
    TextFileDeletionChange,
)
from ...exceptions import CommandError, exit_with_error
from ...i18n import _
from ...utils.file_io import read_text_file_contents
from ...utils.paths import (
    get_line_changes_json_file_path,
    get_selected_hunk_patch_file_path,
)
from .. import change_freshness as _change_freshness
from .. import line_state as _line_state
from . import store as _selected_store
from .lifecycle import (
    clear_selected_change_state_files as _clear_selected_change_state_files,
)
from .snapshots import (
    snapshots_are_stale as _snapshots_are_stale,
)


SelectedChange = Union[
    LineLevelChange,
    BinaryFileChange,
    GitlinkChange,
    RenameChange,
    TextFileDeletionChange,
]


def load_selected_change() -> Optional[SelectedChange]:
    """Load the currently cached selected change, if any."""
    selected_kind = _selected_store.read_selected_change_kind()
    rename_change = _selected_store.load_selected_rename_change()
    if rename_change is not None:
        if (
            selected_kind == _selected_store.SelectedChangeKind.RENAME
            and _change_freshness.rename_change_is_stale(rename_change)
        ):
            raise CommandError(
                _(
                    "Selected rename no longer matches the working tree: {old} -> {new}.\n"
                    "Run 'show' again before using a pathless action."
                ).format(old=rename_change.old_path, new=rename_change.new_path)
            )
        return rename_change

    deletion_change = _selected_store.load_selected_text_deletion_change()
    if deletion_change is not None:
        if (
            selected_kind == _selected_store.SelectedChangeKind.DELETION
            and _change_freshness.text_deletion_change_is_stale(deletion_change)
        ):
            raise CommandError(
                _(
                    "Selected text file deletion no longer matches the working tree: {file}.\n"
                    "Run 'show' again before using a pathless action."
                ).format(file=deletion_change.path())
            )
        return deletion_change

    gitlink_change = _selected_store.load_selected_gitlink_change()
    if gitlink_change is not None:
        if (
            selected_kind == _selected_store.SelectedChangeKind.GITLINK
            and _change_freshness.gitlink_change_is_stale(gitlink_change)
        ):
            raise CommandError(
                _(
                    "Selected submodule pointer no longer matches the working tree: {file}.\n"
                    "Run 'show' again before using a pathless action."
                ).format(file=gitlink_change.path())
            )
        return gitlink_change

    binary_file = _selected_store.load_selected_binary_file()
    if binary_file is not None:
        if (
            selected_kind == _selected_store.SelectedChangeKind.BINARY
            and _change_freshness.binary_file_change_is_stale(binary_file)
        ):
            file_path = (
                binary_file.new_path
                if binary_file.new_path != "/dev/null" else
                binary_file.old_path
            )
            raise CommandError(
                _(
                    "Selected binary file no longer matches the working tree: {file}.\n"
                    "Run 'show' again before using a pathless action."
                ).format(file=file_path)
            )
        return binary_file

    patch_path = get_selected_hunk_patch_file_path()
    if not patch_path.exists():
        return None

    require_selected_hunk()

    line_changes = _line_state.load_line_changes_from_state()
    if line_changes is not None:
        return line_changes

    return _selected_store.load_line_changes_from_patch_path(patch_path)


def require_selected_hunk() -> None:
    """Ensure selected hunk exists and is not stale, exit with error otherwise."""
    if _selected_store.read_selected_change_kind() in (
        _selected_store.SelectedChangeKind.BATCH_FILE,
        _selected_store.SelectedChangeKind.BATCH_BINARY,
    ):
        exit_with_error(
            _(
                "Selected file came from a batch, not a live hunk. "
                "Open a live hunk with 'show' or use the matching '--from' command."
            )
        )

    if not get_selected_hunk_patch_file_path().exists():
        exit_with_error(_("No selected hunk. Run 'start' first."))

    if get_line_changes_json_file_path().exists():
        data = json.loads(read_text_file_contents(get_line_changes_json_file_path()))
        file_path = data["path"]
        if _snapshots_are_stale(file_path):
            _clear_selected_change_state_files()
            exit_with_error(
                _(
                    "Cached hunk is stale (file was changed). "
                    "Run 'start' or 'again' to continue."
                )
            )
