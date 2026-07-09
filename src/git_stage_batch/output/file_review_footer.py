"""Footer command rendering for file review output."""

from __future__ import annotations

import os
import shlex
import sys

from ..core.actionable_changes import ActionableSelection
from ..core.line_selection import format_line_ids
from ..data.file_review.action_commands import line_action_command
from ..data.file_review.records import FileReviewAction, FileReviewState, ReviewSource
from ..data.selected_change.store import SelectedChangeKind
from ..i18n import _
from .colors import Colors
from .file_review_summary import change_summary, page_summary


def _quote(value: str) -> str:
    return shlex.quote(value)


def _selection_supports_action(
    selection: ActionableSelection,
    action: FileReviewAction,
) -> bool:
    return not selection.actions or action.value in selection.actions


def _line_spec_for_selections(selections: list[ActionableSelection]) -> str:
    display_ids: list[int] = []
    for selection in selections:
        display_ids.extend(selection.display_ids)
    return format_line_ids(display_ids)


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
    shown = set(shown_pages)
    is_entire = shown == set(range(1, page_count + 1))
    hints: list[tuple[str, str]] = []
    navigation_hints: list[tuple[str, str]] = []

    review_state = _footer_review_state(
        path=path,
        shown_pages=shown_pages,
        page_count=page_count,
        source=source,
        batch_name=batch_name,
    )
    primary_action = (
        FileReviewAction.INCLUDE_FROM_BATCH
        if source == ReviewSource.BATCH else
        FileReviewAction.INCLUDE
    )
    primary_selections = [
        selection
        for selection in complete_line_action_selections
        if _selection_supports_action(selection, primary_action)
    ]
    reset_selections = [
        selection
        for selection in complete_line_action_selections
        if _selection_supports_action(selection, FileReviewAction.RESET_FROM_BATCH)
    ]

    if primary_selections:
        combined_selection = _line_spec_for_selections(primary_selections)
        include_line_command = line_action_command(
            "include",
            review_state,
            line_spec=combined_selection,
            pathless_line=True,
        )
        hints.append(
            (
                _("include"),
                include_line_command
                or (
                    "git-stage-batch include"
                    f"{command_source_args} --line {combined_selection}"
                ),
            )
        )
        if not command_source_args:
            skip_line_command = line_action_command(
                "skip",
                review_state,
                line_spec=combined_selection,
                pathless_line=True,
            )
            hints.append(
                (
                    _("skip"),
                    skip_line_command
                    or f"git-stage-batch skip --line {combined_selection}",
                )
            )
        discard_line_command = line_action_command(
            "discard",
            review_state,
            line_spec=combined_selection,
            pathless_line=True,
        )
        hints.append(
            (
                _("discard"),
                discard_line_command
                or (
                    "git-stage-batch discard"
                    f"{command_source_args} --line {combined_selection}"
                ),
            )
        )
        if source == ReviewSource.BATCH and reset_selections:
            reset_selection = _line_spec_for_selections(reset_selections)
            reset_line_command = line_action_command(
                FileReviewAction.RESET_FROM_BATCH,
                review_state,
                line_spec=reset_selection,
                pathless_line=True,
            )
            hints.append(
                (
                    _("reset"),
                    reset_line_command
                    or f"git-stage-batch reset{command_source_args} --line {reset_selection}",
                )
            )
    elif reset_selections:
        reset_selection = _line_spec_for_selections(reset_selections)
        reset_line_command = line_action_command(
            FileReviewAction.RESET_FROM_BATCH,
            review_state,
            line_spec=reset_selection,
            pathless_line=True,
        )
        hints.append(
            (
                _("reset"),
                reset_line_command
                or f"git-stage-batch reset{command_source_args} --line {reset_selection}",
            )
        )
    elif not is_entire:
        hints.append((_("No complete change is actionable from this page."), ""))

    if not is_entire and max(shown_pages) < page_count:
        navigation_hints.append(
            (
                _("next"),
                (
                    "git-stage-batch show"
                    f"{command_source_args} --file {_quote(path)} "
                    f"--page {max(shown_pages) + 1}"
                ),
            )
        )

    if is_entire:
        hints.append(
            (
                _("include"),
                f"git-stage-batch include{command_source_args} --file {_quote(path)}",
            )
        )
    else:
        navigation_hints.append(
            (
                _("all"),
                f"git-stage-batch show{command_source_args} --file {_quote(path)} --page all",
            )
        )

    hints.extend(navigation_hints)
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
    action_width = max(len(action) for action, command in hints if command)
    for action, command in hints:
        if not command:
            print(action)
            continue
        display_command = _display_footer_command(command)
        action_text = action.ljust(action_width)
        if use_color:
            print(
                f"{Colors.CYAN}{action_text}{Colors.RESET}  "
                f"{_style_footer_command(display_command)}"
            )
        else:
            print(f"{action_text}  {display_command}")


def _footer_review_state(
    *,
    path: str,
    shown_pages: tuple[int, ...],
    page_count: int,
    source: ReviewSource,
    batch_name: str | None,
) -> FileReviewState:
    if source == ReviewSource.BATCH:
        return FileReviewState(
            source=ReviewSource.BATCH,
            batch_name=batch_name,
            file_path=path,
            page_spec="all",
            shown_pages=shown_pages,
            page_count=page_count,
            entire_file_shown=set(shown_pages) == set(range(1, page_count + 1)),
            selections=tuple(),
            selected_change_kind=SelectedChangeKind.BATCH_FILE,
            selected_file_fingerprint="",
            diff_fingerprint="",
        )
    return FileReviewState(
        source=source,
        batch_name=None,
        file_path=path,
        page_spec="all",
        shown_pages=shown_pages,
        page_count=page_count,
        entire_file_shown=set(shown_pages) == set(range(1, page_count + 1)),
        selections=tuple(),
        selected_change_kind=SelectedChangeKind.FILE,
        selected_file_fingerprint="",
        diff_fingerprint="",
    )
