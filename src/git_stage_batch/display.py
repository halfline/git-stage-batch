"""Display and printing of annotated hunks."""

from __future__ import annotations

from .line_selection import read_line_ids_file
from .models import CurrentLines
from .state import get_processed_exclude_ids_file_path, get_processed_include_ids_file_path


def print_annotated_hunk_with_aligned_gutter(current_lines: CurrentLines) -> None:
    """
    Print a hunk with line IDs in an aligned gutter.

    Changed lines (+ or -) are labeled with [#N] where N is the line ID.
    Context lines have no label. The gutter is aligned based on the
    maximum line ID digit count.
    """
    header = current_lines.header
    print(f"{current_lines.path} :: @@ -{header.old_start},{header.old_len} +{header.new_start},{header.new_len} @@")

    maximum_digits = current_lines.maximum_line_id_digit_count()
    label_width = maximum_digits + 3  # "[#N]" plus one space

    processed_include_ids = set(read_line_ids_file(get_processed_include_ids_file_path()))
    processed_exclude_ids = set(read_line_ids_file(get_processed_exclude_ids_file_path()))

    for line_entry in current_lines.lines:
        if line_entry.id is not None:
            label_text = f"[#{line_entry.id}]"
            label_area = label_text + " " * (label_width - len(label_text))
        else:
            label_area = " " * label_width

        sign_character = line_entry.kind if line_entry.kind in ("+", "-", " ") else " "
        print(f"{label_area} {sign_character} {line_entry.text}")
