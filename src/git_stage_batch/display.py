"""Display and printing of annotated hunks."""

from __future__ import annotations

import sys


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


def print_colored_patch(patch_text: str) -> None:
    """Print a patch with colored diff lines."""
    use_color = Colors.enabled()

    for line in patch_text.splitlines(keepends=True):
        if use_color:
            if line.startswith('+++') or line.startswith('---'):
                print(f"{Colors.BOLD}{line}{Colors.RESET}", end="")
            elif line.startswith('@@'):
                print(f"{Colors.CYAN}{line}{Colors.RESET}", end="")
            elif line.startswith('+'):
                print(f"{Colors.GREEN}{line}{Colors.RESET}", end="")
            elif line.startswith('-'):
                print(f"{Colors.RED}{line}{Colors.RESET}", end="")
            else:
                print(line, end="")
        else:
            print(line, end="")
