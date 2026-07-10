"""Human-readable status rendering."""

from __future__ import annotations

from ..data.progress import format_id_range
from ..data.selected_change.store import SelectedChangeKind
from ..i18n import _


def print_status_summary(summary: dict) -> None:
    """Print a human-readable status summary."""
    iteration = summary["session"]["iteration"]
    status_value = summary["session"]["status"]
    selected_summary = summary["selected_change"]
    file_review_summary = summary["file_review"]
    progress = summary["progress"]
    skipped_hunks = summary["skipped_hunks"]
    status_label = _("in progress") if status_value == "in_progress" else _("complete")
    print(_("Session: iteration {iteration} ({status})").format(
        iteration=iteration,
        status=status_label,
    ))
    print()

    if selected_summary:
        _print_selected_change(selected_summary)

    if file_review_summary:
        _print_file_review(file_review_summary)

    print(_("Progress this iteration:"))
    print(_("  Included:  {count} hunks").format(count=progress["included"]))
    print(_("  Skipped:   {count} hunks").format(count=len(skipped_hunks)))
    print(_("  Discarded: {count} hunks").format(count=progress["discarded"]))
    print(_("  Remaining: ~{count} hunks").format(count=progress["remaining"]))

    if skipped_hunks:
        print()
        print(_("Skipped hunks:"))
        for hunk in skipped_hunks:
            _print_skipped_hunk(hunk)


def _selected_kind_label(selected_kind: str | None) -> str:
    labels = {
        SelectedChangeKind.HUNK.value: _("Current hunk:"),
        SelectedChangeKind.FILE.value: _("Current file review:"),
        SelectedChangeKind.BATCH_FILE.value: _("Current batch file review:"),
        SelectedChangeKind.RENAME.value: _("Current rename:"),
        SelectedChangeKind.DELETION.value: _("Current text file deletion:"),
        SelectedChangeKind.BINARY.value: _("Current binary file:"),
        SelectedChangeKind.BATCH_BINARY.value: _("Current batch binary file:"),
        SelectedChangeKind.GITLINK.value: _("Current submodule pointer:"),
        SelectedChangeKind.BATCH_GITLINK.value: _("Current batch submodule pointer:"),
    }
    return labels.get(selected_kind or SelectedChangeKind.HUNK.value, _("Current selection:"))


def _print_selected_change(selected_summary: dict) -> None:
    ids_str = format_id_range(selected_summary["ids"])
    print(_selected_kind_label(selected_summary.get("kind")))
    if selected_summary.get("line") is None:
        print(_("  {file}").format(file=selected_summary["file"]))
    else:
        print(_("  {file}:{line}").format(
            file=selected_summary["file"],
            line=selected_summary["line"],
        ))
    if ids_str:
        print(_("  [#{ids}]").format(ids=ids_str))
    if selected_summary.get("change_type"):
        print(_("  {change_type}").format(
            change_type=selected_summary["change_type"],
        ))
    print()


def _print_file_review(file_review_summary: dict) -> None:
    print(_("Last file review:"))
    source = file_review_summary["source"]
    if file_review_summary["batch_name"]:
        source = _("batch {name}").format(name=file_review_summary["batch_name"])
    print(_("  source: {source}").format(source=source))
    print(
        _("  pages: {pages}/{count}").format(
            pages=format_id_range(file_review_summary["shown_pages"]),
            count=file_review_summary["page_count"],
        )
    )
    if not file_review_summary["entire_file_shown"]:
        print(_("  partial review; bare whole-file actions will require confirmation by command"))
    if not file_review_summary["fresh"]:
        print(_("  stale; run show again before using pathless line actions"))
    print()


def _print_skipped_hunk(hunk: dict) -> None:
    ids_str = format_id_range(hunk.get("ids", []))
    if hunk.get("line") is None:
        print(_("  {file}").format(file=hunk["file"]))
    elif ids_str:
        print(_("  {file}:{line} [#{ids}]").format(
            file=hunk["file"],
            line=hunk["line"],
            ids=ids_str,
        ))
    else:
        print(_("  {file}:{line}").format(file=hunk["file"], line=hunk["line"]))
