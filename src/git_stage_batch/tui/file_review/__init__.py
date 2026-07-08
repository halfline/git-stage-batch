"""File review browser for interactive mode."""

from __future__ import annotations

import sys
from dataclasses import dataclass

from ...batch.query import read_batch_metadata
from ...data.file_review.state import read_last_file_review_state
from ...data.line_state import load_line_changes_from_state
from ...data.file_tracking import list_untracked_files
from ...exceptions import BypassRefresh, CommandError
from ...i18n import _
from ...utils.file_patterns import list_changed_files, resolve_gitignore_style_patterns
from .batch_actions import (
    apply_batch_file_action,
    apply_batch_line_action,
    apply_batch_replacement_action,
)
from .block_actions import block_review_file, unblock_review_file
from .candidates import browse_candidates
from .display import render_file_review
from .fixup_actions import (
    clear_file_review_fixup_state,
    read_last_fixup_commit_hash,
    suggest_fixup_for_lines,
)
from .live_actions import (
    apply_live_file_action,
    apply_live_line_action,
    apply_live_replacement_action,
)
from .prompts import (
    normalize_review_action,
    print_review_help,
    prompt_review_action,
)
from ..flow import FlowState, LocationRole
from ..prompts import (
    confirm_destructive_operation,
    prompt_fixup_action,
    prompt_line_ids,
    wrap_prompt_for_readline,
)


@dataclass
class FileReviewSessionState:
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

    state = FileReviewSessionState(
        flow_state=flow_state,
        file_path=line_changes.path,
    )
    _review_loop(state)
    raise BypassRefresh()


def handle_file_browser(flow_state: FlowState) -> None:
    """Open a file chooser and review the selected file."""
    selected_file = _choose_file(flow_state)
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
            selected_file = _choose_file(state.flow_state, selected_path=state.file_path)
            if selected_file is not None:
                state.file_path = selected_file
                state.page_spec = None
            continue
        if normalized in {"i", "s", "d"}:
            _apply_line_action(state, normalized)
            continue
        if normalized == "r":
            _apply_replacement_action(state)
            continue
        if normalized == "x":
            _apply_fixup_action(state)
            continue
        if normalized == "c":
            browse_candidates(state)
            continue
        if normalized in {"I", "S", "D"}:
            _apply_file_action(state, normalized)
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


def _prompt_replacement_text() -> str | None:
    try:
        value = input(
            wrap_prompt_for_readline(_("Replacement text (empty cancels): "))
        )
    except (KeyboardInterrupt, EOFError):
        return None
    if value == "":
        return None
    return value


def _prompt_block_local_only() -> bool | None:
    try:
        choice = input(
            wrap_prompt_for_readline(
                _("Block target [g]itignore, [l]ocal exclude, or q: ")
            )
        ).strip().lower()
    except (KeyboardInterrupt, EOFError):
        return None

    if choice in {"q", "quit", "cancel"}:
        return None
    if choice in {"l", "local", "local exclude"}:
        return True
    if choice in {"", "g", "gitignore"}:
        return False

    print(_("Invalid block target."), file=sys.stderr)
    return None


def _choose_file(
    flow_state: FlowState,
    *,
    selected_path: str | None = None,
) -> str | None:
    pattern: str | None = None
    marked_paths: set[str] = set()

    while True:
        try:
            entries = list_review_file_entries(flow_state, pattern=pattern)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            pattern = None
            continue

        if not entries:
            if pattern:
                print(_("No files matched pattern '{pattern}'.").format(pattern=pattern))
            else:
                print(_("No files to review."))
            return None

        visible_paths = {entry.path for entry in entries}
        marked_paths.intersection_update(visible_paths)

        print()
        print(_("Files to review:"))
        for index, entry in enumerate(entries, start=1):
            marker = " *" if entry.path == selected_path else ""
            mark = "*" if entry.path in marked_paths else " "
            print(f"  [{index}] [{mark}] {entry.path}{marker}")

        print()
        try:
            choice = input(
                wrap_prompt_for_readline(
                    _("File number, /pattern, m N, u N, i/s/d/B marked, or q: ")
                )
            ).strip()
        except (KeyboardInterrupt, EOFError):
            return None

        if choice in {"q", "quit", "back"}:
            return None
        if choice.startswith("/"):
            pattern = choice[1:] or None
            continue
        if choice.startswith("m "):
            _mark_file_choice(choice[2:], entries, marked_paths)
            continue
        if choice.startswith("u "):
            _unmark_file_choice(choice[2:], entries, marked_paths)
            continue
        if choice in {"i", "include", "s", "skip", "d", "discard", "B", "block"}:
            _apply_marked_file_action(flow_state, marked_paths, choice)
            marked_paths.clear()
            continue
        if choice.isdigit():
            index = int(choice) - 1
            if 0 <= index < len(entries):
                return entries[index].path

        print(_("Invalid file selection."), file=sys.stderr)


