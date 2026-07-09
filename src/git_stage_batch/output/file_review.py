"""Page-aware file review rendering."""

from __future__ import annotations

import os
import shlex
import sys

from ..core.actionable_changes import (
    ActionableSelection,
    ActionableSelectionReason,
)
from ..core.line_selection import format_line_ids
from ..data.file_review.records import (
    FileReviewAction,
    FileReviewState,
    ReviewSource,
)
from ..data.file_review.action_commands import line_action_command
from ..data.selected_change.store import SelectedChangeKind
from ..i18n import _
from .colors import Colors
from .file_review_action_selections import shown_line_action_selections
from .file_review_display_ids import display_ids_for_rows
from .file_review_model import (
    FileReviewModel,
    ReviewChange,
    ReviewChangeFragment,
)
from .file_review_rows import (
    maximum_display_id_digit_count,
    print_file_review_rows,
)


def _quote(value: str) -> str:
    return shlex.quote(value)


def _line_spec_for_display_ids(display_ids: tuple[int, ...]) -> str:
    if not display_ids:
        return "-"
    return _display_line_spec(format_line_ids(list(display_ids)))


def _change_spec_for_fragments(fragments: list[ReviewChangeFragment]) -> str:
    change_ids: list[int] = []
    seen: set[int] = set()
    for fragment in fragments:
        change_id = fragment.change.index
        if change_id in seen:
            continue
        change_ids.append(change_id)
        seen.add(change_id)
    if not change_ids:
        return "-"
    return _display_line_spec(format_line_ids(change_ids))


def print_file_review(
    model: FileReviewModel,
    *,
    shown_pages: tuple[int, ...],
    source_label: str,
    page_spec: str,
    command_source_args: str = "",
    source: ReviewSource,
    batch_name: str | None = None,
    note: str | None = None,
    opened_near_selected_hunk: bool = False,
) -> None:
    """Print a page-aware file review."""
    page_count = len(model.pages)
    shown_fragments = [
        fragment
        for page in shown_pages
        for fragment in model.pages[page - 1].changes
    ]
    shown_changes = []
    seen_change_indexes: set[int] = set()
    for fragment in shown_fragments:
        if fragment.change.index in seen_change_indexes:
            continue
        shown_changes.append(fragment.change)
        seen_change_indexes.add(fragment.change.index)
    shown_display_ids = []
    seen_display_ids: set[int] = set()
    for fragment in shown_fragments:
        for display_id in display_ids_for_rows(
            fragment.rows,
            model.display_id_by_selection_id,
        ):
            if display_id in seen_display_ids:
                continue
            shown_display_ids.append(display_id)
            seen_display_ids.add(display_id)
    shown_line_spec = _line_spec_for_display_ids(tuple(shown_display_ids))
    shown_change_spec = _change_spec_for_fragments(shown_fragments)
    complete_line_action_selections = shown_line_action_selections(
        model,
        shown_pages,
        source=source,
    )

    _print_header(
        model.line_changes.path,
        source_label=source_label,
        source=source,
        batch_name=batch_name,
        note=note,
        shown_pages=shown_pages,
        page_count=page_count,
        shown_change_spec=shown_change_spec,
        shown_line_spec=shown_line_spec,
        total_changes=len(model.changes),
        opened_near_selected_hunk=opened_near_selected_hunk,
    )

    multi_page = len(shown_pages) > 1
    for page in shown_pages:
        if multi_page:
            print()
            print(f"── page {page}/{page_count} " + "─" * 48)
        for fragment in model.pages[page - 1].changes:
            change = fragment.change
            print()
            fragment_display_ids = display_ids_for_rows(
                fragment.rows,
                model.display_id_by_selection_id,
            )
            selection_spec = (
                _line_spec_for_display_ids(fragment_display_ids)
                if fragment_display_ids else
                change.select_as or "-"
            )
            if change.display_ids:
                line_count = len(fragment_display_ids) if fragment_display_ids else len(change.display_ids)
                size_label = (
                    _("1-line change")
                    if line_count == 1 else
                    _("{count}-line partial group").format(count=line_count)
                    if not fragment.is_first_fragment or not fragment.is_last_fragment else
                    _("{count}-line group").format(count=line_count)
                )
                print(
                    _("Change {index}/{total}   lines {lines}   {size}").format(
                        index=change.index,
                        total=change.total,
                        lines=selection_spec,
                        size=size_label,
                    )
                )
            else:
                print(
                    _("Change {index}/{total}   {note}").format(
                        index=change.index,
                        total=change.total,
                        note=change.note or _("not currently selectable"),
                    )
                )
            print_file_review_rows(
                fragment.rows,
                maximum_display_id_digit_count(model),
                display_id_by_selection_id=model.display_id_by_selection_id,
                allowed_selection_ids=set(change.selection_ids) if change.display_ids else set(),
            )

    _print_footer(
        model.line_changes.path,
        shown_pages=shown_pages,
        page_count=page_count,
        shown_change_spec=shown_change_spec,
        shown_line_spec=shown_line_spec,
        complete_line_action_selections=complete_line_action_selections,
        total_changes=len(model.changes),
        page_spec=page_spec,
        command_source_args=command_source_args,
        source=source,
        batch_name=batch_name,
    )


def _selection_supports_action(selection: ActionableSelection, action: FileReviewAction) -> bool:
    """Return whether a line-action selection supports an action."""
    return not selection.actions or action.value in selection.actions


