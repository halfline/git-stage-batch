"""Display and printing of annotated hunks."""

from __future__ import annotations

import sys

from .line_selection import read_line_ids_file
from .models import CurrentLines
from .state import get_processed_include_ids_file_path, get_processed_skip_ids_file_path


# ANSI color codes
class Colors:
    """ANSI color codes for terminal output."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    CYAN = "\033[36m"

    @staticmethod
    def enabled() -> bool:
        """Check if colors should be enabled (stdout is a TTY)."""
        return sys.stdout.isatty()


def print_annotated_hunk_with_aligned_gutter(current_lines: CurrentLines) -> None:
    """
    Print a hunk with line IDs in an aligned gutter.

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

    processed_include_ids = set(read_line_ids_file(get_processed_include_ids_file_path()))
    processed_skip_ids = set(read_line_ids_file(get_processed_skip_ids_file_path()))

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
