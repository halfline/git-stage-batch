"""Colored patch printing."""

from __future__ import annotations

from .colors import Colors


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
