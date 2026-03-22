"""Display utilities."""

from .colors import Colors, format_hotkey, format_option_list
from .hunk import print_annotated_hunk_with_aligned_gutter
from .patch import print_colored_patch

__all__ = [
    "Colors",
    "format_hotkey",
    "format_option_list",
    "print_annotated_hunk_with_aligned_gutter",
    "print_colored_patch",
]
