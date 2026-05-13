"""Parse unified diff format into structured models."""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import ExitStack
from typing import Iterable, Iterator, Union

from .models import (
    BinaryFileChange,
    GitlinkChange,
    LineLevelChange,
    HunkHeader,
    LineEntry,
    SingleHunkPatch,
)
from ..editor import (
    EditorBuffer,
    buffer_byte_count,
    buffer_preview,
    load_git_object_as_buffer,
    write_buffer_to_path,
)
from ..exceptions import exit_with_error
from ..i18n import _
from ..utils.git import get_git_repository_root_path
from ..utils.journal import log_journal
from ..utils.paths import get_index_snapshot_file_path, get_working_tree_snapshot_file_path


# Type for annotator hooks that enrich LineLevelChange with additional metadata
LineLevelChangeAnnotator = Callable[[str, LineLevelChange], LineLevelChange]
UnifiedDiffItem = Union[SingleHunkPatch, BinaryFileChange, GitlinkChange]


HUNK_HEADER_PATTERN = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@")
INDEX_LINE_PATTERN = re.compile(br"^index ([0-9a-f]+)\.\.([0-9a-f]+)(?: ([0-7]+))?$")
SUBPROJECT_COMMIT_PATTERN = re.compile(br"^[+-]Subproject commit ([0-9a-f]+)")
NULL_OBJECT_PREFIX = "0" * 7


def detach_single_hunk_patch(patch: SingleHunkPatch) -> SingleHunkPatch:
    """Return a patch whose line payload is independent of parser-owned buffers."""
    return SingleHunkPatch(
        old_path=patch.old_path,
        new_path=patch.new_path,
        lines=list(patch.lines),
    )


def _metadata_indicates_gitlink(metadata_lines: list[bytes]) -> bool:
    """Return whether diff metadata describes a mode-160000 entry."""
    for line in metadata_lines:
        if line in (
            b"new file mode 160000",
            b"deleted file mode 160000",
            b"old mode 160000",
            b"new mode 160000",
        ):
            return True
        match = INDEX_LINE_PATTERN.match(line)
        if match is not None and match.group(3) == b"160000":
            return True
    return False


def _gitlink_oids_from_index(metadata_lines: list[bytes]) -> tuple[str | None, str | None]:
    """Extract old and new object ids from a gitlink index line."""
    for line in metadata_lines:
        match = INDEX_LINE_PATTERN.match(line)
        if match is not None:
            return (
                match.group(1).decode("ascii"),
                match.group(2).decode("ascii"),
            )
    return None, None


def _non_null_git_oid(oid: str | None) -> str | None:
    """Return an object id unless it represents the null side of a diff."""
    if oid is None:
        return None
    if oid.startswith(NULL_OBJECT_PREFIX):
        return None
    return oid


def _gitlink_old_path(path: str, old_oid: str | None) -> str:
    """Return /dev/null for the old side of an added gitlink."""
    return "/dev/null" if _non_null_git_oid(old_oid) is None else path


def _gitlink_new_path(path: str, new_oid: str | None) -> str:
    """Return /dev/null for the new side of a deleted gitlink."""
    return "/dev/null" if _non_null_git_oid(new_oid) is None else path


def _gitlink_change_type(
    metadata_lines: list[bytes],
    old_oid: str | None,
    new_oid: str | None,
) -> str:
    """Derive added/modified/deleted from gitlink diff metadata."""
    if any(line == b"new file mode 160000" for line in metadata_lines):
        return "added"
    if any(line == b"deleted file mode 160000" for line in metadata_lines):
        return "deleted"
    if _non_null_git_oid(old_oid) is None:
        return "added"
    if _non_null_git_oid(new_oid) is None:
        return "deleted"
    return "modified"


