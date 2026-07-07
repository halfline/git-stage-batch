"""Multi-file command action helpers."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
import shlex
import sys

from ...data.hunk_tracking import select_next_change_after_action, show_selected_change
from ...data.undo import undo_checkpoint
from ...i18n import _, ngettext
from ..discard import command_discard_files_to_batch
from ..include import command_include_file
from ..skip import command_skip_file


def _format_multi_file_operation(command: str, files: Sequence[str]) -> str:
    """Return a readable undo operation for a resolved multi-file command."""
    return f"{command} --files {' '.join(shlex.quote(file) for file in files)}"


def _multi_file_undo_checkpoint(
    command: str,
    files: Sequence[str],
    *,
    worktree_paths: Sequence[str] | None = None,
) -> AbstractContextManager[None]:
    """Create one undo checkpoint for a resolved multi-file command."""
    paths = list(worktree_paths) if worktree_paths is not None else None
    return undo_checkpoint(
        _format_multi_file_operation(command, files),
        worktree_paths=paths,
    )


def _format_file_summary(files: Sequence[str]) -> str:
    """Return a single path or plural file count for command output."""
    if len(files) == 1:
        return files[0]
    return ngettext(
        "{count} file",
        "{count} files",
        len(files),
    ).format(count=len(files))


def include_each_resolved_file(
    files: Sequence[str],
    *,
    auto_advance: bool | None = None,
) -> None:
    """Stage a multi-file live scope and report one aggregate summary."""
    total_hunks = 0
    staged_files: list[str] = []

    with _multi_file_undo_checkpoint("include", files):
        for file_path in files:
            staged_hunks = command_include_file(
                file_path,
                quiet=True,
                advance=False,
            )
            if staged_hunks > 0:
                total_hunks += staged_hunks
                staged_files.append(file_path)

    if total_hunks == 0:
        print(_("No hunks staged from matched files."), file=sys.stderr)
        return

    should_show_next = select_next_change_after_action(auto_advance=auto_advance)
    file_summary = _format_file_summary(staged_files)

    print(
        ngettext(
            "✓ Staged {count} hunk from {files}",
            "✓ Staged {count} hunks from {files}",
            total_hunks,
        ).format(count=total_hunks, files=file_summary),
        file=sys.stderr,
    )
    if should_show_next:
        show_selected_change()


def skip_each_resolved_file(
    files: Sequence[str],
    *,
    auto_advance: bool | None = None,
) -> None:
    """Skip a multi-file live scope and report one aggregate summary."""
    total_hunks = 0
    skipped_files: list[str] = []

    with _multi_file_undo_checkpoint("skip", files):
        for file_path in files:
            skipped_hunks = command_skip_file(
                file_path,
                quiet=True,
                advance=False,
            )
            if skipped_hunks > 0:
                total_hunks += skipped_hunks
                skipped_files.append(file_path)

    if total_hunks == 0:
        print(_("No hunks skipped from matched files."), file=sys.stderr)
        return

    should_show_next = select_next_change_after_action(auto_advance=auto_advance)
    file_summary = _format_file_summary(skipped_files)

    print(
        ngettext(
            "✓ Skipped {count} hunk from {files}",
            "✓ Skipped {count} hunks from {files}",
            total_hunks,
        ).format(count=total_hunks, files=file_summary),
        file=sys.stderr,
    )
    if should_show_next:
        show_selected_change()


def discard_to_batch_each_resolved_file(
    batch_name: str,
    files: Sequence[str],
    *,
    auto_advance: bool | None = None,
) -> None:
    """Save a multi-file live scope to a batch and report one aggregate summary."""
    operation = f"discard --to {shlex.quote(batch_name)}"
    with _multi_file_undo_checkpoint(operation, files, worktree_paths=files):
        result = command_discard_files_to_batch(
            batch_name,
            list(files),
            quiet=True,
            advance=False,
            auto_advance=auto_advance,
        )

    total_hunks = result.discarded_hunks
    discarded_files = result.discarded_files
    if total_hunks == 0:
        print(_("No hunks saved to batch from matched files."), file=sys.stderr)
        return

    should_show_next = select_next_change_after_action(auto_advance=auto_advance)
    file_summary = _format_file_summary(discarded_files)

    print(
        ngettext(
            "✓ Saved {count} hunk from {files} to batch '{batch}' and discarded it",
            "✓ Saved {count} hunks from {files} to batch '{batch}' and discarded them",
            total_hunks,
        ).format(count=total_hunks, files=file_summary, batch=batch_name),
        file=sys.stderr,
    )
    if should_show_next:
        show_selected_change()
