"""Suggest-fixup command helpers for file review."""

from __future__ import annotations


def suggest_fixup_for_lines(
    line_ids: str,
    *,
    file_path: str,
    reset: bool = False,
) -> None:
    """Show a suggest-fixup candidate for reviewed line IDs."""
    from ...commands.suggest_fixup import command_suggest_fixup_line

    if reset:
        command_suggest_fixup_line(line_ids, file=file_path, reset=True)
        return

    command_suggest_fixup_line(line_ids, file=file_path)


def read_last_fixup_commit_hash() -> str | None:
    """Return the last shown fixup commit hash for review display."""
    from ...data.suggest_fixup_state import read_suggest_fixup_state

    fixup_state = read_suggest_fixup_state()
    if fixup_state and fixup_state.get("last_shown_commit"):
        return fixup_state["last_shown_commit"][:7]
    return None


def clear_file_review_fixup_state() -> None:
    """Clear persisted suggest-fixup selection state."""
    from ...data.suggest_fixup_state import clear_suggest_fixup_state

    clear_suggest_fixup_state()
