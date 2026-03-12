"""Parse unified diff format into structured models."""

from __future__ import annotations

import re

from .models import CurrentLines, HunkHeader, LineEntry, SingleHunkPatch
from .state import exit_with_error


HUNK_HEADER_PATTERN = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@")


def parse_unified_diff_into_single_hunk_patches(diff_text: str) -> list[SingleHunkPatch]:
    """Parse a unified diff into separate single-hunk patches.

    Takes the output of `git diff` and splits it so that each file+hunk
    combination becomes its own SingleHunkPatch object. This allows
    processing hunks independently.

    Args:
        diff_text: Output from `git diff` in unified format

    Returns:
        List of SingleHunkPatch objects, one per hunk
    """
    patches: list[SingleHunkPatch] = []
    lines = diff_text.splitlines()

    i = 0
    while i < len(lines):
        line = lines[i]

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
                i += 1
                continue

            old_path = rest[a_start + 2:b_start]
            new_path = rest[b_start + 3:]  # Skip " b/"

            i += 1
            patch_lines: list[str] = []

            # Collect lines until we hit the --- line (start of unified diff)
            while i < len(lines) and not lines[i].startswith("---"):
                i += 1

            if i >= len(lines):
                break

            # Found the --- line, start of this file's diff
            old_file_line = lines[i]
            i += 1

            if i >= len(lines) or not lines[i].startswith("+++"):
                continue

            new_file_line = lines[i]
            i += 1

            # Process all hunks for this file
            while i < len(lines) and lines[i].startswith("@@"):
                hunk_lines = [old_file_line, new_file_line, lines[i]]
                i += 1

                # Collect hunk body (lines starting with space, +, or -)
                while i < len(lines):
                    if lines[i].startswith("diff --git "):
                        # Next file starting
                        break
                    if lines[i].startswith("@@"):
                        # Next hunk for same file
                        break
                    if lines[i].startswith("---") and i + 1 < len(lines) and lines[i + 1].startswith("+++"):
                        # This might be the start of a new file diff
                        break

                    # Include lines that are part of the hunk
                    if lines[i].startswith((" ", "+", "-", "\\")):
                        hunk_lines.append(lines[i])
                        i += 1
                    else:
                        # Unknown line, stop collecting this hunk
                        break

                # Create a SingleHunkPatch for this hunk
                patches.append(SingleHunkPatch(
                    old_path=old_path,
                    new_path=new_path,
                    lines=hunk_lines
                ))
        else:
            i += 1

    return patches


def build_current_lines_from_patch_text(patch_text: str) -> CurrentLines:
    """Parse a single-hunk patch into a CurrentLines structure."""
    path_value = ""
    old_path_value = ""
    new_path_value = ""
    captured_header_line = ""
    body_lines: list[str] = []

    for line in patch_text.splitlines():
        if line.startswith("--- "):
            old_path_value = line.split(" ", 1)[1].strip()
            if old_path_value != "/dev/null" and old_path_value.startswith("a/"):
                old_path_value = old_path_value[2:]
        elif line.startswith("+++ "):
            new_path_value = line.split(" ", 1)[1].strip()
            if new_path_value != "/dev/null" and new_path_value.startswith("b/"):
                new_path_value = new_path_value[2:]
        elif line.startswith("@@ "):
            captured_header_line = line
            body_lines.append(line)
        else:
            if captured_header_line:
                body_lines.append(line)

    if new_path_value and new_path_value != "/dev/null":
        path_value = new_path_value
    elif old_path_value and old_path_value != "/dev/null":
        path_value = old_path_value
    else:
        path_value = new_path_value or old_path_value or ""

    if not captured_header_line:
        exit_with_error("Failed to parse hunk header.")

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

    for raw in body_lines[1:]:  # skip header
        if raw.startswith("\\ No newline at end of file"):
            continue
        if not raw:
            sign = " "
            text = ""
        else:
            sign = raw[0]
            text = raw[1:]

        if sign == " ":
            line_entries.append(LineEntry(id=None,
                                          kind=" ",
                                          old_line_number=old_line_number,
                                          new_line_number=new_line_number,
                                          text=text))
            old_line_number += 1
            new_line_number += 1
        elif sign == "-":
            next_display_id += 1
            line_entries.append(LineEntry(id=next_display_id,
                                          kind="-",
                                          old_line_number=old_line_number,
                                          new_line_number=None,
                                          text=text))
            old_line_number += 1
        elif sign == "+":
            next_display_id += 1
            line_entries.append(LineEntry(id=next_display_id,
                                          kind="+",
                                          old_line_number=None,
                                          new_line_number=new_line_number,
                                          text=text))
            new_line_number += 1
        else:
            line_entries.append(LineEntry(id=None,
                                          kind=" ",
                                          old_line_number=old_line_number,
                                          new_line_number=new_line_number,
                                          text=text))
            old_line_number += 1
            new_line_number += 1

    return CurrentLines(path=path_value, header=hunk_header, lines=line_entries)
