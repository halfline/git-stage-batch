"""ANSI color codes for terminal output."""

from __future__ import annotations

import sys


class Colors:
    """ANSI color codes for terminal output."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"

    @staticmethod
    def enabled() -> bool:
        """Check if colors should be enabled (stdout is a TTY)."""
        return sys.stdout.isatty()


def format_hotkey(text: str, hotkey: str, color: str = "") -> str:
    """Format text with hotkey highlighted in brackets.

    Args:
        text: The text to format (e.g., "include", "quit")
        hotkey: The hotkey character (e.g., "i", "q", "!")
        color: Optional color code to apply (e.g., Colors.GREEN)

    Returns:
        Formatted string like "[i]nclude" or "[q]uit"

    If the hotkey appears in the text (case-insensitive), it's wrapped
    in brackets. Otherwise, the hotkey is prepended: "[!] run"
    """
    use_color = Colors.enabled() and color
    lower_text = text.lower()
    lower_hotkey = hotkey.lower()

    # Find hotkey in text
    if lower_hotkey in lower_text and len(lower_hotkey) == 1:
        # Find the position (preserve original case)
        idx = lower_text.index(lower_hotkey)
        before = text[:idx]
        key_char = text[idx]
        after = text[idx + 1:]

        if use_color:
            return f"{before}{color}[{key_char}]{Colors.RESET}{after}"
        else:
            return f"{before}[{key_char}]{after}"
    else:
        # Prepend with brackets
        if use_color:
            return f"{color}[{hotkey}]{Colors.RESET} {text}"
        else:
            return f"[{hotkey}] {text}"


def format_option_list(options: list[tuple[str, str, str]]) -> str:
    """Format a list of options with hotkeys.

    Args:
        options: List of (text, hotkey, color) tuples

    Returns:
        Comma-separated formatted string like "[a]ll, [l]ines, [f]ile"
    """
    formatted = [format_hotkey(text, hotkey, color) for text, hotkey, color in options]
    return ", ".join(formatted)
