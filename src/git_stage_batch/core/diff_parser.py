"""Parse unified diff format into structured models."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Iterable, Iterator, Optional

from .models import CurrentLines, HunkHeader, LineEntry, SingleHunkPatch
from ..exceptions import exit_with_error
from ..i18n import _
from ..utils.file_io import read_text_file_contents, write_text_file_contents
from ..utils.git import get_git_repository_root_path, run_git_command
from ..utils.journal import log_journal
from ..utils.paths import get_index_snapshot_file_path, get_working_tree_snapshot_file_path


# Type for annotator hooks that enrich CurrentLines with additional metadata
CurrentLinesAnnotator = Callable[[str, CurrentLines], CurrentLines]


HUNK_HEADER_PATTERN = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@")


def parse_unified_diff_streaming(lines: Iterable[bytes]) -> Iterator[SingleHunkPatch]:
    """Parse a unified diff from a byte line iterator, yielding patches one at a time.

    This is the core streaming parser that accepts any iterable of byte lines
    (from bytes_to_lines(), list[bytes], etc.) and yields SingleHunkPatch objects
    as they are parsed. Callers can stop iterating early to avoid parsing
    the entire diff.

    Args:
        lines: Iterable of diff lines as bytes (each line includes its \\n terminator)

    Yields:
        SingleHunkPatch objects, one per hunk
    """
    line_iter = iter(lines)
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
            # Need to handle paths with spaces, so can't just split()
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

            # Handle files without unified diff hunks
            if old_file_line is None:
                # Check if this is an empty new file (new file with no content)
                # Empty new files have "new file mode" and "index 0000000..e69de29" (empty blob, short hash)
                EMPTY_BLOB_SHORT_HASH = b"e69de29"  # Short hash for empty blob
                is_new_file = any(b"new file mode" in m for m in metadata_lines)
                is_empty = any(EMPTY_BLOB_SHORT_HASH in m for m in metadata_lines)

                if is_new_file and is_empty:
                    # Yield empty new file as a special patch with synthetic hunk
                    # Create fake ---/+++ lines and an empty hunk header (with \n terminators)
                    yield SingleHunkPatch(
                        old_path="/dev/null",
                        new_path=new_path,
                        lines=[
                            b"--- /dev/null\n",
                            f"+++ b/{new_path}\n".encode('utf-8'),
                            b"@@ -0,0 +0,0 @@\n",
                        ]
                    )
                # Skip other files without hunks (binary, mode-only, rename-only, etc.)
                continue

            # Get +++ line
            plus_line = next_line()
            if plus_line is None:
                return
            plus_line_stripped = plus_line.rstrip(b'\n')
            if not plus_line_stripped.startswith(b"+++"):
                continue
            new_file_line = plus_line_stripped

            # Process all hunks for this file
            while True:
                # Check if next line is a hunk header
                hunk_header_line = peek_line()
                if hunk_header_line is None:
                    return
                hunk_header_stripped = hunk_header_line.rstrip(b'\n')
                if not hunk_header_stripped.startswith(b"@@"):
                    # No more hunks for this file
                    break

                # Consume the hunk header
                next_line()

                # Build hunk with \n terminators for proper round-tripping
                hunk_lines = [
                    old_file_line + b'\n',
                    new_file_line + b'\n',
                    hunk_header_stripped + b'\n'
                ]

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
                        else:
                            # False alarm, this --- is part of the hunk body
                            # Add with \n terminator
                            hunk_lines.append(body_line_stripped + b'\n')
                            continue

                    # Include lines that are part of the hunk
                    if body_line_stripped.startswith((b" ", b"+", b"-", b"\\")):
                        next_line()  # consume
                        # Add with \n terminator
                        hunk_lines.append(body_line_stripped + b'\n')
                    else:
                        # Unknown line, stop collecting this hunk
                        break

                # Yield this hunk immediately
                yield SingleHunkPatch(
                    old_path=old_path,
                    new_path=new_path,
                    lines=hunk_lines
                )


def parse_unified_diff_into_single_hunk_patches(diff_bytes: bytes) -> list[SingleHunkPatch]:
    """Parse a unified diff into separate single-hunk patches.

    This is a convenience wrapper around parse_unified_diff_streaming that
    takes bytes and returns a list. For large diffs or early termination,
    use parse_unified_diff_streaming directly.

    Args:
        diff_bytes: Output from `git diff` in unified format as bytes

    Returns:
        List of SingleHunkPatch objects, one per hunk
    """
    # Use splitlines(keepends=True) to preserve line terminators
    return list(parse_unified_diff_streaming(diff_bytes.splitlines(keepends=True)))


def write_snapshots_for_current_file_path(file_path: str) -> None:
    """Write snapshots of the file from both the index and working tree."""
    try:
        index_version = run_git_command(["show", f":{file_path}"], check=True, text_output=False).stdout
    except Exception:
        index_version = b""

    repo_root = get_git_repository_root_path()
    file_full_path = repo_root / file_path
    if file_full_path.exists():
        working_tree_version = file_full_path.read_bytes()
    else:
        working_tree_version = b""

    # If index appears empty but working tree has content, this could be intent-to-add
    # or another edge case. Check if the file exists in HEAD as a safer baseline.
    # For new files (not in HEAD), we keep the empty index snapshot (correct behavior).
    # For existing files with intent-to-add applied, we use HEAD content.
    if not index_version and working_tree_version:
        head_check = run_git_command(["cat-file", "-e", f"HEAD:{file_path}"], check=False)
        if head_check.returncode == 0:
            # File exists in HEAD, use HEAD content as safer baseline than empty index
            head_version = run_git_command(["show", f"HEAD:{file_path}"], check=False, text_output=False).stdout
            if head_version:
                index_version = head_version

    # Write snapshots as bytes
    get_index_snapshot_file_path().write_bytes(index_version)
    get_working_tree_snapshot_file_path().write_bytes(working_tree_version)

    log_journal(
        "write_snapshots_for_current_file",
        file_path=file_path,
        index_len=len(index_version),
        index_lines=len(index_version.splitlines()) if index_version else 0,
        index_preview=index_version[:200] if index_version else "(empty)",
        working_tree_len=len(working_tree_version),
        working_tree_lines=len(working_tree_version.splitlines()) if working_tree_version else 0
    )


def get_first_matching_file_from_diff(
    context_lines: int,
    predicate: Optional[Callable[[bytes], bool]] = None
) -> Optional[str]:
    """Stream git diff and find the first file with a hunk matching the predicate.

    Args:
        context_lines: Number of context lines for diff (-U parameter)
        predicate: Optional function that takes patch bytes and returns True if
                   the hunk counts as a match. If None, returns first file.

    Returns:
        File path if a matching file is found, None otherwise
    """
    from ..utils.git import stream_git_command

    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{context_lines}", "--no-color"])):
        if predicate is None:
            return patch.new_path

        # Get patch bytes for predicate
        patch_bytes = patch.to_patch_bytes()
        if predicate(patch_bytes):
            return patch.new_path

    return None


def build_current_lines_from_patch_bytes(
    patch_bytes: bytes,
    *,
    annotator: CurrentLinesAnnotator | None = None,
) -> CurrentLines:
    """Parse a single-hunk patch into a CurrentLines structure.

    Args:
        patch_bytes: Unified diff patch bytes for a single hunk
        annotator: Optional function to enrich CurrentLines with provenance metadata
                   (e.g., batch source alignment, 3-way merge base). If None, source_line
                   fields remain None (no provenance).

    Returns:
        CurrentLines object with parsed line entries and IDs
    """
    path_value = ""
    old_path_value = ""
    new_path_value = ""
    captured_header_line_bytes = b""
    body_lines_bytes: list[bytes] = []

    # Split preserving line endings - critical for round-tripping
    for line_with_ending in patch_bytes.splitlines(keepends=True):
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
            captured_header_line_bytes = line
            body_lines_bytes.append(line)
        else:
            if captured_header_line_bytes:
                body_lines_bytes.append(line)

    if new_path_value and new_path_value != "/dev/null":
        path_value = new_path_value
    elif old_path_value and old_path_value != "/dev/null":
        path_value = old_path_value
    else:
        path_value = new_path_value or old_path_value or ""

    if not captured_header_line_bytes:
        exit_with_error(_("Failed to parse hunk header."))

    # Decode header to str for regex matching
    captured_header_line = captured_header_line_bytes.decode('utf-8', errors='replace')
    header_match = HUNK_HEADER_PATTERN.match(captured_header_line)
    if not header_match:
        exit_with_error(f"Bad hunk header: {captured_header_line}")

    old_start = int(header_match.group(1))
    old_length = int(header_match.group(2) or "1")
    new_start = int(header_match.group(3))
    new_length = int(header_match.group(4) or "1")
    hunk_header = HunkHeader(old_start, old_length, new_start, new_length)

    line_entries: list[LineEntry] = []
    old_line_number = old_start
    new_line_number = new_start
    next_display_id = 0

    for raw in body_lines_bytes[1:]:  # skip header
        if raw.startswith(b"\\ No newline at end of file"):
            continue
        if not raw:
            sign = " "
            text_bytes = b""
        else:
            sign = raw[0:1].decode('ascii')  # +, -, or space (always ASCII)
            text_bytes = raw[1:]  # Canonical bytes (without +/- prefix)

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

    current_lines = CurrentLines(path=path_value, header=hunk_header, lines=line_entries)

    # Apply annotator hook if provided
    if annotator is not None:
        current_lines = annotator(path_value, current_lines)

    return current_lines
