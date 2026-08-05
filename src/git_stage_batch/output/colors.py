"""ANSI color codes for terminal output."""

from __future__ import annotations

import sys

from ..i18n import bidi_isolate


class Colors:
    """ANSI color codes for terminal output."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    REVERSE = "\033[7m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
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
    # Find hotkey in text. Uppercase hotkeys are distinct from lowercase
    # hotkeys, so only inline them when the text contains that exact case.
    if len(hotkey) != 1:
        hotkey_index = -1
    elif hotkey.isupper():
        hotkey_index = text.find(hotkey)
    else:
        folded_hotkey = hotkey.casefold()
        hotkey_index = next(
            (
                index
                for index, character in enumerate(text)
                if character.casefold() == folded_hotkey
            ),
            -1,
        )

    if hotkey_index >= 0:
        # Find the position (preserve original case)
        before = text[:hotkey_index]
        key_char = text[hotkey_index]
        after = text[hotkey_index + 1:]

        rendered_hotkey = bidi_isolate(f"[{key_char}]")
        if use_color:
            return f"{before}{color}{rendered_hotkey}{Colors.RESET}{after}"
        else:
            return f"{before}{rendered_hotkey}{after}"
    else:
        # Prepend with brackets
        rendered_hotkey = bidi_isolate(f"[{hotkey}]")
        if use_color:
            return f"{color}{rendered_hotkey}{Colors.RESET} {text}"
        else:
            return f"{rendered_hotkey} {text}"
