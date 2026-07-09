"""File review browser for interactive mode."""

from __future__ import annotations

import sys

from ...data.line_state import load_line_changes_from_state
from ...exceptions import BypassRefresh
from ...i18n import _
from .action_router import (
    apply_file_action,
    apply_line_action,
    apply_replacement_action,
)
from .block_actions import apply_block_action as _apply_block_action
from .candidates import browse_candidates
from .display import render_file_review
from .file_browser import choose_review_file
from .fixup_actions import apply_fixup_action as _apply_fixup_action
from .page_navigation import (
    next_page_spec as _next_page_spec,
    previous_page_spec as _previous_page_spec,
    prompt_page_spec as _prompt_page_spec,
)
from .prompts import (
    normalize_review_action,
    print_review_help,
    prompt_review_action,
)
from .session import FileReviewSessionState
from ..flow import FlowState


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
