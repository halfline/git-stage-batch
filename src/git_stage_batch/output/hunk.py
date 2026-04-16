"""Hunk display with line ID gutter."""

from __future__ import annotations

from ..core.models import LineLevelChange
from .colors import Colors


def print_line_level_changes(line_changes: LineLevelChange, *, gutter_to_selection_id: dict[int, int] | None = None) -> None:
    """Print a hunk with line IDs in an aligned gutter.

    Changed lines (+ or -) are labeled with [#N] where N is the line ID.
    Context lines have no label. The gutter is aligned based on the
    maximum line ID digit count.

    If gutter_to_selection_id is provided, only lines with IDs mapped in that dict will be
    numbered in the gutter. This creates filtered gutter numbering where
    unmergeable lines are still shown but without selectable IDs.

    Args:
        line_changes: The hunk to display
        gutter_to_selection_id: Optional mapping from filtered gutter ID to selection ID.
                               If provided, only selection IDs in the mapping get gutter numbers.
                               If None, all non-None IDs are numbered as usual.
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

    # Use provided gutter mapping if available, otherwise build reverse map from selection IDs
    if gutter_to_selection_id is not None:
        # Reverse the mapping: selection ID -> gutter number
        selection_id_to_gutter = {selection_id: gutter_num for gutter_num, selection_id in gutter_to_selection_id.items()}

        # Compute maximum digits for filtered gutter numbers
        if gutter_to_selection_id:
            maximum_digits = len(str(max(gutter_to_selection_id.keys())))
        else:
            maximum_digits = 1
    else:
        selection_id_to_gutter = None
        maximum_digits = line_changes.maximum_line_id_digit_count()

    label_width = maximum_digits + 3  # "[#N]" plus one space

    for line_entry in line_changes.lines:
        is_gap_line = (
            line_entry.id is None
            and line_entry.kind == " "
            and line_entry.old_line_number is None
            and line_entry.new_line_number is None
            and line_entry.source_line is None
        )

        # Build gutter (line ID label area)
        if line_entry.id is not None:
            # Check if this ID should be numbered
            if selection_id_to_gutter is not None:
                # Filtered mode: only show number if ID is in selection_id_to_gutter
                if line_entry.id in selection_id_to_gutter:
                    gutter_number = selection_id_to_gutter[line_entry.id]
                    label_text = f"[#{gutter_number}]"
                    label_padding = " " * (label_width - len(label_text))
                else:
                    # Unmergeable line: no gutter number
                    label_text = ""
                    label_padding = " " * label_width
            else:
                # Normal mode: show original ID
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
            elif is_gap_line:
                print(f"{Colors.GRAY}{line_text}{Colors.RESET}")
            else:
                print(line_text)
        else:
            # No color: just concatenate everything
            line_content = label_text + label_padding + line_text
            print(line_content)