def _line_spec_for_selections(selections: list[ActionableSelection]) -> str:
    display_ids: list[int] = []
    for selection in selections:
        display_ids.extend(selection.display_ids)
    return format_line_ids(display_ids)


def _review_change_heading_action(change: ReviewChange) -> str:
    """Return the concise action label shown for one review change."""
    if change.reason == ActionableSelectionReason.REPLACEMENT:
        return _("select together")
    if change.actions == (FileReviewAction.RESET_FROM_BATCH.value,):
        return _("reset")
    return _("select")


def _display_line_spec(line_spec: str) -> str:
    return line_spec.replace("-", "–")


def _page_summary(shown_pages: tuple[int, ...], page_count: int) -> str:
    page_word = _("page") if len(shown_pages) == 1 else _("pages")
    return _("{page_word} {pages}/{page_count}").format(
        page_word=page_word,
        pages=_display_line_spec(format_line_ids(list(shown_pages))),
        page_count=page_count,
    )


def _change_summary(change_spec: str, total_changes: int) -> str:
    change_word = _("change") if "," not in change_spec and "–" not in change_spec else _("changes")
    return _("{change_word} {changes}/{total}").format(
        change_word=change_word,
        changes=change_spec,
        total=total_changes,
    )


def _review_source_summary(
    source: ReviewSource,
    batch_name: str | None,
    source_label: str,
) -> str:
    if source == ReviewSource.BATCH and batch_name:
        return batch_name
    prefix = _("Changes: ")
    if source_label.startswith(prefix):
        return source_label[len(prefix):]
    return source_label


def _print_header(
    path: str,
    *,
    source_label: str,
    source: ReviewSource,
    batch_name: str | None,
    note: str | None,
    shown_pages: tuple[int, ...],
    page_count: int,
    shown_change_spec: str,
    shown_line_spec: str,
    total_changes: int,
    opened_near_selected_hunk: bool,
) -> None:
    use_color = Colors.enabled()
    status = "  ·  ".join(
        (
            path,
            _review_source_summary(source, batch_name, source_label),
            _page_summary(shown_pages, page_count),
            _change_summary(shown_change_spec, total_changes),
            _("lines {lines}").format(lines=shown_line_spec),
        )
    )
    if use_color:
        print(f"{Colors.BOLD}{status}{Colors.RESET}")
    else:
        print(status)
    if opened_near_selected_hunk:
        message = _("Showing the area around the change you were viewing.")
        print(f"{Colors.GRAY}{message}{Colors.RESET}" if use_color else message)
    if note:
        note_lines = note.splitlines()
        if len(note_lines) == 1:
            note_text = _("Note: {note}").format(note=note_lines[0])
            print(f"{Colors.GRAY}{note_text}{Colors.RESET}" if use_color else note_text)
        else:
            note_label = _("Note:")
            print(f"{Colors.GRAY}{note_label}{Colors.RESET}" if use_color else note_label)
            for line in note_lines:
                print(f"    {line}")
    rule = "─" * 78
    print(f"{Colors.GRAY}{rule}{Colors.RESET}" if use_color else rule)


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


def _print_footer(
    path: str,
    *,
    shown_pages: tuple[int, ...],
    page_count: int,
    shown_change_spec: str,
    shown_line_spec: str,
    complete_line_action_selections: list[ActionableSelection],
    total_changes: int,
    page_spec: str,
    command_source_args: str,
    source: ReviewSource,
    batch_name: str | None,
) -> None:
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
        include_line_command = (
            line_action_command("include", review_state, line_spec=combined_selection, pathless_line=True)
            if review_state is not None else
            None
        )
        hints.append(
            (
                _("include"),
                include_line_command or f"git-stage-batch include{command_source_args} --line {combined_selection}",
            )
        )
        if not command_source_args:
            skip_line_command = (
                line_action_command("skip", review_state, line_spec=combined_selection, pathless_line=True)
                if review_state is not None else
                None
            )
            hints.append(
                (
                    _("skip"),
                    skip_line_command or f"git-stage-batch skip --line {combined_selection}",
                )
            )
        discard_line_command = (
            line_action_command("discard", review_state, line_spec=combined_selection, pathless_line=True)
            if review_state is not None else
            None
        )
        hints.append(
            (
                _("discard"),
                discard_line_command or f"git-stage-batch discard{command_source_args} --line {combined_selection}",
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
                    reset_line_command or f"git-stage-batch reset{command_source_args} --line {reset_selection}",
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
                reset_line_command or f"git-stage-batch reset{command_source_args} --line {reset_selection}",
            )
        )
    elif not is_entire:
        hints.append((_("No complete change is actionable from this page."), ""))

    if not is_entire and max(shown_pages) < page_count:
        navigation_hints.append(
            (
                _("next"),
                f"git-stage-batch show{command_source_args} --file {_quote(path)} --page {max(shown_pages) + 1}",
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
    if hints:
        print()
        use_color = Colors.enabled()
        rule = "─" * 78
        print(f"{Colors.GRAY}{rule}{Colors.RESET}" if use_color else rule)
        status = "  ·  ".join(
            (
                path,
                _page_summary(shown_pages, page_count),
                _change_summary(shown_change_spec, total_changes),
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
                print(f"{Colors.CYAN}{action_text}{Colors.RESET}  {_style_footer_command(display_command)}")
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
