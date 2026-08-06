"""Multi-file review list output."""

from __future__ import annotations

from dataclasses import dataclass

from ..core.models import (
    BinaryFileChange,
    FileModeChange,
    GitlinkChange,
    LineLevelChange,
    RenameChange,
    TextFileDeletionChange,
)
from ..git_paths import display_path, terminal_safe_shell_quote
from ..i18n import _, ngettext, npgettext
from .file_review_model_builder import build_file_review_model
from .terminal_width import pad_to_terminal_width, terminal_cell_width


@dataclass(frozen=True)
class FileReviewListEntry:
    """One file listed in a multi-file review list."""

    path: str
    change_count: int
    changed_line_count: int
    addition_count: int
    deletion_count: int
    page_count: int
    binary_change_type: str | None = None
    gitlink_change_type: str | None = None
    text_deletion: bool = False
    mode_change: bool = False
    rename_old_path: str | None = None
    rename_new_path: str | None = None


def _change_type_label(change_type: str) -> str:
    """Return a localized atomic-file change type."""
    return {
        "added": _("added"),
        "deleted": _("deleted"),
        "modified": _("modified"),
    }.get(change_type, change_type)


def make_file_review_list_entry(
    line_changes: LineLevelChange,
    *,
    gutter_to_selection_id: dict[int, int] | None = None,
) -> FileReviewListEntry:
    """Build a list entry from a file review model."""
    model = build_file_review_model(line_changes, gutter_to_selection_id=gutter_to_selection_id)
    actionable_selection_ids = (
        set(gutter_to_selection_id.values())
        if gutter_to_selection_id is not None else
        {line.id for line in line_changes.lines if line.id is not None}
    )
    addition_count = sum(1 for line in line_changes.lines if line.kind == "+" and line.id in actionable_selection_ids)
    deletion_count = sum(1 for line in line_changes.lines if line.kind == "-" and line.id in actionable_selection_ids)
    return FileReviewListEntry(
        path=line_changes.path,
        change_count=len(model.changes),
        changed_line_count=addition_count + deletion_count,
        addition_count=addition_count,
        deletion_count=deletion_count,
        page_count=len(model.pages),
    )


def make_binary_file_review_list_entry(binary_change: BinaryFileChange) -> FileReviewListEntry:
    """Build a list entry from a binary file change."""
    path = binary_change.path()
    return FileReviewListEntry(
        path=path,
        change_count=1,
        changed_line_count=0,
        addition_count=0,
        deletion_count=0,
        page_count=1,
        binary_change_type=binary_change.change_type,
    )


def make_gitlink_file_review_list_entry(gitlink_change: GitlinkChange) -> FileReviewListEntry:
    """Build a list entry from a gitlink change."""
    return FileReviewListEntry(
        path=gitlink_change.path(),
        change_count=1,
        changed_line_count=0,
        addition_count=0,
        deletion_count=0,
        page_count=1,
        gitlink_change_type=gitlink_change.change_type,
    )


def make_mode_file_review_list_entry(mode_change: FileModeChange) -> FileReviewListEntry:
    """Build a list entry from an executable-mode action."""
    return FileReviewListEntry(
        path=mode_change.path(),
        change_count=1,
        changed_line_count=0,
        addition_count=0,
        deletion_count=0,
        page_count=1,
        mode_change=True,
    )


def make_rename_file_review_list_entry(rename_change: RenameChange) -> FileReviewListEntry:
    """Build a list entry from a rename change."""
    return FileReviewListEntry(
        path=rename_change.new_path,
        change_count=1,
        changed_line_count=0,
        addition_count=0,
        deletion_count=0,
        page_count=1,
        rename_old_path=rename_change.old_path,
        rename_new_path=rename_change.new_path,
    )


def make_text_deletion_file_review_list_entry(deletion_change: TextFileDeletionChange) -> FileReviewListEntry:
    """Build a list entry from a whole-text-file deletion."""
    return FileReviewListEntry(
        path=deletion_change.path(),
        change_count=1,
        changed_line_count=0,
        addition_count=0,
        deletion_count=0,
        page_count=1,
        text_deletion=True,
    )


def print_file_review_list(
    *,
    source_label: str,
    entries: list[FileReviewListEntry],
    command_source_args: str = "",
) -> None:
    """Print a navigational file list for multiple file reviews."""
    print(_("── matched files ") + "─" * 55)
    print(source_label)
    total_changes = sum(entry.change_count for entry in entries)
    total_lines = sum(entry.changed_line_count for entry in entries)
    file_summary = npgettext(
        "file review file count",
        "{count} file",
        "{count} files",
        len(entries),
    ).format(count=len(entries))
    change_summary = ngettext(
        "{count} change",
        "{count} changes",
        total_changes,
    ).format(count=total_changes)
    line_summary = ngettext(
        "{count} changed line",
        "{count} changed lines",
        total_lines,
    ).format(count=total_lines)
    print(
        _("Matched: {files} · {changes} · {lines}").format(
            files=file_summary,
            changes=change_summary,
            lines=line_summary,
        )
    )
    print()
    rendered_paths = [display_path(entry.path) for entry in entries]
    path_width = max(
        (terminal_cell_width(path) for path in rendered_paths),
        default=4,
    )
    for index, (entry, rendered_path) in enumerate(
        zip(entries, rendered_paths),
        start=1,
    ):
        padded_path = pad_to_terminal_width(rendered_path, path_width)
        entry_changes = ngettext(
            "{count} change",
            "{count} changes",
            entry.change_count,
        ).format(count=entry.change_count)
        entry_pages = ngettext(
            "{count} page",
            "{count} pages",
            entry.page_count,
        ).format(count=entry.page_count)
        if entry.gitlink_change_type is not None:
            detail = _("submodule pointer {change_type}").format(
                change_type=_change_type_label(entry.gitlink_change_type),
            )
        elif entry.text_deletion:
            detail = _("text file deleted")
        elif entry.mode_change:
            detail = _("executable mode")
        elif entry.rename_old_path is not None and entry.rename_new_path is not None:
            detail = _("rename {old} -> {new}").format(
                old=display_path(entry.rename_old_path),
                new=display_path(entry.rename_new_path),
            )
        elif entry.binary_change_type is not None:
            detail = _("binary file {change_type}").format(
                change_type=_change_type_label(entry.binary_change_type),
            )
        else:
            detail = "+{additions}/-{deletions}".format(
                additions=entry.addition_count,
                deletions=entry.deletion_count,
            )
        print(
            _("{index}. {path}  {changes} · {detail} · {pages}").format(
                index=index,
                path=padded_path,
                changes=entry_changes,
                detail=detail,
                pages=entry_pages,
            )
        )

    if entries:
        print()
        print(_("Open:"))
        for entry in entries[:5]:
            command = (
                f"git-stage-batch show{command_source_args} "
                f"--file {terminal_safe_shell_quote(entry.path)}"
            )
            print(_("  {command}").format(command=command))
        if len(entries) > 5:
            remaining = len(entries) - 5
            print(
                ngettext(
                    "  ... {count} more file matched",
                    "  ... {count} more files matched",
                    remaining,
                ).format(count=remaining)
            )
