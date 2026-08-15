"""Session-aware batch file scope resolution."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Optional

from ..batch.state.metadata_types import BatchFileMetadataDict
from ..exceptions import CommandError
from ..git_paths import display_path
from ..i18n import _
from ..utils.file_patterns import resolve_gitignore_style_patterns
from .batch_selected_changes import (
    require_current_selected_batch_binary_file_for_batch,
    require_current_selected_batch_gitlink_file_for_batch,
    selected_batch_binary_matches_batch,
    selected_batch_gitlink_matches_batch,
)
from .file_review.records import ReviewSource
from .file_review.state import read_last_file_review_state
from .selected_change.paths import get_selected_change_file_path
from .selected_change.store import (
    SelectedChangeKind,
    read_selected_change_kind,
)
from .selected_change.file_changes import load_selected_mode_change, read_selected_mode_data


def selected_batch_change_matches_batch(batch_name: str) -> bool:
    """Return whether a selected batch change came from the requested batch."""
    selected_kind = read_selected_change_kind()
    if selected_kind == SelectedChangeKind.BATCH_FILE:
        review_state = read_last_file_review_state()
        return (
            review_state is not None
            and review_state.source == ReviewSource.BATCH
            and review_state.batch_name == batch_name
        )
    if selected_kind == SelectedChangeKind.BATCH_BINARY:
        return selected_batch_binary_matches_batch(batch_name)
    if selected_kind == SelectedChangeKind.BATCH_GITLINK:
        return selected_batch_gitlink_matches_batch(batch_name)
    if selected_kind == SelectedChangeKind.BATCH_MODE:
        mode_data = read_selected_mode_data()
        return (
            mode_data is not None
            and mode_data.get("batch_name") == batch_name
        )
    return True


def resolve_batch_file_scope(
    batch_name: str,
    all_files: dict[str, BatchFileMetadataDict],
    file: Optional[str] = None,
    patterns: Optional[list[str]] = None,
    *,
    resolved_file_paths: Sequence[str] | None = None,
) -> dict[str, BatchFileMetadataDict]:
    """Resolve which files from a batch to operate on.

    Args:
        batch_name: Name of the batch
        all_files: All files in the batch metadata
        file: Optional file path filter:
            - None: operate on all files in batch
            - "": use currently selected hunk's file
            - path: specific file path
        patterns: Optional gitignore-style file patterns to resolve against batch files
        resolved_file_paths: Optional pre-resolved literal paths to select. This is
            mutually exclusive with ``file`` and ``patterns``.

    Returns:
        Dictionary of file paths to file metadata for selected files

    Raises:
        CommandError: If file not found or no hunk selected when using ""
    """
    if resolved_file_paths is not None:
        if file is not None or patterns is not None:
            raise ValueError(
                "resolved batch file paths cannot be combined with file or patterns"
            )
        unique_paths = tuple(dict.fromkeys(resolved_file_paths))
        if not unique_paths:
            raise ValueError("resolved batch file paths cannot be empty")
        exact_files = {}
        for file_path in unique_paths:
            target_file = _get_batch_file_for_line_operation(
                batch_name,
                all_files,
                file_path,
            )
            exact_files[target_file] = all_files[target_file]
        return exact_files

    if file is not None:
        if file == "":
            if not selected_batch_change_matches_batch(batch_name):
                raise CommandError(
                    _(
                        "The selected file came from a different batch.\n"
                        "Show a file from batch '{name}' before using a "
                        "pathless --file."
                    ).format(name=batch_name)
                )
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
    all_files: dict[str, BatchFileMetadataDict],
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
    all_files: dict[str, BatchFileMetadataDict],
    file: str | None,
) -> str:
    """Determine which file in batch to operate on."""
    files = sorted(all_files.keys())

    if not files:
        raise CommandError(_("Batch '{name}' is empty").format(name=batch_name))

    if file is None:
        return files[0]

    if file not in all_files:
        raise CommandError(
            _("File '{file}' not found in batch '{name}'").format(
                file=display_path(file),
                name=batch_name,
            )
        )

    return file
