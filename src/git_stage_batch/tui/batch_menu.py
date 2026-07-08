"""Batch-management submenu for interactive mode."""

from __future__ import annotations

import sys

from ..batch.query import list_batch_names, read_batch_metadata
from ..commands.annotate import command_annotate_batch
from ..commands.apply_from import command_apply_from_batch
from ..commands.drop import command_drop_batch
from ..commands.new import command_new_batch
from ..commands.sift import command_sift_batch
from ..i18n import _
from ..output.colors import Colors, format_hotkey


def handle_batch_menu() -> None:
    """Handle batch management submenu."""
    use_color = Colors.enabled()

    while True:
        batch_names = list_batch_names()

        if not batch_names:
            print()
            print(_("No batches found. Create one now."))
            if not _batch_create():
                return
            continue

        print()
        print(_("Existing batches:"))
        for name in batch_names:
            metadata = read_batch_metadata(name)
            note = metadata.get("note", "")
            if note:
                if use_color:
                    print(
                        _("  {name} - {note}").format(
                            name=f"{Colors.CYAN}{name}{Colors.RESET}",
                            note=note,
                        )
                    )
                else:
                    print(_("  {name} - {note}").format(name=name, note=note))
            else:
                if use_color:
                    print(f"  {Colors.CYAN}{name}{Colors.RESET}")
                else:
                    print(f"  {name}")
        print()

        print(_("Batch operations:"))
        operations = [
            (_("create"), "c", Colors.GREEN if use_color else ""),
            (_("edit"), "e", ""),
            (_("drop"), "d", Colors.RED if use_color else ""),
            (_("apply"), "a", ""),
            (_("sift"), "s", ""),
        ]
        for text, hotkey, color in operations:
            formatted = format_hotkey(text, hotkey, color)
            print(f"  {formatted}")
        print()

        try:
            action = input(_("Select: ")).strip().lower()
        except (KeyboardInterrupt, EOFError):
            return

        if not action:
            return

        if action in ("c", "create"):
            _batch_create()
        elif action in ("e", "edit"):
            _batch_edit()
        elif action in ("d", "drop"):
            _batch_drop()
        elif action in ("a", "apply"):
            _batch_apply()
        elif action in ("s", "sift"):
            _batch_sift()
        else:
            print(_("\nUnknown action: '{action}'").format(action=action))


def _batch_create() -> bool:
    """Prompt for batch ID and note, then create a new batch."""
    try:
        batch_id = input(_("Batch ID: ")).strip()
        if not batch_id:
            return False

        note = input(_("Note (optional): ")).strip()
    except (KeyboardInterrupt, EOFError):
        return False

    command_new_batch(batch_name=batch_id, note=note if note else None)
    print(_("\nBatch '{name}' created.").format(name=batch_id))
    return True


def _batch_edit() -> None:
    """Prompt to select a batch and edit its note."""
    batch_name = _prompt_select_batch(purpose=_("edit"), skip_if_single=True)
    if not batch_name:
        return

    try:
        note = input(_("New note: ")).strip()
    except (KeyboardInterrupt, EOFError):
        return

    command_annotate_batch(batch_name, note)
    print(_("\nBatch '{name}' note updated.").format(name=batch_name))


def _batch_drop() -> None:
    """Prompt to select a batch and drop it."""
    batch_name = _prompt_select_batch(purpose=_("drop"), skip_if_single=True)
    if not batch_name:
        return

    command_drop_batch(batch_name)
    print(_("\nBatch '{name}' dropped.").format(name=batch_name))


def _batch_apply() -> None:
    """Prompt to select a batch and apply it."""
    batch_name = _prompt_select_batch(purpose=_("apply"), skip_if_single=False)
    if not batch_name:
        return

    command_apply_from_batch(batch_name)
    print(_("\nBatch '{name}' applied to staging area.").format(name=batch_name))


def _batch_sift() -> None:
    """Prompt to select a batch and sift it."""
    source_batch = _prompt_select_batch(purpose=_("sift"), skip_if_single=True)
    if not source_batch:
        return

    try:
        dest_batch = input(_("Destination batch (empty for in-place): ")).strip()
    except (KeyboardInterrupt, EOFError):
        return

    if not dest_batch:
        dest_batch = source_batch

    command_sift_batch(source_batch, dest_batch)
    print(
        _("\nBatch '{source}' sifted to '{dest}'.").format(
            source=source_batch,
            dest=dest_batch,
        )
    )


def _prompt_select_batch(purpose: str, skip_if_single: bool = False) -> str:
    """Show list of batches and prompt the user to select one."""
    batch_names = list_batch_names()
    if not batch_names:
        print()
        print(_("No batches found."), file=sys.stderr)
        return ""

    if len(batch_names) == 1 and skip_if_single:
        return batch_names[0]

    print()
    print(_("Select batch to {purpose}:").format(purpose=purpose))
    for idx, name in enumerate(batch_names, 1):
        metadata = read_batch_metadata(name)
        note = metadata.get("note", "")
        note_display = f" - {note}" if note else ""
        print(f"  [{idx}] {name}{note_display}")

    print()
    try:
        choice = input(_("Select: ")).strip()
    except (KeyboardInterrupt, EOFError):
        return ""

    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(batch_names):
            return batch_names[idx]
    elif choice in batch_names:
        return choice

    print(_("\nInvalid selection."), file=sys.stderr)
    return ""