def _mark_file_choice(
    choice: str,
    entries: list[ReviewFileEntry],
    marked_paths: set[str],
) -> None:
    path = _file_choice_to_path(choice, entries)
    if path is None:
        print(_("Invalid file selection."), file=sys.stderr)
        return
    marked_paths.add(path)


def _unmark_file_choice(
    choice: str,
    entries: list[ReviewFileEntry],
    marked_paths: set[str],
) -> None:
    path = _file_choice_to_path(choice, entries)
    if path is None:
        print(_("Invalid file selection."), file=sys.stderr)
        return
    marked_paths.discard(path)


def _file_choice_to_path(choice: str, entries: list[ReviewFileEntry]) -> str | None:
    value = choice.strip()
    if value.isdigit():
        index = int(value) - 1
        if 0 <= index < len(entries):
            return entries[index].path
    for entry in entries:
        if entry.path == value:
            return entry.path
    return None


def _apply_marked_file_action(
    flow_state: FlowState,
    marked_paths: set[str],
    raw_action: str,
) -> None:
    if not marked_paths:
        print(_("No files marked."), file=sys.stderr)
        return

    action = _normalize_marked_file_action(raw_action)
    if action is None:
        print(_("Invalid marked file action."), file=sys.stderr)
        return
    if action == "S" and flow_state.source.role is LocationRole.BATCH:
        print(_("Skip is not available when pulling from a batch."), file=sys.stderr)
        return
    if action == "B" and flow_state.source.role is LocationRole.BATCH:
        print(_("Block is not available when pulling from a batch."), file=sys.stderr)
        return

    if action == "D" and flow_state.source.role is LocationRole.WORKING_TREE:
        if not confirm_destructive_operation(
            "discard",
            _("This will discard the marked files from your working tree."),
        ):
            return

    local_only = None
    if action == "B":
        if not confirm_destructive_operation(
            "block",
            _("This will add the marked files to ignore state."),
        ):
            return
        local_only = _prompt_block_local_only()
        if local_only is None:
            return

    for path in sorted(marked_paths):
        try:
            if action == "B":
                block_review_file(path, local_only=local_only)
                continue

            state = FileReviewSessionState(flow_state=flow_state, file_path=path)
            if flow_state.source.role is LocationRole.BATCH:
                apply_batch_file_action(state, action)
            else:
                apply_live_file_action(state, action)
        except CommandError as e:
            print(e.message, file=sys.stderr)


def _normalize_marked_file_action(raw_action: str) -> str | None:
    action = raw_action.strip()
    if action in {"B", "block"}:
        return "B"
    lowered = action.lower()
    if lowered in {"i", "include"}:
        return "I"
    if lowered in {"s", "skip"}:
        return "S"
    if lowered in {"d", "discard"}:
        return "D"
    return None


def _apply_replacement_action(state: FileReviewSessionState) -> None:
    line_ids = prompt_line_ids()
    if not line_ids:
        return

    replacement_text = _prompt_replacement_text()
    if replacement_text is None:
        return

    try:
        if state.flow_state.source.role is LocationRole.BATCH:
            apply_batch_replacement_action(state, line_ids, replacement_text)
        else:
            apply_live_replacement_action(state, line_ids, replacement_text)
    except CommandError as e:
        print(e.message, file=sys.stderr)


def _apply_line_action(state: FileReviewSessionState, action: str) -> None:
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
            apply_batch_line_action(state, action, line_ids)
        else:
            apply_live_line_action(state, action, line_ids)
    except CommandError as e:
        print(e.message, file=sys.stderr)


def _apply_file_action(state: FileReviewSessionState, action: str) -> None:
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
            apply_batch_file_action(state, action)
        else:
            apply_live_file_action(state, action)
    except CommandError as e:
        print(e.message, file=sys.stderr)


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
