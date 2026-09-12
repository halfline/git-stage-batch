"""Read per-file diff headers and derive atomic file-change metadata."""

from __future__ import annotations

from dataclasses import dataclass
from . import diff_headers as _diff_headers
from . import file_metadata_diff as _file_metadata_diff
from . import gitlink_diff as _gitlink_diff
from .diff_stream import DiffLineCursor
from .models import FileModeChange, FileTypeChange
from ..exceptions import CommandError
from ..i18n import _
from ..git_paths import display_path


@dataclass(frozen=True, slots=True)
class FileDiffMetadata:
    """Header facts for one file; hunk content stays in the input stream."""

    old_path: str
    new_path: str
    metadata_lines: list[bytes]
    old_file_line: bytes | None
    is_gitlink: bool
    is_rename: bool
    mode_change: FileModeChange | None
    type_change: FileTypeChange | None
    is_deleted_file: bool
    index_old_oid: str | None
    index_new_oid: str | None


def read_file_diff_metadata(
    line: bytes,
    cursor: DiffLineCursor,
    deleted_modes_by_path: dict[str, str],
    *,
    allow_file_type_changes: bool,
) -> FileDiffMetadata:
    """Read file headers, retaining delete/add type-transition evidence."""
    file_paths = _diff_headers.diff_git_paths(line)
    if file_paths is None:
        raise CommandError(_("Malformed diff --git header"))
    old_path, new_path = file_paths

    # Collect metadata lines until we hit the --- line (start of unified diff)
    # Files with no hunks (binary, mode-only, rename-only, empty) won't have --- line
    metadata_lines: list[bytes] = []
    old_file_line: bytes | None = None
    while True:
        next_l = cursor.next_line()
        if next_l is None:
            # End of input - check for empty file before returning
            break
        next_l = next_l.rstrip(b"\n")
        if next_l.startswith(b"---"):
            old_file_line = next_l
            break
        # Collect metadata lines
        metadata_lines.append(next_l)
        # If we hit another diff header, this file has no hunks - check if it's an empty new file
        if _diff_headers.line_is_diff_git_header(next_l):
            # Put the line back for next iteration (with \n restored for consistency)
            cursor.push_back(next_l + b"\n")
            break

    is_gitlink = _gitlink_diff.metadata_indicates_gitlink(metadata_lines)
    is_rename = _file_metadata_diff.metadata_indicates_rename(metadata_lines)
    if is_rename:
        renamed_paths = _file_metadata_diff.rename_paths(metadata_lines)
        if renamed_paths is not None:
            old_path, new_path = renamed_paths
    mode_transition = _file_metadata_diff.executable_mode_change(metadata_lines)
    type_transition = _file_metadata_diff.file_type_change(metadata_lines)
    deleted_mode = _file_metadata_diff.deleted_file_mode(metadata_lines)
    if allow_file_type_changes and deleted_mode is not None:
        deleted_modes_by_path[old_path] = deleted_mode
    new_mode = _file_metadata_diff.new_file_mode(metadata_lines)
    if (
        type_transition is None
        and allow_file_type_changes
        and new_mode is not None
        and new_path in deleted_modes_by_path
        and deleted_modes_by_path[new_path] != new_mode
    ):
        type_transition = (
            deleted_modes_by_path.pop(new_path),
            new_mode,
        )
    if type_transition is not None and not is_gitlink and not allow_file_type_changes:
        raise CommandError(
            _(
                "File type changes are atomic and are not supported yet: "
                "{file} ({old} -> {new})"
            ).format(
                file=display_path(old_path),
                old=type_transition[0],
                new=type_transition[1],
            )
        )
    type_change = (
        FileTypeChange(
            new_path,
            *type_transition,
            index_path=old_path if is_rename else None,
        )
        if type_transition is not None and not is_gitlink
        else None
    )
    mode_change = (
        FileModeChange(
            new_path,
            *mode_transition,
            index_path=old_path if is_rename else None,
        )
        if mode_transition is not None
        else None
    )
    is_deleted_file = _file_metadata_diff.metadata_indicates_deleted_file(
        metadata_lines
    )
    index_old_oid, index_new_oid = _gitlink_diff.gitlink_oids_from_index(metadata_lines)

    return FileDiffMetadata(
        old_path=old_path,
        new_path=new_path,
        metadata_lines=metadata_lines,
        old_file_line=old_file_line,
        is_gitlink=is_gitlink,
        is_rename=is_rename,
        mode_change=mode_change,
        type_change=type_change,
        is_deleted_file=is_deleted_file,
        index_old_oid=index_old_oid,
        index_new_oid=index_new_oid,
    )
