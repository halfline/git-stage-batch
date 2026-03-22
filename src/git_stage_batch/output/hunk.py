"""Hunk display with line ID gutter."""

from __future__ import annotations

from ..core.models import CurrentLines
from .colors import Colors


def print_annotated_hunk_with_aligned_gutter(current_lines: CurrentLines) -> None:
    """Print a hunk with line IDs in an aligned gutter.

    Changed lines (+ or -) are labeled with [#N] where N is the line ID.
    Context lines have no label. The gutter is aligned based on the
    maximum line ID digit count.
    """
    use_color = Colors.enabled()

    header = current_lines.header
    header_line = f"{current_lines.path} :: @@ -{header.old_start},{header.old_len} +{header.new_start},{header.new_len} @@"

    if use_color:
        # Color the file path in bold and the @@ header in cyan
        path_part = current_lines.path
        header_part = f"@@ -{header.old_start},{header.old_len} +{header.new_start},{header.new_len} @@"
        print(f"{Colors.BOLD}{path_part}{Colors.RESET} :: {Colors.CYAN}{header_part}{Colors.RESET}")
    else:
        print(header_line)

    maximum_digits = current_lines.maximum_line_id_digit_count()
    label_width = maximum_digits + 3  # "[#N]" plus one space

    for line_entry in current_lines.lines:
        if line_entry.id is not None:
            label_text = f"[#{line_entry.id}]"
            label_area = label_text + " " * (label_width - len(label_text))
        else:
            label_area = " " * label_width

        sign_character = line_entry.kind if line_entry.kind in ("+", "-", " ") else " "
        line_content = f"{label_area} {sign_character} {line_entry.text}"

        if use_color:
            if line_entry.kind == "+":
                print(f"{Colors.GREEN}{line_content}{Colors.RESET}")
            elif line_entry.kind == "-":
                print(f"{Colors.RED}{line_content}{Colors.RESET}")
            else:
                print(line_content)
        else:
            print(line_content)
