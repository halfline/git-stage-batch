"""File-scoped text hunk rendering and selected-state caching."""

from __future__ import annotations

from collections.abc import Generator
import os
import json
import subprocess
import tempfile
from typing import Optional

from ..core.diff_parser import (
    acquire_unified_diff,
    build_line_changes_from_patch_lines,
)
from ..core.hashing import compute_stable_hunk_hash_from_lines
from ..core.line_selection import write_line_ids_file
from ..core.models import (
    BinaryFileChange,
    GitlinkChange,
    HunkHeader,
    LineEntry,
    LineLevelChange,
    RenameChange,
    TextFileDeletionChange,
)
from ..core.buffer import LineBuffer
from .repository_buffers import load_git_object_as_buffer
from ..i18n import ngettext
from ..utils.file_io import write_text_file_contents
from ..utils.git import stream_git_command
from ..utils.paths import (
    get_context_lines,
    get_line_changes_json_file_path,
    get_processed_include_ids_file_path,
    get_processed_skip_ids_file_path,
    get_selected_hunk_hash_file_path,
)
from ..utils.text import bytes_to_lines
from .file_tracking import auto_add_untracked_files
from .line_state import convert_line_changes_to_serializable_dict
from .live_diff import stream_live_git_diff
from .selected_change.store import (
    SelectedChangeKind,
    write_selected_change_kind,
    write_selected_hunk_patch_lines,
)
from .selected_change.snapshots import write_snapshots_for_selected_file_path


def cache_file_as_single_hunk(file_path: str) -> Optional[LineLevelChange]:
    """Cache all changes for a file as a single concatenated hunk.

    Reads the CURRENT working tree state for the file and fetches ALL
    hunks (ignoring blocklist/batches), concatenating them into one
    LineLevelChange with continuous line IDs.

    This always reflects the live working tree state, unlike regular
    hunk caching which uses snapshots.

    Args:
        file_path: Repository-relative path to file

    Returns:
        LineLevelChange with all file changes, or None if no changes
    """
    try:
        combined_line_changes = render_file_as_single_hunk(file_path)
        return _cache_combined_file_line_changes(file_path, combined_line_changes)
    except subprocess.CalledProcessError:
        return None


def cache_unstaged_file_as_single_hunk(file_path: str) -> Optional[LineLevelChange]:
    """Cache the remaining unstaged changes for a file as a single hunk."""
    try:
        combined_line_changes = render_unstaged_file_as_single_hunk(file_path)
        return _cache_combined_file_line_changes(file_path, combined_line_changes)
    except subprocess.CalledProcessError:
        return None


def _cache_combined_file_line_changes(
    file_path: str,
    combined_line_changes: Optional[LineLevelChange],
) -> Optional[LineLevelChange]:
    """Persist a combined file-scoped view as the current selection."""
    if combined_line_changes is None:
        return None

    patch_lines = [
        f"--- a/{file_path}\n".encode("utf-8"),
        f"+++ b/{file_path}\n".encode("utf-8"),
        (
            f"@@ -{combined_line_changes.header.old_start},{combined_line_changes.header.old_len} "
            f"+{combined_line_changes.header.new_start},{combined_line_changes.header.new_len} @@\n"
        ).encode("utf-8"),
    ]
    for entry in combined_line_changes.lines:
        patch_lines.append(entry.kind.encode("utf-8") + entry.text_bytes + b"\n")
        if not entry.has_trailing_newline:
            patch_lines.append(b"\\ No newline at end of file\n")

    patch_hash = compute_stable_hunk_hash_from_lines(patch_lines)

    write_selected_hunk_patch_lines(patch_lines)
    write_text_file_contents(get_selected_hunk_hash_file_path(), patch_hash)
    write_selected_change_kind(SelectedChangeKind.FILE)
    write_line_ids_file(get_processed_include_ids_file_path(), set())
    write_line_ids_file(get_processed_skip_ids_file_path(), set())
    write_text_file_contents(
        get_line_changes_json_file_path(),
        json.dumps(
            convert_line_changes_to_serializable_dict(combined_line_changes),
            ensure_ascii=False,
            indent=0,
        ),
    )

    write_snapshots_for_selected_file_path(file_path)

    return combined_line_changes


def render_file_as_single_hunk(file_path: str) -> Optional[LineLevelChange]:
    """Render all changes for a file as a single hunk without caching state."""
    auto_add_untracked_files([file_path])
    with acquire_unified_diff(
        stream_live_git_diff(
            base="HEAD",
            context_lines=get_context_lines(),
            full_index=True,
            ignore_submodules="none",
            submodule_format="short",
            paths=[file_path],
        )
    ) as patches:
        return _build_combined_file_line_changes(
            file_path,
            patches,
        )


def render_unstaged_file_as_single_hunk(file_path: str) -> Optional[LineLevelChange]:
    """Render the remaining unstaged changes for a file as a single hunk."""
    auto_add_untracked_files([file_path])
    with acquire_unified_diff(
        stream_live_git_diff(
            context_lines=get_context_lines(),
            full_index=True,
            ignore_submodules="none",
            submodule_format="short",
            paths=[file_path],
        )
    ) as patches:
        return _build_combined_file_line_changes(
            file_path,
            patches,
        )


