"""Prompt status rendering."""

from __future__ import annotations

from string import Formatter
from typing import TypedDict

from ..data.progress import format_id_range
from ..data.status_types import StatusSummary
from ..exceptions import CommandError
from ..i18n import _


DEFAULT_PROMPT_FORMAT = "STAGING"
_PROMPT_FIELDS = frozenset(
    {
        "active",
        "change_type",
        "discarded",
        "file_review_batch",
        "file_review_fresh",
        "file_review_source",
        "included",
        "in_progress",
        "iteration",
        "processed",
        "progress_label",
        "progress_status",
        "remaining",
        "selected_file",
        "selected_ids",
        "selected_kind",
        "selected_line",
        "skipped",
        "status",
        "status_label",
        "total",
    }
)
_LIGHT_PROMPT_FIELDS = frozenset({"active"})


class _PromptValues(TypedDict, total=False):
    """Values exposed to status prompt format strings."""

    active: bool
    change_type: str
    discarded: int
    file_review_batch: str
    file_review_fresh: bool | str
    file_review_source: str
    included: int
    in_progress: bool
    iteration: int
    processed: int
    progress_label: str
    progress_status: str
    remaining: int
    selected_file: str
    selected_ids: str
    selected_kind: str
    selected_line: int | str
    skipped: int
    status: str
    status_label: str
    total: int


def prompt_needs_status_summary(prompt_format: str) -> bool:
    """Return whether rendering a prompt format requires session state."""
    return bool(_prompt_field_names(prompt_format) - _LIGHT_PROMPT_FIELDS)


def _prompt_field_names(prompt_format: str) -> set[str]:
    """Return top-level field names used by a status prompt format string."""
    fields: set[str] = set()
    try:
        parsed = Formatter().parse(prompt_format)
        for _literal_text, field_name, _format_spec, _conversion in parsed:
            if field_name is None:
                continue
            if field_name == "":
                raise CommandError(_("Status prompt format cannot use positional fields."))
            field_name = field_name.split(".", 1)[0].split("[", 1)[0]
            if field_name not in _PROMPT_FIELDS:
                raise CommandError(
                    _("Unknown status prompt field '{field}'.").format(
                        field=field_name
                    )
                )
            fields.add(field_name)
    except ValueError as error:
        raise CommandError(
            _("Invalid status prompt format: {error}").format(error=str(error))
        ) from error
    return fields


def _prompt_values(
    summary: StatusSummary | None = None,
) -> _PromptValues:
    """Return values available to `status --for-prompt` format strings."""
    if summary is None:
        return {"active": True}

    session = summary["session"]
    progress = summary["progress"]
    selected = summary["selected_change"]
    file_review = summary["file_review"]
    progress_status = session["status"]
    progress_label = _("in progress") if progress_status == "in_progress" else _("complete")
    processed = progress["included"] + progress["skipped"] + progress["discarded"]
    total = processed + progress["remaining"]
    status = DEFAULT_PROMPT_FORMAT

    return {
        "active": session["active"],
        "change_type": (
            selected.get("change_type") or ""
            if selected is not None
            else ""
        ),
        "discarded": progress["discarded"],
        "file_review_batch": (
            file_review.get("batch_name") or ""
            if file_review is not None
            else ""
        ),
        "file_review_fresh": (
            file_review.get("fresh", "")
            if file_review is not None
            else ""
        ),
        "file_review_source": (
            file_review.get("source") or ""
            if file_review is not None
            else ""
        ),
        "included": progress["included"],
        "in_progress": session["in_progress"],
        "iteration": session["iteration"],
        "processed": processed,
        "progress_label": progress_label,
        "progress_status": progress_status,
        "remaining": progress["remaining"],
        "selected_file": (
            selected.get("file") or ""
            if selected is not None
            else ""
        ),
        "selected_ids": format_id_range(
            selected.get("ids") or []
            if selected is not None
            else []
        ),
        "selected_kind": (
            selected.get("kind") or ""
            if selected is not None
            else ""
        ),
        "selected_line": (
            selected.get("line") or ""
            if selected is not None
            else ""
        ),
        "skipped": progress["skipped"],
        "status": status,
        "status_label": status,
        "total": total,
    }


def render_prompt_status(
    prompt_format: str,
    summary: StatusSummary | None = None,
) -> str:
    """Render a prompt status segment for an active session."""
    fields = _prompt_field_names(prompt_format)
    values = _prompt_values(summary if fields - _LIGHT_PROMPT_FIELDS else None)
    try:
        return prompt_format.format_map(values)
    except KeyError as error:
        raise CommandError(
            _("Unknown status prompt field '{field}'.").format(field=error.args[0])
        ) from error
    except ValueError as error:
        raise CommandError(
            _("Invalid status prompt format: {error}").format(error=str(error))
        ) from error