def _consume_gitlink_hunks(
    next_line: Callable[[], bytes | None],
    peek_line: Callable[[], bytes | None],
) -> tuple[str | None, str | None]:
    """Consume all gitlink hunks for the current file and return full oids."""
    old_oid = None
    new_oid = None

    while True:
        next_l = peek_line()
        if next_l is None:
            break
        next_l_stripped = next_l.rstrip(b"\n")
        if next_l_stripped.startswith(b"diff --git "):
            break
        if next_l_stripped.startswith(b"---"):
            break

        next_line()
        if next_l_stripped.startswith(b"-Subproject commit "):
            match = SUBPROJECT_COMMIT_PATTERN.match(next_l_stripped)
            if match is not None:
                old_oid = match.group(1).decode("ascii")
        elif next_l_stripped.startswith(b"+Subproject commit "):
            match = SUBPROJECT_COMMIT_PATTERN.match(next_l_stripped)
            if match is not None:
                new_oid = match.group(1).decode("ascii")

    return old_oid, new_oid


class _UnifiedDiffParserBuildContext:
    """Own parser-created hunk buffers for a scoped unified diff parse."""

    def __init__(self, lines: Iterable[bytes]) -> None:
        self._lines = lines
        self._buffers: list[EditorBuffer] = []
        self._parser: Iterator[UnifiedDiffItem] | None = None
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        if self._parser is not None:
            self._parser.close()
            self._parser = None

        for buffer in self._buffers:
            buffer.close()
        self._buffers.clear()

    def __enter__(self) -> Iterator[UnifiedDiffItem]:
        if self._closed:
            raise ValueError("parser context is closed")
        if self._parser is not None:
            raise RuntimeError("parser context can only be entered once")
        self._parser = self._iter_owned()
        return self._parser

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _own_buffer(self, buffer: EditorBuffer) -> EditorBuffer:
        self._buffers.append(buffer)
        return buffer

    def _release_buffer(self, buffer: EditorBuffer) -> None:
        try:
            self._buffers.remove(buffer)
        except ValueError:
            return
        buffer.close()

    def _release_item(self, item: UnifiedDiffItem | None) -> None:
        if isinstance(item, SingleHunkPatch) and isinstance(item.lines, EditorBuffer):
            self._release_buffer(item.lines)

    def _iter_owned(self) -> Iterator[UnifiedDiffItem]:
        current_item: UnifiedDiffItem | None = None
        parser = self._parse()

        try:
            while True:
                self._release_item(current_item)
                current_item = None
                current_item = next(parser)
                yield current_item
        except StopIteration:
            return
        finally:
            self._release_item(current_item)
            parser.close()

    def _build_single_hunk_patch(
        self,
        *,
        old_path: str,
        new_path: str,
        lines: Iterable[bytes],
    ) -> SingleHunkPatch:
        return SingleHunkPatch(
            old_path=old_path,
            new_path=new_path,
            lines=self._own_buffer(EditorBuffer.from_chunks(lines)),
        )

    def _parse(self) -> Iterator[UnifiedDiffItem]:
        line_iter = iter(self._lines)
        lookahead = None  # One-line lookahead buffer

        def next_line():
            """Get next line, using lookahead if available."""
            nonlocal lookahead
            if lookahead is not None:
                line = lookahead
                lookahead = None
                return line
            try:
                return next(line_iter)
            except StopIteration:
                return None

        def peek_line():
            """Peek at next line without consuming it."""
            nonlocal lookahead
            if lookahead is None:
                try:
                    lookahead = next(line_iter)
                except StopIteration:
                    lookahead = None
            return lookahead

        def hunk_line_chunks(
            old_file_line: bytes,
            new_file_line: bytes,
            hunk_header_line: bytes,
        ) -> Iterator[bytes]:
            nonlocal lookahead

            yield old_file_line + b'\n'
            yield new_file_line + b'\n'
            yield hunk_header_line + b'\n'

            # Collect hunk body (lines starting with space, +, or -)
            while True:
                body_line = peek_line()
                if body_line is None:
                    # End of input
                    break

                body_line_stripped = body_line.rstrip(b'\n')

                if body_line_stripped.startswith(b"diff --git "):
                    # Next file starting
                    break
                if body_line_stripped.startswith(b"@@"):
                    # Next hunk for same file
                    break
                # Check for start of new file diff (---/+++)
                if body_line_stripped.startswith(b"---"):
                    # Peek ahead one more line to see if it's followed by +++
                    next_line()  # consume ---
                    peek_plus = peek_line()
                    if peek_plus and peek_plus.rstrip(b'\n').startswith(b"+++"):
                        # This is a new file diff, put --- back in lookahead
                        lookahead = body_line
                        break

                    # False alarm, this --- is part of the hunk body
                    # Add with \n terminator
                    yield body_line_stripped + b'\n'
                    continue

                # Include lines that are part of the hunk
                if body_line_stripped.startswith((b" ", b"+", b"-", b"\\")):
                    next_line()  # consume
                    # Add with \n terminator
                    yield body_line_stripped + b'\n'
                else:
                    # Unknown line, stop collecting this hunk
                    break

        try:
            while True:
                line = next_line()
                if line is None:
                    break

                # Strip only the diff format's \n terminator (preserve \r in content)
                line = line.rstrip(b'\n')

                # Look for start of a file diff
                if line.startswith(b"diff --git "):
                    # Extract file paths from the diff --git line
                    # Format: diff --git a/path b/path
                    rest = line[len(b"diff --git "):]

                    # Find a/ and b/ markers
                    a_start = rest.find(b"a/")
                    b_start = rest.find(b" b/")

                    if a_start == -1 or b_start == -1:
                        continue

                    # Paths in git are always valid UTF-8, decode them to str
                    old_path = rest[a_start + 2:b_start].decode('utf-8')
                    new_path = rest[b_start + 3:].decode('utf-8')  # Skip " b/"

                    # Collect metadata lines until we hit the --- line (start of unified diff)
                    # Files with no hunks (binary, mode-only, rename-only, empty) won't have --- line
                    metadata_lines = []
                    old_file_line = None
                    while True:
                        next_l = next_line()
                        if next_l is None:
                            # End of input - check for empty file before returning
                            break
                        next_l = next_l.rstrip(b'\n')
                        if next_l.startswith(b"---"):
                            old_file_line = next_l
                            break
                        # Collect metadata lines
                        metadata_lines.append(next_l)
                        # If we hit another diff header, this file has no hunks - check if it's an empty new file
                        if next_l.startswith(b"diff --git "):
                            # Put the line back for next iteration (with \n restored for consistency)
                            lookahead = next_l + b'\n'
                            break

                    is_gitlink = _metadata_indicates_gitlink(metadata_lines)
                    index_old_oid, index_new_oid = _gitlink_oids_from_index(metadata_lines)

                    # Handle files without unified diff hunks
                    if old_file_line is None:
                        if is_gitlink:
                            yield GitlinkChange(
                                old_path=_gitlink_old_path(old_path, index_old_oid),
                                new_path=_gitlink_new_path(new_path, index_new_oid),
                                old_oid=_non_null_git_oid(index_old_oid),
                                new_oid=_non_null_git_oid(index_new_oid),
                                change_type=_gitlink_change_type(
                                    metadata_lines,
                                    index_old_oid,
                                    index_new_oid,
                                ),
                            )
                            continue

                        # Check if this is a binary file
                        # Binary files have lines like "Binary files a/X and b/X differ"
                        is_binary = any(b"Binary files" in m for m in metadata_lines)

                        if is_binary:
                            # Yield binary file change
                            # Extract the binary file line to determine change type
                            binary_line = next(
                                (m for m in metadata_lines if b"Binary files" in m),
                                b"Binary files differ",
                            )

                            # Determine if it's a new, modified, or deleted binary file
                            is_new_binary = b"/dev/null" in binary_line and b"and b/" in binary_line
                            is_deleted_binary = b"a/" in binary_line and b"/dev/null" in binary_line

                            if is_new_binary:
                                change_type = "added"
                            elif is_deleted_binary:
                                change_type = "deleted"
                            else:
                                change_type = "modified"

                            yield BinaryFileChange(
                                old_path=old_path,
                                new_path=new_path,
                                change_type=change_type
                            )
                            continue

                        # Check if this is an empty new file (new file with no content)
                        # Empty new files have "new file mode" and "index 0000000..e69de29" (empty blob, short hash)
                        EMPTY_BLOB_SHORT_HASH = b"e69de29"  # Short hash for empty blob
                        is_new_file = any(b"new file mode" in m for m in metadata_lines)
                        is_empty = any(EMPTY_BLOB_SHORT_HASH in m for m in metadata_lines)

                        if is_new_file and is_empty:
                            # Yield empty new file as a special patch with synthetic hunk
                            # Create fake ---/+++ lines and an empty hunk header (with \n terminators)
                            yield self._build_single_hunk_patch(
                                old_path="/dev/null",
                                new_path=new_path,
                                lines=[
                                    b"--- /dev/null\n",
                                    f"+++ b/{new_path}\n".encode('utf-8'),
                                    b"@@ -0,0 +0,0 @@\n",
                                ],
                            )
                        # Skip other files without hunks (mode-only, rename-only, etc.)
                        continue

                    # Get +++ line
                    plus_line = next_line()
                    if plus_line is None:
                        return
                    plus_line_stripped = plus_line.rstrip(b'\n')
                    if not plus_line_stripped.startswith(b"+++"):
                        continue
                    new_file_line = plus_line_stripped

                    if is_gitlink:
                        hunk_old_oid, hunk_new_oid = _consume_gitlink_hunks(next_line, peek_line)
                        old_oid = hunk_old_oid or _non_null_git_oid(index_old_oid)
                        new_oid = hunk_new_oid or _non_null_git_oid(index_new_oid)
                        yield GitlinkChange(
                            old_path=_gitlink_old_path(old_path, old_oid or index_old_oid),
                            new_path=_gitlink_new_path(new_path, new_oid or index_new_oid),
                            old_oid=old_oid,
                            new_oid=new_oid,
                            change_type=_gitlink_change_type(
                                metadata_lines,
                                old_oid or index_old_oid,
                                new_oid or index_new_oid,
                            ),
                        )
                        continue

                    # Process all hunks for this file
                    has_hunks = False
                    while True:
                        # Check if next line is a hunk header
                        hunk_header_line = peek_line()
                        if hunk_header_line is None:
                            return
                        hunk_header_stripped = hunk_header_line.rstrip(b'\n')
                        if not hunk_header_stripped.startswith(b"@@"):
                            # No more hunks for this file
                            break

                        has_hunks = True

                        # Consume the hunk header
                        next_line()

                        # Yield this hunk immediately
                        yield self._build_single_hunk_patch(
                            old_path=old_path,
                            new_path=new_path,
                            lines=hunk_line_chunks(
                                old_file_line,
                                new_file_line,
                                hunk_header_stripped,
                            ),
                        )

                    # If we got --- and +++ but no hunks, check if it's an empty new file
                    if not has_hunks:
                        # Check if this is an empty new file (new file with no content)
                        # Empty new files have "new file mode" and "index 0000000..e69de29" (empty blob)
                        EMPTY_BLOB_SHORT_HASH = b"e69de29"
                        is_new_file = any(b"new file mode" in m for m in metadata_lines)
                        is_empty = any(EMPTY_BLOB_SHORT_HASH in m for m in metadata_lines)

                        if is_new_file and is_empty:
                            # Yield empty new file as a special patch with synthetic hunk
                            yield self._build_single_hunk_patch(
                                old_path=old_path,
                                new_path=new_path,
                                lines=[
                                    old_file_line + b'\n',
                                    new_file_line + b'\n',
                                    b"@@ -0,0 +0,0 @@\n",
                                ],
                            )
        finally:
            close = getattr(line_iter, "close", None)
            if close is not None:
                close()


