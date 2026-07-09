"""File review browser for interactive mode."""

from __future__ import annotations

import sys

from ...data.file_review.state import read_last_file_review_state
from ...data.line_state import load_line_changes_from_state
from ...exceptions import BypassRefresh, CommandError
from ...i18n import _
from .action_router import (
    apply_file_action,
    apply_line_action,
    apply_replacement_action,
)
from .block_actions import block_review_file, unblock_review_file
from .candidates import browse_candidates
from .display import render_file_review
from .file_browser import (
    choose_review_file,
    prompt_block_local_only as _prompt_block_local_only,
)
from .fixup_actions import (
    clear_file_review_fixup_state,
    read_last_fixup_commit_hash,
    suggest_fixup_for_lines,
)
from .prompts import (
    normalize_review_action,
    print_review_help,
    prompt_review_action,
)
from .session import FileReviewSessionState
from ..flow import FlowState, LocationRole
from ..prompts import (
    confirm_destructive_operation,
    prompt_fixup_action,
    prompt_line_ids,
    wrap_prompt_for_readline,
)


def handle_current_file_review(flow_state: FlowState) -> None:
    """Open a file review for the current selected file."""
    line_changes = load_line_changes_from_state()
    if line_changes is None:
        print(_("No current file to review."), file=sys.stderr)
        raise BypassRefresh()

    state = FileReviewSessionState(
        flow_state=flow_state,
        file_path=line_changes.path,
    )
    _review_loop(state)
    raise BypassRefresh()


def handle_file_browser(flow_state: FlowState) -> None:
    """Open a file chooser and review the selected file."""
    selected_file = choose_review_file(flow_state)
    if selected_file is None:
        raise BypassRefresh()

    _review_loop(
        FileReviewSessionState(flow_state=flow_state, file_path=selected_file)
    )
    raise BypassRefresh()


def _review_loop(state: FileReviewSessionState) -> None:
    while True:
        if not render_file_review(
            state.flow_state,
            file_path=state.file_path,
            page_spec=state.page_spec,
        ):
            return

        action = prompt_review_action(state.flow_state)
        normalized = normalize_review_action(action)

        if normalized in {"q", "back", "quit"}:
            return
        if normalized in {"?", "help"}:
            print_review_help(state.flow_state)
            continue
        if normalized in {"g", "page"}:
            state.page_spec = _prompt_page_spec()
            continue
        if normalized in {"n", "next"}:
            state.page_spec = _next_page_spec()
            continue
        if normalized in {"p", "prev", "previous"}:
            state.page_spec = _previous_page_spec()
            continue
        if normalized in {"o", "open"}:
            selected_file = choose_review_file(
                state.flow_state,
                selected_path=state.file_path,
            )
            if selected_file is not None:
                state.file_path = selected_file
                state.page_spec = None
            continue
        if normalized in {"i", "s", "d"}:
            apply_line_action(state, normalized)
            continue
        if normalized == "r":
            apply_replacement_action(state)
            continue
        if normalized == "x":
            _apply_fixup_action(state)
            continue
        if normalized == "c":
            browse_candidates(state)
            continue
        if normalized in {"I", "S", "D"}:
            apply_file_action(state, normalized)
            continue
        if normalized in {"B", "U"}:
            _apply_block_action(state, normalized)
            continue

        print(_("Unknown review action: {action}").format(action=action))


def _prompt_page_spec() -> str | None:
    try:
        value = input(
            wrap_prompt_for_readline(_("Page(s), for example 1, 2-4, all: "))
        ).strip()
    except (KeyboardInterrupt, EOFError):
        return None
    return value or None


def _next_page_spec() -> str | None:
    review_state = read_last_file_review_state()
    if review_state is None:
        print(_("No file review page state is available."), file=sys.stderr)
        return None

    current_page = max(review_state.shown_pages)
    if current_page >= review_state.page_count:
        print(_("Already at the last file review page."), file=sys.stderr)
        return review_state.page_spec

    return str(current_page + 1)


def _previous_page_spec() -> str | None:
    review_state = read_last_file_review_state()
    if review_state is None:
        print(_("No file review page state is available."), file=sys.stderr)
        return None

    current_page = min(review_state.shown_pages)
    if current_page <= 1:
        print(_("Already at the first file review page."), file=sys.stderr)
        return review_state.page_spec

    return str(current_page - 1)


def _apply_block_action(state: FileReviewSessionState, action: str) -> None:
    if action == "B":
        if not confirm_destructive_operation(
            "block",
            _("This will add the reviewed file to ignore state."),
        ):
            return

        local_only = _prompt_block_local_only()
        if local_only is None:
            return

        try:
            block_review_file(state.file_path, local_only=local_only)
        except CommandError as e:
            print(e.message, file=sys.stderr)
        return

    try:
        unblock_review_file(state.file_path)
    except CommandError as e:
        print(e.message, file=sys.stderr)


def _apply_fixup_action(state: FileReviewSessionState) -> None:
    if state.flow_state.source.role is LocationRole.BATCH:
        print(_("Suggest-fixup is not available when pulling from a batch."), file=sys.stderr)
        return

    line_ids = prompt_line_ids()
    if not line_ids:
        return

    use_color = sys.stdout.isatty()

    try:
        suggest_fixup_for_lines(line_ids, file_path=state.file_path)
    except CommandError as e:
        print(e.message, file=sys.stderr)
        return

    while True:
        print()
        action = prompt_fixup_action(use_color=use_color)

        if action == "y":
            commit_hash = read_last_fixup_commit_hash()
            if commit_hash is not None:
                print()
                print(_("Create fixup commit with:"))
                print(f"  git commit --fixup={commit_hash}")
                print()
            return
        if action == "n":
            try:
                suggest_fixup_for_lines(line_ids, file_path=state.file_path)
            except CommandError as e:
                print(e.message, file=sys.stderr)
                return
            continue
        if action == "r":
            try:
                suggest_fixup_for_lines(
                    line_ids,
                    file_path=state.file_path,
                    reset=True,
                )
            except CommandError as e:
                print(e.message, file=sys.stderr)
                return
            continue
        if action == "q":
            clear_file_review_fixup_state()
            print(_("\nCanceled."))
            return

        print(_("Unknown action: {action}").format(action=action))
