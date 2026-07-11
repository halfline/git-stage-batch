"""Session-aware batch file scope resolution."""

from __future__ import annotations

from typing import Optional

from ..exceptions import CommandError
from ..i18n import _
from ..utils.file_patterns import resolve_gitignore_style_patterns
from .batch_selected_changes import (
    require_current_selected_batch_binary_file_for_batch,
    require_current_selected_batch_gitlink_file_for_batch,
)
from .selected_change.paths import get_selected_change_file_path
from .selected_change.store import (
    SelectedChangeKind,
    read_selected_change_kind,
)
from .selected_change.file_changes import load_selected_mode_change, read_selected_mode_data


def resolve_batch_file_scope(
    batch_name: str,
    all_files: dict[str, dict],
    file: Optional[str] = None,
    patterns: Optional[list[str]] = None,
) -> dict[str, dict]:
    """Resolve which files from a batch to operate on.

    Args:
        batch_name: Name of the batch
        all_files: All files in the batch metadata
        file: Optional file path filter:
            - None: operate on all files in batch
            - "": use currently selected hunk's file
            - path: specific file path
        patterns: Optional gitignore-style file patterns to resolve against batch files

    Returns:
        Dictionary of file paths to file metadata for selected files

    Raises:
        CommandError: If file not found or no hunk selected when using ""
    """
    if file is not None:
        if file == "":
            file_to_use = get_selected_change_file_path()
            if file_to_use is None:
                raise CommandError(
                    _("No selected hunk. Run 'show' first or specify file path.")
                )
        else:
            file_to_use = file

        target_file = _get_batch_file_for_line_operation(
            batch_name,
            all_files,
            file_to_use,
        )
        return {target_file: all_files[target_file]}
    if patterns is not None:
        resolved_files = resolve_gitignore_style_patterns(all_files.keys(), patterns)
        if not resolved_files:
            raise CommandError(
                _("No files in batch '{name}' matched: {patterns}").format(
                    name=batch_name,
                    patterns=", ".join(patterns),
                )
            )
        return {file_path: all_files[file_path] for file_path in resolved_files}
    return all_files


def resolve_current_batch_atomic_file_scope(
    batch_name: str,
    all_files: dict[str, dict],
    file: Optional[str] = None,
    patterns: Optional[list[str]] = None,
    line_ids: Optional[str] = None,
) -> Optional[str]:
    """Resolve a pathless whole-file batch action through an atomic selection.

    Selected batch binaries and submodule pointers are atomic current-file
    selections. Both the bare command and `--file` with no path are pathless
    whole-file actions, so both must revalidate cached batch state before
    narrowing to the selected file.
    """
    if patterns is not None or line_ids is not None or file not in (None, ""):
        return file

    selected_kind = read_selected_change_kind()
    if selected_kind == SelectedChangeKind.BATCH_BINARY:
        selected_file = require_current_selected_batch_binary_file_for_batch(
            batch_name,
            all_files,
        )
        return selected_file if selected_file is not None else file
    if selected_kind == SelectedChangeKind.BATCH_GITLINK:
        selected_file = require_current_selected_batch_gitlink_file_for_batch(
            batch_name,
            all_files,
        )
        return selected_file if selected_file is not None else file
    if selected_kind == SelectedChangeKind.BATCH_MODE:
        mode_change = load_selected_mode_change()
        mode_data = read_selected_mode_data()
        if (
            mode_change is not None
            and mode_data is not None
            and mode_data.get("batch_name") == batch_name
            and mode_change.path() in all_files
        ):
            return mode_change.path()

    return file


def _get_batch_file_for_line_operation(
    batch_name: str,
    all_files: dict[str, dict],
    file: str | None,
) -> str:
    """Determine which file in batch to operate on."""
    files = sorted(all_files.keys())

    if not files:
        raise CommandError(f"Batch '{batch_name}' is empty")

    if file is None:
        return files[0]

    if file not in all_files:
        raise CommandError(f"File '{file}' not found in batch '{batch_name}'")

    return file
