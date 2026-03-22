"""Parse unified diff format into structured models."""

from __future__ import annotations

from typing import Callable, Iterable, Iterator, Optional

from .models import SingleHunkPatch
from ..utils.file_io import read_text_file_contents, write_text_file_contents
from ..utils.git import get_git_repository_root_path, run_git_command
from ..utils.paths import get_index_snapshot_file_path, get_working_tree_snapshot_file_path


def parse_unified_diff_streaming(lines: Iterable[str]) -> Iterator[SingleHunkPatch]:
    """Parse a unified diff from a line iterator, yielding patches one at a time.

    This is the core streaming parser that accepts any iterable of lines
    (file, subprocess pipe, list, etc.) and yields SingleHunkPatch objects
    as they are parsed. Callers can stop iterating early to avoid parsing
    the entire diff.

    Args:
        lines: Iterable of diff lines (e.g., file.readlines(), proc.stdout, list)

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

        # Strip trailing newlines for consistency
        line = line.rstrip('\n\r')

        # Look for start of a file diff
        if line.startswith("diff --git "):
            # Extract file paths from the diff --git line
            # Format: diff --git a/path b/path
            # Need to handle paths with spaces, so can't just split()
            rest = line[len("diff --git "):]

            # Find a/ and b/ markers
            a_start = rest.find("a/")
            b_start = rest.find(" b/")

            if a_start == -1 or b_start == -1:
                continue

            old_path = rest[a_start + 2:b_start]
            new_path = rest[b_start + 3:]  # Skip " b/"

            # Collect lines until we hit the --- line (start of unified diff)
            while True:
                next_l = next_line()
                if next_l is None:
                    return
                next_l = next_l.rstrip('\n\r')
                if next_l.startswith("---"):
                    old_file_line = next_l
                    break

            # Get +++ line
            plus_line = next_line()
            if plus_line is None:
                return
            plus_line = plus_line.rstrip('\n\r')
            if not plus_line.startswith("+++"):
                continue
            new_file_line = plus_line

            # Process all hunks for this file
            while True:
                # Check if next line is a hunk header
                hunk_header = peek_line()
                if hunk_header is None:
                    return
                hunk_header = hunk_header.rstrip('\n\r')
                if not hunk_header.startswith("@@"):
                    # No more hunks for this file
                    break

                # Consume the hunk header
                next_line()

                hunk_lines = [old_file_line, new_file_line, hunk_header]

                # Collect hunk body (lines starting with space, +, or -)
                while True:
                    body_line = peek_line()
                    if body_line is None:
                        # End of input
                        break

                    body_line_stripped = body_line.rstrip('\n\r')

                    if body_line_stripped.startswith("diff --git "):
                        # Next file starting
                        break
                    if body_line_stripped.startswith("@@"):
                        # Next hunk for same file
                        break
                    # Check for start of new file diff (---/+++)
                    if body_line_stripped.startswith("---"):
                        # Peek ahead one more line to see if it's followed by +++
                        next_line()  # consume ---
                        peek_plus = peek_line()
                        if peek_plus and peek_plus.rstrip('\n\r').startswith("+++"):
                            # This is a new file diff, put --- back in lookahead
                            lookahead = body_line
                            break
                        else:
                            # False alarm, this --- is part of the hunk body
                            hunk_lines.append(body_line_stripped)
                            continue

                    # Include lines that are part of the hunk
                    if body_line_stripped.startswith((" ", "+", "-", "\\")):
                        next_line()  # consume
                        hunk_lines.append(body_line_stripped)
                    else:
                        # Unknown line, stop collecting this hunk
                        break

                # Yield this hunk immediately
                yield SingleHunkPatch(
                    old_path=old_path,
                    new_path=new_path,
                    lines=hunk_lines
                )


def parse_unified_diff_into_single_hunk_patches(diff_text: str) -> list[SingleHunkPatch]:
    """Parse a unified diff into separate single-hunk patches.

    This is a convenience wrapper around parse_unified_diff_streaming that
    takes a string and returns a list. For large diffs or early termination,
    use parse_unified_diff_streaming directly.

    Args:
        diff_text: Output from `git diff` in unified format

    Returns:
        List of SingleHunkPatch objects, one per hunk
    """
    return list(parse_unified_diff_streaming(diff_text.splitlines()))


def write_snapshots_for_current_file_path(file_path: str) -> None:
    """Write snapshots of the file from both the index and working tree."""
    try:
        index_version = run_git_command(["show", f":{file_path}"], check=True).stdout
    except Exception:
        index_version = ""
    write_text_file_contents(get_index_snapshot_file_path(), index_version)

    repo_root = get_git_repository_root_path()
    file_full_path = repo_root / file_path
    if file_full_path.exists():
        working_tree_version = read_text_file_contents(file_full_path)
    else:
        working_tree_version = ""
    write_text_file_contents(get_working_tree_snapshot_file_path(), working_tree_version)


def get_first_matching_file_from_diff(
    context_lines: int,
    predicate: Optional[Callable[[str], bool]] = None
) -> Optional[str]:
    """Stream git diff and find the first file with a hunk matching the predicate.

    Args:
        context_lines: Number of context lines for diff (-U parameter)
        predicate: Optional function that takes patch text and returns True if
                   the hunk counts as a match. If None, returns first file.

    Returns:
        File path if a matching file is found, None otherwise
    """
    from ..utils.git import stream_git_command

    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{context_lines}", "--no-color"])):
        if predicate is None:
            return patch.new_path

        patch_text = patch.to_patch_text()
        if predicate(patch_text):
            return patch.new_path

    return None
