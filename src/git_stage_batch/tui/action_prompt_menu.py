"""Action prompt menu formatting for interactive TUI mode."""

from __future__ import annotations

import re
import shutil

from ..i18n import _
from ..output.colors import Colors, format_hotkey


ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*m")


def _visible_len(text: str) -> int:
    """Return display length without ANSI color sequences."""
    return len(ANSI_PATTERN.sub("", text))


def format_menu_section_lines(
    label: str,
    options: list[tuple[str, str, str]],
    use_color: bool,
) -> list[str]:
    """Format one menu section, wrapping options across continuation lines."""
    width = max(40, shutil.get_terminal_size((88, 20)).columns)
    formatted_options = [
        format_hotkey(text, hotkey, color)
        for text, hotkey, color in options
    ]

    if use_color and Colors.enabled():
        rendered_label = f"{Colors.GRAY}{label}{Colors.RESET}"
        rendered_prefix = f"{rendered_label}: {Colors.CYAN}"
        rendered_suffix = Colors.RESET
    else:
        rendered_prefix = _("{label}: ").format(label=label)
        rendered_suffix = ""

    continuation_prefix = " " * (_visible_len(label) + 2)
    lines: list[str] = []
    current = rendered_prefix
    current_visible_len = _visible_len(current)

    for option in formatted_options:
        separator = "" if current == rendered_prefix else ", "
        candidate_len = current_visible_len + len(separator) + _visible_len(option)
        if current != rendered_prefix and candidate_len > width:
            lines.append(f"{current}{rendered_suffix}")
            current = continuation_prefix + option
            current_visible_len = _visible_len(current)
            continue

        current = f"{current}{separator}{option}"
        current_visible_len = candidate_len

    if current != rendered_prefix:
        lines.append(f"{current}{rendered_suffix}")

    return lines