def acquire_unified_diff(lines: Iterable[bytes]) -> _UnifiedDiffParserBuildContext:
    """Acquire a scoped unified diff parser with parser-owned hunk buffers."""
    return _UnifiedDiffParserBuildContext(lines)


def parse_unified_diff_streaming(
    lines: Iterable[bytes],
) -> Iterator[UnifiedDiffItem]:
    """Parse a unified diff and yield detached patches and binary changes.

    This compatibility parser returns SingleHunkPatch values whose lines remain
    usable after iteration advances. Use acquire_unified_diff() for scoped
    EditorBuffer-backed hunk payloads.
    """
    context = acquire_unified_diff(lines)
    with context as items:
        for item in items:
            if isinstance(item, SingleHunkPatch):
                detached_item = detach_single_hunk_patch(item)
                if isinstance(item.lines, EditorBuffer):
                    context._release_buffer(item.lines)
                yield detached_item
            else:
                yield item


def write_snapshots_for_selected_file_path(file_path: str) -> None:
    """Write snapshots of the file from both the index and working tree."""
    with ExitStack() as stack:
        index_version = load_git_object_as_buffer(f":{file_path}")
        if index_version is None:
            index_version = EditorBuffer.from_bytes(b"")
        stack.enter_context(index_version)

        repo_root = get_git_repository_root_path()
        file_full_path = repo_root / file_path
        if file_full_path.exists():
            working_tree_version = EditorBuffer.from_path(file_full_path)
        else:
            working_tree_version = EditorBuffer.from_bytes(b"")
        stack.enter_context(working_tree_version)

        # When index is empty but working tree has content, check if file exists in HEAD.
        # For new files (not in HEAD), use empty index snapshot.
        # For existing files with intent-to-add applied, use HEAD content.
        if buffer_byte_count(index_version) == 0 and buffer_byte_count(working_tree_version) > 0:
            head_version = load_git_object_as_buffer(f"HEAD:{file_path}")
            if head_version is not None:
                if buffer_byte_count(head_version) > 0:
                    index_version = stack.enter_context(head_version)
                else:
                    head_version.close()

        write_buffer_to_path(get_index_snapshot_file_path(), index_version)
        write_buffer_to_path(get_working_tree_snapshot_file_path(), working_tree_version)

        log_journal(
            "write_snapshots_for_selected_file",
            file_path=file_path,
            index_len=buffer_byte_count(index_version),
            index_lines=_buffer_line_count(index_version),
            index_preview=(
                buffer_preview(index_version)
                if buffer_byte_count(index_version) > 0 else
                "(empty)"
            ),
            working_tree_len=buffer_byte_count(working_tree_version),
            working_tree_lines=_buffer_line_count(working_tree_version),
        )


