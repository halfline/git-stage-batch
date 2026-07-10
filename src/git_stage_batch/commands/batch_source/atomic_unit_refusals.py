"""Atomic ownership refusal helpers for batch-source commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...data.progress import format_id_range
from ...exceptions import exit_with_error
from ...i18n import _

if TYPE_CHECKING:
    from ...core.models import RenderedBatchDisplay
    from ...exceptions import AtomicUnitError


def translate_atomic_unit_error_to_gutter_ids(
    error: "AtomicUnitError",
    rendered: "RenderedBatchDisplay",
    operation_verb: str,
    batch_name: str,
) -> None:
    """Translate ownership selection IDs to gutter IDs before refusing."""
    if error.required_selection_ids:
        gutter_ids = [
            rendered.selection_id_to_gutter[selection_id]
            for selection_id in sorted(error.required_selection_ids)
            if selection_id in rendered.selection_id_to_gutter
        ]

        if gutter_ids:
            required_range = format_id_range(gutter_ids)
            explanation = _atomic_unit_explanation(error.unit_kind)

            exit_with_error(
                _("{explanation}\nUse: --line {range}").format(
                    explanation=explanation,
                    range=required_range,
                )
            )

    exit_with_error(
        _("Failed to {operation} batch '{name}': {error}").format(
            operation=operation_verb,
            name=batch_name,
            error=str(error),
        )
    )


def _atomic_unit_explanation(unit_kind: str | None) -> str:
    if unit_kind == "replacement":
        return _(
            "These lines form a replacement (deletion + addition) and must "
            "be selected together."
        )
    if unit_kind == "deletion_only":
        return _("These lines form a deletion and must be selected together.")
    return _("These lines must be selected together.")
