"""Parsing git unified diffs into structured data models."""

from __future__ import annotations

import re
from pathlib import Path

from .models import CurrentLines, HunkHeader, LineEntry, SingleHunkPatch
from .state import exit_with_error, read_text_file_contents, run_git_command, write_text_file_contents, get_index_snapshot_file_path, get_working_tree_snapshot_file_path


# --------------------------- Parsing patterns ---------------------------

DIFF_FILE_HEADER_PATTERN = re.compile(r"^diff --git a/(.+?) b/(.+?)$")
HUNK_HEADER_PATTERN = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@")


# --------------------------- Parsing unified diff into single-hunk patches ---------------------------

def parse_unified_diff_into_single_hunk_patches(diff_text: str) -> list[SingleHunkPatch]:
    """
    Convert a full diff into a list of SingleHunkPatch (each with exactly one @@ hunk).
    """
    single_hunk_patches: list[SingleHunkPatch] = []
    current_old_path = ""
    current_new_path = ""
    current_file_buffer: list[str] = []
    saw_file_header = False

    def flush_file_hunks(buffer: list[str], old_path_value: str, new_path_value: str) -> None:
        nonlocal single_hunk_patches
        hunk_lists: list[list[str]] = []
        current_hunk_buffer: list[str] = []
        for line in buffer:
            if line.startswith("@@ "):
                if current_hunk_buffer:
                    hunk_lists.append(current_hunk_buffer)
                current_hunk_buffer = [line]
            else:
                if current_hunk_buffer:
                    current_hunk_buffer.append(line)
        if current_hunk_buffer:
            hunk_lists.append(current_hunk_buffer)
        for hunk in hunk_lists:
            # Absolute paths (like /dev/null) don't get a/ or b/ prefix
            old_header = f"--- {old_path_value}" if Path(old_path_value).is_absolute() else f"--- a/{old_path_value}"
            new_header = f"+++ {new_path_value}" if Path(new_path_value).is_absolute() else f"+++ b/{new_path_value}"
            lines = [old_header, new_header, *hunk]
            single_hunk_patches.append(SingleHunkPatch(old_path_value, new_path_value, lines))

    for raw_line in diff_text.splitlines():
        match = DIFF_FILE_HEADER_PATTERN.match(raw_line)
        if match:
            if saw_file_header and current_file_buffer and (current_old_path or current_new_path):
                flush_file_hunks(current_file_buffer, current_old_path, current_new_path)
            current_old_path = match.group(1)
            current_new_path = match.group(2)
            current_file_buffer = []
            saw_file_header = True
            continue
        if raw_line.startswith("--- "):
            normalized = raw_line.split(" ", 1)[1].strip()
            current_old_path = "/dev/null" if normalized == "/dev/null" else (normalized[2:] if normalized.startswith("a/") else normalized)
            continue
        if raw_line.startswith("+++ "):
            normalized = raw_line.split(" ", 1)[1].strip()
            current_new_path = "/dev/null" if normalized == "/dev/null" else (normalized[2:] if normalized.startswith("b/") else normalized)
            continue
        if raw_line.startswith("@@ ") or raw_line.startswith(" ") or raw_line.startswith("+") or raw_line.startswith("-") or raw_line.startswith("\\ "):
            current_file_buffer.append(raw_line)

    if saw_file_header and current_file_buffer and (current_old_path or current_new_path):
        flush_file_hunks(current_file_buffer, current_old_path, current_new_path)

    return single_hunk_patches


# --------------------------- Build CurrentLines from patch text ---------------------------

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


# --------------------------- Helper functions ---------------------------

def get_path_from_patch_text(patch_text: str) -> str:
    """Extract the file path from a patch."""
    current_lines = build_current_lines_from_patch_text(patch_text)
    return current_lines.path


def write_snapshots_for_current_file_path(file_path: str) -> None:
    """Write snapshots of the file from both the index and working tree."""
    try:
        index_version = run_git_command(["show", f":{file_path}"], check=True).stdout
    except Exception:
        index_version = ""
    write_text_file_contents(get_index_snapshot_file_path(), index_version)

    working_tree_version = ""
    absolute_path = Path(file_path)
    if absolute_path.exists():
        working_tree_version = read_text_file_contents(absolute_path)
    write_text_file_contents(get_working_tree_snapshot_file_path(), working_tree_version)
