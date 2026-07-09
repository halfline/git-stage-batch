"""Footer command rendering for file review output."""

from __future__ import annotations

import os
import shlex
import sys

from ..core.actionable_changes import ActionableSelection
from ..data.file_review.records import ReviewSource
from ..i18n import _
from .colors import Colors
from . import file_review_footer_hints
from .file_review_summary import change_summary, page_summary


def _style_footer_command(command: str) -> str:
    try:
        tokens = shlex.split(command, posix=False)
    except ValueError:
        return command

    dynamic_value_options = {"--file", "--from", "--line", "--page", "--to"}
    if tokens[:2] == ["git", "stage-batch"]:
        action_index = 2
    elif tokens[:1] == ["git-stage-batch"]:
        action_index = 1
    else:
        action_index = -1
    styled_tokens: list[str] = []
    bold_next = False
    for index, token in enumerate(tokens):
        should_bold = bold_next or index == action_index
        if should_bold:
            styled_tokens.append(f"{Colors.BOLD}{token}{Colors.RESET}")
        else:
            styled_tokens.append(token)
        bold_next = token in dynamic_value_options
    return " ".join(styled_tokens)


def _invoked_command_prefix() -> str:
    executable = os.path.basename(sys.argv[0])
    if executable == "git" and len(sys.argv) > 1 and sys.argv[1] == "stage-batch":
        return "git stage-batch"
    return "git-stage-batch"


def _display_footer_command(command: str) -> str:
    if command.startswith("git-stage-batch "):
        return _invoked_command_prefix() + command[len("git-stage-batch"):]
    return command


def print_file_review_footer(
    path: str,
    *,
    shown_pages: tuple[int, ...],
    page_count: int,
    shown_change_spec: str,
    shown_line_spec: str,
    complete_line_action_selections: list[ActionableSelection],
    total_changes: int,
    command_source_args: str,
    source: ReviewSource,
    batch_name: str | None,
) -> None:
    """Print the command footer for one file review."""
    hints = file_review_footer_hints.build_file_review_footer_hints(
        path,
        shown_pages=shown_pages,
        page_count=page_count,
        complete_line_action_selections=complete_line_action_selections,
        command_source_args=command_source_args,
        source=source,
        batch_name=batch_name,
    )
    if not hints:
        return

    print()
    use_color = Colors.enabled()
    rule = "─" * 78
    print(f"{Colors.GRAY}{rule}{Colors.RESET}" if use_color else rule)
    status = "  ·  ".join(
        (
            path,
            page_summary(shown_pages, page_count),
            change_summary(shown_change_spec, total_changes),
            _("lines {lines}").format(lines=shown_line_spec),
        )
    )
    if use_color:
        print(f"{Colors.BOLD}{status}{Colors.RESET}")
    else:
        print(status)
    print()
    action_width = max(len(hint.action) for hint in hints if hint.command)
    for hint in hints:
        if not hint.command:
            print(hint.action)
            continue
        display_command = _display_footer_command(hint.command)
        action_text = hint.action.ljust(action_width)
        if use_color:
            print(
                f"{Colors.CYAN}{action_text}{Colors.RESET}  "
                f"{_style_footer_command(display_command)}"
            )
        else:
            print(f"{action_text}  {display_command}")
