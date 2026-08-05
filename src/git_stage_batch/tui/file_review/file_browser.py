"""File review file browser for interactive mode."""

from __future__ import annotations

import sys
from dataclasses import dataclass

from ...batch.state.query import read_batch_metadata
from ...data.file_tracking import list_untracked_files
from ...exceptions import CommandError
from ...i18n import _
from ...utils.file_patterns import list_changed_files, resolve_gitignore_style_patterns
from . import block_actions
from .batch_actions import apply_batch_file_action
from .live_actions import apply_live_file_action
from .prompts import normalize_review_action
from .session import FileReviewSessionState
from ..flow import FlowState, LocationRole
from ..prompts import confirm_destructive_operation, unlocked_input, wrap_prompt_for_readline


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
        batch_name = flow_state.source.require_batch_name()
        metadata = read_batch_metadata(batch_name)
        candidates = list(metadata.get("files", {}).keys())
    else:
        candidates = list(
            dict.fromkeys([*list_changed_files(), *list_untracked_files()])
        )

    if pattern:
        candidates = resolve_gitignore_style_patterns(candidates, [pattern])

    return [ReviewFileEntry(path=path) for path in candidates]


def choose_review_file(
    flow_state: FlowState,
    *,
    selected_path: str | None = None,
) -> str | None:
    """Prompt for a reviewable file path."""
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
            print(
                _("  {number} {mark} {path}{marker}").format(
                    number=f"[{index}]",
                    mark=f"[{mark}]",
                    path=entry.path,
                    marker=marker,
                )
            )

        print()
        try:
            choice = unlocked_input(
                wrap_prompt_for_readline(
                    _("File number, /pattern, m N, u N, i/s/d/B marked, or q: ")
                )
            ).strip()
        except (KeyboardInterrupt, EOFError):
            return None

        normalized_choice = normalize_review_action(choice)
        if normalized_choice == "q":
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
        if normalized_choice in {"i", "s", "d", "B"}:
            _apply_marked_file_action(
                flow_state,
                marked_paths,
                normalized_choice,
            )
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
        local_only = block_actions.prompt_block_local_only()
        if local_only is None:
            return

    for path in sorted(marked_paths):
        try:
            if action == "B":
                assert local_only is not None
                block_actions.block_review_file(path, local_only=local_only)
                continue

            state = FileReviewSessionState(flow_state=flow_state, file_path=path)
            if flow_state.source.role is LocationRole.BATCH:
                apply_batch_file_action(state, action)
            else:
                apply_live_file_action(state, action)
        except CommandError as e:
            print(e.message, file=sys.stderr)


def _normalize_marked_file_action(raw_action: str) -> str | None:
    action = normalize_review_action(raw_action.strip())
    if action == "B":
        return "B"
    if action == "i":
        return "I"
    if action == "s":
        return "S"
    if action == "d":
        return "D"
    return None