def build_file_hunk_from_buffer(
    file_path: str,
    file_buffer: LineBuffer,
) -> Optional[LineLevelChange]:
    """Build a file-scoped line view for a hypothetical file buffer without writing it."""
    head_buffer = load_git_object_as_buffer(f"HEAD:{file_path}")

    with tempfile.NamedTemporaryFile(delete=False) as old_tmp:
        if head_buffer is not None:
            with head_buffer:
                for chunk in head_buffer.byte_chunks():
                    old_tmp.write(chunk)
        old_path = old_tmp.name
    with tempfile.NamedTemporaryFile(delete=False) as new_tmp:
        for chunk in file_buffer.byte_chunks():
            new_tmp.write(chunk)
        new_path = new_tmp.name

    try:
        with acquire_unified_diff(
            _stream_no_index_diff_lines(
                file_path=file_path,
                old_path=old_path,
                new_path=new_path,
            )
        ) as patches:
            return _build_combined_file_line_changes(
                file_path,
                patches,
            )
    finally:
        try:
            os.unlink(old_path)
            os.unlink(new_path)
        except OSError:
            pass


def _stream_no_index_diff_lines(
    *,
    file_path: str,
    old_path: str,
    new_path: str,
) -> Generator[bytes, None, None]:
    arguments = [
        "diff",
        "--no-index",
        f"-U{get_context_lines()}",
        "--no-color",
        old_path,
        new_path,
    ]
    stderr_chunks: list[bytes] = []
    exit_code = 0

    def stdout_chunks() -> Generator[bytes, None, None]:
        nonlocal exit_code

        try:
            yield from stream_git_command(arguments, requires_index_lock=False)
        except subprocess.CalledProcessError as error:
            exit_code = error.returncode
            stderr = error.stderr
            if stderr is not None:
                stderr_chunks.append(stderr.encode("utf-8", errors="replace"))

    old_header = f"a{old_path}".encode("utf-8")
    new_header = f"b{new_path}".encode("utf-8")
    rendered_old_header = f"a/{file_path}".encode("utf-8")
    rendered_new_header = f"b/{file_path}".encode("utf-8")

    for line in bytes_to_lines(stdout_chunks()):
        yield line.replace(old_header, rendered_old_header).replace(
            new_header,
            rendered_new_header,
        )

    if exit_code not in (0, 1):
        stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        raise subprocess.CalledProcessError(
            exit_code,
            arguments,
            stderr=stderr_text,
        )


def _build_combined_file_line_changes(
    file_path: str,
    patches,
) -> Optional[LineLevelChange]:
    """Combine file diff hunks into one file-scoped LineLevelChange."""
    all_line_entries = []
    line_id_counter = 1
    min_old_start = None
    max_old_end = None
    min_new_start = None
    max_new_end = None
    previous_old_end = None
    previous_new_end = None

    for single_hunk in patches:
        if isinstance(
            single_hunk,
            (BinaryFileChange, GitlinkChange, RenameChange, TextFileDeletionChange),
        ):
            continue

        line_changes = build_line_changes_from_patch_lines(single_hunk.lines)

        if previous_old_end is not None and previous_new_end is not None:
            omitted_old_lines = line_changes.header.old_start - previous_old_end
            omitted_new_lines = line_changes.header.new_start - previous_new_end
            omitted_line_count = max(omitted_old_lines, omitted_new_lines)
            if omitted_line_count > 0:
                gap_text = ngettext(
                    "... {count} more line ...",
                    "... {count} more lines ...",
                    omitted_line_count,
                ).format(count=omitted_line_count)
                all_line_entries.append(
                    LineEntry(
                        id=None,
                        kind=" ",
                        old_line_number=None,
                        new_line_number=None,
                        text_bytes=gap_text.encode("utf-8"),
                        source_line=None,
                    )
                )

        if min_old_start is None:
            min_old_start = line_changes.header.old_start
            min_new_start = line_changes.header.new_start

        max_old_end = line_changes.header.old_start + line_changes.header.old_len
        max_new_end = line_changes.header.new_start + line_changes.header.new_len
        previous_old_end = max_old_end
        previous_new_end = max_new_end

        for line_entry in line_changes.lines:
            if line_entry.kind != " ":
                new_entry = LineEntry(
                    id=line_id_counter,
                    kind=line_entry.kind,
                    old_line_number=line_entry.old_line_number,
                    new_line_number=line_entry.new_line_number,
                    text_bytes=line_entry.text_bytes,
                    source_line=line_entry.source_line,
                    has_trailing_newline=line_entry.has_trailing_newline,
                )
                line_id_counter += 1
            else:
                new_entry = LineEntry(
                    id=None,
                    kind=line_entry.kind,
                    old_line_number=line_entry.old_line_number,
                    new_line_number=line_entry.new_line_number,
                    text_bytes=line_entry.text_bytes,
                    source_line=line_entry.source_line,
                    has_trailing_newline=line_entry.has_trailing_newline,
                )
            all_line_entries.append(new_entry)

    if not all_line_entries:
        return None

    combined_header = HunkHeader(
        old_start=min_old_start,
        old_len=max_old_end - min_old_start,
        new_start=min_new_start,
        new_len=max_new_end - min_new_start,
    )

    return LineLevelChange(
        path=file_path,
        header=combined_header,
        lines=all_line_entries,
    )
