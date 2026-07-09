"""Diff rendering for operation candidate previews."""

from __future__ import annotations

from ..batch.operation_candidates import render_candidate_buffer_diff
from ..core.buffer import LineBuffer
from ..core.diff_parser import build_line_changes_from_patch_lines
from . import candidate_preview_summary
from .colors import Colors


def _candidate_diff_hunks(diff_text: str) -> tuple[tuple[bytes, ...], ...]:
    headers: list[bytes] = []
    current_hunk: list[bytes] = []
    hunks: list[tuple[bytes, ...]] = []
    for line in diff_text.splitlines(keepends=True):
        line_bytes = line.encode("utf-8", errors="surrogateescape")
        if line_bytes.startswith((b"--- ", b"+++ ")):
            headers.append(line_bytes)
            continue
        if line_bytes.startswith(b"@@ "):
            if current_hunk:
                hunks.append(tuple(headers + current_hunk))
            current_hunk = [line_bytes]
            continue
        if current_hunk:
            current_hunk.append(line_bytes)

    if current_hunk:
        hunks.append(tuple(headers + current_hunk))
    return tuple(hunks)


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
            f"{candidate_preview_summary.CANDIDATE_GUTTER_SEPARATOR} "
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
        elif candidate_preview_summary.candidate_line_in_range(
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
    diff_text = render_candidate_buffer_diff(
        file_path,
        before_buffer,
        after_buffer,
        label_before="a",
        label_after="b",
        context_lines=context_lines,
    )
    if not diff_text:
        return False

    if leading_blank:
        print()

    hunks = _candidate_diff_hunks(diff_text)
    if not hunks:
        print(diff_text, end="" if diff_text.endswith("\n") else "\n")
        return True

    for index, hunk in enumerate(hunks):
        if index:
            print()
        line_changes = build_line_changes_from_patch_lines(hunk)
        _print_candidate_line_changes(
            line_changes,
            ambiguity_target_line_range=ambiguity_target_line_range,
        )
    return True
