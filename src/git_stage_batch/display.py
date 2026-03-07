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
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"  # Bright black (muted gray)

    @staticmethod
    def enabled() -> bool:
        """Check if colors should be enabled (stdout is a TTY)."""
        return sys.stdout.isatty()


def format_hotkey(text: str, hotkey: str, color: str = "") -> str:
    """
    Format a menu option by bracketing the hotkey letter.

    Finds the first occurrence of the hotkey (case-insensitive) and wraps it
    in brackets with optional color formatting. If the hotkey is not found in
    the text, prepends it as [hotkey] text.

    Args:
        text: The menu option text (e.g., "include  - Stage this hunk")
        hotkey: The single character to bracket (e.g., "i")
        color: Optional ANSI color code (e.g., Colors.GREEN)

    Returns:
        Formatted string with bracketed hotkey

    Example:
        format_hotkey("include  - Stage this hunk", "i", Colors.GREEN)
        # With colors: "\033[1m\033[32m[i]\033[0mnclude  - Stage this hunk"
        # Without: "[i]nclude  - Stage this hunk"

        format_hotkey("einschließen", "i", Colors.GREEN)  # 'i' found
        # "e[i]nschließen"

        format_hotkey("einschließen", "e", Colors.GREEN)  # 'e' at start
        # "[e]inschließen"
    """
    if not hotkey or len(hotkey) != 1:
        return text

    # Find first occurrence of hotkey (case-insensitive)
    lower_text = text.lower()
    hotkey_lower = hotkey.lower()

    index = lower_text.find(hotkey_lower)

    if index == -1:
        # Hotkey not found in text - prepend it
        if Colors.enabled() and color:
            bracketed = f"{Colors.BOLD}{color}[{hotkey}]{Colors.RESET}"
        else:
            bracketed = f"[{hotkey}]"
        return f"{bracketed} {text}"

    # Extract the actual character (preserving original case)
    actual_char = text[index]

    # Build formatted string with hotkey in place
    before = text[:index]
    after = text[index + 1:]

    if Colors.enabled() and color:
        bracketed = f"{Colors.BOLD}{color}[{actual_char}]{Colors.RESET}"
    else:
        bracketed = f"[{actual_char}]"

    return before + bracketed + after


def format_option_list(options: list[tuple[str, str, str]]) -> str:
    """
    Format a comma-separated list of options with bracketed hotkeys.

    Args:
        options: List of (text, hotkey, color) tuples

    Returns:
        Formatted string like "[a]ll, [l]ines, [f]ile"

    Example:
        format_option_list([("all", "a", ""), ("lines", "l", ""), ("file", "f", "")])
    """
    formatted = []
    for text, hotkey, color in options:
        formatted.append(format_hotkey(text, hotkey, color))

    return ", ".join(formatted)


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
        line_content_part = f" {sign_character} {line_entry.text}"

        if use_color:
            # Color the gutter (line number) in gray, rest based on line type
            colored_gutter = f"{Colors.GRAY}{label_area}{Colors.RESET}"

            if line_entry.kind == "+":
                colored_content = f"{Colors.GREEN}{line_content_part}{Colors.RESET}"
            elif line_entry.kind == "-":
                colored_content = f"{Colors.RED}{line_content_part}{Colors.RESET}"
            else:
                colored_content = line_content_part

            print(f"{colored_gutter}{colored_content}")
        else:
            print(f"{label_area}{line_content_part}")
