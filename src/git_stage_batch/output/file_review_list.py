"""Multi-file review list output."""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from ..core.models import BinaryFileChange, GitlinkChange, LineLevelChange
from ..i18n import _
from .file_review import build_file_review_model


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
    path = binary_change.new_path if binary_change.new_path != "/dev/null" else binary_change.old_path
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


def print_file_review_list(
    *,
    source_label: str,
    entries: list[FileReviewListEntry],
    command_source_args: str = "",
) -> None:
    """Print a navigational file list for multiple file reviews."""
    print("── matched files " + "─" * 55)
    print(source_label)
    total_changes = sum(entry.change_count for entry in entries)
    total_lines = sum(entry.changed_line_count for entry in entries)
    print(
        _("Matched: {files} files · {changes} changes · {lines} changed lines").format(
            files=len(entries),
            changes=total_changes,
            lines=total_lines,
        )
    )
    print()
    path_width = max((len(entry.path) for entry in entries), default=4)
    for index, entry in enumerate(entries, start=1):
        change_word = _("change") if entry.change_count == 1 else _("changes")
        page_word = _("page") if entry.page_count == 1 else _("pages")
        if entry.gitlink_change_type is not None:
            print(
                f"{index}. {entry.path.ljust(path_width)}  "
                f"{entry.change_count} {change_word} · "
                f"submodule pointer {entry.gitlink_change_type} · "
                f"{entry.page_count} {page_word}"
            )
        elif entry.binary_change_type is not None:
            print(
                f"{index}. {entry.path.ljust(path_width)}  "
                f"{entry.change_count} {change_word} · "
                f"binary {entry.binary_change_type} · "
                f"{entry.page_count} {page_word}"
            )
        else:
            print(
                f"{index}. {entry.path.ljust(path_width)}  "
                f"{entry.change_count} {change_word} · "
                f"+{entry.addition_count}/-{entry.deletion_count} · "
                f"{entry.page_count} {page_word}"
            )

    if entries:
        print()
        print(_("Open:"))
        for entry in entries[:5]:
            print(f"  git-stage-batch show{command_source_args} --file {shlex.quote(entry.path)}")
        if len(entries) > 5:
            remaining = len(entries) - 5
            print(_("  ... {count} more files matched").format(count=remaining))
