"""Inspect and remove diagnostic journal data."""

from __future__ import annotations

import json

from ..exceptions import CommandError
from ..i18n import _, ngettext
from ..utils.journal import (
    JournalLevel,
    get_journal_level,
    get_journal_path,
    purge_journal,
    summarize_journal,
)


def command_journal(
    *,
    path_only: bool = False,
    purge: bool = False,
    all_repositories: bool = False,
    porcelain: bool = False,
) -> None:
    """Locate, summarize, or purge diagnostic journal data."""
    if all_repositories and not purge:
        raise CommandError(_("--all can only be used with --purge."))

    if path_only:
        path = str(get_journal_path())
        if porcelain:
            print(json.dumps({"path": path}, sort_keys=True))
        else:
            print(path)
        return

    if purge:
        removed = purge_journal(all_repositories=all_repositories)
        if porcelain:
            print(json.dumps({"removed_file_count": removed}, sort_keys=True))
        else:
            print(
                ngettext(
                    "Removed {count} diagnostic journal file.",
                    "Removed {count} diagnostic journal files.",
                    removed,
                ).format(count=removed)
            )
        return

    summary = summarize_journal()
    if porcelain:
        print(json.dumps(summary, sort_keys=True))
        return

    print(_("Diagnostic journal"))
    level_labels = {
        "disabled": _("disabled"),
        "metadata-only": _("metadata only"),
        "verbose": _("verbose"),
        "content-debug": _("content debug"),
    }
    print(
        _("  Level: {level}").format(
            level=level_labels.get(summary["level"], summary["level"])
        )
    )
    print(_("  Path: {path}").format(path=summary["path"]))
    print(
        ngettext(
            "  File: {count}",
            "  Files: {count}",
            summary["file_count"],
        ).format(count=summary["file_count"])
    )
    print(
        ngettext(
            "  Entry: {count}",
            "  Entries: {count}",
            summary["entry_count"],
        ).format(count=summary["entry_count"])
    )
    print(
        ngettext(
            "  Size: {count} byte",
            "  Size: {count} bytes",
            summary["total_bytes"],
        ).format(count=summary["total_bytes"])
    )
    if get_journal_level() == JournalLevel.CONTENT_DEBUG:
        print(
            _(
                "  Warning: content-debug records raw paths and short content "
                "previews."
            )
        )
