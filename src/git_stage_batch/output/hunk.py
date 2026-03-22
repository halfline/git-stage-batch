"""Hunk display with line ID gutter."""

from __future__ import annotations

from ..core.models import LineLevelChange
from .colors import Colors


def print_line_level_changes(line_changes: LineLevelChange) -> None:
    """Print a hunk with line IDs in an aligned gutter.

    Changed lines (+ or -) are labeled with [#N] where N is the line ID.
    Context lines have no label. The gutter is aligned based on the
    maximum line ID digit count.
    """
    use_color = Colors.enabled()

    header = line_changes.header
    header_line = f"{line_changes.path} :: @@ -{header.old_start},{header.old_len} +{header.new_start},{header.new_len} @@"

    if use_color:
        # Color the file path in bold and the @@ header in cyan
        path_part = line_changes.path
        header_part = f"@@ -{header.old_start},{header.old_len} +{header.new_start},{header.new_len} @@"
        print(f"{Colors.BOLD}{path_part}{Colors.RESET} :: {Colors.CYAN}{header_part}{Colors.RESET}")
    else:
        print(header_line)

    maximum_digits = line_changes.maximum_line_id_digit_count()
    label_width = maximum_digits + 3  # "[#N]" plus one space

    for line_entry in line_changes.lines:
        # Build gutter (line ID label area)
        if line_entry.id is not None:
            label_text = f"[#{line_entry.id}]"
            label_padding = " " * (label_width - len(label_text))
        else:
            label_text = ""
            label_padding = " " * label_width

        sign_character = line_entry.kind if line_entry.kind in ("+", "-", " ") else " "
        line_text = f" {sign_character} {line_entry.text}"

        if use_color:
            # Print gutter in gray if it has a label, otherwise just padding
            if label_text:
                print(f"{Colors.GRAY}{label_text}{Colors.RESET}{label_padding}", end="")
            else:
                print(label_padding, end="")

            # Print line content in appropriate color
            if line_entry.kind == "+":
                print(f"{Colors.GREEN}{line_text}{Colors.RESET}")
            elif line_entry.kind == "-":
                print(f"{Colors.RED}{line_text}{Colors.RESET}")
            else:
                print(line_text)
        else:
            # No color: just concatenate everything
            line_content = label_text + label_padding + line_text
            print(line_content)
