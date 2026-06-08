"""File review browser for interactive mode."""

from __future__ import annotations

import sys
from dataclasses import dataclass

from ..batch.query import read_batch_metadata
from ..data.line_state import load_line_changes_from_state
from ..data.file_tracking import list_untracked_files
from ..data.progress import get_hunk_counts
from ..exceptions import BypassRefresh, CommandError
from ..i18n import _
from ..utils.file_patterns import list_changed_files, resolve_gitignore_style_patterns
from .display import print_status_bar
from .flow import FlowState, LocationRole
from .prompts import (
    confirm_destructive_operation,
    prompt_line_ids,
    wrap_prompt_for_readline,
)


@dataclass
class FileReviewState:
    """State for one interactive file review session."""

    flow_state: FlowState
    file_path: str
    page_spec: str | None = None


@dataclass(frozen=True)
class ReviewFileEntry:
    """One file that can be opened from a TUI file review source."""

    path: str


def list_review_file_entries(
    flow_state: FlowState,
    pattern: str | None = None,
) -> list[ReviewFileEntry]:
    """Return reviewable files for the current interactive source."""
    if flow_state.source.role is LocationRole.BATCH:
        batch_name = flow_state.source.batch_name
        metadata = read_batch_metadata(batch_name)
        candidates = list(metadata.get("files", {}).keys())
    else:
        candidates = list(
            dict.fromkeys([*list_changed_files(), *list_untracked_files()])
        )

    if pattern:
        candidates = resolve_gitignore_style_patterns(candidates, [pattern])

    return [ReviewFileEntry(path=path) for path in candidates]


def handle_current_file_review(flow_state: FlowState) -> None:
    """Open a file review for the current selected file."""
    line_changes = load_line_changes_from_state()
    if line_changes is None:
        print(_("No current file to review."), file=sys.stderr)
        raise BypassRefresh()

    state = FileReviewState(
        flow_state=flow_state,
        file_path=line_changes.path,
    )
    _review_loop(state)
    raise BypassRefresh()


def _review_loop(state: FileReviewState) -> None:
    while True:
        if not _render_review(state):
            return

        action = _prompt_review_action(state.flow_state)
        normalized = _normalize_review_action(action)

        if normalized in {"q", "back", "quit"}:
            return
        if normalized in {"?", "help"}:
            _print_review_help(state.flow_state)
            continue
        if normalized in {"g", "page"}:
            state.page_spec = _prompt_page_spec()
            continue
        if normalized in {"i", "s", "d"}:
            _apply_line_action(state, normalized)
            continue
        if normalized in {"I", "S", "D"}:
            _apply_file_action(state, normalized)
            continue

        print(_("Unknown review action: {action}").format(action=action))


def _render_review(state: FileReviewState) -> bool:
    print()
    print_status_bar(get_hunk_counts(), state.flow_state)
    print()

    try:
        if state.flow_state.source.role is LocationRole.BATCH:
            from ..commands.show_from import command_show_from_batch

            command_show_from_batch(
                state.flow_state.source.batch_name,
                file=state.file_path,
                page=state.page_spec,
                selectable=True,
            )
        else:
            from ..commands.show import command_show

            command_show(
                file=state.file_path,
                page=state.page_spec,
                selectable=True,
            )
    except CommandError as e:
        print(e.message, file=sys.stderr)
        return False
    return True


def _prompt_review_action(flow_state: FlowState) -> str:
    print()
    if flow_state.source.role is LocationRole.BATCH:
        print(
            _(
                "Review action: [i]nclude lines [d]iscard lines "
                "[I]include file [D]discard file [g]page [q]back [?]help"
            )
        )
    else:
        print(
            _(
                "Review action: [i]nclude lines [s]kip lines [d]iscard lines "
                "[I]include file [S]skip file [D]discard file "
                "[g]page [q]back [?]help"
            )
        )

    try:
        return input(wrap_prompt_for_readline(_("Action: "))).strip()
    except (KeyboardInterrupt, EOFError):
        return "q"


def _normalize_review_action(action: str) -> str:
    if action in {"I", "S", "D"}:
        return action

    lowered = action.lower()
    word_to_action = {
        "include": "i",
        "skip": "s",
        "discard": "d",
        "include-file": "I",
        "include file": "I",
        "skip-file": "S",
        "skip file": "S",
        "discard-file": "D",
        "discard file": "D",
        "page": "g",
        "goto": "g",
        "back": "q",
        "quit": "q",
        "help": "?",
    }
    return word_to_action.get(lowered, lowered)


def _prompt_page_spec() -> str | None:
    try:
        value = input(
            wrap_prompt_for_readline(_("Page(s), for example 1, 2-4, all: "))
        ).strip()
    except (KeyboardInterrupt, EOFError):
        return None
    return value or None


def _apply_line_action(state: FileReviewState, action: str) -> None:
    if action == "s" and state.flow_state.source.role is LocationRole.BATCH:
        print(_("Skip is not available when pulling from a batch."), file=sys.stderr)
        return

    line_ids = prompt_line_ids()
    if not line_ids:
        return

    if action == "d" and state.flow_state.source.role is LocationRole.WORKING_TREE:
        if not confirm_destructive_operation(
            "discard",
            _("This will discard the selected lines from your working tree."),
        ):
            return

    try:
        if state.flow_state.source.role is LocationRole.BATCH:
            _apply_batch_line_action(state, action, line_ids)
        else:
            _apply_live_line_action(state, action, line_ids)
    except CommandError as e:
        print(e.message, file=sys.stderr)