def _buffer_line_count(buffer: EditorBuffer) -> int:
    """Return a line count for journal metadata without materializing content."""
    line_breaks = 0
    seen_data = False
    pending_cr = False
    last_byte: int | None = None

    for chunk in buffer.byte_chunks():
        if not chunk:
            continue

        seen_data = True
        chunk_breaks = chunk.count(b"\n") + chunk.count(b"\r") - chunk.count(b"\r\n")
        if pending_cr and chunk.startswith(b"\n"):
            chunk_breaks -= 1

        line_breaks += chunk_breaks
        pending_cr = chunk.endswith(b"\r")
        last_byte = chunk[-1]

    if not seen_data:
        return 0
    if last_byte in (ord("\n"), ord("\r")):
        return line_breaks
    return line_breaks + 1


def build_line_changes_from_patch_lines(
    patch_lines: Iterable[bytes],
    *,
    annotator: LineLevelChangeAnnotator | None = None,
) -> LineLevelChange:
    """Parse single-hunk patch lines into a LineLevelChange structure.

    Args:
        patch_lines: Unified diff patch lines for a single hunk
        annotator: Optional function to enrich LineLevelChange with provenance metadata
                   (e.g., batch source alignment, 3-way merge base). If None, source_line
                   fields remain None (no provenance).

    Returns:
        LineLevelChange object with parsed line entries and IDs
    """
    path_value = ""
    old_path_value = ""
    new_path_value = ""
    hunk_header: HunkHeader | None = None
    old_line_number = 0
    new_line_number = 0
    next_display_id = 0
    line_entries: list[LineEntry] = []

    # Preserve line endings so a parsed hunk can be emitted unchanged.
    for line_with_ending in patch_lines:
        # Strip only \n for comparison (preserve \r in content)
        line = line_with_ending.rstrip(b'\n')

        if line.startswith(b"--- "):
            # Decode path to str (paths are always UTF-8 in git)
            old_path_value = line.split(b" ", 1)[1].strip().decode('utf-8')
            if old_path_value != "/dev/null" and old_path_value.startswith("a/"):
                old_path_value = old_path_value[2:]
        elif line.startswith(b"+++ "):
            new_path_value = line.split(b" ", 1)[1].strip().decode('utf-8')
            if new_path_value != "/dev/null" and new_path_value.startswith("b/"):
                new_path_value = new_path_value[2:]
        elif line.startswith(b"@@ "):
            captured_header_line = line.decode('utf-8', errors='replace')
            header_match = HUNK_HEADER_PATTERN.match(captured_header_line)
            if not header_match:
                exit_with_error(f"Bad hunk header: {captured_header_line}")

            old_start = int(header_match.group(1))
            old_length = int(header_match.group(2) or "1")
            new_start = int(header_match.group(3))
            new_length = int(header_match.group(4) or "1")
            hunk_header = HunkHeader(old_start, old_length, new_start, new_length)
            old_line_number = old_start
            new_line_number = new_start
            next_display_id = 0
        elif hunk_header is not None:
            if line.startswith(b"\\ No newline at end of file"):
                if line_entries:
                    line_entries[-1].has_trailing_newline = False
                continue
            if not line:
                sign = " "
                text_bytes = b""
            else:
                sign = line[0:1].decode('ascii')  # +, -, or space (always ASCII)
                text_bytes = line[1:]  # Canonical bytes (without +/- prefix)

            # Decode for display (with replacement for non-UTF-8)
            text = text_bytes.decode('utf-8', errors='replace')

            if sign == " ":
                # Context line
                line_entries.append(LineEntry(
                    id=None,
                    kind=" ",
                    old_line_number=old_line_number,
                    new_line_number=new_line_number,
                    text_bytes=text_bytes,
                    text=text,
                    source_line=None,
                ))
                old_line_number += 1
                new_line_number += 1
            elif sign == "-":
                next_display_id += 1
                # Deletion: doesn't exist in working tree
                line_entries.append(LineEntry(
                    id=next_display_id,
                    kind="-",
                    old_line_number=old_line_number,
                    new_line_number=None,
                    text_bytes=text_bytes,
                    text=text,
                    source_line=None,
                ))
                old_line_number += 1
            elif sign == "+":
                next_display_id += 1
                # Addition: exists in working tree
                line_entries.append(LineEntry(
                    id=next_display_id,
                    kind="+",
                    old_line_number=None,
                    new_line_number=new_line_number,
                    text_bytes=text_bytes,
                    text=text,
                    source_line=None,
                ))
                new_line_number += 1
            else:
                # Treat as context line
                line_entries.append(LineEntry(
                    id=None,
                    kind=" ",
                    old_line_number=old_line_number,
                    new_line_number=new_line_number,
                    text_bytes=text_bytes,
                    text=text,
                    source_line=None,
                ))
                old_line_number += 1
                new_line_number += 1

    if new_path_value and new_path_value != "/dev/null":
        path_value = new_path_value
    elif old_path_value and old_path_value != "/dev/null":
        path_value = old_path_value
    else:
        path_value = new_path_value or old_path_value or ""

    if hunk_header is None:
        exit_with_error(_("Failed to parse hunk header."))

    line_changes = LineLevelChange(path=path_value, header=hunk_header, lines=line_entries)

    # Apply annotator hook if provided
    if annotator is not None:
        line_changes = annotator(path_value, line_changes)

    return line_changes
