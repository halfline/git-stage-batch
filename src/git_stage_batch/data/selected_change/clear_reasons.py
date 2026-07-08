"""Selected-change clear-reason persistence and refusal helpers."""

from __future__ import annotations

import json
from enum import Enum

from ...exceptions import CommandError
from ...i18n import _
from ...utils.file_io import read_text_file_contents, write_text_file_contents
from ...utils.paths import get_selected_change_clear_reason_file_path
from .store import read_selected_change_kind


class SelectedChangeClearReason(str, Enum):
    """Reasons selected change state was intentionally cleared."""

    AUTO_ADVANCE_DISABLED = "auto-advance-disabled"
    FILE_LIST = "file-list"
    STALE_BATCH_SELECTION = "stale-batch-selection"


def mark_selected_change_cleared_by_file_list(
    *,
    source: str,
    batch_name: str | None = None,
) -> None:
    """Record that a navigational file list intentionally cleared selection."""
    _write_selected_change_clear_reason(
        reason=SelectedChangeClearReason.FILE_LIST,
        source=source,
        batch_name=batch_name,
    )


def mark_selected_change_cleared_by_stale_batch_selection(
    *,
    batch_name: str,
    file_path: str,
) -> None:
    """Record that a batch mutation invalidated the selected batch file."""
    _write_selected_change_clear_reason(
        reason=SelectedChangeClearReason.STALE_BATCH_SELECTION,
        source="batch",
        batch_name=batch_name,
        file_path=file_path,
    )


def mark_selected_change_cleared_by_auto_advance_disabled() -> None:
    """Record that an action left the next change unselected."""
    _write_selected_change_clear_reason(
        reason=SelectedChangeClearReason.AUTO_ADVANCE_DISABLED,
        source="auto-advance",
    )


def selected_change_was_cleared_by_file_list(
    *,
    source: str | None = None,
    batch_name: str | None = None,
) -> bool:
    """Return whether the current empty selection came from a file list."""
    if read_selected_change_kind() is not None:
        return False
    marker = _read_selected_change_clear_reason()
    if marker is None:
        return False
    if marker["reason"] != SelectedChangeClearReason.FILE_LIST.value:
        return False
    marker_source = marker["source"]
    marker_batch_name = marker["batch_name"]
    if source is not None and marker_source != source:
        return False
    if batch_name is not None and marker_batch_name != batch_name:
        return False
    return True


def selected_change_was_cleared_by_stale_batch_selection(
    *,
    batch_name: str | None = None,
) -> bool:
    """Return whether the current empty selection is a stale batch selection."""
    if read_selected_change_kind() is not None:
        return False
    marker = _read_selected_change_clear_reason()
    if marker is None:
        return False
    if marker["reason"] != SelectedChangeClearReason.STALE_BATCH_SELECTION.value:
        return False
    if batch_name is not None and marker["batch_name"] != batch_name:
        return False
    return True


def selected_change_was_cleared_by_auto_advance_disabled() -> bool:
    """Return whether the current empty selection needs an explicit show."""
    if read_selected_change_kind() is not None:
        return False
    marker = _read_selected_change_clear_reason()
    if marker is None:
        return False
    return marker["reason"] == SelectedChangeClearReason.AUTO_ADVANCE_DISABLED.value


def refuse_bare_action_after_file_list(
    action_command: str,
    *,
    open_command: str = "git-stage-batch show --file PATH",
    source: str | None = None,
    batch_name: str | None = None,
) -> None:
    """Refuse a bare action after a navigational file list cleared selection."""
    if not selected_change_was_cleared_by_file_list(source=source, batch_name=batch_name):
        return
    raise CommandError(
        _(
            "No selected change.\n"
            "The last command only showed files; it did not choose one for follow-up actions.\n\n"
            "Run:\n"
            "  git-stage-batch show\n"
            "or choose a file with:\n"
            "  {open_command}\n"
            "before running:\n"
            "  git-stage-batch {action}"
        ).format(open_command=open_command, action=action_command)
    )


def refuse_bare_action_after_auto_advance_disabled(action_command: str) -> None:
    """Refuse a bare action after a command declined to select the next hunk."""
    if not selected_change_was_cleared_by_auto_advance_disabled():
        return
    raise CommandError(
        _(
            "No selected change.\n"
            "The previous command did not choose the next hunk because automatic "
            "advancement is disabled.\n\n"
            "Run:\n"
            "  git-stage-batch show\n"
            "before running:\n"
            "  git-stage-batch {action}"
        ).format(action=action_command)
    )


def refuse_bare_action_after_stale_batch_selection(
    action_command: str,
    *,
    batch_name: str,
) -> None:
    """Refuse a bare batch action after the selected batch file went stale."""
    if not selected_change_was_cleared_by_stale_batch_selection(batch_name=batch_name):
        return

    marker = _read_selected_change_clear_reason() or {}
    file_path = marker.get("file_path") or "the previously selected file"
    raise CommandError(
        _(
            "No selected change.\n"
            "The selected batch file '{file}' was changed or removed from batch '{batch}'.\n\n"
            "Open a current batch file with:\n"
            "  git-stage-batch show --from {batch} --file PATH\n"
            "before running:\n"
            "  git-stage-batch {action}"
        ).format(file=file_path, batch=batch_name, action=action_command)
    )


def _write_selected_change_clear_reason(
    *,
    reason: SelectedChangeClearReason,
    source: str,
    batch_name: str | None = None,
    file_path: str | None = None,
) -> None:
    """Write a structured selected-change clear marker."""
    write_text_file_contents(
        get_selected_change_clear_reason_file_path(),
        json.dumps(
            {
                "reason": reason.value,
                "source": source,
                "batch_name": batch_name,
                "file_path": file_path,
            },
            ensure_ascii=False,
            indent=0,
        ),
    )


def _read_selected_change_clear_reason() -> dict[str, str | None] | None:
    """Return the structured clear marker, tolerating legacy plain-text state."""
    raw_reason = read_text_file_contents(get_selected_change_clear_reason_file_path()).strip()
    if not raw_reason:
        return None
    if raw_reason == SelectedChangeClearReason.FILE_LIST.value:
        return {
            "reason": SelectedChangeClearReason.FILE_LIST.value,
            "source": None,
            "batch_name": None,
        }
    try:
        data = json.loads(raw_reason)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    reason = data.get("reason")
    if reason not in {item.value for item in SelectedChangeClearReason}:
        return None
    return {
        "reason": reason,
        "source": data.get("source") if isinstance(data.get("source"), str) else None,
        "batch_name": data.get("batch_name") if isinstance(data.get("batch_name"), str) else None,
        "file_path": data.get("file_path") if isinstance(data.get("file_path"), str) else None,
    }
