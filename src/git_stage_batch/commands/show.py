"""Show command implementation."""

from __future__ import annotations

import sys

from ..core.diff_parser import parse_unified_diff_streaming
from ..i18n import _
from ..output.patch import print_colored_patch
from ..utils.git import require_git_repository, stream_git_command


def command_show() -> None:
    """Show the first available hunk."""
    require_git_repository()

    # Stream diff and show first hunk
    for first_patch in parse_unified_diff_streaming(stream_git_command(["diff", "--no-color"])):
        print_colored_patch(first_patch.to_patch_text())
        return

    print(_("No changes to stage."), file=sys.stderr)
