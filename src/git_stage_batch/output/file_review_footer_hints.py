"""Command hint construction for file review footers."""

from __future__ import annotations

from dataclasses import dataclass
import shlex

from ..core.actionable_changes import ActionableSelection
from ..core.line_selection import format_line_ids
from ..data.file_review.action_commands import line_action_command
from ..data.file_review.records import FileReviewAction, FileReviewState, ReviewSource
from ..data.selected_change.store import SelectedChangeKind
from ..i18n import _


@dataclass(frozen=True)
class FileReviewFooterHint:
    action: str
    command: str


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


def build_file_review_footer_hints(
    path: str,
    *,
    shown_pages: tuple[int, ...],
    page_count: int,
    complete_line_action_selections: list[ActionableSelection],
    command_source_args: str,
    source: ReviewSource,
    batch_name: str | None,
) -> tuple[FileReviewFooterHint, ...]:
    """Return command hints for one file-review footer."""
    shown = set(shown_pages)
    is_entire = shown == set(range(1, page_count + 1))
    hints: list[FileReviewFooterHint] = []
    navigation_hints: list[FileReviewFooterHint] = []

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
            FileReviewFooterHint(
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
                FileReviewFooterHint(
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
            FileReviewFooterHint(
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
                FileReviewFooterHint(
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
            FileReviewFooterHint(
                _("reset"),
                reset_line_command
                or f"git-stage-batch reset{command_source_args} --line {reset_selection}",
            )
        )
    elif not is_entire:
        hints.append(
            FileReviewFooterHint(
                _("No complete change is actionable from this page."),
                "",
            )
        )

    if not is_entire and max(shown_pages) < page_count:
        navigation_hints.append(
            FileReviewFooterHint(
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
            FileReviewFooterHint(
                _("include"),
                f"git-stage-batch include{command_source_args} --file {_quote(path)}",
            )
        )
    else:
        navigation_hints.append(
            FileReviewFooterHint(
                _("all"),
                f"git-stage-batch show{command_source_args} --file {_quote(path)} --page all",
            )
        )

    hints.extend(navigation_hints)
    return tuple(hints)


def _footer_review_state(
    *,
    path: str,
    shown_pages: tuple[int, ...],
    page_count: int,
    source: ReviewSource,
    batch_name: str | None,
) -> FileReviewState:
    entire_file_shown = set(shown_pages) == set(range(1, page_count + 1))
    if source == ReviewSource.BATCH:
        return FileReviewState(
            source=ReviewSource.BATCH,
            batch_name=batch_name,
            file_path=path,
            page_spec="all",
            shown_pages=shown_pages,
            page_count=page_count,
            entire_file_shown=entire_file_shown,
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
        entire_file_shown=entire_file_shown,
        selections=tuple(),
        selected_change_kind=SelectedChangeKind.FILE,
        selected_file_fingerprint="",
        diff_fingerprint="",
    )
