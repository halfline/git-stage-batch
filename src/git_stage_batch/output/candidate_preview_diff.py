"""Diff rendering for operation candidate previews."""

from __future__ import annotations

import difflib
from collections.abc import Iterable, Iterator, Sequence
from itertools import chain

from ..core.buffer import LineBuffer
from ..core.diff_parser import build_line_changes_from_patch_lines
from . import candidate_preview_snippets
from .colors import Colors


def render_candidate_buffer_diff(
    file_path: str,
    before_buffer: LineBuffer,
    after_buffer: LineBuffer,
    *,
    label_before: str,
    label_after: str,
    context_lines: int,
) -> str:
    """Render a unified diff between two candidate buffers."""
    with _candidate_buffer_diff(
        file_path,
        before_buffer,
        after_buffer,
        label_before=label_before,
        label_after=label_after,
        context_lines=context_lines,
    ) as diff_buffer:
        return "".join(
            line.decode("utf-8", errors="surrogateescape")
            for line in diff_buffer
        )


def _candidate_buffer_diff(
    file_path: str,
    before_buffer: LineBuffer,
    after_buffer: LineBuffer,
    *,
    label_before: str,
    label_after: str,
    context_lines: int,
) -> LineBuffer:
    """Build a mapped line buffer containing one candidate diff."""
    return LineBuffer.from_chunks(
        _unified_diff_lines(
            before_buffer,
            after_buffer,
            fromfile=f"{label_before}/{file_path}".encode(),
            tofile=f"{label_after}/{file_path}".encode(),
            context_lines=context_lines,
        )
    )


def _format_unified_range(start: int, stop: int) -> str:
    beginning = start + 1
    length = stop - start
    if length == 1:
        return str(beginning)
    if length == 0:
        beginning -= 1
    return f"{beginning},{length}"


def _unified_diff_lines(
    before_buffer: LineBuffer,
    after_buffer: LineBuffer,
    *,
    fromfile: bytes,
    tofile: bytes,
    context_lines: int,
) -> Iterator[bytes]:
    """Yield a unified diff while retaining file content in line buffers."""
    with (
        before_buffer.acquire_lines() as before_lines,
        after_buffer.acquire_lines() as after_lines,
    ):
        matcher = difflib.SequenceMatcher(None, before_lines, after_lines)
        groups = matcher.get_grouped_opcodes(context_lines)
        started = False
        for group in groups:
            if not started:
                started = True
                yield b"--- " + fromfile + b"\n"
                yield b"+++ " + tofile + b"\n"
            first, last = group[0], group[-1]
            old_range = _format_unified_range(first[1], last[2]).encode()
            new_range = _format_unified_range(first[3], last[4]).encode()
            yield b"@@ -" + old_range + b" +" + new_range + b" @@\n"
            for tag, old_start, old_end, new_start, new_end in group:
                if tag == "equal":
                    for index in range(old_start, old_end):
                        yield b" " + bytes(before_lines[index])
                if tag in {"replace", "delete"}:
                    for index in range(old_start, old_end):
                        yield b"-" + bytes(before_lines[index])
                if tag in {"replace", "insert"}:
                    for index in range(new_start, new_end):
                        yield b"+" + bytes(after_lines[index])


def _candidate_diff_hunks(
    diff_lines: Sequence[bytes],
) -> Iterator[Iterable[bytes]]:
    headers: list[bytes] = []
    current_hunk_start: int | None = None
    for index in range(len(diff_lines)):
        line = diff_lines[index]
        if line.startswith((b"--- ", b"+++ ")):
            headers.append(line)
            continue
        if line.startswith(b"@@ "):
            if current_hunk_start is not None:
                yield chain(headers, diff_lines[current_hunk_start:index])
            current_hunk_start = index

    if current_hunk_start is not None:
        yield chain(headers, diff_lines[current_hunk_start:])


def _candidate_line_number(line) -> int | None:
    if line.kind == "+":
        return line.new_line_number
    return line.old_line_number if line.old_line_number is not None else line.new_line_number


def _print_candidate_line_changes(
    line_changes,
    *,
    ambiguity_target_line_range: tuple[int, int] | None,
) -> None:
    use_color = Colors.enabled()
    header = line_changes.header
    header_part = f"@@ -{header.old_start},{header.old_len} +{header.new_start},{header.new_len} @@"
    if use_color:
        print(f"{Colors.BOLD}{line_changes.path}{Colors.RESET} :: {Colors.CYAN}{header_part}{Colors.RESET}")
    else:
        print(f"{line_changes.path} :: {header_part}")

    line_numbers = [
        line_number
        for line_number in (_candidate_line_number(line) for line in line_changes.lines)
        if line_number is not None
    ]
    width = max((len(str(line_number)) for line_number in line_numbers), default=1)

    for line in line_changes.lines:
        line_number = _candidate_line_number(line)
        gutter_number = " " * width if line_number is None else f"{line_number:>{width}}"
        gutter = (
            f"{gutter_number}"
            f"{candidate_preview_snippets.CANDIDATE_GUTTER_SEPARATOR} "
        )
        body = f"{line.kind}{line.display_text()}"

        if not use_color:
            print(f"{gutter}{body}")
            continue

        styled_gutter = f"{Colors.GRAY}{gutter}{Colors.RESET}"
        if line.kind == "+":
            print(f"{styled_gutter}{Colors.GREEN}{body}{Colors.RESET}")
        elif line.kind == "-":
            print(f"{styled_gutter}{Colors.RED}{body}{Colors.RESET}")
        elif candidate_preview_snippets.candidate_line_in_range(
            line_number,
            ambiguity_target_line_range,
        ):
            print(f"{styled_gutter}{Colors.REVERSE}{Colors.GRAY}{body}{Colors.RESET}")
        else:
            print(f"{styled_gutter}{body}")


def print_candidate_buffer_diff(
    file_path: str,
    before_buffer: LineBuffer,
    after_buffer: LineBuffer,
    *,
    context_lines: int,
    ambiguity_target_line_range: tuple[int, int] | None,
    leading_blank: bool = False,
) -> bool:
    """Print the diff between one candidate target's buffers."""
    diff_buffer = _candidate_buffer_diff(
        file_path,
        before_buffer,
        after_buffer,
        label_before="a",
        label_after="b",
        context_lines=context_lines,
    )
    with diff_buffer:
        if diff_buffer.byte_count == 0:
            return False

        if leading_blank:
            print()

        hunks = iter(_candidate_diff_hunks(diff_buffer))
        first_hunk = next(hunks, None)
        if first_hunk is None:
            diff_text = "".join(
                line.decode("utf-8", errors="surrogateescape")
                for line in diff_buffer
            )
            print(diff_text, end="" if diff_text.endswith("\n") else "\n")
            return True

        for index, hunk in enumerate(chain((first_hunk,), hunks)):
            if index:
                print()
            line_changes = build_line_changes_from_patch_lines(hunk)
            _print_candidate_line_changes(
                line_changes,
                ambiguity_target_line_range=ambiguity_target_line_range,
            )
        return True
