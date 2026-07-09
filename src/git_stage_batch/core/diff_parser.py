"""Parse unified diff format into structured models."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Iterable, Iterator, Union

from . import binary_diff as _binary_diff
from . import gitlink_diff as _gitlink_diff
from .buffer import LineBuffer
from .models import (
    BinaryFileChange,
    GitlinkChange,
    LineLevelChange,
    HunkHeader,
    LineEntry,
    RenameChange,
    SingleHunkPatch,
    TextFileDeletionChange,
)
from ..exceptions import CommandError
from ..i18n import _


# Type for annotator hooks that enrich LineLevelChange with additional metadata
LineLevelChangeAnnotator = Callable[[str, LineLevelChange], LineLevelChange]
UnifiedDiffItem = Union[SingleHunkPatch, BinaryFileChange, GitlinkChange, RenameChange, TextFileDeletionChange]


HUNK_HEADER_PATTERN = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@")


def patch_is_file_deletion(patch_lines: Iterable[bytes]) -> bool:
    """Return whether patch lines target a deleted file path."""
    return any(line.rstrip(b"\n") == b"+++ /dev/null" for line in patch_lines)


def patch_is_new_file(patch_lines: Iterable[bytes]) -> bool:
    """Return whether patch lines target a newly added file path."""
    return any(line.rstrip(b"\n") == b"--- /dev/null" for line in patch_lines)


def patch_is_empty_file_change(patch_lines: Iterable[bytes]) -> bool:
    """Return whether patch lines describe a synthetic empty-file change."""
    return any(line.rstrip(b"\n") == b"@@ -0,0 +0,0 @@" for line in patch_lines)


def _metadata_indicates_rename(metadata_lines: list[bytes]) -> bool:
    """Return whether diff metadata describes a path rename."""
    has_rename_from = any(line.startswith(b"rename from ") for line in metadata_lines)
    has_rename_to = any(line.startswith(b"rename to ") for line in metadata_lines)
    return has_rename_from and has_rename_to


def _metadata_indicates_deleted_file(metadata_lines: list[bytes]) -> bool:
    """Return whether diff metadata describes a deleted file."""
    return any(line.startswith(b"deleted file mode ") for line in metadata_lines)


class _UnifiedDiffParserBuildContext:
    """Own parser-created hunk buffers for a scoped unified diff parse."""

    def __init__(self, lines: Iterable[bytes]) -> None:
        self._lines = lines
        self._buffers: list[LineBuffer] = []
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

    def _own_buffer(self, buffer: LineBuffer) -> LineBuffer:
        self._buffers.append(buffer)
        return buffer

    def _release_buffer(self, buffer: LineBuffer) -> None:
        try:
            self._buffers.remove(buffer)
        except ValueError:
            return
        buffer.close()

    def _release_item(self, item: UnifiedDiffItem | None) -> None:
        if isinstance(item, SingleHunkPatch) and isinstance(item.lines, LineBuffer):
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
            lines=self._own_buffer(LineBuffer.from_chunks(lines)),
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

                    is_gitlink = _gitlink_diff.metadata_indicates_gitlink(
                        metadata_lines
                    )
                    is_rename = _metadata_indicates_rename(metadata_lines)
                    is_deleted_file = _metadata_indicates_deleted_file(metadata_lines)
                    index_old_oid, index_new_oid = (
                        _gitlink_diff.gitlink_oids_from_index(metadata_lines)
                    )

                    # Handle files without unified diff hunks
                    if old_file_line is None:
                        if is_rename:
                            yield RenameChange(old_path=old_path, new_path=new_path)

                        if is_gitlink:
                            yield GitlinkChange(
                                old_path=_gitlink_diff.gitlink_old_path(
                                    old_path,
                                    index_old_oid,
                                ),
                                new_path=_gitlink_diff.gitlink_new_path(
                                    new_path,
                                    index_new_oid,
                                ),
                                old_oid=_gitlink_diff.non_null_git_oid(index_old_oid),
                                new_oid=_gitlink_diff.non_null_git_oid(index_new_oid),
                                change_type=_gitlink_diff.gitlink_change_type(
                                    metadata_lines,
                                    index_old_oid,
                                    index_new_oid,
                                ),
                            )
                            continue

                        if _binary_diff.metadata_indicates_binary_file(metadata_lines):
                            yield BinaryFileChange(
                                old_path=old_path,
                                new_path=new_path,
                                change_type=_binary_diff.binary_change_type(
                                    metadata_lines
                                ),
                            )
                            continue

                        if is_rename:
                            continue

                        if is_deleted_file:
                            yield TextFileDeletionChange(old_path=old_path)
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

                    if is_rename:
                        yield RenameChange(old_path=old_path, new_path=new_path)

                    if is_gitlink:
                        hunk_old_oid, hunk_new_oid = (
                            _gitlink_diff.consume_gitlink_hunks(
                                next_line,
                                peek_line,
                            )
                        )
                        old_oid = hunk_old_oid or _gitlink_diff.non_null_git_oid(
                            index_old_oid
                        )
                        new_oid = hunk_new_oid or _gitlink_diff.non_null_git_oid(
                            index_new_oid
                        )
                        if old_oid is not None and old_oid == new_oid:
                            continue
                        yield GitlinkChange(
                            old_path=_gitlink_diff.gitlink_old_path(
                                old_path,
                                old_oid or index_old_oid,
                            ),
                            new_path=_gitlink_diff.gitlink_new_path(
                                new_path,
                                new_oid or index_new_oid,
                            ),
                            old_oid=old_oid,
                            new_oid=new_oid,
                            change_type=_gitlink_diff.gitlink_change_type(
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

                        patch_lines: Iterable[bytes] = hunk_line_chunks(
                            old_file_line,
                            new_file_line,
                            hunk_header_stripped,
                        )
                        subproject_oids = None
                        if not is_gitlink and not metadata_lines:
                            patch_lines = list(patch_lines)
                            subproject_oids = (
                                _gitlink_diff.gitlink_oids_from_subproject_commit_patch(
                                    patch_lines
                                )
                            )
                        if subproject_oids is not None:
                            old_oid, new_oid = subproject_oids
                            if old_oid is not None and old_oid == new_oid:
                                continue
                            yield GitlinkChange(
                                old_path=_gitlink_diff.gitlink_old_path(
                                    old_path,
                                    old_oid,
                                ),
                                new_path=_gitlink_diff.gitlink_new_path(
                                    new_path,
                                    new_oid,
                                ),
                                old_oid=old_oid,
                                new_oid=new_oid,
                                change_type=_gitlink_diff.gitlink_change_type(
                                    metadata_lines,
                                    old_oid,
                                    new_oid,
                                ),
                            )
                            continue

                        # Yield this hunk immediately
                        yield self._build_single_hunk_patch(
                            old_path=old_path,
                            new_path=new_path,
                            lines=patch_lines,
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
                        elif is_deleted_file:
                            yield TextFileDeletionChange(old_path=old_path)
        finally:
            close = getattr(line_iter, "close", None)
            if close is not None:
                close()


def acquire_unified_diff(lines: Iterable[bytes]) -> _UnifiedDiffParserBuildContext:
    """Acquire a scoped unified diff parser with parser-owned hunk buffers."""
    return _UnifiedDiffParserBuildContext(lines)


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
                raise CommandError(f"Bad hunk header: {captured_header_line}")

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

            if sign == " ":
                # Context line
                line_entries.append(LineEntry(
                    id=None,
                    kind=" ",
                    old_line_number=old_line_number,
                    new_line_number=new_line_number,
                    text_bytes=text_bytes,
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
        raise CommandError(_("Failed to parse hunk header."))

    line_changes = LineLevelChange(path=path_value, header=hunk_header, lines=line_entries)

    # Apply annotator hook if provided
    if annotator is not None:
        line_changes = annotator(path_value, line_changes)

    return line_changes