def _apply_file_action(state: FileReviewState, action: str) -> None:
    if action == "S" and state.flow_state.source.role is LocationRole.BATCH:
        print(_("Skip is not available when pulling from a batch."), file=sys.stderr)
        return

    if action == "D" and state.flow_state.source.role is LocationRole.WORKING_TREE:
        if not confirm_destructive_operation(
            "discard",
            _("This will discard the reviewed file from your working tree."),
        ):
            return

    try:
        if state.flow_state.source.role is LocationRole.BATCH:
            _apply_batch_file_action(state, action)
        else:
            _apply_live_file_action(state, action)
    except CommandError as e:
        print(e.message, file=sys.stderr)


def _apply_live_line_action(
    state: FileReviewState,
    action: str,
    line_ids: str,
) -> None:
    if action == "i":
        if state.flow_state.target.role is LocationRole.BATCH:
            from ..commands.include import command_include_to_batch

            command_include_to_batch(
                state.flow_state.target.batch_name,
                line_ids=line_ids,
                file=state.file_path,
                quiet=True,
                auto_advance=False,
            )
            return

        from ..commands.include import command_include_line

        command_include_line(line_ids, file=state.file_path, auto_advance=False)
        return

    if action == "s":
        if state.flow_state.target.role is LocationRole.BATCH:
            from ..commands.include import command_include_to_batch

            command_include_to_batch(
                state.flow_state.target.batch_name,
                line_ids=line_ids,
                file=state.file_path,
                quiet=True,
                auto_advance=False,
            )
            return

        from ..commands.skip import command_skip_line

        command_skip_line(line_ids, file=state.file_path, auto_advance=False)
        return

    if state.flow_state.target.role is LocationRole.BATCH:
        from ..commands.discard import command_discard_to_batch

        command_discard_to_batch(
            state.flow_state.target.batch_name,
            line_ids=line_ids,
            file=state.file_path,
            quiet=True,
            auto_advance=False,
        )
        return

    from ..commands.discard import command_discard_line

    command_discard_line(line_ids, file=state.file_path, auto_advance=False)


def _apply_live_file_action(state: FileReviewState, action: str) -> None:
    if action == "I":
        if state.flow_state.target.role is LocationRole.BATCH:
            from ..commands.include import command_include_to_batch

            command_include_to_batch(
                state.flow_state.target.batch_name,
                file=state.file_path,
                quiet=True,
                auto_advance=False,
            )
            return

        from ..commands.include import command_include_file

        command_include_file(
            state.file_path,
            quiet=True,
            advance=False,
            auto_advance=False,
        )
        return

    if action == "S":
        if state.flow_state.target.role is LocationRole.BATCH:
            from ..commands.include import command_include_to_batch

            command_include_to_batch(
                state.flow_state.target.batch_name,
                file=state.file_path,
                quiet=True,
                auto_advance=False,
            )
            return

        from ..commands.skip import command_skip_file

        command_skip_file(
            state.file_path,
            quiet=True,
            advance=False,
            auto_advance=False,
        )
        return

    if state.flow_state.target.role is LocationRole.BATCH:
        from ..commands.discard import command_discard_to_batch

        command_discard_to_batch(
            state.flow_state.target.batch_name,
            file=state.file_path,
            quiet=True,
            advance=False,
            auto_advance=False,
        )
        return

    from ..commands.discard import command_discard_file

    command_discard_file(state.file_path, auto_advance=False)


def _apply_batch_line_action(
    state: FileReviewState,
    action: str,
    line_ids: str,
) -> None:
    if state.flow_state.target.role is not LocationRole.STAGING_AREA:
        print(
            _("Batch-to-batch transfers not yet supported. Target must be staging."),
            file=sys.stderr,
        )
        return

    if action == "i":
        from ..commands.include_from import command_include_from_batch

        command_include_from_batch(
            state.flow_state.source.batch_name,
            line_ids=line_ids,
            file=state.file_path,
        )
        return

    from ..commands.discard_from import command_discard_from_batch

    command_discard_from_batch(
        state.flow_state.source.batch_name,
        line_ids=line_ids,
        file=state.file_path,
    )


def _apply_batch_file_action(state: FileReviewState, action: str) -> None:
    if state.flow_state.target.role is not LocationRole.STAGING_AREA:
        print(
            _("Batch-to-batch transfers not yet supported. Target must be staging."),
            file=sys.stderr,
        )
        return

    if action == "I":
        from ..commands.include_from import command_include_from_batch

        command_include_from_batch(
            state.flow_state.source.batch_name,
            file=state.file_path,
        )
        return

    from ..commands.discard_from import command_discard_from_batch

    command_discard_from_batch(
        state.flow_state.source.batch_name,
        file=state.file_path,
    )


def _print_review_help(flow_state: FlowState) -> None:
    print()
    print(_("File Review Commands:"))
    print(_("  i, include       Include selected file-review line IDs"))
    if flow_state.source.role is not LocationRole.BATCH:
        print(_("  s, skip          Skip selected file-review line IDs"))
    print(_("  d, discard       Discard selected file-review line IDs"))
    print(_("  I                Include the reviewed file"))
    if flow_state.source.role is not LocationRole.BATCH:
        print(_("  S                Skip the reviewed file"))
    print(_("  D                Discard the reviewed file"))
    print(_("  g, page          Show a page or page range"))
    print(_("  q, back          Return to hunk review"))
